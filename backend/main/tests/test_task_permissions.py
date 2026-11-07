from types import SimpleNamespace
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from main.instance_tasks import (
    create_instance_task_binding,
    dispatch_instance_task,
    enqueue_instance_task,
    mark_instance_task_finished,
    track_instance_task,
)
from main.models import InstanceTask, Instances, Ports


User = get_user_model()


class InstanceTaskPermissionTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="test-password")
        self.other = User.objects.create_user(username="other", password="test-password")
        self.staff = User.objects.create_user(
            username="staff", password="test-password", is_staff=True
        )
        self.client = APIClient()
        self.port_number = 23100

    def make_instance(self, owner=None, *, is_admin_instance=False):
        owner = owner or self.owner
        port = Ports.objects.create(port_number=self.port_number, is_available=False)
        self.port_number += 1
        return Instances.objects.create(
            name=f"task-server-{self.port_number}",
            user=owner,
            preset=SimpleUploadedFile(f"task-server-{self.port_number}.html", b"preset"),
            port=port,
            is_admin_instance=is_admin_instance,
        )

    def request_status(self, user, task_id):
        self.client.force_authenticate(user=user)
        return self.client.get(f"/api/instances/task_status/{task_id}/")

    def assert_binding_visible_on_separate_sqlite_connection(self, task_id):
        connection = connections["default"]
        database_name = connection.settings_dict["NAME"]
        if connection.vendor != "sqlite" or not str(database_name).startswith("file:"):
            self.skipTest("The isolated test database does not support a shared SQLite observer")

        observer = connection.copy(alias="task_binding_observer")
        try:
            with observer.cursor() as cursor:
                cursor.execute(
                    "SELECT task_id FROM main_instancetask WHERE task_id = %s",
                    [task_id],
                )
                row = cursor.fetchone()
        finally:
            observer.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], task_id)

    def test_task_status_checks_persisted_owner_before_querying_celery(self):
        instance = self.make_instance()
        binding = create_instance_task_binding(instance, "download")
        celery_result = SimpleNamespace(
            status="SUCCESS", result={"status": "completed"}
        )

        with patch("main.views.AsyncResult", return_value=celery_result) as async_result:
            owner_response = self.request_status(self.owner, binding.task_id)
            self.assertEqual(owner_response.status_code, 200)
            self.assertEqual(owner_response.data["id"], binding.task_id)
            async_result.assert_called_once_with(binding.task_id)

            async_result.reset_mock()
            other_response = self.request_status(self.other, binding.task_id)
            self.assertEqual(other_response.status_code, 404)
            async_result.assert_not_called()

            staff_response = self.request_status(self.staff, binding.task_id)
            self.assertEqual(staff_response.status_code, 200)
            async_result.assert_called_once_with(binding.task_id)

            async_result.reset_mock()
            missing_response = self.request_status(self.owner, "legacy-unknown-task")
            self.assertEqual(missing_response.status_code, 404)
            async_result.assert_not_called()

    def test_nonstaff_cannot_poll_tasks_bound_to_administrative_instances(self):
        admin_instance = self.make_instance(self.owner, is_admin_instance=True)
        binding = create_instance_task_binding(admin_instance, "start")

        with patch("main.views.AsyncResult") as async_result:
            response = self.request_status(self.owner, binding.task_id)

        self.assertEqual(response.status_code, 404)
        async_result.assert_not_called()

    def test_enqueue_creates_binding_before_apply_async_and_returns_preselected_id(self):
        instance = self.make_instance()
        task = Mock()

        def assert_binding_exists(*, kwargs, task_id):
            self.assert_binding_visible_on_separate_sqlite_connection(task_id)
            binding = InstanceTask.objects.get(pk=task_id)
            self.assertEqual(binding.instance, instance)
            self.assertEqual(binding.kind, "start")
            self.assertIsNone(binding.finished_at)
            self.assertEqual(kwargs, {"instance_id": instance.pk})
            return SimpleNamespace(id="broker-returned-another-id")

        task.apply_async.side_effect = assert_binding_exists
        task_id = enqueue_instance_task(
            instance,
            "start",
            task,
            kwargs={"instance_id": instance.pk},
        )

        self.assertRegex(task_id, r"^[0-9a-f-]{36}$")
        task.apply_async.assert_called_once_with(
            kwargs={"instance_id": instance.pk},
            task_id=task_id,
        )
        self.assertTrue(InstanceTask.objects.filter(pk=task_id).exists())

    def test_failed_dispatch_removes_active_binding(self):
        instance = self.make_instance()
        task = Mock()
        task.apply_async.side_effect = RuntimeError("invented queue failure")

        with self.assertRaisesRegex(RuntimeError, "invented queue failure"):
            enqueue_instance_task(
                instance,
                "stop",
                task,
                kwargs={"instance_id": instance.pk},
            )

        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())

    def test_create_and_dispatch_primitives_support_post_commit_dispatch(self):
        instance = self.make_instance()
        task = Mock()
        task.apply_async.side_effect = lambda *, kwargs, task_id: (
            self.assert_binding_visible_on_separate_sqlite_connection(task_id)
        )
        dispatched_ids = []

        with transaction.atomic():
            binding = create_instance_task_binding(instance, "download")
            self.assertTrue(InstanceTask.objects.filter(pk=binding.task_id).exists())
            transaction.on_commit(
                lambda: dispatched_ids.append(
                    dispatch_instance_task(
                        binding,
                        task,
                        kwargs={"instance_id": instance.pk},
                    )
                )
            )

        self.assertEqual(dispatched_ids, [binding.task_id])
        task.apply_async.assert_called_once_with(
            kwargs={"instance_id": instance.pk},
            task_id=binding.task_id,
        )

    def test_enqueue_rejects_outer_atomic_and_manual_transactions_without_stranding_work(self):
        instance = self.make_instance()
        task = Mock()

        class ForceRollback(Exception):
            pass

        with self.assertRaises(ForceRollback):
            with transaction.atomic():
                with self.assertRaisesRegex(RuntimeError, "outside an active transaction"):
                    enqueue_instance_task(
                        instance,
                        "start",
                        task,
                        kwargs={"instance_id": instance.pk},
                    )
                raise ForceRollback()

        connection = connections["default"]
        self.assertTrue(connection.get_autocommit())
        connection.set_autocommit(False)
        try:
            with self.assertRaisesRegex(RuntimeError, "outside an active transaction"):
                enqueue_instance_task(
                    instance,
                    "stop",
                    task,
                    kwargs={"instance_id": instance.pk},
                )
        finally:
            connection.rollback()
            connection.set_autocommit(True)

        self.assertEqual(InstanceTask.objects.filter(instance=instance).count(), 0)
        task.apply_async.assert_not_called()

    def test_dispatch_rejects_uncommitted_binding_and_rollback_does_not_publish(self):
        instance = self.make_instance()
        task = Mock()

        class ForceRollback(Exception):
            pass

        with self.assertRaises(ForceRollback):
            with transaction.atomic():
                binding = create_instance_task_binding(instance, "download")
                with self.assertRaisesRegex(RuntimeError, "outside an active transaction"):
                    dispatch_instance_task(
                        binding,
                        task,
                        kwargs={"instance_id": instance.pk},
                    )
                raise ForceRollback()

        self.assertEqual(InstanceTask.objects.filter(instance=instance).count(), 0)
        task.apply_async.assert_not_called()

    def test_terminal_completion_marks_only_registered_nonretry_tasks_finished(self):
        instance = self.make_instance()
        binding = create_instance_task_binding(instance, "stop")

        class BoundTask:
            request = SimpleNamespace(id=binding.task_id)

        @track_instance_task
        def complete(task):
            return "done"

        self.assertEqual(complete(BoundTask()), "done")
        binding.refresh_from_db()
        self.assertIsNotNone(binding.finished_at)

        retry_binding = create_instance_task_binding(instance, "start")
        BoundTask.request = SimpleNamespace(id=retry_binding.task_id)

        @track_instance_task
        def retry(task):
            raise Retry("try again")

        with self.assertRaises(Retry):
            retry(BoundTask())
        retry_binding.refresh_from_db()
        self.assertIsNone(retry_binding.finished_at)

        return_retry_binding = create_instance_task_binding(instance, "download")
        BoundTask.request = SimpleNamespace(id=return_retry_binding.task_id)

        @track_instance_task
        def return_retry(task):
            return Retry("retry without raising")

        self.assertIsInstance(return_retry(BoundTask()), Retry)
        return_retry_binding.refresh_from_db()
        self.assertIsNone(return_retry_binding.finished_at)

        mark_instance_task_finished("direct-unregistered-task")
