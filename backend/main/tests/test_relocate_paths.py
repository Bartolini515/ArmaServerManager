from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TransactionTestCase

from main.models import Instances, Ports, Profile


class RelocatePathsTests(TransactionTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = Path(self.tmp.name) / "old"
        self.new = Path(self.tmp.name) / "new"
        self.old.mkdir()
        self.new.mkdir()
        self.owner = Profile.objects.create_user(username="invented-owner", password="invented")
        self.owner.server_config = str(self.old / "server_invented-owner.cfg")
        self.owner.save(update_fields=["server_config"])
        port = Ports.objects.create(port_number=25000, is_available=False)
        self.instance = Instances.objects.create(
            name="invented-instance", user=self.owner, port=port,
            preset="user_1/presets/example.html",
            start_file_path=str(self.old / "startinvented-instance.sh"),
        )
        (self.new / "server_invented-owner.cfg").write_text("example")
        (self.new / "startinvented-instance.sh").write_text("example")
        (self.new / "arma3server_x64").write_text("example")

    def test_dry_run_does_not_update_paths(self):
        call_command("relocate_arma_paths", str(self.old), str(self.new))
        self.instance.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.instance.start_file_path, str(self.old / "startinvented-instance.sh"))
        self.assertEqual(self.owner.server_config, str(self.old / "server_invented-owner.cfg"))

    def test_updates_matching_instance_and_profile_paths(self):
        call_command("relocate_arma_paths", str(self.old), str(self.new), apply=True)
        self.instance.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.instance.start_file_path, str(self.new / "startinvented-instance.sh"))
        self.assertEqual(self.owner.server_config, str(self.new / "server_invented-owner.cfg"))

    def test_missing_target_leaves_all_rows_unchanged(self):
        (self.new / "server_invented-owner.cfg").unlink()
        with self.assertRaises(CommandError):
            call_command("relocate_arma_paths", str(self.old), str(self.new), apply=True)
        self.instance.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.instance.start_file_path, str(self.old / "startinvented-instance.sh"))
        self.assertEqual(self.owner.server_config, str(self.old / "server_invented-owner.cfg"))

    def test_busy_instance_is_rejected(self):
        self.instance.lifecycle_state = Instances.LifecycleState.RUNNING
        self.instance.save(update_fields=["lifecycle_state"])
        with self.assertRaises(CommandError):
            call_command("relocate_arma_paths", str(self.old), str(self.new), apply=True)
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.start_file_path, str(self.old / "startinvented-instance.sh"))

    def test_unexpected_profile_path_leaves_instance_unchanged(self):
        self.owner.server_config = str(self.old / "other.cfg")
        self.owner.save(update_fields=["server_config"])
        with self.assertRaises(CommandError):
            call_command("relocate_arma_paths", str(self.old), str(self.new), apply=True)
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.start_file_path, str(self.old / "startinvented-instance.sh"))

    def test_missing_server_executable_is_rejected(self):
        (self.new / "arma3server_x64").unlink()
        with self.assertRaises(CommandError):
            call_command("relocate_arma_paths", str(self.old), str(self.new), apply=True)
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.start_file_path, str(self.old / "startinvented-instance.sh"))

    def test_database_failure_rolls_back_earlier_instance_updates(self):
        with patch.object(Profile, "save", side_effect=DatabaseError("invented failure")):
            with self.assertRaises(DatabaseError):
                call_command("relocate_arma_paths", str(self.old), str(self.new), apply=True)
        self.instance.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.instance.start_file_path, str(self.old / "startinvented-instance.sh"))
        self.assertEqual(self.owner.server_config, str(self.old / "server_invented-owner.cfg"))
