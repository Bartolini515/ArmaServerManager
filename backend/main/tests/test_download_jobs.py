from contextlib import ExitStack, contextmanager
import logging
import socket
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from celery.exceptions import Ignore
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

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
