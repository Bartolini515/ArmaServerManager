"""Update stored instance and profile paths after an offline Arma relocation."""

import os
import stat
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from main.instance_lifecycle import is_instance_lifecycle_busy, lifecycle_transaction
from main.models import Instances, Profile


def _directory(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise CommandError("Roots must be absolute paths without parent components.")
    return Path(os.path.abspath(path))


def _real_directory(path):
    current = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            current /= component
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                return False
        return True
    except OSError:
        return False


def _regular_file(path):
    if not _real_directory(path.parent):
        return False
    try:
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
    except OSError:
        return False


class Command(BaseCommand):
    help = "Offline: validate and update instance/profile paths after copying Arma."

    def add_arguments(self, parser):
        parser.add_argument("old_root")
        parser.add_argument("new_root")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        old = _directory(options["old_root"])
        new = _directory(options["new_root"])
        if old == new or old in new.parents or new in old.parents:
            raise CommandError("Roots must be distinct and non-overlapping.")
        if not _real_directory(new):
            raise CommandError("The new server directory is missing or unsafe.")
        if not _regular_file(new / "arma3server_x64"):
            self.stderr.write("Warning: arma3server_x64 is missing or unsafe; server start will fail.")

        with lifecycle_transaction():
            profiles = list(Profile.objects.select_for_update().order_by("pk"))
            instances = list(Instances.objects.select_for_update().order_by("pk"))
            changes = []
            for instance in instances:
                if is_instance_lifecycle_busy(instance):
                    raise CommandError("All instances and tasks must be stopped.")
                if instance.start_file_path:
                    leaf = f"start{instance.name}.sh"
                    self._add_change(changes, instance, "start_file_path", old, new, leaf)
            for profile in profiles:
                if profile.server_config:
                    leaf = f"server_{profile.username}.cfg"
                    self._add_change(changes, profile, "server_config", old, new, leaf)
            if options["apply"]:
                for obj, field, target in changes:
                    setattr(obj, field, target)
                    obj.save(update_fields=[field])
        action = "Updated" if options["apply"] else "Validated"
        self.stdout.write(f"{action} {len(changes)} stored paths.")

    @staticmethod
    def _add_change(changes, obj, field, old, new, leaf):
        if Path(leaf).name != leaf or leaf in (".", ".."):
            raise CommandError("A stored name is not a safe filename.")
        expected = str(old / leaf)
        target = str(new / leaf)
        if getattr(obj, field) != expected:
            raise CommandError("A stored path does not match the expected old root.")
        if len(target) > obj._meta.get_field(field).max_length:
            raise CommandError("A replacement path is too long.")
        if not _regular_file(Path(target)):
            raise CommandError("A replacement file is missing or unsafe.")
        changes.append((obj, field, target))
