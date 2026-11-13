"""Retire one verified-quiescent attention binding without deleting evidence."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from main.instance_lifecycle import lifecycle_transaction
from main.models import InstanceTask


class Command(BaseCommand):
    help = "Review or retire one download attention binding after operator verification."

    def add_arguments(self, parser):
        parser.add_argument("task_id")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--confirm-quiescent", action="store_true",
            help="Attest all download workers and SteamCMD processes are stopped and unable to resume.",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["confirm_quiescent"]:
            raise CommandError("Verify all download workers and SteamCMD are quiescent, then use --confirm-quiescent.")
        with lifecycle_transaction():
            task = InstanceTask.objects.select_for_update().filter(
                pk=options["task_id"], kind=InstanceTask.Kind.DOWNLOAD,
                requires_attention=True, finished_at__isnull=True,
                superseded_at__isnull=True,
            ).first()
            if task is None:
                raise CommandError("No matching unfinished download requiring attention.")
            if options["apply"]:
                task.superseded_at = timezone.now()
                task.save(update_fields=["superseded_at"])
        if options["apply"]:
            self.stdout.write("Download reservation retired; diagnostic evidence retained.")
        else:
            self.stdout.write("Dry run: matching reservation found; no changes made. Verify worker and SteamCMD quiescence before applying.")
