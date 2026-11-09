from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from main.instance_lifecycle import is_instance_lifecycle_busy
from main.instance_tasks import create_instance_task_binding, track_instance_task
from main.models import InstanceTask, Instances, Ports
from main.serializers import InstanceSerializer


User = get_user_model()


class DownloadAttentionStateTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="attention-owner",
            password="test-password",
        )
        self.client = APIClient()
        self.port_number = 23200

    def make_instance(self):
        self.port_number += 1
        port = Ports.objects.create(
            port_number=self.port_number,
            is_available=False,
        )
        return Instances.objects.create(
            name=f"attention-server-{self.port_number}",
            user=self.owner,
            preset=SimpleUploadedFile(
                f"attention-server-{self.port_number}.html",
                b"<html></html>",
            ),
            port=port,
        )

    def create_attention_binding(self, instance, *, reason, workspace):
        binding = create_instance_task_binding(instance, InstanceTask.Kind.DOWNLOAD)
        binding.requires_attention = True
        binding.attention_reason = reason
        binding.attention_workspace = workspace
        binding.save(
            update_fields=(
                "requires_attention",
                "attention_reason",
                "attention_workspace",
            )
        )
        return binding

    def test_attention_state_persists_on_the_task_binding(self):
        instance = self.make_instance()
        workspace = "C:/repair/attention-run-01"
        binding = self.create_attention_binding(
            instance,
            reason="process_cleanup_unconfirmed",
            workspace=workspace,
        )

        persisted = InstanceTask.objects.get(pk=binding.task_id)

        self.assertTrue(persisted.requires_attention)
        self.assertEqual(persisted.attention_reason, "process_cleanup_unconfirmed")
        self.assertEqual(persisted.attention_workspace, workspace)
        self.assertEqual(
            {value for value, _label in InstanceTask._meta.get_field("attention_reason").choices},
            {"process_cleanup_unconfirmed", "worker_state_unverified"},
        )

    def test_generic_completion_keeps_attention_binding_busy_and_recoverable(self):
        instance = self.make_instance()
        binding = self.create_attention_binding(
            instance,
            reason="worker_state_unverified",
            workspace="C:/repair/attention-run-02",
        )

        class BoundTask:
            request = SimpleNamespace(id=binding.task_id)

        @track_instance_task
        def finish(task):
            return {"status": "completed"}

        self.assertEqual(finish(BoundTask()), {"status": "completed"})

        persisted = InstanceTask.objects.get(pk=binding.task_id)
        self.assertIsNone(persisted.finished_at)
        self.assertIsNone(persisted.superseded_at)
        self.assertEqual(
            InstanceSerializer(instance).data["active_tasks"][InstanceTask.Kind.DOWNLOAD],
            binding.task_id,
        )
        self.assertTrue(is_instance_lifecycle_busy(instance))

    def test_ordinary_completion_still_releases_the_active_task(self):
        instance = self.make_instance()
        binding = create_instance_task_binding(instance, InstanceTask.Kind.DOWNLOAD)

        class BoundTask:
            request = SimpleNamespace(id=binding.task_id)

        @track_instance_task
        def finish(task):
            return {"status": "completed"}

        self.assertEqual(finish(BoundTask()), {"status": "completed"})

        binding.refresh_from_db()
        self.assertIsNotNone(binding.finished_at)
        self.assertNotIn(
            binding.task_id,
            InstanceSerializer(instance).data["active_tasks"].values(),
        )
        self.assertFalse(is_instance_lifecycle_busy(instance))

    def test_attention_workspace_and_reason_stay_out_of_safe_outputs(self):
        instance = self.make_instance()
        workspace = "C:/private/repair/attention-run-03"
        reason = "process_cleanup_unconfirmed"
        binding = self.create_attention_binding(
            instance,
            reason=reason,
            workspace=workspace,
        )
        instance_output = InstanceSerializer(instance).data

        self.client.force_authenticate(user=self.owner)
        with patch(
            "main.views.AsyncResult",
            side_effect=AssertionError("attention status must not query AsyncResult"),
        ) as async_result:
            response = self.client.get(
                f"/api/instances/task_status/{binding.task_id}/"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "REQUIRES_ATTENTION")
        self.assertEqual(
            response.data["result"],
            {"status": "Stan zadania wymaga ręcznej weryfikacji przez administratora."},
        )
        async_result.assert_not_called()
        safe_outputs = f"{instance_output!r} {response.data!r} {str(binding)!r}"
        for private_value in (
            "requires_attention",
            "attention_reason",
            "attention_workspace",
            reason,
            workspace,
        ):
            self.assertNotIn(private_value, safe_outputs)
