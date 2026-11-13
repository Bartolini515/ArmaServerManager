"""Inspect download attention without exposing workspace paths."""

import json

from django.core.management.base import BaseCommand

from main.models import InstanceTask


class Command(BaseCommand):
    help = "List download tasks requiring attention; optionally include resolved history."

    def add_arguments(self, parser):
        parser.add_argument("--history", action="store_true")

    def handle(self, *args, **options):
        tasks = InstanceTask.objects.filter(
            kind=InstanceTask.Kind.DOWNLOAD, requires_attention=True,
        )
        if not options["history"]:
            tasks = tasks.filter(finished_at__isnull=True, superseded_at__isnull=True)
        allowed_reasons = set(InstanceTask.AttentionReason.values)
        for task in tasks.order_by("created_at", "task_id"):
            self.stdout.write(json.dumps({
                "task_id": task.task_id,
                "instance_id": task.instance_id,
                "reason": task.attention_reason if task.attention_reason in allowed_reasons else "unknown",
                "created_at": task.created_at.isoformat(),
                "resolved": task.superseded_at is not None or task.finished_at is not None,
            }))
