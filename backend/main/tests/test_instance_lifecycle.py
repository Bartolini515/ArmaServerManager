from contextlib import ExitStack, contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from celery.exceptions import Ignore, Retry
from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from main.instance_tasks import create_instance_task_binding
from main.models import InstanceLifecycleLock, InstanceTask, Instances, Ports
from main.serverhandling.process_identity import ProcessIdentity, UnverifiedProcessError
from main.tasks import (
    check_all_servers_status_task,
    instance_timeout_task,
    start_server_task,
    stop_server_task,
)


User = get_user_model()


def storage_path_override(log_file_name, on_log_file_path):
    original_path = FileSystemStorage.path

    def resolve_path(storage, name):
        if name == log_file_name:
            return on_log_file_path()
        return original_path(storage, name)

    return resolve_path


@contextmanager
def bound_task_request(task, task_id):
    task.push_request(id=task_id)
    try:
        yield
    finally:
        task.pop_request()


class InstanceLifecycleTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="test-password")
        self.other = User.objects.create_user(username="other", password="test-password")
        self.staff = User.objects.create_user(
            username="staff", password="test-password", is_staff=True
        )
        self.superuser = User.objects.create_superuser(
            username="root", password="test-password"
        )
        self.client = APIClient()
        self.next_port = 24100

    def make_instance(
        self,
        owner=None,
        *,
        name=None,
        state="stopped",
        is_admin_instance=False,
        with_log=False,
    ):
        owner = owner or self.owner
        name = name or f"lifecycle-{self.next_port}"
        port = Ports.objects.create(port_number=self.next_port, is_available=False)
        self.next_port += 1
        instance = Instances.objects.create(
            name=name,
            user=owner,
            preset=SimpleUploadedFile(f"{name}.html", b"<html></html>"),
            port=port,
            lifecycle_state=state,
            is_admin_instance=is_admin_instance,
            start_file_path=f"/isolated/arma3/start-{name}.sh",
        )
        if with_log:
            instance.log_file.save(
                f"{name}-log.txt",
                SimpleUploadedFile(f"{name}-log.txt", b"invented log line\n"),
                save=True,
            )
        return instance

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    @contextmanager
    def fake_operation_loggers(self, *args, **kwargs):
        yield {
            "start": MagicMock(),
            "create": MagicMock(),
            "change_preset": MagicMock(),
        }

    def patch_start_readiness(self):
        return (
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.check_sh_file_exists", return_value=True),
            patch("main.views.config.get", return_value="/isolated/arma3"),
        )

    @contextmanager
    def start_readiness_patches(self):
        with ExitStack() as stack:
            for context in self.patch_start_readiness():
                stack.enter_context(context)
            yield stack

    def test_lifecycle_transaction_recreates_fixed_lock_after_test_database_flush(self):
        from main.instance_lifecycle import lifecycle_transaction

        InstanceLifecycleLock.objects.filter(pk=1).delete()
        self.assertFalse(InstanceLifecycleLock.objects.filter(pk=1).exists())
        with lifecycle_transaction():
            self.assertTrue(InstanceLifecycleLock.objects.filter(pk=1).exists())
            locked = InstanceLifecycleLock.objects.select_for_update().get(pk=1)
            self.assertEqual(locked.pk, 1)

    def test_serializer_exposes_only_public_lifecycle_state_and_deadline(self):
        instance = self.make_instance()
        instance.lifecycle_state = "running"
        instance.shutdown_at = timezone.now() + timedelta(hours=1)
        instance.run_id = uuid4()
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.lifecycle_task_id = "private-task-id"
        instance.start_claimed_at = timezone.now()
        instance.timeout_task_id = "private-timeout-id"
        instance.save()

        self.authenticate_as(self.owner)
        response = self.client.get("/api/instances/")

        public_instance = response.data["user_instances"][0]
        self.assertEqual(public_instance["lifecycle_state"], "running")
        self.assertEqual(
            public_instance["shutdown_at"],
            timezone.localtime(instance.shutdown_at).isoformat(),
        )
        for internal_field in (
            "run_id",
            "process_created_at",
            "process_executable",
            "lifecycle_task_id",
            "start_claimed_at",
            "timeout_task_id",
        ):
            self.assertNotIn(internal_field, public_instance)

    def test_start_reservation_is_atomic_and_repeated_start_reuses_task_id(self):
        instance = self.make_instance()
        self.authenticate_as(self.owner)
        dispatched = []

        def fake_dispatch(binding, task, *, kwargs):
            dispatched.append((binding, task, kwargs))
            return binding.task_id

        with self.start_readiness_patches() as patches:
            patches.enter_context(
                patch("main.views.dispatch_instance_task", side_effect=fake_dispatch)
            )
            first = self.client.post(f"/api/instances/{instance.pk}/start/", {}, format="json")
            second = self.client.post(f"/api/instances/{instance.pk}/start/", {}, format="json")

        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(second.status_code, 202, second.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(len(dispatched), 1)
        binding, task, kwargs = dispatched[0]
        self.assertEqual(task, start_server_task)
        self.assertEqual(binding.kind, InstanceTask.Kind.START)
        self.assertEqual(kwargs["instance_id"], instance.pk)
        self.assertEqual(kwargs["arma3_dir"], "/isolated/arma3")
        self.assertEqual(kwargs["run_id"], str(Instances.objects.get(pk=instance.pk).run_id))

        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "starting")
        self.assertFalse(instance.is_running)
        self.assertIsNone(instance.shutdown_at)

    def test_start_dispatch_failure_compensates_only_unclaimed_matching_reservation(self):
        instance = self.make_instance()
        self.authenticate_as(self.owner)

        with self.start_readiness_patches() as patches:
            patches.enter_context(patch(
                "main.views.dispatch_instance_task",
                side_effect=RuntimeError("invented broker failure"),
            ))
            response = self.client.post(f"/api/instances/{instance.pk}/start/", {}, format="json")

        self.assertEqual(response.status_code, 500)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.lifecycle_task_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())

    def test_start_rejects_outer_transaction_before_readiness_or_file_side_effects(self):
        instance = self.make_instance()
        self.authenticate_as(self.owner)

        with self.start_readiness_patches() as patches:
            preset_parser = patches.enter_context(patch("main.views.preset_parser"))
            with transaction.atomic():
                response = self.client.post(
                    f"/api/instances/{instance.pk}/start/",
                    {},
                    format="json",
                )

        self.assertEqual(response.status_code, 500)
        preset_parser.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertFalse(instance.log_file)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())

    def test_start_dispatch_error_after_worker_claim_keeps_delivered_reservation(self):
        instance = self.make_instance()
        self.authenticate_as(self.owner)

        def claimed_then_uncertain_ack(binding, task, *, kwargs):
            binding.claimed_at = timezone.now()
            binding.save(update_fields=["claimed_at"])
            Instances.objects.filter(pk=instance.pk).update(start_claimed_at=timezone.now())
            raise RuntimeError("invented acknowledgement loss after delivery")

        with self.start_readiness_patches() as patches:
            patches.enter_context(
                patch("main.views.dispatch_instance_task", side_effect=claimed_then_uncertain_ack)
            )
            response = self.client.post(f"/api/instances/{instance.pk}/start/", {}, format="json")

        self.assertEqual(response.status_code, 202)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "starting")
        self.assertIsNotNone(instance.start_claimed_at)
        self.assertTrue(
            InstanceTask.objects.filter(
                pk=response.data["task_id"],
                finished_at__isnull=True,
                claimed_at__isnull=False,
            ).exists()
        )

    def test_start_rejects_global_reserved_slots_and_staff_still_obeys_owner_limit(self):
        target = self.make_instance()
        reserved_owners = [
            User.objects.create_user(username=f"reserved-{index}", password="test-password")
            for index in range(5)
        ]
        for index, owner in enumerate(reserved_owners):
            self.make_instance(owner, state=("starting" if index == 0 else "unknown"))

        self.authenticate_as(self.owner)
        with self.start_readiness_patches() as patches:
            dispatch = patches.enter_context(patch("main.views.dispatch_instance_task"))
            response = self.client.post(f"/api/instances/{target.pk}/start/", {}, format="json")
        self.assertEqual(response.status_code, 403)
        dispatch.assert_not_called()

        target.lifecycle_state = "stopped"
        target.save(update_fields=["lifecycle_state"])
        self.make_instance(self.owner, name="owner-already-reserved", state="unknown")
        self.authenticate_as(self.staff)
        with self.start_readiness_patches() as patches:
            dispatch = patches.enter_context(patch("main.views.dispatch_instance_task"))
            response = self.client.post(f"/api/instances/{target.pk}/start/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        dispatch.assert_not_called()

    def test_unfinished_download_binding_blocks_start(self):
        instance = self.make_instance()
        create_instance_task_binding(instance, "download")
        self.authenticate_as(self.owner)

        with self.start_readiness_patches() as patches:
            dispatch = patches.enter_context(patch("main.views.dispatch_instance_task"))
            response = self.client.post(f"/api/instances/{instance.pk}/start/", {}, format="json")

        self.assertEqual(response.status_code, 400)
        dispatch.assert_not_called()

    def test_duplicate_start_claim_ignores_redelivery_without_finishing_binding(self):
        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, "start")
        instance.run_id = uuid4()
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.lifecycle_task_id = binding.task_id
        instance.save()
        def retry_after_claim(*args, **kwargs):
            raise Retry("invented retry")

        with (
            patch("main.tasks.start_server", side_effect=retry_after_claim) as start,
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaises(Retry):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(instance.run_id),
                )

            with self.assertRaises(Ignore):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(instance.run_id),
                )

        start.assert_called_once()
        binding.refresh_from_db()
        self.assertIsNone(binding.finished_at)
        instance.refresh_from_db()
        self.assertIsNotNone(instance.start_claimed_at)

    def test_queued_start_with_missing_metadata_releases_only_its_unlaunched_reservation(self):
        instance = self.make_instance(state="starting", with_log=True)
        run_id = uuid4()
        binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        instance.start_file_path = "  "
        instance.run_id = run_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.lifecycle_task_id = binding.task_id
        instance.save()

        with (
            patch("main.tasks.start_server") as start,
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaisesRegex(RuntimeError, "server start could not be prepared"):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        start.assert_not_called()
        instance.refresh_from_db()
        binding.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.pid)
        self.assertFalse(instance.is_running)
        self.assertIsNone(instance.lifecycle_task_id)
        self.assertIsNotNone(binding.finished_at)
        self.assertFalse(
            InstanceTask.objects.filter(
                instance=instance,
                finished_at__isnull=True,
                superseded_at__isnull=True,
            ).exists()
        )

    def test_queued_start_with_unavailable_log_path_releases_unlaunched_reservation(self):
        instance = self.make_instance(state="starting", with_log=True)
        run_id = uuid4()
        binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        instance.run_id = run_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.lifecycle_task_id = binding.task_id
        instance.save()

        def unavailable_log_path():
            raise OSError("invented inaccessible storage")

        with (
            patch.object(
                FileSystemStorage,
                "path",
                new=storage_path_override(
                    instance.log_file.name,
                    unavailable_log_path,
                ),
            ),
            patch("main.tasks.start_server") as start,
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaisesRegex(RuntimeError, "server start could not be prepared"):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        start.assert_not_called()
        instance.refresh_from_db()
        binding.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.pid)
        self.assertFalse(instance.is_running)
        self.assertIsNone(instance.lifecycle_task_id)
        self.assertIsNotNone(binding.finished_at)
        self.assertFalse(
            InstanceTask.objects.filter(
                instance=instance,
                finished_at__isnull=True,
                superseded_at__isnull=True,
            ).exists()
        )

    def test_stale_start_preflight_failure_cannot_clear_a_newer_run(self):
        instance = self.make_instance(state="starting", with_log=True)
        old_run_id = uuid4()
        old_binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        instance.run_id = old_run_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.lifecycle_task_id = old_binding.task_id
        instance.save()

        new_run_id = uuid4()
        new_binding = None

        def replace_attempt_then_fail():
            nonlocal new_binding
            current = Instances.objects.get(pk=instance.pk)
            new_binding = create_instance_task_binding(current, InstanceTask.Kind.START)
            current.run_id = new_run_id
            current.lifecycle_task_id = new_binding.task_id
            current.lifecycle_state = Instances.LifecycleState.STARTING
            current.start_claimed_at = None
            current.save(
                update_fields=[
                    "run_id",
                    "lifecycle_task_id",
                    "lifecycle_state",
                    "start_claimed_at",
                ]
            )
            raise OSError("invented inaccessible storage")

        with (
            patch.object(
                FileSystemStorage,
                "path",
                new=storage_path_override(
                    instance.log_file.name,
                    replace_attempt_then_fail,
                ),
            ),
            patch("main.tasks.start_server") as start,
            bound_task_request(start_server_task, old_binding.task_id),
        ):
            with self.assertRaisesRegex(RuntimeError, "server start could not be prepared"):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(old_run_id),
                )

        start.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STARTING)
        self.assertEqual(str(instance.run_id), str(new_run_id))
        self.assertEqual(instance.lifecycle_task_id, new_binding.task_id)
        self.assertIsNone(instance.pid)
        self.assertFalse(instance.is_running)
        new_binding.refresh_from_db()
        self.assertIsNone(new_binding.finished_at)

    def test_legacy_unknown_is_busy_for_delete_and_log_deletion(self):
        instance = self.make_instance(state="unknown", with_log=True)
        self.authenticate_as(self.owner)

        delete_response = self.client.delete(f"/api/instances/{instance.pk}/")
        log_response = self.client.delete(f"/api/instances/{instance.pk}/logs/delete/")

        self.assertEqual(delete_response.status_code, 400)
        self.assertEqual(log_response.status_code, 400)
        self.assertTrue(Instances.objects.filter(pk=instance.pk).exists())

    def test_legacy_unknown_blocks_admin_preset_replacement_and_user_deletion(self):
        instance = self.make_instance(self.owner, state="unknown", is_admin_instance=True)
        self.authenticate_as(self.staff)

        with (
            patch("main.views.preset_parser") as preset_parser,
            patch("main.views.generate_sh_file") as generate_sh_file,
        ):
            preset_response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"preset": SimpleUploadedFile("replacement.html", b"replacement")},
                format="multipart",
            )

        self.assertEqual(preset_response.status_code, 400)
        preset_parser.assert_not_called()
        generate_sh_file.assert_not_called()

        delete_user_response = self.client.delete(
            f"/api/moderator_panel/{self.owner.pk}/user/delete/"
        )
        self.assertEqual(delete_user_response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_repeated_stop_returns_same_active_binding_without_second_publish(self):
        instance = self.make_instance(state="running")
        instance.run_id = uuid4()
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.save()
        self.authenticate_as(self.owner)

        with patch("main.instance_lifecycle.dispatch_instance_task") as dispatch:
            dispatch.side_effect = lambda binding, task, *, kwargs: binding.task_id
            first = self.client.post(f"/api/instances/{instance.pk}/stop/", {}, format="json")
            second = self.client.post(f"/api/instances/{instance.pk}/stop/", {}, format="json")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.data, second.data)
        dispatch.assert_called_once()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopping")
        self.assertEqual(instance.lifecycle_task_id, first.data["task_id"])

    def test_manual_stop_keeps_deadline_retryable_when_worker_cannot_confirm_exit(self):
        instance = self.make_instance(state="running")
        run_id = uuid4()
        deadline = timezone.now() - timedelta(seconds=1)
        instance.run_id = run_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.lifecycle_task_id = "start-for-timeout-retry"
        instance.timeout_task_id = "timeout-for-retry-run"
        instance.shutdown_at = deadline
        instance.is_running = True
        instance.save()
        self.authenticate_as(self.owner)

        dispatched = []

        def record_dispatch(binding, task, *, kwargs):
            dispatched.append((binding.task_id, task, kwargs))
            return binding.task_id

        with (
            patch("main.instance_lifecycle.dispatch_instance_task", side_effect=record_dispatch),
            patch("main.tasks.celery_app.control.revoke") as revoke,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/stop/", {}, format="json"
            )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(len(dispatched), 1)
        revoke.assert_not_called()
        stop_binding_id = response.data["task_id"]

        with (
            patch(
                "main.tasks._stop_verified_instance",
                side_effect=UnverifiedProcessError("exit could not be verified"),
            ),
            bound_task_request(stop_server_task, stop_binding_id),
        ):
            with self.assertRaises(UnverifiedProcessError):
                stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))

        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "unknown")
        self.assertEqual(instance.shutdown_at, deadline)
        self.assertEqual(instance.timeout_task_id, "timeout-for-retry-run")

        with (
            patch("main.instance_lifecycle.dispatch_instance_task", side_effect=record_dispatch),
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            patch("main.tasks.celery_app.control.revoke") as revoke,
            bound_task_request(instance_timeout_task, "timeout-for-retry-run"),
        ):
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=deadline.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        self.assertEqual(len(dispatched), 2)
        retry.assert_called_once()
        revoke.assert_not_called()

    def test_start_progress_backend_failure_does_not_strand_claimed_reservation(self):
        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, "start")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = SimpleNamespace(pid=identity.pid, _asm_process_identity=identity)

        with (
            patch("main.tasks.start_server", return_value=fake_process),
            patch("main.tasks._resolve_launched_identity", return_value=identity) as resolve,
            patch("main.tasks.instance_timeout_task.apply_async", return_value=SimpleNamespace(id="timeout")),
            patch.object(
                start_server_task,
                "update_state",
                side_effect=lambda **kwargs: (
                    (_ for _ in ()).throw(RuntimeError("result backend unavailable"))
                    if kwargs.get("state") == "PROGRESS"
                    else None
                ),
            ),
            bound_task_request(start_server_task, binding.task_id),
        ):
            result = start_server_task.run(
                instance_id=instance.pk,
                arma3_dir="/isolated/arma3",
                run_id=str(run_id),
            )

        self.assertIn("uruchomiony", result["status"])
        resolve.assert_called_once()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "running")
        self.assertTrue(instance.is_running)
        self.assertEqual(instance.pid, identity.pid)
        binding.refresh_from_db()
        self.assertIsNotNone(binding.finished_at)

    def test_stop_progress_backend_failure_does_not_drop_deadline_or_skip_stop(self):
        instance = self.make_instance(state="stopping")
        binding = create_instance_task_binding(instance, "stop")
        run_id = uuid4()
        deadline = timezone.now() + timedelta(minutes=10)
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.timeout_task_id = "timeout-for-stop-progress"
        instance.shutdown_at = deadline
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks._stop_verified_instance"),
            patch.object(
                stop_server_task,
                "update_state",
                side_effect=lambda **kwargs: (
                    (_ for _ in ()).throw(RuntimeError("result backend unavailable"))
                    if kwargs.get("state") == "PROGRESS"
                    else None
                ),
            ),
            patch("main.tasks.celery_app.control.revoke") as revoke,
            bound_task_request(stop_server_task, binding.task_id),
        ):
            result = stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))

        self.assertIn("zatrzymany", result["status"])
        revoke.assert_called_once_with("timeout-for-stop-progress", terminate=False)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertIsNone(instance.shutdown_at)

    def test_start_rejects_recycled_pid_from_launch_identity_before_persisting(self):
        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, "start")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()
        anchored_identity = ProcessIdentity(pid=12345, created_at=100.5)
        replacement_identity = ProcessIdentity(pid=12345, created_at=200.5)
        fake_process = SimpleNamespace(
            pid=anchored_identity.pid,
            _asm_process_identity=anchored_identity,
        )

        with (
            patch("main.tasks.start_server", return_value=fake_process),
            patch("main.tasks.capture_process_identity", return_value=replacement_identity) as capture,
            patch(
                "main.tasks._resolve_launched_identity",
                side_effect=UnverifiedProcessError("PID birth time changed"),
            ),
            patch("main.tasks._stop_verified_instance") as stop,
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaises(UnverifiedProcessError):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        capture.assert_not_called()
        stop.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "unknown")
        self.assertTrue(instance.is_running)
        self.assertEqual(instance.pid, anchored_identity.pid)
        self.assertNotEqual(instance.process_created_at, replacement_identity.created_at)

    def test_start_helper_anchors_pid_before_starting_output_reader(self):
        import importlib

        start_module = importlib.import_module("main.serverhandling.start_server")
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        events = []
        fake_process = MagicMock()
        fake_process.pid = identity.pid
        fake_process.poll.side_effect = [None, None, None]
        verified = MagicMock()
        verified.environ.return_value = {"ASM_INSTANCE_RUN_ID": "owned-run"}
        output_thread = MagicMock()
        output_thread.start.side_effect = lambda: events.append("reader-start")

        def capture(pid):
            events.append("identity-captured")
            self.assertEqual(pid, identity.pid)
            return identity

        def verify(captured):
            events.append("identity-verified")
            self.assertEqual(captured, identity)
            return verified

        with (
            patch("main.serverhandling.start_server.subprocess.Popen", return_value=fake_process),
            patch("main.serverhandling.start_server.capture_process_identity", side_effect=capture),
            patch("main.serverhandling.start_server.get_verified_process", side_effect=verify),
            patch("main.serverhandling.start_server.threading.Thread", return_value=output_thread),
        ):
            result = start_module.start_server(
                "/isolated/arma3",
                "/isolated/arma3/start-lifecycle.sh",
                "/isolated/logs/server.log",
                run_id="owned-run",
                launch_signal_guard=MagicMock(return_value=True),
            )

        self.assertIs(result, fake_process)
        self.assertEqual(result._asm_process_identity, identity)
        self.assertLess(events.index("identity-captured"), events.index("reader-start"))
        self.assertLess(events.index("identity-verified"), events.index("reader-start"))

    def test_output_reader_start_failure_keeps_start_unknown_and_reserved(self):
        from main.instance_lifecycle import is_instance_lifecycle_busy
        from main.serverhandling import start_server as start_server_module

        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = MagicMock()
        fake_process.pid = identity.pid
        fake_process.poll.return_value = None
        verified_process = MagicMock()
        verified_process.environ.return_value = {"ASM_INSTANCE_RUN_ID": str(run_id)}
        output_thread = MagicMock()
        output_thread.start.side_effect = RuntimeError("invented output reader startup failure")

        def capture_anchored_process(process, expected_run_id):
            process._asm_process_identity = identity
            return identity

        with (
            patch("main.tasks.start_server", wraps=start_server_module.start_server),
            patch.object(start_server_module.subprocess, "Popen", return_value=fake_process),
            patch.object(
                start_server_module,
                "_capture_launch_identity",
                side_effect=capture_anchored_process,
            ),
            patch.object(
                start_server_module,
                "get_verified_process",
                return_value=verified_process,
            ),
            patch.object(start_server_module.threading, "Thread", return_value=output_thread),
            patch("main.tasks._update_task_state_safely"),
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaises(Exception) as raised:
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        self.assertEqual(
            raised.exception.__class__.__name__,
            "PostLaunchMayBeLiveError",
        )

        instance.refresh_from_db()
        binding.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "unknown")
        self.assertTrue(instance.is_running)
        self.assertEqual(instance.run_id, run_id)
        self.assertTrue(is_instance_lifecycle_busy(instance))
        self.assertIsNotNone(binding.finished_at)
        self.assertEqual(instance.pid, identity.pid)
        self.assertEqual(instance.process_created_at, identity.created_at)
        fake_process.terminate.assert_not_called()
        fake_process.kill.assert_not_called()

    def test_post_popen_oserrors_keep_start_unknown_and_reserved(self):
        from main.instance_lifecycle import is_instance_lifecycle_busy
        from main.serverhandling import start_server as start_server_module

        failure_modes = (
            "reader-start",
            "thread-construction",
            "initial-poll",
            "identity-capture",
        )
        for offset, failure_mode in enumerate(failure_modes):
            with self.subTest(failure_mode=failure_mode):
                instance = self.make_instance(state="starting", with_log=True)
                binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
                run_id = uuid4()
                instance.run_id = run_id
                instance.lifecycle_task_id = binding.task_id
                instance.process_executable = "/isolated/arma3/arma3server_x64"
                instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
                instance.save()

                identity = ProcessIdentity(
                    pid=12345 + offset,
                    created_at=100.5 + offset,
                )
                fake_process = MagicMock()
                fake_process.pid = identity.pid
                fake_process.poll.return_value = None
                if failure_mode == "initial-poll":
                    calls = {"count": 0}

                    def fail_first_poll():
                        calls["count"] += 1
                        if calls["count"] == 1:
                            raise OSError("injected Popen child poll failure")
                        return None

                    fake_process.poll.side_effect = fail_first_poll

                verified_process = MagicMock()
                verified_process.environ.return_value = {
                    "ASM_INSTANCE_RUN_ID": str(run_id)
                }
                output_thread = MagicMock()
                output_thread.start.side_effect = OSError(
                    "injected output reader startup failure"
                )
                if failure_mode == "thread-construction":
                    thread_factory = MagicMock(
                        side_effect=OSError("injected reader thread construction failure")
                    )
                else:
                    thread_factory = MagicMock(return_value=output_thread)

                capture = MagicMock(return_value=identity)
                if failure_mode == "identity-capture":
                    capture.side_effect = OSError("injected birth identity capture failure")

                with (
                    patch("main.tasks.start_server", wraps=start_server_module.start_server),
                    patch.object(
                        start_server_module.subprocess,
                        "Popen",
                        return_value=fake_process,
                    ),
                    patch.object(start_server_module, "capture_process_identity", capture),
                    patch.object(
                        start_server_module,
                        "get_verified_process",
                        return_value=verified_process,
                    ),
                    patch.object(start_server_module.threading, "Thread", thread_factory),
                    patch("main.tasks._update_task_state_safely"),
                    bound_task_request(start_server_task, binding.task_id),
                ):
                    with self.assertRaises(Exception) as raised:
                        start_server_task.run(
                            instance_id=instance.pk,
                            arma3_dir="/isolated/arma3",
                            run_id=str(run_id),
                        )

                self.assertEqual(
                    raised.exception.__class__.__name__,
                    "PostLaunchMayBeLiveError",
                )
                instance.refresh_from_db()
                binding.refresh_from_db()
                self.assertEqual(instance.lifecycle_state, "unknown")
                self.assertTrue(instance.is_running)
                self.assertEqual(instance.run_id, run_id)
                self.assertTrue(is_instance_lifecycle_busy(instance))
                if failure_mode == "identity-capture" or failure_mode == "initial-poll":
                    self.assertIsNone(instance.pid)
                    self.assertIsNone(instance.process_created_at)
                else:
                    self.assertEqual(instance.pid, identity.pid)
                    self.assertEqual(instance.process_created_at, identity.created_at)
                self.assertIsNotNone(binding.finished_at)
                fake_process.terminate.assert_not_called()
                fake_process.kill.assert_not_called()

    def test_wrong_or_unreadable_run_marker_keeps_captured_unknown_process(self):
        from main.instance_lifecycle import is_instance_lifecycle_busy
        from main.serverhandling import start_server as start_server_module

        marker_cases = ("wrong", "unreadable")
        for offset, marker_case in enumerate(marker_cases):
            with self.subTest(marker_case=marker_case):
                instance = self.make_instance(state="starting", with_log=True)
                binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
                run_id = uuid4()
                instance.run_id = run_id
                instance.lifecycle_task_id = binding.task_id
                instance.process_executable = "/isolated/arma3/arma3server_x64"
                instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
                instance.save()
                identity = ProcessIdentity(pid=12400 + offset, created_at=200.5 + offset)
                fake_process = MagicMock()
                fake_process.pid = identity.pid
                fake_process.poll.return_value = None
                verified_process = MagicMock()
                if marker_case == "wrong":
                    verified_process.environ.return_value = {
                        "ASM_INSTANCE_RUN_ID": "different-run"
                    }
                else:
                    verified_process.environ.side_effect = PermissionError(
                        "injected marker access failure"
                    )

                with (
                    patch("main.tasks.start_server", wraps=start_server_module.start_server),
                    patch.object(
                        start_server_module.subprocess,
                        "Popen",
                        return_value=fake_process,
                    ),
                    patch.object(
                        start_server_module,
                        "capture_process_identity",
                        return_value=identity,
                    ),
                    patch.object(
                        start_server_module,
                        "get_verified_process",
                        return_value=verified_process,
                    ),
                    patch("main.tasks._update_task_state_safely"),
                    bound_task_request(start_server_task, binding.task_id),
                ):
                    with self.assertRaises(Exception) as raised:
                        start_server_task.run(
                            instance_id=instance.pk,
                            arma3_dir="/isolated/arma3",
                            run_id=str(run_id),
                        )

                self.assertEqual(
                    raised.exception.__class__.__name__,
                    "PostLaunchMayBeLiveError",
                )
                instance.refresh_from_db()
                self.assertEqual(instance.lifecycle_state, "unknown")
                self.assertTrue(instance.is_running)
                self.assertTrue(is_instance_lifecycle_busy(instance))
                self.assertEqual(instance.pid, identity.pid)
                self.assertEqual(instance.process_created_at, identity.created_at)
                fake_process.terminate.assert_not_called()
                fake_process.kill.assert_not_called()

    def test_popen_creation_oserror_releases_only_unlaunched_reservation(self):
        from main.serverhandling import start_server as start_server_module

        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()

        with (
            patch("main.tasks.start_server", wraps=start_server_module.start_server),
            patch.object(
                start_server_module.subprocess,
                "Popen",
                side_effect=OSError("injected Popen creation failure"),
            ),
            patch("main.tasks._update_task_state_safely"),
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaises(Exception) as raised:
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        self.assertEqual(
            raised.exception.__class__.__name__,
            "ProcessCreationFailedError",
        )
        instance.refresh_from_db()
        binding.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertFalse(instance.is_running)
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.lifecycle_task_id)
        self.assertIsNotNone(binding.finished_at)

    def test_generic_start_server_oserror_is_uncertain_not_no_process_proof(self):
        from main.instance_lifecycle import is_instance_lifecycle_busy

        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()

        with (
            patch("main.tasks.start_server", side_effect=OSError("untyped launch error")),
            patch("main.tasks._update_task_state_safely"),
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaisesRegex(OSError, "untyped launch error"):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "unknown")
        self.assertTrue(instance.is_running)
        self.assertEqual(instance.run_id, run_id)
        self.assertTrue(is_instance_lifecycle_busy(instance))

    def test_popen_launch_guard_rechecks_birth_marker_and_attempt_before_signaling(self):
        from main.tasks import _signal_if_current_start_launch

        marker_cases = ("wrong", "unreadable", "missing-identity")
        for offset, marker_case in enumerate(marker_cases):
            with self.subTest(marker_case=marker_case):
                instance = self.make_instance(state="unknown")
                run_id = uuid4()
                binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
                binding.claimed_at = timezone.now()
                binding.save(update_fields=["claimed_at"])
                instance.run_id = run_id
                instance.lifecycle_task_id = binding.task_id
                instance.start_claimed_at = binding.claimed_at
                instance.is_running = True
                instance.save()

                identity = ProcessIdentity(pid=12500 + offset, created_at=300.5 + offset)
                child = MagicMock()
                child.pid = identity.pid
                child._asm_process_identity = identity
                child.poll.return_value = None
                verified_process = MagicMock()
                verified_process.create_time.return_value = identity.created_at
                if marker_case == "wrong":
                    verified_process.environ.return_value = {
                        "ASM_INSTANCE_RUN_ID": "wrong-run"
                    }
                elif marker_case == "unreadable":
                    verified_process.environ.side_effect = PermissionError(
                        "injected marker access failure"
                    )
                else:
                    child._asm_process_identity = None

                with patch(
                    "main.serverhandling.process_identity.psutil.Process",
                    return_value=verified_process,
                ):
                    result = _signal_if_current_start_launch(
                        instance.pk,
                        str(run_id),
                        binding.task_id,
                        "terminate",
                        child,
                    )

                self.assertFalse(result)
                child.terminate.assert_not_called()
                child.kill.assert_not_called()
                instance.refresh_from_db()
                self.assertEqual(instance.lifecycle_state, "unknown")
                self.assertTrue(instance.is_running)

    def test_launch_identity_requires_expected_executable_and_run_marker(self):
        from main.tasks import _resolve_launched_identity

        run_id = str(uuid4())
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = SimpleNamespace(_asm_process_identity=identity, poll=lambda: None)
        verified = MagicMock()
        verified.children.return_value = []
        clock = {"now": 0.0}

        with (
            patch("main.tasks.time.monotonic", side_effect=lambda: clock["now"]),
            patch(
                "main.tasks.time.sleep",
                side_effect=lambda seconds: clock.update(now=clock["now"] + seconds),
            ),
            patch("main.tasks.get_verified_process", return_value=verified),
            patch("main.tasks._process_marker") as marker,
            patch("main.tasks._process_executable_matches", return_value=False) as executable,
        ):
            with self.assertRaises(UnverifiedProcessError):
                _resolve_launched_identity(
                    fake_process,
                    identity,
                    run_id,
                    "/isolated/arma3/arma3server_x64",
                )

        marker.assert_any_call(verified, identity, run_id)
        executable.assert_any_call(
            verified,
            identity,
            "/isolated/arma3/arma3server_x64",
        )
        self.assertGreaterEqual(clock["now"], 10.0)

    def test_duplicate_start_after_running_transition_does_not_finish_active_binding(self):
        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, "start")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = SimpleNamespace(pid=identity.pid, _asm_process_identity=identity)
        duplicate_observation = []

        def state_update(*, state, **kwargs):
            if state == "SUCCESS":
                with bound_task_request(start_server_task, binding.task_id):
                    with self.assertRaises(Ignore):
                        start_server_task.run(
                            instance_id=instance.pk,
                            arma3_dir="/isolated/arma3",
                            run_id=str(run_id),
                        )
                binding.refresh_from_db()
                duplicate_observation.append(binding.finished_at)

        with (
            patch("main.tasks.start_server", return_value=fake_process),
            patch("main.tasks._resolve_launched_identity", return_value=identity) as resolve,
            patch("main.tasks.instance_timeout_task.apply_async", return_value=SimpleNamespace(id="timeout")),
            patch.object(start_server_task, "update_state", side_effect=state_update),
            bound_task_request(start_server_task, binding.task_id),
        ):
            start_server_task.run(
                instance_id=instance.pk,
                arma3_dir="/isolated/arma3",
                run_id=str(run_id),
            )

        resolve.assert_called_once()
        self.assertEqual(duplicate_observation, [None])

    def test_duplicate_stop_after_stopped_transition_does_not_finish_active_binding(self):
        instance = self.make_instance(state="stopping")
        binding = create_instance_task_binding(instance, "stop")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()
        duplicate_observation = []

        def state_update(*, state, **kwargs):
            if state == "SUCCESS":
                with bound_task_request(stop_server_task, binding.task_id):
                    with self.assertRaises(Ignore):
                        stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))
                binding.refresh_from_db()
                duplicate_observation.append(binding.finished_at)

        with (
            patch("main.tasks._stop_verified_instance"),
            patch.object(stop_server_task, "update_state", side_effect=state_update),
            bound_task_request(stop_server_task, binding.task_id),
        ):
            stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))

        self.assertEqual(duplicate_observation, [None])

    def test_reconciliation_commits_unknown_and_continues_after_identity_error(self):
        uncertain = self.make_instance(state="running")
        uncertain.run_id = uuid4()
        uncertain.pid = 12345
        uncertain.process_created_at = 100.5
        uncertain.process_executable = "/isolated/arma3/arma3server_x64"
        uncertain.is_running = True
        uncertain.save()
        later = self.make_instance(state="running")
        later.run_id = uuid4()
        later.pid = 12346
        later.process_created_at = 100.6
        later.process_executable = "/isolated/arma3/arma3server_x64"
        later.is_running = True
        later.save()

        with (
            patch(
                "main.tasks._reconcile_instance_locked",
                side_effect=[
                    UnverifiedProcessError("PID identity changed"),
                    None,
                ],
            ) as reconcile,
        ):
            with self.assertRaises(UnverifiedProcessError):
                check_all_servers_status_task.run()

        self.assertEqual(reconcile.call_count, 2)
        uncertain.refresh_from_db()
        self.assertEqual(uncertain.lifecycle_state, "unknown")
        self.assertTrue(uncertain.is_running)
        later.refresh_from_db()
        self.assertEqual(later.lifecycle_state, "running")

    def test_early_timeout_retries_until_saved_deadline(self):
        instance = self.make_instance(state="running")
        run_id = uuid4()
        now = timezone.now()
        deadline = now + timedelta(seconds=45)
        instance.run_id = run_id
        instance.shutdown_at = deadline
        instance.timeout_task_id = "timeout-early"
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks.timezone.now", return_value=now),
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            patch("main.tasks.reserve_stop") as reserve_stop,
            bound_task_request(instance_timeout_task, "timeout-early"),
        ):
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=deadline.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        retry.assert_called_once()
        self.assertGreaterEqual(retry.call_args.kwargs["countdown"], 45)
        reserve_stop.assert_not_called()

    def test_early_timeout_retries_during_start_publication_window(self):
        instance = self.make_instance(state="starting")
        run_id = uuid4()
        now = timezone.now()
        deadline = now + timedelta(hours=1)
        binding = create_instance_task_binding(instance, "start")
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.start_claimed_at = timezone.now()
        instance.save()
        binding.claimed_at = instance.start_claimed_at
        binding.save(update_fields=["claimed_at"])

        with (
            patch("main.tasks.timezone.now", return_value=now),
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            patch("main.tasks.reserve_stop") as reserve_stop,
            bound_task_request(instance_timeout_task, "timeout-published-before-commit"),
        ):
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=deadline.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        retry.assert_called_once_with(countdown=3600)
        reserve_stop.assert_not_called()

    def test_expired_timeout_recovers_a_stalled_start_publication_window(self):
        instance = self.make_instance(state="starting")
        run_id = uuid4()
        binding = create_instance_task_binding(instance, "start")
        deadline = timezone.now() - timedelta(seconds=1)
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.start_claimed_at = timezone.now() - timedelta(hours=1)
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()
        binding.claimed_at = instance.start_claimed_at
        binding.save(update_fields=["claimed_at"])

        with (
            patch("main.tasks.timezone.now", return_value=deadline + timedelta(seconds=1)),
            patch("main.instance_lifecycle.dispatch_instance_task") as dispatch,
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            bound_task_request(instance_timeout_task, "timeout-for-stalled-start"),
        ):
            dispatch.side_effect = lambda stop_binding, task, *, kwargs: stop_binding.task_id
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=deadline.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        retry.assert_called_once_with(countdown=30)
        dispatch.assert_called_once()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopping")
        self.assertEqual(instance.shutdown_at, deadline)
        self.assertEqual(instance.timeout_task_id, "timeout-for-stalled-start")

    def test_deadline_recovery_waits_sixty_seconds_then_supersedes_prior_stop(self):
        instance = self.make_instance(state="stopping")
        run_id = uuid4()
        deadline = timezone.now() - timedelta(minutes=2)
        old_binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        claim_time = timezone.now() - timedelta(seconds=59)
        old_binding.claimed_at = claim_time
        old_binding.save(update_fields=["claimed_at"])
        instance.run_id = run_id
        instance.lifecycle_task_id = old_binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.timeout_task_id = "timeout-recovery-run"
        instance.shutdown_at = deadline
        instance.is_running = True
        instance.save()
        now = claim_time + timedelta(seconds=59)

        with (
            patch("main.tasks.timezone.now", return_value=now),
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            patch("main.instance_lifecycle.dispatch_instance_task") as dispatch,
            bound_task_request(instance_timeout_task, "timeout-recovery-run"),
        ):
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=deadline.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        retry.assert_called_once_with(countdown=1)
        dispatch.assert_not_called()

        now += timedelta(seconds=1)
        with (
            patch("main.tasks.timezone.now", return_value=now),
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            patch("main.instance_lifecycle.dispatch_instance_task") as dispatch,
            bound_task_request(instance_timeout_task, "timeout-recovery-run"),
        ):
            dispatch.side_effect = lambda binding, task, *, kwargs: binding.task_id
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=deadline.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        retry.assert_called_once_with(countdown=30)
        dispatch.assert_called_once()
        recovery_binding = dispatch.call_args.args[0]
        self.assertNotEqual(recovery_binding.task_id, old_binding.task_id)
        old_binding.refresh_from_db()
        self.assertIsNone(old_binding.superseded_at)
        self.assertIsNone(old_binding.finished_at)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_task_id, recovery_binding.task_id)

        with (
            patch("main.tasks._stop_verified_instance") as stop,
            patch("main.tasks.celery_app.control.revoke") as revoke,
            bound_task_request(stop_server_task, recovery_binding.task_id),
        ):
            stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))

        stop.assert_called_once()
        revoke.assert_called_once_with("timeout-recovery-run", terminate=False)
        old_binding.refresh_from_db()
        self.assertIsNotNone(old_binding.superseded_at)
        self.assertIsNone(old_binding.finished_at)

    def test_deadline_republishes_aged_unclaimed_stop_with_same_id_once_only(self):
        instance = self.make_instance(state="stopping")
        run_id = uuid4()
        now = timezone.now()
        created_at = now - timedelta(seconds=61)
        deadline = now - timedelta(minutes=1)
        binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        InstanceTask.objects.filter(pk=binding.pk).update(created_at=created_at)
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.timeout_task_id = "timeout-for-unclaimed-stop"
        instance.shutdown_at = deadline
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks.timezone.now", return_value=now),
            patch("main.tasks.stop_server_task.apply_async") as dispatch,
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            patch("main.tasks.celery_app.control.revoke") as revoke,
            bound_task_request(instance_timeout_task, "timeout-for-unclaimed-stop"),
        ):
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=deadline.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.kwargs["task_id"], binding.task_id)
        self.assertEqual(
            dispatch.call_args.kwargs["kwargs"],
            {"instance_id": instance.pk, "run_id": str(run_id)},
        )
        retry.assert_called_once_with(countdown=60)
        revoke.assert_not_called()
        binding.refresh_from_db()
        self.assertIsNone(binding.claimed_at)
        self.assertIsNone(binding.finished_at)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_task_id, binding.task_id)
        self.assertEqual(instance.lifecycle_state, "stopping")

        # The original queue message and its republished copy share one claim;
        # a late duplicate cannot repeat the host stop effect.
        with (
            patch("main.tasks._stop_verified_instance") as stop_effect,
            patch("main.tasks.celery_app.control.revoke"),
            bound_task_request(stop_server_task, binding.task_id),
        ):
            stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))
            with bound_task_request(stop_server_task, binding.task_id):
                with self.assertRaises(Ignore):
                    stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))

        stop_effect.assert_called_once()

    def test_failed_deadline_republish_preserves_unclaimed_binding_and_retries_in_sixty(self):
        instance = self.make_instance(state="stopping")
        run_id = uuid4()
        now = timezone.now()
        binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        InstanceTask.objects.filter(pk=binding.pk).update(
            created_at=now - timedelta(seconds=61)
        )
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.timeout_task_id = "timeout-for-failed-republish"
        instance.shutdown_at = now - timedelta(minutes=1)
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks.timezone.now", return_value=now),
            patch(
                "main.tasks.stop_server_task.apply_async",
                side_effect=RuntimeError("invented ambiguous broker acknowledgement"),
            ) as dispatch,
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            bound_task_request(instance_timeout_task, "timeout-for-failed-republish"),
        ):
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=instance.shutdown_at.isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        dispatch.assert_called_once()
        retry.assert_called_once_with(countdown=60, exc=dispatch.side_effect)
        binding.refresh_from_db()
        self.assertTrue(InstanceTask.objects.filter(pk=binding.pk).exists())
        self.assertIsNone(binding.claimed_at)
        self.assertIsNone(binding.finished_at)
        self.assertIsNone(binding.superseded_at)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopping")
        self.assertEqual(instance.lifecycle_task_id, binding.task_id)

    def test_lost_start_worker_is_superseded_after_deadline_stop_confirms_exit(self):
        from main.instance_lifecycle import is_instance_lifecycle_busy

        instance = self.make_instance(state="stopping")
        run_id = uuid4()
        old_start = create_instance_task_binding(instance, InstanceTask.Kind.START)
        old_start.claimed_at = timezone.now() - timedelta(hours=1)
        old_start.save(update_fields=["claimed_at"])
        stop_binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        instance.run_id = run_id
        instance.lifecycle_task_id = stop_binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks._stop_verified_instance"),
            patch("main.tasks.celery_app.control.revoke"),
            bound_task_request(stop_server_task, stop_binding.task_id),
        ):
            stop_server_task.run(instance_id=instance.pk, run_id=str(run_id))

        old_start.refresh_from_db()
        self.assertIsNotNone(old_start.superseded_at)
        self.assertIsNone(old_start.finished_at)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertFalse(is_instance_lifecycle_busy(instance))

        self.authenticate_as(self.owner)
        with self.start_readiness_patches() as patches:
            dispatch = patches.enter_context(patch("main.views.dispatch_instance_task"))
            dispatch.side_effect = lambda binding, task, *, kwargs: binding.task_id
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 202, response.data)
        dispatch.assert_called_once()

    def test_superseded_stop_delivery_cannot_touch_later_run_with_reused_pid(self):
        instance = self.make_instance(state="stopped")
        old_binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        old_binding.claimed_at = timezone.now() - timedelta(minutes=2)
        old_binding.superseded_at = timezone.now()
        old_binding.save(update_fields=["claimed_at", "superseded_at"])
        later_run_id = uuid4()
        later_start = create_instance_task_binding(instance, InstanceTask.Kind.START)
        instance.lifecycle_state = "running"
        instance.run_id = later_run_id
        instance.lifecycle_task_id = later_start.task_id
        instance.start_claimed_at = timezone.now()
        instance.pid = 12345
        instance.process_created_at = 200.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks._stop_verified_instance") as stop,
            patch("main.tasks.get_verified_process") as verified,
            bound_task_request(stop_server_task, old_binding.task_id),
        ):
            with self.assertRaises(Ignore):
                stop_server_task.run(
                    instance_id=instance.pk,
                    run_id=str(uuid4()),
                )

        stop.assert_not_called()
        verified.assert_not_called()
        old_binding.refresh_from_db()
        self.assertIsNone(old_binding.finished_at)
        self.assertIsNotNone(old_binding.superseded_at)
        instance.refresh_from_db()
        self.assertEqual(instance.run_id, later_run_id)
        self.assertEqual(instance.process_created_at, 200.5)

    def test_old_stop_attempt_signal_is_fenced_after_recovery_and_later_run(self):
        from main.tasks import _signal_if_current_stop_attempt

        instance = self.make_instance(state="stopping")
        run_id = uuid4()
        old_binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        old_binding.claimed_at = timezone.now() - timedelta(minutes=2)
        old_binding.save(update_fields=["claimed_at"])
        recovery_binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        recovery_binding.claimed_at = timezone.now()
        recovery_binding.save(update_fields=["claimed_at"])
        instance.run_id = run_id
        instance.lifecycle_task_id = recovery_binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()
        old_identity = ProcessIdentity(pid=12345, created_at=100.5)

        with patch("main.tasks.get_verified_process") as verified:
            with self.assertRaises(Ignore):
                _signal_if_current_stop_attempt(
                    instance.pk,
                    str(run_id),
                    old_binding.task_id,
                    old_identity,
                    "terminate",
                )
            verified.assert_not_called()

        later_run = uuid4()
        later_start = create_instance_task_binding(instance, InstanceTask.Kind.START)
        old_binding.superseded_at = timezone.now()
        old_binding.save(update_fields=["superseded_at"])
        instance.lifecycle_state = "running"
        instance.run_id = later_run
        instance.lifecycle_task_id = later_start.task_id
        instance.process_created_at = 200.5
        instance.save()

        with patch("main.tasks.get_verified_process") as verified:
            with self.assertRaises(Ignore):
                _signal_if_current_stop_attempt(
                    instance.pk,
                    str(run_id),
                    old_binding.task_id,
                    old_identity,
                    "terminate",
                )
            verified.assert_not_called()

    def test_deadline_stop_ownership_blocks_resumed_start_compensation(self):
        instance = self.make_instance(state="starting", with_log=True)
        start_binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = start_binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = SimpleNamespace(pid=identity.pid, _asm_process_identity=identity)
        deadline_job = "timeout-after-start-publication"
        stop_bindings = []

        def deadline_dispatch(binding, task, *, kwargs):
            self.assertEqual(task, stop_server_task)
            binding.claimed_at = timezone.now()
            binding.save(update_fields=["claimed_at"])
            stop_bindings.append(binding.task_id)
            return binding.task_id

        def publish_timeout(*, kwargs, countdown):
            deadline = timezone.datetime.fromisoformat(kwargs["shutdown_at"])
            future = deadline + timedelta(seconds=1)
            with (
                patch("main.tasks.timezone.now", return_value=future),
                patch("main.instance_lifecycle.dispatch_instance_task", side_effect=deadline_dispatch),
                patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}),
                bound_task_request(instance_timeout_task, deadline_job),
            ):
                instance_timeout_task.run(
                    instance_id=instance.pk,
                    run_id=str(run_id),
                    shutdown_at=kwargs["shutdown_at"],
                )
            return SimpleNamespace(id="scheduled-timeout")

        with (
            patch("main.tasks.start_server", return_value=fake_process),
            patch("main.tasks._resolve_launched_identity", return_value=identity),
            patch("main.tasks.instance_timeout_task.apply_async", side_effect=publish_timeout),
            patch("main.tasks._stop_verified_instance") as stop,
            patch("main.tasks.get_verified_process") as verified,
            patch("main.tasks.celery_app.control.revoke") as revoke,
            patch("main.tasks._update_task_state_safely"),
            bound_task_request(start_server_task, start_binding.task_id),
        ):
            with self.assertRaises(Ignore):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        stop.assert_not_called()
        verified.assert_not_called()
        revoke.assert_not_called()
        self.assertEqual(len(stop_bindings), 1)
        start_binding.refresh_from_db()
        self.assertIsNone(start_binding.finished_at)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopping")
        self.assertEqual(instance.lifecycle_task_id, stop_bindings[0])
        self.assertEqual(instance.run_id, run_id)
        self.assertEqual(instance.timeout_task_id, deadline_job)

    def test_stale_start_fallback_cannot_signal_later_run_with_reused_pid(self):
        from main.tasks import _start_attempt_signal_guard, _terminate_launched_identity

        instance = self.make_instance(state="running")
        old_run_id = uuid4()
        old_start = create_instance_task_binding(instance, InstanceTask.Kind.START)
        old_start.claimed_at = timezone.now() - timedelta(hours=1)
        old_start.superseded_at = timezone.now()
        old_start.save(update_fields=["claimed_at", "superseded_at"])
        new_start = create_instance_task_binding(instance, InstanceTask.Kind.START)
        new_start.claimed_at = timezone.now()
        new_start.save(update_fields=["claimed_at"])
        new_run_id = uuid4()
        instance.run_id = new_run_id
        instance.lifecycle_task_id = new_start.task_id
        instance.start_claimed_at = timezone.now()
        instance.pid = 12345
        instance.process_created_at = 200.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.timeout_task_id = "timeout-for-new-run"
        instance.shutdown_at = timezone.now() + timedelta(hours=1)
        instance.save()
        old_identity = ProcessIdentity(pid=12345, created_at=100.5)
        signal_guard = _start_attempt_signal_guard(
            instance.pk,
            str(old_run_id),
            old_start.task_id,
        )

        def try_stale_signal(snapshot, run_id, *, signal_guard):
            signal_guard(old_identity, "terminate")

        with (
            patch("main.tasks._stop_verified_instance", side_effect=try_stale_signal) as stop,
            patch("main.tasks.get_verified_process") as verified,
            patch("main.tasks.celery_app.control.revoke") as revoke,
        ):
            with self.assertRaises(Ignore):
                _terminate_launched_identity(
                    instance,
                    old_identity,
                    str(old_run_id),
                    task_id=old_start.task_id,
                    signal_guard=signal_guard,
                )
            with self.assertRaises(Ignore):
                signal_guard(old_identity, "terminate")

        stop.assert_not_called()
        verified.assert_not_called()
        revoke.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.run_id, new_run_id)
        self.assertEqual(instance.pid, old_identity.pid)
        self.assertEqual(instance.process_created_at, 200.5)
        self.assertEqual(instance.timeout_task_id, "timeout-for-new-run")

    def test_current_start_signal_guard_revalidates_owned_marker_for_each_signal(self):
        from main.tasks import _start_attempt_signal_guard

        instance = self.make_instance(state="starting")
        run_id = uuid4()
        binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        binding.claimed_at = timezone.now()
        binding.save(update_fields=["claimed_at"])
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.start_claimed_at = binding.claimed_at
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.save()
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        process = MagicMock()
        process.environ.return_value = {"ASM_INSTANCE_RUN_ID": str(run_id)}
        signal_guard = _start_attempt_signal_guard(instance.pk, str(run_id), binding.task_id)

        with patch("main.tasks.get_verified_process", return_value=process) as verified:
            self.assertTrue(signal_guard(identity, "terminate"))
            self.assertTrue(signal_guard(identity, "kill"))

        self.assertEqual(verified.call_count, 2)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

    def test_popen_cleanup_signal_is_fenced_when_stop_takes_ownership(self):
        from main.tasks import _signal_if_current_start_launch

        instance = self.make_instance(state="starting")
        run_id = uuid4()
        start_binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
        start_binding.claimed_at = timezone.now()
        start_binding.save(update_fields=["claimed_at"])
        instance.run_id = run_id
        instance.lifecycle_task_id = start_binding.task_id
        instance.start_claimed_at = start_binding.claimed_at
        instance.save()
        first_child = MagicMock()
        launch_identity = ProcessIdentity(pid=12345, created_at=100.5)
        first_child.pid = launch_identity.pid
        first_child._asm_process_identity = launch_identity
        first_child.poll.return_value = None
        verified_process = MagicMock()
        verified_process.create_time.return_value = launch_identity.created_at
        verified_process.environ.return_value = {
            "ASM_INSTANCE_RUN_ID": str(run_id)
        }

        with patch(
            "main.serverhandling.process_identity.psutil.Process",
            return_value=verified_process,
        ):
            self.assertTrue(
                _signal_if_current_start_launch(
                    instance.pk,
                    str(run_id),
                    start_binding.task_id,
                    "terminate",
                    first_child,
                )
            )
        first_child.terminate.assert_called_once_with()

        stop_binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        instance.lifecycle_state = "stopping"
        instance.lifecycle_task_id = stop_binding.task_id
        instance.save()
        stale_child = MagicMock()
        stale_child.pid = launch_identity.pid
        stale_child._asm_process_identity = launch_identity
        stale_child.poll.return_value = None

        with (
            patch(
                "main.serverhandling.process_identity.psutil.Process",
                return_value=verified_process,
            ),
            self.assertRaises(Ignore),
        ):
            _signal_if_current_start_launch(
                instance.pk,
                str(run_id),
                start_binding.task_id,
                "terminate",
                stale_child,
            )

        stale_child.terminate.assert_not_called()

    def test_retired_binding_is_busy_excluded_rejected_and_reported_revoked(self):
        from main.instance_lifecycle import is_instance_lifecycle_busy
        from main.serializers import InstanceSerializer
        from main.instance_tasks import dispatch_instance_task

        instance = self.make_instance()
        binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        binding.superseded_at = timezone.now()
        binding.save(update_fields=["superseded_at"])

        self.assertFalse(is_instance_lifecycle_busy(instance))
        self.assertNotIn(
            binding.task_id,
            InstanceSerializer(instance).data["active_tasks"].values(),
        )

        task = MagicMock()
        with self.assertRaises(RuntimeError):
            dispatch_instance_task(binding, task, kwargs={})

        self.authenticate_as(self.owner)
        with patch("main.views.AsyncResult") as async_result:
            response = self.client.get(f"/api/instances/task_status/{binding.task_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "REVOKED")
        self.assertIn("zastąpione", response.data["result"]["status"])
        async_result.assert_not_called()

    def test_launch_readiness_waits_for_delayed_expected_child(self):
        from main.tasks import _resolve_launched_identity

        run_id = str(uuid4())
        root_identity = ProcessIdentity(pid=12340, created_at=100.0)
        child_identity = ProcessIdentity(pid=12345, created_at=100.5)
        root_process = MagicMock()
        child_process = MagicMock()
        fake_popen = SimpleNamespace(_asm_process_identity=root_identity, poll=lambda: None)
        clock = {"now": 0.0}

        def advance(seconds):
            clock["now"] += seconds

        root_process.children.side_effect = [[], [SimpleNamespace(pid=child_identity.pid)]]

        with (
            patch("main.tasks.time.monotonic", side_effect=lambda: clock["now"]),
            patch("main.tasks.time.sleep", side_effect=advance) as sleep,
            patch(
                "main.tasks.get_verified_process",
                side_effect=lambda identity: {
                    root_identity: root_process,
                    child_identity: child_process,
                }[identity],
            ),
            patch(
                "main.tasks.capture_process_identity",
                return_value=child_identity,
            ),
            patch(
                "main.tasks._process_marker",
                return_value=True,
            ) as marker,
            patch(
                "main.tasks._process_executable_matches",
                side_effect=lambda process, identity, executable: identity == child_identity,
            ),
            patch("main.tasks._descendant_depth", return_value=1) as depth,
        ):
            resolved = _resolve_launched_identity(
                fake_popen,
                root_identity,
                run_id,
                "/isolated/arma3/arma3server_x64",
            )

        self.assertEqual(resolved, child_identity)
        sleep.assert_called_once_with(0.1)
        self.assertGreaterEqual(marker.call_count, 4)
        self.assertGreaterEqual(depth.call_count, 2)

    def test_launch_readiness_rechecks_shell_that_execs_arma_between_observations(self):
        from main.tasks import _resolve_launched_identity

        run_id = str(uuid4())
        root_identity = ProcessIdentity(pid=12345, created_at=100.5)
        root_process = MagicMock()
        fake_popen = SimpleNamespace(_asm_process_identity=root_identity, poll=lambda: None)
        clock = {"now": 0.0}
        root_is_arma = {"value": False}
        observations = []

        def advance(seconds):
            clock["now"] += seconds
            root_is_arma["value"] = True
            observations.append(("sleep", root_is_arma["value"]))

        def verify_marker(process, identity, expected_run_id):
            observations.append(("marker", root_is_arma["value"]))
            return True

        executable_observations = iter([False, True, True])

        def verify_executable(process, identity, expected_executable):
            result = next(executable_observations)
            observations.append(("executable", result))
            return result

        with (
            patch("main.tasks.time.monotonic", side_effect=lambda: clock["now"]),
            patch("main.tasks.time.sleep", side_effect=advance) as sleep,
            patch("main.tasks.get_verified_process", return_value=root_process),
            patch(
                "main.tasks._process_marker",
                side_effect=verify_marker,
            ) as marker,
            patch(
                "main.tasks._process_executable_matches",
                side_effect=verify_executable,
            ) as executable,
        ):
            resolved = _resolve_launched_identity(
                fake_popen,
                root_identity,
                run_id,
                "/isolated/arma3/arma3server_x64",
            )

        self.assertEqual(resolved, root_identity)
        sleep.assert_called_once_with(0.1)
        self.assertTrue(any(kind == "executable" and not result for kind, result in observations))
        self.assertTrue(any(kind == "executable" and result for kind, result in observations))
        expected_executable_checks = [
            index
            for index, (kind, result) in enumerate(observations)
            if kind == "executable" and result
        ]
        marker_checks = [
            index for index, (kind, _result) in enumerate(observations) if kind == "marker"
        ]
        # The same-shell exec is accepted only after its marker is checked
        # again between the initial and confirming executable observations.
        self.assertTrue(
            any(
                expected_executable_checks[0] < index < expected_executable_checks[-1]
                for index in marker_checks
            )
        )
        self.assertEqual(marker.call_args.args[1:], (root_identity, run_id))

    def test_launch_readiness_expiry_keeps_uncertain_process_identity(self):
        from main.tasks import _resolve_launched_identity

        run_id = str(uuid4())
        root_identity = ProcessIdentity(pid=12345, created_at=100.5)
        root_process = MagicMock()
        fake_popen = SimpleNamespace(_asm_process_identity=root_identity, poll=lambda: None)
        clock = {"now": 0.0}

        with (
            patch("main.tasks.time.monotonic", side_effect=lambda: clock["now"]),
            patch("main.tasks.time.sleep", side_effect=lambda seconds: clock.update(now=clock["now"] + seconds)),
            patch("main.tasks.get_verified_process", return_value=root_process),
            patch("main.tasks._process_marker", return_value=True),
            patch("main.tasks._process_executable_matches", return_value=False),
        ):
            with self.assertRaises(UnverifiedProcessError):
                _resolve_launched_identity(
                    fake_popen,
                    root_identity,
                    run_id,
                    "/isolated/arma3/arma3server_x64",
                )

        self.assertGreaterEqual(clock["now"], 10.0)

    def test_reconciliation_does_not_clear_a_pending_start_reservation(self):
        instance = self.make_instance(state="starting", with_log=True)
        instance.run_id = uuid4()
        instance.lifecycle_task_id = "pending-start-task"
        instance.save()

        result = check_all_servers_status_task.run()

        self.assertIn("Status wszystkich", result["status"])
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "starting")
        self.assertEqual(instance.run_id, UUID(str(instance.run_id)))

    def test_reconciliation_leaves_a_claimed_start_for_its_worker_to_finish(self):
        instance = self.make_instance(state="starting")
        instance.run_id = uuid4()
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_claimed_at = timezone.now()
        instance.lifecycle_task_id = "claimed-start-task"
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks.get_verified_process") as get_verified_process,
            patch("main.tasks.find_udp_processes") as find_udp_processes,
        ):
            check_all_servers_status_task.run()

        get_verified_process.assert_not_called()
        find_udp_processes.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "starting")
        self.assertEqual(instance.pid, 12345)

    def test_reconciliation_clears_only_confirmed_disappearance_and_revokes_its_timeout(self):
        instance = self.make_instance(state="running")
        instance.run_id = uuid4()
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.lifecycle_task_id = "completed-start-task"
        instance.timeout_task_id = "timeout-for-gone-run"
        instance.shutdown_at = timezone.now() + timedelta(minutes=20)
        instance.is_running = True
        instance.save()
        stale_stop_binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        stale_stop_binding.claimed_at = timezone.now() - timedelta(minutes=2)
        stale_stop_binding.save(update_fields=["claimed_at"])

        with (
            patch("main.tasks.get_verified_process", return_value=None),
            patch("main.tasks.find_udp_processes", return_value=[]) as find_udp,
            patch("main.tasks.time.sleep"),
            patch("main.tasks.celery_app.control.revoke") as revoke,
        ):
            check_all_servers_status_task.run()

        self.assertEqual(find_udp.call_count, 3)
        revoke.assert_called_once_with("timeout-for-gone-run", terminate=False)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertFalse(instance.is_running)
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.shutdown_at)
        stale_stop_binding.refresh_from_db()
        self.assertIsNotNone(stale_stop_binding.superseded_at)
        self.assertIsNone(stale_stop_binding.finished_at)

    def test_stale_timeout_cannot_stop_a_later_run(self):
        instance = self.make_instance(state="running")
        instance.run_id = uuid4()
        instance.shutdown_at = timezone.now() - timedelta(minutes=1)
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.is_running = True
        instance.save()
        captured_run_id = str(uuid4())

        with patch("main.tasks.reserve_stop") as reserve_stop:
            instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=captured_run_id,
                shutdown_at=instance.shutdown_at.isoformat(),
            )

        reserve_stop.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "running")
        self.assertNotEqual(str(instance.run_id), captured_run_id)

    def test_matching_expired_timeout_reserves_verified_stop_for_its_run(self):
        instance = self.make_instance(state="running")
        run_id = uuid4()
        deadline = timezone.now() - timedelta(minutes=1)
        instance.run_id = run_id
        instance.shutdown_at = deadline
        instance.timeout_task_id = "timeout-for-run"
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()

        with (
            patch("main.instance_lifecycle.dispatch_instance_task") as dispatch,
            patch("main.tasks.celery_app.control.revoke") as revoke,
            patch.object(instance_timeout_task, "retry", return_value={"rescheduled": True}) as retry,
            bound_task_request(instance_timeout_task, "timeout-for-run"),
        ):
            dispatch.side_effect = lambda binding, task, *, kwargs: binding.task_id
            result = instance_timeout_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
                shutdown_at=timezone.localtime(deadline).isoformat(),
            )

        self.assertEqual(result, {"rescheduled": True})
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(dispatch.call_args.kwargs["kwargs"]["run_id"], str(run_id))
        revoke.assert_not_called()
        retry.assert_called_once_with(countdown=30)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopping")
        self.assertEqual(instance.lifecycle_task_id, dispatch.call_args.args[0].task_id)

    def test_stop_worker_keeps_unknown_state_on_pid_reuse(self):
        instance = self.make_instance(state="stopping")
        binding = create_instance_task_binding(instance, "stop")
        instance.run_id = uuid4()
        instance.lifecycle_task_id = binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()

        with (
            patch(
                "main.tasks.get_verified_process",
                side_effect=UnverifiedProcessError("PID identity changed"),
            ),
            patch("main.tasks.find_udp_processes", return_value=[]),
            bound_task_request(stop_server_task, binding.task_id),
        ):
            with self.assertRaises(Exception):
                stop_server_task.run(
                    instance_id=instance.pk,
                    run_id=str(instance.run_id),
                )

        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "unknown")
        self.assertTrue(instance.is_running)
        self.assertEqual(instance.pid, 12345)

    def test_verified_stop_confirms_identity_exit_before_clearing_lifecycle(self):
        instance = self.make_instance(state="stopping")
        binding = create_instance_task_binding(instance, "stop")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.pid = 12345
        instance.process_created_at = 100.5
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.timeout_task_id = "timeout-for-stopped-run"
        instance.shutdown_at = timezone.now() + timedelta(minutes=20)
        instance.is_running = True
        instance.save()
        process_identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = MagicMock()
        fake_process.environ.return_value = {"ASM_INSTANCE_RUN_ID": str(run_id)}
        fake_process.children.return_value = []
        live = {"value": True}

        def verified_process(identity):
            self.assertEqual(identity, process_identity)
            return fake_process if live["value"] else None

        def terminate():
            live["value"] = False

        fake_process.terminate.side_effect = terminate

        with (
            patch("main.tasks.get_verified_process", side_effect=verified_process),
            patch("main.tasks.find_udp_processes", return_value=[]),
            patch("main.tasks.time.sleep"),
            patch("main.tasks.celery_app.control.revoke") as revoke,
            bound_task_request(stop_server_task, binding.task_id),
        ):
            result = stop_server_task.run(
                instance_id=instance.pk,
                run_id=str(run_id),
            )

        self.assertIn("zatrzymany", result["status"])
        fake_process.terminate.assert_called_once_with()
        fake_process.wait.assert_called_once_with(timeout=30)
        revoke.assert_called_once_with("timeout-for-stopped-run", terminate=False)
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertFalse(instance.is_running)
        self.assertIsNone(instance.pid)
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.shutdown_at)

    def test_stop_does_not_infer_shutdown_from_empty_udp_without_saved_identity(self):
        instance = self.make_instance(state="stopping")
        binding = create_instance_task_binding(instance, "stop")
        instance.run_id = uuid4()
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.is_running = True
        instance.save()

        with (
            patch("main.tasks.find_udp_processes", return_value=[]),
            bound_task_request(stop_server_task, binding.task_id),
        ):
            with self.assertRaises(UnverifiedProcessError):
                stop_server_task.run(
                    instance_id=instance.pk,
                    run_id=str(instance.run_id),
                )

        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "unknown")
        self.assertTrue(instance.is_running)
        self.assertIsNone(instance.pid)

    def test_child_pid_recycled_after_snapshot_is_never_signalled(self):
        import main.tasks as task_module

        run_id = str(uuid4())
        root_identity = ProcessIdentity(pid=12000, created_at=100.0)
        child_identity = ProcessIdentity(pid=12001, created_at=100.5)
        root_process = MagicMock()
        root_process.environ.return_value = {"ASM_INSTANCE_RUN_ID": run_id}
        child_process = MagicMock()
        child_process.environ.return_value = {"ASM_INSTANCE_RUN_ID": run_id}
        root_process.children.return_value = [SimpleNamespace(pid=child_identity.pid)]

        with (
            patch.object(
                task_module,
                "get_verified_process",
                side_effect=lambda identity: {
                    root_identity: root_process,
                    child_identity: child_process,
                }[identity],
            ),
            patch.object(
                task_module,
                "capture_process_identity",
                return_value=child_identity,
            ),
            patch.object(
                task_module,
                "_descendant_depth",
                side_effect=[1, UnverifiedProcessError("child PID was recycled")],
            ),
        ):
            with self.assertRaises(UnverifiedProcessError):
                task_module._terminate_process_tree(
                    root_identity,
                    run_id,
                    expected_executable=None,
                    signal_guard=lambda *args, **kwargs: True,
                )

        child_process.terminate.assert_not_called()
        child_process.kill.assert_not_called()
        root_process.terminate.assert_not_called()
        root_process.kill.assert_not_called()

    def test_matching_server_deadline_is_exposed_only_after_worker_launch(self):
        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, "start")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-lifecycle.sh"
        instance.save()
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = SimpleNamespace(pid=12345, _asm_process_identity=identity)

        with (
            patch("main.tasks.start_server", return_value=fake_process) as start,
            patch("main.tasks._resolve_launched_identity", return_value=identity),
            patch(
                "main.tasks.instance_timeout_task.apply_async",
                return_value=SimpleNamespace(id="timeout-for-this-run"),
            ) as schedule,
            bound_task_request(start_server_task, binding.task_id),
        ):
            result = start_server_task.run(
                instance_id=instance.pk,
                arma3_dir="/isolated/arma3",
                run_id=str(run_id),
            )

        self.assertIn("uruchomiony", result["status"])
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["run_id"], str(run_id))
        schedule.assert_called_once()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "running")
        self.assertTrue(instance.is_running)
        self.assertEqual(instance.pid, 12345)
        self.assertEqual(instance.process_created_at, 100.5)
        self.assertEqual(instance.timeout_task_id, "timeout-for-this-run")
        self.assertGreater(instance.shutdown_at, timezone.now())

    def test_administrative_start_has_no_timeout_task_or_deadline(self):
        instance = self.make_instance(
            self.superuser,
            state="starting",
            is_admin_instance=True,
            with_log=True,
        )
        binding = create_instance_task_binding(instance, "start")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-admin.sh"
        instance.save()

        with (
            patch(
                "main.tasks.start_server",
                return_value=SimpleNamespace(
                    pid=12345,
                    _asm_process_identity=ProcessIdentity(pid=12345, created_at=100.5),
                ),
            ),
            patch(
                "main.tasks._resolve_launched_identity",
                return_value=ProcessIdentity(pid=12345, created_at=100.5),
            ),
            patch("main.tasks.instance_timeout_task.apply_async") as schedule,
            bound_task_request(start_server_task, binding.task_id),
        ):
            start_server_task.run(
                instance_id=instance.pk,
                arma3_dir="/isolated/arma3",
                run_id=str(run_id),
            )

        schedule.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "running")
        self.assertIsNone(instance.shutdown_at)
        self.assertIsNone(instance.timeout_task_id)

    def test_timeout_scheduling_failure_never_promises_a_deadline_when_stop_is_uncertain(self):
        instance = self.make_instance(state="starting", with_log=True)
        binding = create_instance_task_binding(instance, "start")
        run_id = uuid4()
        instance.run_id = run_id
        instance.lifecycle_task_id = binding.task_id
        instance.process_executable = "/isolated/arma3/arma3server_x64"
        instance.start_file_path = "/isolated/arma3/start-timeout-failure.sh"
        instance.save()
        identity = ProcessIdentity(pid=12345, created_at=100.5)
        fake_process = SimpleNamespace(pid=12345, _asm_process_identity=identity)

        with (
            patch("main.tasks.start_server", return_value=fake_process),
            patch("main.tasks._resolve_launched_identity", return_value=identity),
            patch(
                "main.tasks.instance_timeout_task.apply_async",
                side_effect=RuntimeError("invented scheduler failure"),
            ),
            patch(
                "main.tasks._stop_verified_instance",
                side_effect=UnverifiedProcessError("server exit could not be verified"),
            ),
            bound_task_request(start_server_task, binding.task_id),
        ):
            with self.assertRaises(RuntimeError):
                start_server_task.run(
                    instance_id=instance.pk,
                    arma3_dir="/isolated/arma3",
                    run_id=str(run_id),
                )

        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, "unknown")
        self.assertIsNone(instance.shutdown_at)
        self.assertIsNone(instance.timeout_task_id)
        self.assertTrue(instance.is_running)
        self.assertEqual(instance.pid, 12345)
