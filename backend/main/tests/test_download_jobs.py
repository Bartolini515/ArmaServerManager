from contextlib import ExitStack, contextmanager
import logging
import socket
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from celery.exceptions import Ignore
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from main.download_jobs import (
    DownloadAdmissionError,
    DownloadLeasePreparationError,
    build_download_redis_client,
    reserve_download,
)
from main.download_leases import (
    AtomicDownloadLease,
    BoundedConnection,
    BoundedSSLConnection,
)
from main.instance_tasks import create_instance_task_binding
from main.models import InstanceTask, Instances, Ports
from main.steamcmd.processes import CleanupUncertainError
from main.tasks import download_mods_task
from main.utils.steam_errors import SteamCommandError


User = get_user_model()


class DownloadAdmissionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_user(
            username="download-owner",
            password="test-password",
        )
        self.next_port = 25200

    def make_instance(self, *, state=Instances.LifecycleState.STOPPED):
        self.next_port += 1
        port = Ports.objects.create(
            port_number=self.next_port,
            is_available=False,
        )
        return Instances.objects.create(
            name=f"download-server-{self.next_port}",
            user=self.owner,
            preset=SimpleUploadedFile(
                f"download-server-{self.next_port}.html",
                b"<html></html>",
            ),
            port=port,
            lifecycle_state=state,
        )

    def test_same_instance_reservation_reuses_the_committed_binding(self):
        instance = self.make_instance()

        first_binding, first_reused = reserve_download(instance.pk)
        second_binding, second_reused = reserve_download(instance.pk)

        self.assertFalse(first_reused)
        self.assertTrue(second_reused)
        self.assertEqual(second_binding.task_id, first_binding.task_id)
        self.assertEqual(
            InstanceTask.objects.filter(
                instance=instance,
                kind=InstanceTask.Kind.DOWNLOAD,
                finished_at__isnull=True,
                superseded_at__isnull=True,
            ).count(),
            1,
        )

    def test_unfinished_download_is_a_global_admission_fence(self):
        first = self.make_instance()
        second = self.make_instance()
        binding, _reused = reserve_download(first.pk)

        with self.assertRaises(DownloadAdmissionError) as raised:
            reserve_download(second.pk)

        self.assertEqual(raised.exception.code, "busy")
        self.assertTrue(
            InstanceTask.objects.filter(pk=binding.task_id, finished_at__isnull=True).exists()
        )
        self.assertFalse(
            InstanceTask.objects.filter(instance=second, kind=InstanceTask.Kind.DOWNLOAD).exists()
        )

    def test_superseded_download_does_not_hold_the_global_fence(self):
        first = self.make_instance()
        second = self.make_instance()
        stale = create_instance_task_binding(first, InstanceTask.Kind.DOWNLOAD)
        stale.superseded_at = timezone.now()
        stale.save(update_fields=["superseded_at"])

        binding, reused = reserve_download(second.pk)

        self.assertFalse(reused)
        self.assertNotEqual(binding.task_id, stale.task_id)
        self.assertEqual(binding.instance_id, second.pk)

    def test_active_server_or_other_unfinished_work_rejects_admission(self):
        running = self.make_instance(state=Instances.LifecycleState.RUNNING)
        running.is_running = True
        running.pid = 31415
        running.save(update_fields=["is_running", "pid"])

        with self.assertRaises(DownloadAdmissionError) as raised:
            reserve_download(running.pk)

        self.assertEqual(raised.exception.code, "not_stopped")
        self.assertFalse(
            InstanceTask.objects.filter(instance=running, kind=InstanceTask.Kind.DOWNLOAD).exists()
        )

        stopped = self.make_instance()
        create_instance_task_binding(stopped, InstanceTask.Kind.START)
        with self.assertRaises(DownloadAdmissionError) as raised:
            reserve_download(stopped.pk)
        self.assertEqual(raised.exception.code, "busy")

    def test_autocommit_is_required_before_admission(self):
        instance = self.make_instance()

        from django.db import transaction

        with transaction.atomic():
            with self.assertRaises(RuntimeError):
                reserve_download(instance.pk)

        self.assertFalse(
            InstanceTask.objects.filter(instance=instance, kind=InstanceTask.Kind.DOWNLOAD).exists()
        )


