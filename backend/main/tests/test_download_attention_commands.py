from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase
from django.utils import timezone
from celery.exceptions import Ignore

from main.download_jobs import _claim_download
from main.instance_tasks import create_instance_task_binding
from main.models import InstanceTask, Instances, Ports


class DownloadAttentionCommandTests(TransactionTestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username="test-owner", password="test-password")
        port = Ports.objects.create(port_number=23091, is_available=False)
        self.instance = Instances.objects.create(
            name="test-instance", user=owner, port=port, preset="test.html"
        )
        self.task = create_instance_task_binding(self.instance, InstanceTask.Kind.DOWNLOAD)
        self.task.requires_attention = True
        self.task.attention_reason = "process_cleanup_unconfirmed"
        self.task.attention_workspace = "/private/invented-workspace"
        self.task.save()

    def test_list_reports_reason_without_private_workspace_and_includes_history_on_request(self):
        out = StringIO()
        call_command("download_attention", stdout=out)
        self.assertIn(self.task.pk, out.getvalue())
        self.assertIn("process_cleanup_unconfirmed", out.getvalue())
        self.assertNotIn(self.task.attention_workspace, out.getvalue())
        self.task.superseded_at = timezone.now()
        self.task.save(update_fields=["superseded_at"])
        out = StringIO()
        call_command("download_attention", stdout=out)
        self.assertNotIn(self.task.pk, out.getvalue())
        out = StringIO()
        call_command("download_attention", history=True, stdout=out)
        self.assertIn(self.task.pk, out.getvalue())

    def test_resolution_is_dry_run_by_default_and_requires_operator_attestation(self):
        call_command("resolve_download_attention", self.task.pk, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("resolve_download_attention", self.task.pk, apply=True, stdout=StringIO())
        self.task.refresh_from_db()
        self.assertIsNone(self.task.superseded_at)

    def test_resolution_preserves_evidence_and_fences_old_delivery(self):
        call_command("resolve_download_attention", self.task.pk, apply=True,
                     confirm_quiescent=True, stdout=StringIO())
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.superseded_at)
        self.assertTrue(self.task.requires_attention)
        self.assertEqual(self.task.attention_reason, "process_cleanup_unconfirmed")
        self.assertEqual(self.task.attention_workspace, "/private/invented-workspace")
        with self.assertRaises(Ignore):
            _claim_download(self.task.pk, self.instance.pk)
        with self.assertRaises(CommandError):
            call_command("resolve_download_attention", self.task.pk, apply=True,
                         confirm_quiescent=True, stdout=StringIO())

    def test_resolution_refuses_nonattention_or_non_download_tasks(self):
        for kind, attention in (("download", False), ("start", True)):
            self.task.kind = kind
            self.task.requires_attention = attention
            self.task.save(update_fields=["kind", "requires_attention"])
            with self.assertRaises(CommandError):
                call_command("resolve_download_attention", self.task.pk, apply=True,
                             confirm_quiescent=True, stdout=StringIO())
            self.task.refresh_from_db()
            self.assertIsNone(self.task.superseded_at)
