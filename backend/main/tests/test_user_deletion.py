from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from main.instance_files import ManagedFileError
from main.models import Instances, Ports


User = get_user_model()


class UserDeletionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media_root = self.root / "media"
        self.arma_root = self.root / "arma3"
        self.private_root = self.root / ".asm-private"
        self.static_root = self.root / "static"
        for path in (self.media_root, self.arma_root, self.private_root, self.static_root):
            path.mkdir(parents=True)
        self.settings_override = override_settings(
            MEDIA_ROOT=str(self.media_root),
            STATIC_ROOT=str(self.static_root),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.owner = User.objects.create_user(username="delete-owner", password="test-password")
        self.other = User.objects.create_user(username="delete-other", password="test-password")
        self.staff = User.objects.create_user(
            username="delete-staff", password="test-password", is_staff=True
        )
        self.client = APIClient()
        self.next_port = 26100
        self.snapshot = {
            "paths": {
                "arma3": str(self.arma_root),
                "mods_directory": str(self.arma_root / "mods"),
                "logs_directory": str(self.root / "operation-logs"),
                "download_directory": str(self.root / "downloads"),
                "steamcmd": "",
            },
            "steam_auth": {"username": "", "password": "", "shared_secret": ""},
        }
        (self.arma_root / "arma3server_x64").write_bytes(b"invented executable placeholder")

    def config_context(self):
        def get_value(key, default=None):
            value = self.snapshot
            try:
                for component in key.split("."):
                    value = value[component]
                return value
            except (KeyError, TypeError):
                return default

        stack = ExitStack()
        stack.enter_context(patch("main.views.config.snapshot", return_value=self.snapshot))
        stack.enter_context(patch("main.views.config.get", side_effect=get_value))
        stack.enter_context(patch("main.utils.config.config.snapshot", return_value=self.snapshot))
        return stack

    def add_port(self):
        port = Ports.objects.create(port_number=self.next_port, is_available=False)
        self.next_port += 1
        return port

    def make_instance(self, owner, name, *, state=Instances.LifecycleState.STOPPED, with_log=True):
        port = self.add_port()
        start_path = self.arma_root / f"start{name}.sh"
        start_path.write_bytes(f"invented start bytes for {name}\n".encode())
        instance = Instances.objects.create(
            name=name,
            user=owner,
            preset=SimpleUploadedFile(f"{name}.html", f"<html>{name}</html>".encode()),
            port=port,
            lifecycle_state=state,
            start_file_path=str(start_path),
            is_running=(state == Instances.LifecycleState.RUNNING),
        )
        if with_log:
            instance.log_file.save(
                f"{name}-log.txt",
                SimpleUploadedFile(f"{name}-log.txt", f"invented log bytes for {name}\n".encode()),
                save=True,
            )
        return instance

    def create_server_config(self, owner):
        path = self.arma_root / f"server_{owner.username}.cfg"
        path.write_bytes(f"invented cfg bytes for {owner.username}\n".encode())
        owner.server_config = str(path)
        owner.save(update_fields=["server_config"])
        return path

    def delete_profile_request(self, profile):
        self.client.force_authenticate(user=self.staff)
        return self.client.delete(f"/api/moderator_panel/{profile.pk}/user/delete/")

    def owned_file_paths(self, instance):
        return [Path(instance.preset.path), Path(instance.log_file.path), Path(instance.start_file_path)]

    def test_busy_owned_instance_blocks_user_delete_before_any_file_or_port_change(self):
        first = self.make_instance(self.owner, "preflight-first")
        second = self.make_instance(self.owner, "preflight-second")
        busy = self.make_instance(
            self.owner,
            "preflight-busy",
            state=Instances.LifecycleState.UNKNOWN,
        )
        cfg_path = self.create_server_config(self.owner)
        expected_files = [path for instance in (first, second, busy) for path in self.owned_file_paths(instance)]
        expected_bytes = {path: path.read_bytes() for path in expected_files}
        self.client.force_authenticate(user=self.staff)

        with self.config_context():
            response = self.delete_profile_request(self.owner)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
        self.assertEqual(Instances.objects.filter(user=self.owner).count(), 3)
        for instance in (first, second, busy):
            instance.port.refresh_from_db()
            self.assertFalse(instance.port.is_available)
        for path, content in expected_bytes.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), content)
        self.assertTrue(cfg_path.is_file())

    def test_user_delete_removes_every_stopped_instance_file_and_releases_ports(self):
        first = self.make_instance(self.owner, "delete-first")
        second = self.make_instance(self.owner, "delete-second")
        cfg_path = self.create_server_config(self.owner)
        owned_files = [path for instance in (first, second) for path in self.owned_file_paths(instance)]
        ports = [first.port, second.port]
        self.client.force_authenticate(user=self.staff)

        with self.config_context():
            response = self.delete_profile_request(self.owner)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["message"], "Użytkownik usunięty")
        self.assertFalse(User.objects.filter(pk=self.owner.pk).exists())
        self.assertFalse(Instances.objects.filter(pk__in=[first.pk, second.pk]).exists())
        for path in owned_files + [cfg_path]:
            self.assertFalse(path.exists(), f"owned file remains: {path.name}")
        for port in ports:
            port.refresh_from_db()
            self.assertTrue(port.is_available)

    def test_user_delete_preserves_server_config_still_referenced_by_another_profile(self):
        instance = self.make_instance(self.owner, "shared-config-instance")
        cfg_path = self.create_server_config(self.owner)
        self.other.server_config = str(cfg_path)
        self.other.save(update_fields=["server_config"])
        self.client.force_authenticate(user=self.staff)

        with self.config_context():
            response = self.delete_profile_request(self.owner)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(User.objects.filter(pk=self.other.pk).exists())
        self.assertEqual(User.objects.get(pk=self.other.pk).server_config, str(cfg_path))
        self.assertTrue(cfg_path.is_file())
        self.assertFalse(Instances.objects.filter(pk=instance.pk).exists())

    def test_profile_single_delete_runs_the_same_stopped_instance_cleanup(self):
        instance = self.make_instance(self.owner, "model-user-delete")
        cfg_path = self.create_server_config(self.owner)
        owned_files = self.owned_file_paths(instance)
        port = instance.port
        owner_id = self.owner.pk

        with self.config_context():
            delete_result = self.owner.delete()

        self.assertIsNone(self.owner.pk)
        self.assertIsInstance(delete_result, tuple)
        self.assertGreaterEqual(delete_result[0], 1)
        self.assertFalse(User.objects.filter(pk=owner_id).exists())
        self.assertFalse(Instances.objects.filter(pk=instance.pk).exists())
        self.assertFalse(cfg_path.exists())
        for path in owned_files:
            self.assertFalse(path.exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_instance_single_delete_runs_guarded_cleanup_and_releases_port(self):
        instance = self.make_instance(self.owner, "model-instance-delete")
        owned_files = self.owned_file_paths(instance)
        port = instance.port
        instance_id = instance.pk

        with self.config_context():
            delete_result = instance.delete()

        self.assertIsNone(instance.pk)
        self.assertIsInstance(delete_result, tuple)
        self.assertGreaterEqual(delete_result[0], 1)
        self.assertFalse(Instances.objects.filter(pk=instance_id).exists())
        for path in owned_files:
            self.assertFalse(path.exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_profile_model_delete_exposes_postcommit_cleanup_pending(self):
        instance = self.make_instance(self.owner, "model-profile-cleanup-pending")
        owner_id = self.owner.pk
        port = instance.port
        public_files = self.owned_file_paths(instance)

        with (
            self.config_context(),
            patch("main.instance_files._unlink_private_file", side_effect=ManagedFileError()),
            self.assertLogs("main.instance_management", level="WARNING") as captured,
        ):
            delete_result = self.owner.delete()

        self.assertIsNone(self.owner.pk)
        self.assertIsInstance(delete_result, tuple)
        self.assertGreaterEqual(delete_result[0], 1)
        self.assertFalse(User.objects.filter(pk=owner_id).exists())
        self.assertFalse(Instances.objects.filter(pk=instance.pk).exists())
        self.assertTrue(self.owner._asm_cleanup_pending["pending"])
        self.assertEqual(self.owner._asm_cleanup_pending["reason"], "private_cleanup_failed")
        self.assertGreater(self.owner._asm_cleanup_pending["pending_count"], 0)
        self.assertNotIn(str(self.root), repr(self.owner._asm_cleanup_pending))
        self.assertNotIn(str(self.root), "\n".join(captured.output))
        self.assertTrue(all(not path.exists() for path in public_files))
        self.assertTrue(list(self.private_root.iterdir()))
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_instance_model_delete_exposes_postcommit_cleanup_pending(self):
        instance = self.make_instance(self.owner, "model-instance-cleanup-pending")
        instance_id = instance.pk
        port = instance.port
        public_files = self.owned_file_paths(instance)

        with (
            self.config_context(),
            patch("main.instance_files._unlink_private_file", side_effect=ManagedFileError()),
            self.assertLogs("main.instance_management", level="WARNING") as captured,
        ):
            delete_result = instance.delete()

        self.assertIsNone(instance.pk)
        self.assertIsInstance(delete_result, tuple)
        self.assertGreaterEqual(delete_result[0], 1)
        self.assertFalse(Instances.objects.filter(pk=instance_id).exists())
        self.assertTrue(instance._asm_cleanup_pending["pending"])
        self.assertEqual(instance._asm_cleanup_pending["reason"], "private_cleanup_failed")
        self.assertGreater(instance._asm_cleanup_pending["pending_count"], 0)
        self.assertNotIn(str(self.root), repr(instance._asm_cleanup_pending))
        self.assertNotIn(str(self.root), "\n".join(captured.output))
        self.assertTrue(all(not path.exists() for path in public_files))
        self.assertTrue(list(self.private_root.iterdir()))
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_user_delete_database_failure_rolls_back_rows_ports_and_file_changes(self):
        instance = self.make_instance(self.owner, "delete-db-failure")
        cfg_path = self.create_server_config(self.owner)
        owned_files = self.owned_file_paths(instance)
        all_files = owned_files + [cfg_path]
        expected_bytes = {path: path.read_bytes() for path in all_files}
        port = instance.port
        self.client.force_authenticate(user=self.staff)
        base_delete = models.Model.delete

        def delete_then_fail(model, *args, **kwargs):
            fail_for_owner = model.pk == self.owner.pk and isinstance(model, User)
            result = base_delete(model, *args, **kwargs)
            if fail_for_owner:
                raise OSError("invented database delete failure")
            return result

        with (
            self.config_context(),
            patch.object(models.Model, "delete", new=delete_then_fail),
        ):
            response = self.delete_profile_request(self.owner)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("invented database delete failure", str(response.data))
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(Instances.objects.filter(pk=instance.pk).exists())
        port.refresh_from_db()
        self.assertFalse(port.is_available)
        for path, content in expected_bytes.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), content)