class DownloadRedisFactoryTests(SimpleTestCase):
    @staticmethod
    def resolved_ipv4(host, port, *, type):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("192.0.2.44", int(port)),
            )
        ]

    def test_factory_builds_the_fixed_offline_bounded_client(self):
        resolver = Mock(side_effect=self.resolved_ipv4)

        client = build_download_redis_client(
            "redis://lease-user:lease-password@redis.example:6379/4",
            resolver=resolver,
        )
        lease = AtomicDownloadLease(client)
        pool = client.connection_pool

        resolver.assert_called_once()
        self.assertTrue(lease._has_bounded_redis_io(client))
        self.assertEqual(pool.max_connections, 4)
        self.assertIs(pool.connection_class, BoundedConnection)
        self.assertEqual(pool.connection_kwargs["numeric_host"], "192.0.2.44")
        self.assertEqual(pool.connection_kwargs["db"], 4)
        self.assertEqual(pool.connection_kwargs["username"], "lease-user")
        self.assertEqual(pool.connection_kwargs["password"], "lease-password")
        self.assertEqual(pool.timeout, 1)
        self.assertEqual(pool.connection_kwargs["socket_timeout"], 1)
        self.assertEqual(pool.connection_kwargs["socket_connect_timeout"], 1)
        self.assertFalse(client._single_connection_client)

    def test_factory_skips_resolution_for_numeric_host(self):
        resolver = Mock(side_effect=AssertionError("numeric host must not resolve"))

        client = build_download_redis_client(
            "redis://127.0.0.1:6379/1",
            resolver=resolver,
        )

        resolver.assert_not_called()
        self.assertEqual(client.connection_pool.connection_kwargs["numeric_host"], "127.0.0.1")

    def test_factory_keeps_verified_tls_and_original_host_identity(self):
        resolver = Mock(side_effect=self.resolved_ipv4)

        client = build_download_redis_client(
            "rediss://redis.example:6380/2",
            resolver=resolver,
        )
        lease = AtomicDownloadLease(client)
        options = client.connection_pool.connection_kwargs

        self.assertTrue(lease._has_bounded_redis_io(client))
        self.assertIs(client.connection_pool.connection_class, BoundedSSLConnection)
        self.assertEqual(options["host"], "redis.example")
        self.assertEqual(options["numeric_host"], "192.0.2.44")
        self.assertEqual(options["ssl_cert_reqs"], "required")
        self.assertTrue(options["ssl_check_hostname"])
        self.assertFalse(options["ssl_validate_ocsp"])
        self.assertFalse(options["ssl_validate_ocsp_stapled"])

    def test_unsupported_location_is_rejected_before_resolution_without_echoing_it(self):
        resolver = Mock(side_effect=AssertionError("unsupported URL must fail before DNS"))
        secret_url = "redis://secret-user:secret-password@redis.example:6379/0?socket_timeout=9"

        with self.assertRaises(DownloadLeasePreparationError) as raised:
            build_download_redis_client(secret_url, resolver=resolver)

        resolver.assert_not_called()
        self.assertNotIn("secret-user", str(raised.exception))
        self.assertNotIn("secret-password", str(raised.exception))
        self.assertNotIn("redis.example", str(raised.exception))

    def test_unsupported_transports_and_multi_server_urls_fail_before_resolution(self):
        resolver = Mock(side_effect=AssertionError("unsupported transport must fail before DNS"))

        for location in (
            "redis+unix:///run/redis.sock",
            "redis://redis-a.example:6379,redis-b.example:6379/0",
            "redis-sentinel://redis.example:26379/0",
        ):
            with self.subTest(location=location):
                with self.assertRaises(DownloadLeasePreparationError):
                    build_download_redis_client(location, resolver=resolver)

        resolver.assert_not_called()

    def test_top_level_cache_hooks_fail_before_dns_sockets_or_hook_calls(self):
        secret_location = "redis://secret-user:secret-password@redis.example:6379/1"
        hook_calls: list[object] = []

        def custom_key_function(*args, **kwargs):
            hook_calls.append((args, kwargs))
            return "invented-key"

        unsupported_top_level_settings = (
            ("KEY_FUNCTION", custom_key_function),
            ("CUSTOM_HOOK_MODE", custom_key_function),
        )
        for setting_name, hook_value in unsupported_top_level_settings:
            with self.subTest(setting=setting_name):
                resolver = Mock(side_effect=self.resolved_ipv4)
                cache_configuration = {
                    "BACKEND": "django_redis.cache.RedisCache",
                    "LOCATION": secret_location,
                    "OPTIONS": {
                        "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    },
                    setting_name: hook_value,
                }

                with (
                    patch.object(settings, "CACHES", {"default": cache_configuration}),
                    patch("main.download_jobs.socket.socket") as socket_constructor,
                ):
                    with self.assertRaises(DownloadLeasePreparationError) as raised:
                        build_download_redis_client(resolver=resolver)

                resolver.assert_not_called()
                socket_constructor.assert_not_called()
                self.assertEqual(hook_calls, [])
                self.assertNotIn(secret_location, str(raised.exception))
                self.assertNotIn("secret-user", str(raised.exception))
                self.assertNotIn("secret-password", str(raised.exception))
                self.assertNotIn("redis.example", str(raised.exception))


@contextmanager
def bound_task_request(task, task_id):
    task.push_request(id=task_id)
    try:
        yield
    finally:
        task.pop_request()


class DownloadWorkerTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_user(
            username="worker-owner",
            password="test-password",
        )
        self.next_port = 25300

    def make_instance(self, *, ready=False):
        self.next_port += 1
        port = Ports.objects.create(
            port_number=self.next_port,
            is_available=False,
        )
        return Instances.objects.create(
            name=f"worker-server-{self.next_port}",
            user=self.owner,
            preset=SimpleUploadedFile(
                f"worker-server-{self.next_port}.html",
                b"<html>current preset</html>",
            ),
            port=port,
            lifecycle_state=Instances.LifecycleState.STOPPED,
            is_ready=ready,
        )

    def mark_download_attention_through_api(self, binding, *, expected_claimed_at=None):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        private_result = {"status": "PRIVATE_LATE_DELIVERY_BACKEND_RESULT"}
        with patch(
            "main.views.AsyncResult",
            return_value=SimpleNamespace(status="REVOKED", result=private_result),
        ) as async_result:
            response = client.get(
                f"/api/instances/task_status/{binding.task_id}/"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "id": binding.task_id,
                "state": "REQUIRES_ATTENTION",
                "result": {
                    "status": "Stan zadania wymaga ręcznej weryfikacji przez administratora."
                },
            },
        )
        self.assertNotIn("PRIVATE_LATE_DELIVERY_BACKEND_RESULT", repr(response.data))
        async_result.assert_called_once_with(binding.task_id)
        binding.refresh_from_db()
        self.assertTrue(binding.requires_attention)
        self.assertEqual(
            binding.attention_reason,
            InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
        )
        self.assertEqual(binding.claimed_at, expected_claimed_at)
        self.assertIsNone(binding.finished_at)
        return client

    def test_late_delivery_of_api_attention_binding_is_ignored_before_worker_work(self):
        instance = self.make_instance(ready=True)
        binding, _reused = reserve_download(instance.pk)
        client = self.mark_download_attention_through_api(binding)
        binding.refresh_from_db()
        attention_fields = (
            binding.requires_attention,
            binding.attention_reason,
            binding.attention_workspace,
            binding.claimed_at,
            binding.finished_at,
            binding.superseded_at,
        )

        with (
            patch(
                "main.download_jobs.config.snapshot",
                side_effect=AssertionError("attention delivery must stop before config"),
            ) as snapshot,
            patch(
                "main.download_jobs._validate_current_preset",
                side_effect=AssertionError("attention delivery must stop before preset IO"),
            ) as validate_preset,
            patch(
                "main.download_jobs._new_download_lease",
                side_effect=AssertionError("attention delivery must stop before Redis"),
            ) as lease_factory,
            patch(
                "main.download_jobs.operation_loggers",
                side_effect=AssertionError("attention delivery must stop before logging"),
            ) as operation_loggers,
            patch(
                "main.download_jobs.preset_parser",
                side_effect=AssertionError("attention delivery must stop before parsing"),
            ) as preset_parser,
            patch(
                "main.download_jobs.check_installed",
                side_effect=AssertionError("attention delivery must stop before file checks"),
            ) as check_installed,
            patch(
                "main.download_jobs.download_mods",
                side_effect=AssertionError("attention delivery must stop before SteamCMD"),
            ) as downloader,
            bound_task_request(download_mods_task, binding.task_id),
        ):
            with self.assertRaises(Ignore):
                download_mods_task.run(instance_id=instance.pk)

        for boundary in (
            snapshot,
            validate_preset,
            lease_factory,
            operation_loggers,
            preset_parser,
            check_installed,
            downloader,
        ):
            boundary.assert_not_called()

        binding.refresh_from_db()
        instance.refresh_from_db()
        self.assertEqual(
            (
                binding.requires_attention,
                binding.attention_reason,
                binding.attention_workspace,
                binding.claimed_at,
                binding.finished_at,
                binding.superseded_at,
            ),
            attention_fields,
        )
        self.assertTrue(instance.is_ready)

        target = self.make_instance(ready=True)
        with (
            patch(
                "main.views.config.snapshot",
                side_effect=AssertionError("global attention fence precedes config"),
            ) as config_snapshot,
            patch(
                "main.views.validate_instance_preset",
                side_effect=AssertionError("global attention fence precedes preset IO"),
            ) as api_preset_check,
            patch(
                "main.views.psutil.disk_usage",
                side_effect=AssertionError("global attention fence precedes disk checks"),
            ) as disk_usage,
            patch(
                "main.views.download_mods_task.apply_async",
                side_effect=AssertionError("global attention fence must not publish"),
            ) as dispatch,
        ):
            blocked = client.post(
                f"/api/instances/{target.pk}/download_mods/",
                {},
                format="json",
            )

        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            blocked.data,
            {"message": "Inna operacja serwera lub pobierania jest już w toku."},
        )
        config_snapshot.assert_not_called()
        api_preset_check.assert_not_called()
        disk_usage.assert_not_called()
        dispatch.assert_not_called()
        self.assertFalse(InstanceTask.objects.filter(instance=target).exists())

    def test_attention_download_fence_blocks_another_queued_delivery_before_preparation(self):
        attention_instance = self.make_instance(ready=True)
        attention_binding, _reused = reserve_download(attention_instance.pk)
        self.mark_download_attention_through_api(attention_binding)

        waiting_instance = self.make_instance(ready=True)
        waiting_binding = create_instance_task_binding(
            waiting_instance,
            InstanceTask.Kind.DOWNLOAD,
        )

        with (
            patch(
                "main.download_jobs.config.snapshot",
                side_effect=AssertionError("attention fence must precede config"),
            ) as snapshot,
            patch(
                "main.download_jobs._validate_current_preset",
                side_effect=AssertionError("attention fence must precede preset IO"),
            ) as validate_preset,
            patch(
                "main.download_jobs._new_download_lease",
                side_effect=AssertionError("attention fence must precede Redis"),
            ) as lease_factory,
            patch(
                "main.download_jobs.operation_loggers",
                side_effect=AssertionError("attention fence must precede logging"),
            ) as operation_loggers,
            patch(
                "main.download_jobs.preset_parser",
                side_effect=AssertionError("attention fence must precede parsing"),
            ) as preset_parser,
            patch(
                "main.download_jobs.check_installed",
                side_effect=AssertionError("attention fence must precede file checks"),
            ) as check_installed,
            patch(
                "main.download_jobs.download_mods",
                side_effect=AssertionError("attention fence must precede SteamCMD"),
            ) as downloader,
            bound_task_request(download_mods_task, waiting_binding.task_id),
        ):
            with self.assertRaises(DownloadAdmissionError) as rejected:
                download_mods_task.run(instance_id=waiting_instance.pk)

        self.assertEqual(rejected.exception.code, "busy")
        for boundary in (
            snapshot,
            validate_preset,
            lease_factory,
            operation_loggers,
            preset_parser,
            check_installed,
            downloader,
        ):
            boundary.assert_not_called()

        attention_binding.refresh_from_db()
        attention_instance.refresh_from_db()
        waiting_binding.refresh_from_db()
        waiting_instance.refresh_from_db()
        self.assertTrue(attention_binding.requires_attention)
        self.assertEqual(
            attention_binding.attention_reason,
            InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
        )
        self.assertIsNone(attention_binding.claimed_at)
        self.assertIsNone(attention_binding.finished_at)
        self.assertTrue(attention_instance.is_ready)
        self.assertIsNone(waiting_binding.claimed_at)
        self.assertIsNotNone(waiting_binding.finished_at)
        self.assertFalse(waiting_binding.requires_attention)
        self.assertTrue(waiting_instance.is_ready)

    def test_claim_before_terminal_status_keeps_claim_and_attention_fence(self):
        from main.download_jobs import _claim_download

        instance = self.make_instance(ready=True)
        binding, _reused = reserve_download(instance.pk)
        _claimed_instance, _claimed_binding = _claim_download(
            binding.task_id,
            instance.pk,
        )
        binding.refresh_from_db()
        claimed_at = binding.claimed_at
        self.assertIsNotNone(claimed_at)

        self.mark_download_attention_through_api(
            binding,
            expected_claimed_at=claimed_at,
        )

        binding.refresh_from_db()
        instance.refresh_from_db()
        self.assertEqual(binding.claimed_at, claimed_at)
        self.assertIsNone(binding.finished_at)
        self.assertTrue(binding.requires_attention)
        self.assertEqual(
            binding.attention_reason,
            InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
        )
        self.assertFalse(instance.is_ready)

    def test_cross_instance_attention_transition_serializes_claim_and_later_guards(self):
        from main.download_jobs import (
            _assert_current_claim,
            _claim_download,
            _persist_ready,
        )

        attention_instance = self.make_instance(ready=True)
        attention_binding = create_instance_task_binding(
            attention_instance,
            InstanceTask.Kind.DOWNLOAD,
        )
        worker_instance = self.make_instance(ready=True)
        worker_binding = create_instance_task_binding(
            worker_instance,
            InstanceTask.Kind.DOWNLOAD,
        )

        # These test-only contexts impose an application-level schedule while
        # ORM statements autocommit. They do not model database row locks.
        coordinator = threading.Lock()
        state_lock = threading.Lock()
        coordinator_owner = [None]
        decision_checked = threading.Event()
        allow_claim_to_continue = threading.Event()
        status_attempted = threading.Event()
        status_finished = threading.Event()
        events = []
        thread_errors = []
        attention_query_results = []
        worker_result = {}
        status_result = {}
        manager = InstanceTask.objects
        original_select_for_update = manager.select_for_update
        original_exists = QuerySet.exists
        original_save = InstanceTask.save
        terminal_result = {"status": "PRIVATE_CROSS_INSTANCE_TERMINAL_RESULT"}

        @contextmanager
        def controlled_lifecycle_transaction():
            thread_name = threading.current_thread().name
            if thread_name == "attention-status":
                status_attempted.set()
            if not coordinator.acquire(timeout=3):
                raise AssertionError("controlled lifecycle coordinator timed out")
            try:
                with state_lock:
                    coordinator_owner[0] = thread_name
                yield
            finally:
                with state_lock:
                    coordinator_owner[0] = None
                coordinator.release()

        @contextmanager
        def legacy_noop_atomic():
            if threading.current_thread().name == "attention-status":
                status_attempted.set()
            yield

        def contains_attention_lookup(node):
            lhs = getattr(node, "lhs", None)
            target = getattr(lhs, "target", None)
            if getattr(target, "name", None) == "requires_attention":
                return True
            return any(
                contains_attention_lookup(child)
                for child in getattr(node, "children", ())
            )

        def watched_exists(queryset):
            if (
                threading.current_thread().name == "claim-worker"
                and contains_attention_lookup(queryset.query.where)
            ):
                result = original_exists(queryset)
                attention_query_results.append(result)
                decision_checked.set()
                if not allow_claim_to_continue.wait(timeout=3):
                    raise AssertionError("claim schedule was not released")
                return result
            return original_exists(queryset)

        def record_write(label):
            with state_lock:
                events.append((label, coordinator_owner[0]))

        def observed_save(task_binding, *args, **kwargs):
            result = original_save(task_binding, *args, **kwargs)
            updated = set(kwargs.get("update_fields") or ())
            if task_binding.pk == worker_binding.pk and updated == {"claimed_at"}:
                record_write("claim")
            elif (
                task_binding.pk == attention_binding.pk
                and updated == {"requires_attention", "attention_reason"}
            ):
                record_write("attention")
            return result

        def watched_select_for_update(*args, **kwargs):
            if threading.current_thread().name == "attention-status":
                with state_lock:
                    events.append(("status-binding-lock", coordinator_owner[0]))
            return original_select_for_update(*args, **kwargs)

        def claim_worker():
            try:
                worker_result["claim"] = _claim_download(
                    worker_binding.task_id,
                    worker_instance.pk,
                )
            except BaseException as exc:
                thread_errors.append(("claim-worker", exc))

        def status_worker():
            client = APIClient()
            client.force_authenticate(user=self.owner)
            try:
                with patch(
                    "main.views.AsyncResult",
                    return_value=SimpleNamespace(
                        status="REVOKED",
                        result=terminal_result,
                    ),
                ):
                    status_result["response"] = client.get(
                        f"/api/instances/task_status/{attention_binding.task_id}/"
                    )
            except BaseException as exc:
                thread_errors.append(("attention-status", exc))
            finally:
                status_finished.set()

        worker = threading.Thread(target=claim_worker, name="claim-worker")
        status = threading.Thread(target=status_worker, name="attention-status")
        threads = []

        with (
            patch.object(QuerySet, "exists", new=watched_exists),
            patch.object(
                manager,
                "select_for_update",
                side_effect=watched_select_for_update,
            ),
            patch.object(InstanceTask, "save", new=observed_save),
            patch(
                "main.download_jobs.lifecycle_transaction",
                new=controlled_lifecycle_transaction,
            ),
            patch(
                "main.views.lifecycle_transaction",
                new=controlled_lifecycle_transaction,
            ),
            patch("main.views.transaction.atomic", new=legacy_noop_atomic),
        ):
            try:
                worker.start()
                threads.append(worker)
                decision_reached = decision_checked.wait(timeout=3)
                if not decision_reached:
                    worker_error_types = [
                        type(error).__name__ for _thread_name, error in thread_errors
                    ]
                    self.fail(
                        "worker did not reach its other-attention decision; "
                        f"captured worker exception types: {worker_error_types}"
                    )
                self.assertEqual(attention_query_results, [False])

                status.start()
                threads.append(status)
                self.assertTrue(
                    status_attempted.wait(timeout=3),
                    "status request did not enter a persistence boundary",
                )
                self.assertFalse(
                    status_finished.wait(timeout=0.5),
                    "attention committed while the worker held the coordinator",
                )
            finally:
                allow_claim_to_continue.set()
                for thread in threads:
                    thread.join(timeout=3)

            self.assertTrue(all(not thread.is_alive() for thread in threads))

            self.assertEqual(thread_errors, [])
            self.assertIn("claim", worker_result)
            claimed_instance, claimed_binding = worker_result["claim"]
            self.assertEqual(claimed_instance.pk, worker_instance.pk)
            self.assertEqual(claimed_binding.pk, worker_binding.pk)
            self.assertEqual(
                [label for label, _owner in events if label in ("claim", "attention")],
                ["claim", "attention"],
            )
            self.assertEqual(
                [owner for label, owner in events if label in ("claim", "attention")],
                ["claim-worker", "attention-status"],
            )
            self.assertEqual(
                [owner for label, owner in events if label == "status-binding-lock"],
                ["attention-status"],
            )

            status_response = status_result["response"]
            self.assertEqual(status_response.status_code, 200)
            self.assertEqual(status_response.data["state"], "REQUIRES_ATTENTION")
            self.assertEqual(
                status_response.data["result"],
                {
                    "status": "Stan zadania wymaga ręcznej weryfikacji przez administratora."
                },
            )
            self.assertNotIn(
                "PRIVATE_CROSS_INSTANCE_TERMINAL_RESULT",
                repr(status_response.data),
            )

            lease = Mock(spec_set=["assert_owned"])
            with self.assertRaises(DownloadAdmissionError) as current_claim_rejected:
                _assert_current_claim(
                    worker_binding.task_id,
                    worker_instance.pk,
                    lease,
                )
            self.assertEqual(current_claim_rejected.exception.code, "busy")

            with self.assertRaises(DownloadAdmissionError) as readiness_rejected:
                _persist_ready(
                    worker_binding.task_id,
                    worker_instance.pk,
                    ready=True,
                    lease=lease,
                )
            self.assertEqual(readiness_rejected.exception.code, "busy")
            lease.assert_owned.assert_not_called()

        attention_binding.refresh_from_db()
        attention_instance.refresh_from_db()
        worker_binding.refresh_from_db()
        worker_instance.refresh_from_db()
        self.assertTrue(attention_binding.requires_attention)
        self.assertEqual(
            attention_binding.attention_reason,
            InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
        )
        self.assertIsNone(attention_binding.claimed_at)
        self.assertIsNone(attention_binding.finished_at)
        self.assertTrue(attention_instance.is_ready)
        self.assertIsNotNone(worker_binding.claimed_at)
        self.assertIsNone(worker_binding.finished_at)
        self.assertFalse(worker_binding.requires_attention)
        self.assertFalse(worker_instance.is_ready)

    def test_healthy_unclaimed_legacy_download_does_not_block_another_claim(self):
        from main.download_jobs import _claim_download

        legacy_instance = self.make_instance(ready=True)
        legacy_binding = create_instance_task_binding(
            legacy_instance,
            InstanceTask.Kind.DOWNLOAD,
        )
        waiting_instance = self.make_instance(ready=True)
        waiting_binding = create_instance_task_binding(
            waiting_instance,
            InstanceTask.Kind.DOWNLOAD,
        )

        claimed_instance, claimed_binding = _claim_download(
            waiting_binding.task_id,
            waiting_instance.pk,
        )

        self.assertEqual(claimed_instance.pk, waiting_instance.pk)
        self.assertEqual(claimed_binding.pk, waiting_binding.pk)
        legacy_binding.refresh_from_db()
        waiting_binding.refresh_from_db()
        legacy_instance.refresh_from_db()
        waiting_instance.refresh_from_db()
        self.assertIsNone(legacy_binding.claimed_at)
        self.assertIsNone(legacy_binding.finished_at)
        self.assertFalse(legacy_binding.requires_attention)
        self.assertIsNotNone(waiting_binding.claimed_at)
        self.assertIsNone(waiting_binding.finished_at)
        self.assertFalse(waiting_binding.requires_attention)
        self.assertTrue(legacy_instance.is_ready)
        self.assertFalse(waiting_instance.is_ready)

    def test_duplicate_delivery_ignores_an_already_claimed_binding(self):
        instance = self.make_instance(ready=True)
        binding, _reused = reserve_download(instance.pk)
        InstanceTask.objects.filter(pk=binding.task_id).update(claimed_at=timezone.now())

        with (
            patch("main.download_jobs.config.snapshot") as snapshot,
            patch("main.download_jobs._new_download_lease") as lease_factory,
            patch("main.download_jobs.download_mods") as downloader,
            bound_task_request(download_mods_task, binding.task_id),
        ):
            with self.assertRaises(Ignore):
                download_mods_task.run(
                    instance_id=instance.pk,
                    name="untrusted-name",
                    user="untrusted-user",
                    file_path="/untrusted/preset.html",
                    mods_directory="/untrusted/mods",
                )

        snapshot.assert_not_called()
        lease_factory.assert_not_called()
        downloader.assert_not_called()
        binding.refresh_from_db()
        instance.refresh_from_db()
        self.assertIsNone(binding.finished_at)
        self.assertTrue(instance.is_ready)

    def test_mismatched_legacy_delivery_does_not_finish_or_mutate_either_instance(self):
        owner_instance = self.make_instance(ready=True)
        other_instance = self.make_instance(ready=True)
        binding, _reused = reserve_download(owner_instance.pk)

        with (
            patch("main.download_jobs.config.snapshot") as snapshot,
            patch("main.download_jobs.download_mods") as downloader,
            bound_task_request(download_mods_task, binding.task_id),
        ):
            with self.assertRaises(Ignore):
                download_mods_task.run(
                    instance_id=other_instance.pk,
                    name="legacy-name",
                    user="legacy-user",
                    file_path="/legacy/preset.html",
                    mods_directory="/legacy/mods",
                )

        snapshot.assert_not_called()
        downloader.assert_not_called()
        binding.refresh_from_db()
        owner_instance.refresh_from_db()
        other_instance.refresh_from_db()
        self.assertIsNone(binding.finished_at)
        self.assertTrue(owner_instance.is_ready)
        self.assertTrue(other_instance.is_ready)

    def test_worker_uses_one_locked_snapshot_and_publishes_only_verified_content(self):
        from main import download_jobs

        instance = self.make_instance(ready=True)
        binding, _reused = reserve_download(instance.pk)
        snapshot = {
            "paths": {
                "logs_directory": "/isolated/logs",
                "mods_directory": "/isolated/mods",
                "steamcmd": "/isolated/steamcmd",
                "download_directory": "/isolated/downloads",
            },
            "steam_auth": {
                "username": "private-user",
                "password": "private-password",
                "shared_secret": "private-shared-secret",
            },
        }
        lease = _FakeLease()
        loggers = {
            "operations": Mock(),
            "download": Mock(),
        }

        @contextmanager
        def fake_loggers(*_args, **_kwargs):
            yield loggers

        with (
            patch.object(download_jobs.config, "snapshot", return_value=snapshot) as get_snapshot,
            patch.object(download_jobs, "_validate_current_preset", return_value="/isolated/current.html") as validate,
            patch.object(download_jobs, "_new_download_lease", return_value=lease),
            patch.object(download_jobs, "operation_loggers", side_effect=fake_loggers),
            patch.object(download_jobs, "preset_parser", return_value=["123456"]),
            patch.object(
                download_jobs,
                "check_installed",
                side_effect=[([], ["123456"]), (["123456"], [])],
            ) as verify_installed,
            patch.object(download_jobs, "download_mods", return_value=None) as downloader,
            patch.object(download_mods_task, "update_state") as update_state,
            bound_task_request(download_mods_task, binding.task_id),
        ):
            result = download_mods_task.run(
                instance_id=instance.pk,
                name="untrusted-name",
                user="private-password",
                file_path="/private/legacy-preset.html",
                mods_directory="/private/legacy-mods",
            )

        self.assertEqual(result, {"status": "Pobieranie zakończone pomyślnie!"})
        get_snapshot.assert_called_once_with()
        validate.assert_called_once_with(instance, snapshot)
        downloader.assert_called_once()
        self.assertEqual(downloader.call_args.kwargs["mods_to_download"], ["123456"])
        self.assertEqual(downloader.call_args.kwargs["name"], instance.name)
        self.assertEqual(downloader.call_args.kwargs["config_snapshot"], snapshot)
        self.assertIsNotNone(downloader.call_args.kwargs["secret_redactor"])
        self.assertEqual(verify_installed.call_count, 2)
        self.assertGreaterEqual(lease.assert_owned_calls, 2)
        self.assertTrue(lease.released)
        self.assertTrue(update_state.called)
        self.assertNotIn("private-user", repr(result))
        self.assertNotIn("private-password", repr(result))
        self.assertNotIn("private-shared-secret", repr(result))
        instance.refresh_from_db()
        binding.refresh_from_db()
        self.assertTrue(instance.is_ready)
        self.assertIsNotNone(binding.finished_at)

    def test_valid_claim_clears_readiness_before_preparation_or_lease_failures(self):
        from main import download_jobs

        failure_stages = ("snapshot", "preset", "factory", "acquire", "heartbeat")
        for failure_stage in failure_stages:
            with self.subTest(stage=failure_stage):
                instance = self.make_instance(ready=True)
                binding, _reused = reserve_download(instance.pk)
                snapshot = self._snapshot()
                lease = _FakeLease()

                with ExitStack() as stack:
                    get_snapshot = stack.enter_context(
                        patch.object(download_jobs.config, "snapshot")
                    )
                    get_snapshot.return_value = snapshot
                    validate_preset = stack.enter_context(
                        patch.object(
                            download_jobs,
                            "_validate_current_preset",
                            return_value="/isolated/current.html",
                        )
                    )
                    lease_factory = stack.enter_context(
                        patch.object(download_jobs, "_new_download_lease", return_value=lease)
                    )
                    stack.enter_context(patch.object(download_jobs, "operation_loggers", side_effect=_fake_loggers))
                    stack.enter_context(bound_task_request(download_mods_task, binding.task_id))

                    if failure_stage == "snapshot":
                        get_snapshot.side_effect = RuntimeError("private snapshot failure")
                    elif failure_stage == "preset":
                        validate_preset.side_effect = RuntimeError("private preset failure")
                    elif failure_stage == "factory":
                        lease_factory.side_effect = DownloadLeasePreparationError()
                    elif failure_stage == "acquire":
                        lease.acquire = Mock(side_effect=RuntimeError("private acquire failure"))
                    elif failure_stage == "heartbeat":
                        lease.start_heartbeat = Mock(side_effect=RuntimeError("private heartbeat failure"))

                    with self.assertRaises(Exception):
                        download_mods_task.run(instance_id=instance.pk)

                instance.refresh_from_db()
                binding.refresh_from_db()
                self.assertFalse(instance.is_ready)
                self.assertIsNotNone(binding.claimed_at)

    def test_preparation_failure_without_redactor_is_safe_and_finishes_binding(self):
        from main import download_jobs

        instance = self.make_instance(ready=True)
        binding, _reused = reserve_download(instance.pk)
        private_values = (
            "INVENTED_PREP_USERNAME_7f31",
            "INVENTED_PREP_PASSWORD_a921",
            "INVENTED_PREP_SHARED_SECRET_c840",
        )
        preparation_error = RuntimeError(";".join(private_values))

        with (
            patch("main.download_jobs.config.snapshot", side_effect=preparation_error) as snapshot,
            patch(
                "main.download_jobs._validate_current_preset",
                side_effect=AssertionError("preset validation must not run"),
            ) as validate_preset,
            patch(
                "main.download_jobs._new_download_lease",
                side_effect=AssertionError("Redis lease creation must not run"),
            ) as lease_factory,
            patch(
                "main.download_jobs.operation_loggers",
                side_effect=AssertionError("operation logging must not run"),
            ) as operation_loggers,
            patch(
                "main.download_jobs.preset_parser",
                side_effect=AssertionError("preset parsing must not run"),
            ) as preset_parser,
            patch(
                "main.download_jobs.check_installed",
                side_effect=AssertionError("host file checks must not run"),
            ) as check_installed,
            patch(
                "main.download_jobs.download_mods",
                side_effect=AssertionError("SteamCMD must not run"),
            ) as downloader,
            bound_task_request(download_mods_task, binding.task_id),
        ):
            with self.assertRaises(SteamCommandError) as raised:
                download_mods_task.run(instance_id=instance.pk)

        failure = raised.exception
        diagnostic = failure.diagnostic
        formatted_traceback = "".join(
            traceback.format_exception(
                type(failure),
                failure,
                failure.__traceback__,
            )
        )
        self.assertEqual(str(failure), "Nie udało się pobrać modów. Sprawdź logi operacji.")
        self.assertEqual(
            diagnostic,
            "Preparation failed before download setup; details omitted",
        )
        self.assertTrue(failure.__suppress_context__)
        for private_value in private_values:
            self.assertNotIn(private_value, diagnostic)
            self.assertNotIn(private_value, formatted_traceback)
            self.assertNotIn(private_value, repr(failure))

        snapshot.assert_called_once_with()
        for boundary in (
            validate_preset,
            lease_factory,
            operation_loggers,
            preset_parser,
            check_installed,
            downloader,
        ):
            boundary.assert_not_called()
        instance.refresh_from_db()
        binding.refresh_from_db()
        self.assertFalse(instance.is_ready)
        self.assertIsNotNone(binding.claimed_at)
        self.assertIsNotNone(binding.finished_at)
        self.assertFalse(binding.requires_attention)

        client = APIClient()
        client.force_authenticate(user=self.owner)
        with patch(
            "main.views.AsyncResult",
            return_value=SimpleNamespace(status="FAILURE", result=failure),
        ) as async_result:
            response = client.get(f"/api/instances/task_status/{binding.task_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "FAILURE")
        self.assertEqual(response.data["result"], {"status": "Nie udało się wykonać zadania."})
        async_result.assert_called_once_with(binding.task_id)
        for private_value in private_values:
            self.assertNotIn(private_value, repr(response.data))

    def test_worker_fails_when_post_download_verification_still_finds_missing_content(self):
        from main import download_jobs

        instance = self.make_instance(ready=True)
        binding, _reused = reserve_download(instance.pk)
        snapshot = self._snapshot()
        lease = _FakeLease()

        with (
            patch.object(download_jobs.config, "snapshot", return_value=snapshot),
            patch.object(download_jobs, "_validate_current_preset", return_value="/isolated/current.html"),
            patch.object(download_jobs, "_new_download_lease", return_value=lease),
            patch.object(download_jobs, "operation_loggers", side_effect=_fake_loggers),
            patch.object(download_jobs, "preset_parser", return_value=["654321"]),
            patch.object(download_jobs, "check_installed", side_effect=[([], ["654321"]), ([], ["654321"])]),
            patch.object(download_jobs, "download_mods", return_value=None),
            bound_task_request(download_mods_task, binding.task_id),
        ):
            with self.assertRaises(Exception):
                download_mods_task.run(instance_id=instance.pk)

        instance.refresh_from_db()
        binding.refresh_from_db()
        self.assertFalse(instance.is_ready)
        self.assertIsNotNone(binding.finished_at)
        self.assertTrue(lease.released)

    def test_cleanup_uncertainty_persists_attention_without_finishing_binding(self):
        from main import download_jobs

        instance = self.make_instance()
        binding, _reused = reserve_download(instance.pk)
        snapshot = self._snapshot()
        lease = _FakeLease()
        uncertain = CleanupUncertainError(RuntimeError("private process diagnostic"))
        uncertain.workspace_path = "/isolated/private-workspace"

        with (
            patch.object(download_jobs.config, "snapshot", return_value=snapshot),
            patch.object(download_jobs, "_validate_current_preset", return_value="/isolated/current.html"),
            patch.object(download_jobs, "_new_download_lease", return_value=lease),
            patch.object(download_jobs, "operation_loggers", side_effect=_fake_loggers),
            patch.object(download_jobs, "preset_parser", return_value=["777"]),
            patch.object(download_jobs, "check_installed", return_value=([], ["777"])),
            patch.object(download_jobs, "download_mods", side_effect=uncertain),
            patch.object(download_mods_task, "update_state") as update_state,
            bound_task_request(download_mods_task, binding.task_id),
        ):
            with self.assertRaises(Ignore):
                download_mods_task.run(instance_id=instance.pk)

        binding.refresh_from_db()
        self.assertIsNone(binding.finished_at)
        self.assertTrue(binding.requires_attention)
        self.assertEqual(
            binding.attention_reason,
            InstanceTask.AttentionReason.PROCESS_CLEANUP_UNCONFIRMED,
        )
        self.assertEqual(binding.attention_workspace, "/isolated/private-workspace")
        self.assertNotIn("private-workspace", repr(update_state.call_args_list))
        self.assertNotIn("private process diagnostic", repr(update_state.call_args_list))
        self.assertTrue(lease.released)

    def test_attention_persistence_failure_still_keeps_claimed_binding_unfinished(self):
        from main import download_jobs

        instance = self.make_instance()
        binding, _reused = reserve_download(instance.pk)
        snapshot = self._snapshot()
        lease = _FakeLease()
        uncertain = CleanupUncertainError(RuntimeError("private process diagnostic"))
        uncertain.workspace_path = "/isolated/private-workspace"

        with (
            patch.object(download_jobs.config, "snapshot", return_value=snapshot),
            patch.object(download_jobs, "_validate_current_preset", return_value="/isolated/current.html"),
            patch.object(download_jobs, "_new_download_lease", return_value=lease),
            patch.object(download_jobs, "operation_loggers", side_effect=_fake_loggers),
            patch.object(download_jobs, "preset_parser", return_value=["777"]),
            patch.object(download_jobs, "check_installed", return_value=([], ["777"])),
            patch.object(download_jobs, "download_mods", side_effect=uncertain),
            patch.object(download_jobs, "_persist_attention", return_value=False) as persist_attention,
            bound_task_request(download_mods_task, binding.task_id),
        ):
            with self.assertRaises(Ignore):
                download_mods_task.run(instance_id=instance.pk)

        persist_attention.assert_called_once()
        binding.refresh_from_db()
        self.assertIsNotNone(binding.claimed_at)
        self.assertIsNone(binding.finished_at)
        self.assertFalse(binding.requires_attention)

    def test_real_worker_persists_attention_before_deferred_file_handler_close(self):
        from main import download_jobs

        with tempfile.TemporaryDirectory() as temp_dir:
            logs_directory = Path(temp_dir)
            instance = self.make_instance(ready=True)
            binding, _reused = reserve_download(instance.pk)
            snapshot = self._snapshot()
            snapshot["paths"]["logs_directory"] = str(logs_directory)
            lease = _FakeLease()
            emit_started = threading.Event()
            release_emit = threading.Event()
            worker_finished = threading.Event()
            all_handlers_closed = threading.Event()
            callback_threads: list[threading.Thread] = []
            handlers_with_emission: list[logging.FileHandler] = []
            closed_handler_paths: set[str] = set()
            outcome: dict[str, BaseException] = {}
            original_emit = logging.FileHandler.emit
            original_close = logging.FileHandler.close

            def blocked_emit(handler, record):
                if record.getMessage() == "admitted callback line":
                    handlers_with_emission.append(handler)
                    emit_started.set()
                    release_emit.wait()
                return original_emit(handler, record)

            def tracking_close(handler):
                result = original_close(handler)
                try:
                    handler_path = Path(handler.baseFilename)
                    handler_path.relative_to(logs_directory)
                except (AttributeError, ValueError):
                    return result
                closed_handler_paths.add(str(handler_path))
                if len(closed_handler_paths) >= 3:
                    all_handlers_closed.set()
                return result

            def fail_download(**kwargs):
                callback_thread = threading.Thread(
                    target=lambda: kwargs["log_callback"]("admitted callback line"),
                    daemon=True,
                )
                callback_threads.append(callback_thread)
                callback_thread.start()
                if not emit_started.wait(1):
                    raise RuntimeError("test callback did not enter FileHandler.emit")
                lease.is_lost = True
                uncertain = CleanupUncertainError(
                    RuntimeError("private process cleanup diagnostic")
                )
                uncertain.workspace_path = "/private/worker-workspace"
                raise uncertain

            def run_worker():
                try:
                    with bound_task_request(download_mods_task, binding.task_id):
                        download_mods_task.run(instance_id=instance.pk)
                except BaseException as exc:
                    outcome["error"] = exc
                finally:
                    worker_finished.set()

            with (
                patch.object(download_jobs.config, "snapshot", return_value=snapshot),
                patch.object(download_jobs, "_validate_current_preset", return_value="/isolated/current.html"),
                patch.object(download_jobs, "_new_download_lease", return_value=lease),
                patch.object(download_jobs, "preset_parser", return_value=["777"]),
                patch.object(download_jobs, "check_installed", return_value=([], ["777"])),
                patch.object(download_jobs, "download_mods", side_effect=fail_download),
                patch.object(download_mods_task, "update_state"),
                patch.object(logging.FileHandler, "emit", new=blocked_emit),
                patch.object(logging.FileHandler, "close", new=tracking_close),
            ):
                worker = threading.Thread(target=run_worker, daemon=True)
                worker.start()
                try:
                    self.assertTrue(emit_started.wait(1))
                    self.assertTrue(
                        worker_finished.wait(2),
                        "worker attention persistence and return must not wait for an in-flight FileHandler emit",
                    )
                    self.assertIsInstance(outcome.get("error"), Ignore)
                    self.assertFalse(release_emit.is_set())
                    self.assertFalse(all_handlers_closed.is_set())
                    self.assertEqual(len(handlers_with_emission), 1)
                    binding.refresh_from_db()
                    instance.refresh_from_db()
                    self.assertTrue(binding.requires_attention)
                    self.assertEqual(
                        binding.attention_reason,
                        InstanceTask.AttentionReason.PROCESS_CLEANUP_UNCONFIRMED,
                    )
                    self.assertEqual(binding.attention_workspace, "/private/worker-workspace")
                    self.assertIsNone(binding.finished_at)
                    self.assertIsNotNone(binding.claimed_at)
                    self.assertFalse(instance.is_ready)
                finally:
                    # Release the already-admitted callback so it can write and
                    # complete its bounded owned-handler cleanup.
                    release_emit.set()
                    worker.join(timeout=2)
                    for callback_thread in callback_threads:
                        callback_thread.join(timeout=2)

                self.assertFalse(worker.is_alive())
                self.assertTrue(all_handlers_closed.wait(1))
                self.assertTrue(all(handler.stream is None for handler in handlers_with_emission))

            log_contents = "\n".join(
                path.read_text(encoding="utf-8")
                for path in logs_directory.rglob("*.txt")
            )
            self.assertIn("admitted callback line", log_contents)
            self.assertNotIn("private process cleanup diagnostic", log_contents)
            self.assertNotIn("/private/worker-workspace", log_contents)
            self.assertNotIn("private process cleanup diagnostic", repr(outcome))

    def test_another_claimed_download_fails_only_the_waiting_binding(self):
        first = self.make_instance()
        second = self.make_instance()
        active, _reused = reserve_download(first.pk)
        # Legacy queues can coexist before this worker generation; admission
        # fences new jobs, while claim must leave an existing owner untouched.
        from main.instance_tasks import create_instance_task_binding

        waiting = create_instance_task_binding(second, InstanceTask.Kind.DOWNLOAD)
        InstanceTask.objects.filter(pk=active.task_id).update(claimed_at=timezone.now())

        with (
            patch("main.download_jobs.config.snapshot") as snapshot,
            patch("main.download_jobs.download_mods") as downloader,
            bound_task_request(download_mods_task, waiting.task_id),
        ):
            with self.assertRaises(DownloadAdmissionError):
                download_mods_task.run(instance_id=second.pk)

        snapshot.assert_not_called()
        downloader.assert_not_called()
        active.refresh_from_db()
        waiting.refresh_from_db()
        self.assertIsNone(active.finished_at)
        self.assertIsNotNone(active.claimed_at)
        self.assertIsNotNone(waiting.finished_at)

    def _snapshot(self):
        return {
            "paths": {
                "logs_directory": "/isolated/logs",
                "mods_directory": "/isolated/mods",
                "steamcmd": "/isolated/steamcmd",
                "download_directory": "/isolated/downloads",
            },
            "steam_auth": {
                "username": "fake-user",
                "password": "fake-password",
                "shared_secret": "ZmFrZS1zZWNyZXQ=",
            },
        }


def _fake_loggers(*_args, **_kwargs):
    return _FakeLoggerContext()


class _FakeLoggerContext:
    def __enter__(self):
        return {"operations": Mock(), "download": Mock()}

    def __exit__(self, *_exc_info):
        return False


class _FakeLease:
    def __init__(self):
        self.assert_owned_calls = 0
        self.released = False
        self.is_lost = False

    def acquire(self):
        return True

    def start_heartbeat(self):
        return None

    def assert_owned(self):
        self.assert_owned_calls += 1

    def wait_lost(self, timeout=None):
        return self.is_lost

    def release(self):
        self.released = True
        return True
