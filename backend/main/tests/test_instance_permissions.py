from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from main.instance_tasks import create_instance_task_binding
from main.models import InstanceTask, Instances, Ports
from main.tasks import start_server_task, stop_server_task


User = get_user_model()


def storage_path_override(log_file_name, on_log_file_path):
    original_path = FileSystemStorage.path

    def resolve_path(storage, name):
        if name == log_file_name:
            return on_log_file_path()
        return original_path(storage, name)

    return resolve_path


class InstancePermissionTests(TransactionTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.test_root = Path(self.temp.name)
        self.media_root = self.test_root / "media"
        self.arma_root = self.test_root / "arma3"
        self.private_root = self.test_root / ".asm-private"
        self.static_root = self.test_root / "static"
        for path in (self.media_root, self.arma_root, self.private_root, self.static_root):
            path.mkdir(parents=True)
        self.settings_override = override_settings(
            MEDIA_ROOT=str(self.media_root),
            STATIC_ROOT=str(self.static_root),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.config_snapshot = {
            "paths": {
                "arma3": str(self.arma_root),
                "mods_directory": str(self.arma_root / "mods"),
                "logs_directory": str(self.test_root / "operation-logs"),
                "download_directory": str(self.test_root / "downloads"),
                "steamcmd": "",
            },
            "steam_auth": {"username": "", "password": "", "shared_secret": ""},
        }
        (self.arma_root / "arma3server_x64").write_bytes(b"invented executable placeholder")
        self.snapshot_patcher = patch(
            "main.views.config.snapshot",
            return_value=self.config_snapshot,
        )
        self.snapshot_patcher.start()
        self.addCleanup(self.snapshot_patcher.stop)

        self.owner = User.objects.create_user(username="owner", password="test-password")
        self.other = User.objects.create_user(username="other", password="test-password")
        self.staff = User.objects.create_user(
            username="staff", password="test-password", is_staff=True
        )
        self.quiet_staff = User.objects.create_user(
            username="quiet-staff", password="test-password", is_staff=True
        )
        self.superuser = User.objects.create_superuser(
            username="root", password="test-password"
        )
        self.client = APIClient()
        self.next_port = 23000

    def make_instance(
        self,
        owner=None,
        *,
        name=None,
        is_admin_instance=False,
        is_running=False,
        with_log=False,
    ):
        owner = owner or self.owner
        name = name or f"server-{self.next_port}"
        port_number = self.next_port
        self.next_port += 1
        port = Ports.objects.create(port_number=port_number, is_available=False)
        instance = Instances.objects.create(
            name=name,
            user=owner,
            preset=SimpleUploadedFile(f"{name}.html", b"<html></html>"),
            port=port,
            is_admin_instance=is_admin_instance,
            is_running=is_running,
            lifecycle_state=("running" if is_running else "stopped"),
            run_id=(uuid4() if is_running else None),
            process_created_at=(100.5 if is_running else None),
            process_executable=("/isolated/arma3/arma3server_x64" if is_running else None),
            pid=(12345 if is_running else None),
            start_file_path=str(self.write_start_file(name)),
        )
        if with_log:
            instance.log_file.save(
                f"{name}-log.txt",
                SimpleUploadedFile(f"{name}-log.txt", b"invented log line\n"),
                save=True,
            )
        return instance

    def write_start_file(self, name):
        path = self.arma_root / f"start{name}.sh"
        path.write_text("invented managed start script\n", encoding="utf-8")
        return path

    def parser_context(self, return_value=None):
        stack = ExitStack()
        parser_patches = [
            stack.enter_context(patch("main.views.preset_parser", return_value=return_value))
        ]
        service_module = sys.modules.get("main.instance_management")
        if service_module is not None and hasattr(service_module, "preset_parser"):
            parser_patches.append(
                stack.enter_context(
                    patch.object(service_module, "preset_parser", return_value=return_value)
                )
            )
        stack.parser_patches = parser_patches
        return stack

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    def test_list_sections_are_disjoint_and_regular_users_see_only_owned_instances(self):
        owner_instance = self.make_instance(self.owner, name="owner-normal")
        other_instance = self.make_instance(self.other, name="other-normal")
        owner_admin_instance = self.make_instance(
            self.owner, name="owner-admin", is_admin_instance=True
        )
        other_admin_instance = self.make_instance(
            self.other, name="other-admin", is_admin_instance=True
        )
        staff_instance = self.make_instance(self.staff, name="staff-normal")
        older_owner_task = create_instance_task_binding(owner_instance, "download")
        newer_owner_task = create_instance_task_binding(owner_instance, "download")
        InstanceTask.objects.filter(pk=older_owner_task.task_id).update(
            created_at=newer_owner_task.created_at - timedelta(seconds=1)
        )
        staff_start_task = create_instance_task_binding(staff_instance, "start")
        other_stop_task = create_instance_task_binding(other_instance, "stop")

        self.authenticate_as(self.owner)
        owner_data = self.client.get("/api/instances/").data
        self.assertEqual(
            [item["id"] for item in owner_data["user_instances"]],
            [owner_instance.id],
        )
        self.assertEqual(owner_data["admin_instances"], [])
        self.assertEqual(owner_data["other_instances"], [])
        self.assertEqual(owner_data["user_instances"][0]["user"]["username"], "owner")
        self.assertEqual(owner_data["user_instances"][0]["port"], str(owner_instance.port.port_number))
        self.assertEqual(
            owner_data["user_instances"][0]["active_tasks"],
            {"download": newer_owner_task.task_id},
        )
        self.assertNotEqual(older_owner_task.task_id, newer_owner_task.task_id)

        self.authenticate_as(self.staff)
        staff_data = self.client.get("/api/instances/").data
        sections = {
            key: {item["id"] for item in staff_data[key]}
            for key in ("user_instances", "admin_instances", "other_instances")
        }
        self.assertEqual(sections["user_instances"], {staff_instance.id})
        self.assertEqual(
            sections["admin_instances"],
            {owner_admin_instance.id, other_admin_instance.id},
        )
        self.assertEqual(sections["other_instances"], {owner_instance.id, other_instance.id})
        self.assertEqual(sum(len(items) for items in sections.values()), len(set().union(*sections.values())))
        listed_tasks = {
            item["id"]: item["active_tasks"]
            for section in ("user_instances", "admin_instances", "other_instances")
            for item in staff_data[section]
        }
        self.assertEqual(listed_tasks[staff_instance.id], {"start": staff_start_task.task_id})
        self.assertEqual(listed_tasks[other_instance.id], {"stop": other_stop_task.task_id})

    def test_all_instance_actions_use_owner_or_staff_scope(self):
        action_cases = (
            ("retrieve", "get", "", {}, 200),
            ("delete", "delete", "", {"is_running": False}, 200),
            ("start", "post", "start/", {"is_running": True}, 400),
            ("stop", "post", "stop/", {"is_running": False}, 400),
            ("download", "post", "download_mods/", {}, 202),
            ("logs", "get", "logs/", {"with_log": True}, 200),
            ("download logs", "get", "logs/download/", {"with_log": True}, 200),
            (
                "delete logs",
                "delete",
                "logs/delete/",
                {"with_log": True, "is_running": True},
                400,
            ),
        )

        with (
            patch("main.views.psutil.disk_usage", return_value=MagicMock(free=10**12)),
            patch("main.views.config.get", return_value="/isolated/mods"),
            patch("main.views.dispatch_instance_task", return_value="invented-task-id"),
        ):
            for admin_target in (False, True):
                for label, method, suffix, options, allowed_status in action_cases:
                    for actor in (self.owner, self.other, self.staff):
                        expected_status = (
                            404
                            if actor == self.other or (admin_target and not actor.is_staff)
                            else allowed_status
                        )
                        with self.subTest(
                            admin_target=admin_target,
                            action=label,
                            actor=actor.username,
                        ):
                            instance = self.make_instance(
                                self.owner,
                                name=f"{label.replace(' ', '-')}-{actor.username}-{self.next_port}",
                                is_admin_instance=admin_target,
                                **options,
                            )
                            self.authenticate_as(actor)
                            path = f"/api/instances/{instance.pk}/{suffix}"
                            response = getattr(self.client, method)(path, format="json")
                            self.assertEqual(response.status_code, expected_status)
                            if label == "download" and expected_status == 202:
                                self.assertEqual(response.data, {"task_id": "invented-task-id"})
                                queued_bindings = InstanceTask.objects.filter(
                                    instance=instance,
                                    kind=InstanceTask.Kind.DOWNLOAD,
                                    claimed_at__isnull=True,
                                    finished_at__isnull=True,
                                    superseded_at__isnull=True,
                                    requires_attention=False,
                                )
                                self.assertEqual(queued_bindings.count(), 1)
                                queued_bindings.get().delete()

    def test_start_and_stop_keep_the_202_task_id_contract(self):
        instance = self.make_instance(self.owner, name="queued-instance")
        staff_running_instance = self.make_instance(
            self.staff,
            name="staff-running",
            is_running=True,
        )
        self.authenticate_as(self.staff)

        @contextmanager
        def fake_operation_loggers(*args, **kwargs):
            yield {"start": MagicMock()}

        with (
            patch("main.views.operation_loggers", side_effect=fake_operation_loggers),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.check_sh_file_exists", return_value=True),
            patch("main.views.config.get", return_value=str(self.arma_root)),
            patch("main.views.dispatch_instance_task", return_value="start-task-id") as dispatch,
        ):
            start_response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(start_response.status_code, 202)
        self.assertEqual(start_response.data, {"task_id": "start-task-id"})
        self.assertEqual(dispatch.call_count, 1)
        binding, task, kwargs = dispatch.call_args.args[0], dispatch.call_args.args[1], dispatch.call_args.kwargs["kwargs"]
        self.assertEqual(binding.kind, "start")
        self.assertEqual(task, start_server_task)
        self.assertEqual(
            kwargs,
            {
                "instance_id": instance.pk,
                "run_id": str(Instances.objects.get(pk=instance.pk).run_id),
            },
        )

        instance.is_running = True
        instance.lifecycle_state = "running"
        instance.save(update_fields=["is_running", "lifecycle_state"])
        with patch("main.views.dispatch_stop_reservation", return_value="stop-task-id") as dispatch_stop:
            stop_response = self.client.post(
                f"/api/instances/{instance.pk}/stop/", {}, format="json"
            )

        self.assertEqual(stop_response.status_code, 202)
        self.assertEqual(stop_response.data, {"task_id": "stop-task-id"})
        self.assertEqual(dispatch_stop.call_count, 1)
        self.assertEqual(dispatch_stop.call_args.args[1], stop_server_task)
        self.assertEqual(staff_running_instance.is_running, True)

    def test_start_rejects_missing_saved_script_path_even_when_named_script_exists(self):
        instance = self.make_instance(self.owner, name="incomplete-start-snapshot")
        instance.start_file_path = "  "
        instance.save(update_fields=["start_file_path"])
        self.authenticate_as(self.owner)

        @contextmanager
        def fake_operation_loggers(*args, **kwargs):
            yield {"start": MagicMock()}

        with (
            patch("main.views.operation_loggers", side_effect=fake_operation_loggers),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.check_sh_file_exists", return_value=True) as check_script,
            patch("main.views.config.get", return_value=str(self.arma_root)),
            patch(
                "main.views.dispatch_instance_task",
                return_value="unexpected-task-id",
            ) as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["message"],
            "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem",
        )
        check_script.assert_not_called()
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertFalse(instance.log_file)
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.process_executable)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())

    def test_start_rejects_log_path_that_storage_cannot_resolve(self):
        instance = self.make_instance(
            self.owner,
            name="unavailable-log-path",
            with_log=True,
        )
        self.authenticate_as(self.owner)

        def unavailable_log_path():
            raise NotImplementedError("remote storage has no local path")

        @contextmanager
        def fake_operation_loggers(*args, **kwargs):
            yield {"start": MagicMock()}

        with (
            patch.object(
                FileSystemStorage,
                "path",
                new=storage_path_override(
                    instance.log_file.name,
                    unavailable_log_path,
                ),
            ),
            patch("main.views.operation_loggers", side_effect=fake_operation_loggers),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.check_sh_file_exists", return_value=True),
            patch("main.views.config.get", return_value=str(self.arma_root)),
            patch(
                "main.views.dispatch_instance_task",
                return_value="unexpected-task-id",
            ) as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["message"],
            "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem",
        )
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())

    def test_staff_start_limit_uses_target_owner_and_staff_stop_can_target_other_owner(self):
        owner_running_instance = self.make_instance(
            self.owner,
            name="owner-running",
            is_running=True,
        )
        target_instance = self.make_instance(self.owner, name="owner-target")
        self.authenticate_as(self.quiet_staff)

        @contextmanager
        def fake_operation_loggers(*args, **kwargs):
            yield {"start": MagicMock()}

        with (
            patch("main.views.operation_loggers", side_effect=fake_operation_loggers),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.check_sh_file_exists", return_value=True),
            patch("main.views.config.get", return_value=str(self.arma_root)),
            patch("main.views.dispatch_instance_task", return_value="should-not-queue") as dispatch,
        ):
            rejected = self.client.post(
                f"/api/instances/{target_instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(rejected.status_code, 400)
        dispatch.assert_not_called()

        target_instance.is_running = True
        target_instance.lifecycle_state = "running"
        target_instance.run_id = uuid4()
        target_instance.process_executable = "/isolated/arma3/arma3server_x64"
        target_instance.save(update_fields=["is_running", "lifecycle_state", "run_id", "process_executable"])
        with patch("main.views.dispatch_stop_reservation", return_value="staff-stop-task") as dispatch_stop:
            stopped = self.client.post(
                f"/api/instances/{target_instance.pk}/stop/", {}, format="json"
            )

        self.assertEqual(stopped.status_code, 202)
        self.assertEqual(stopped.data, {"task_id": "staff-stop-task"})
        self.assertEqual(dispatch_stop.call_count, 1)
        self.assertEqual(dispatch_stop.call_args.args[1], stop_server_task)
        self.assertTrue(owner_running_instance.is_running)

    def test_preset_replacement_is_staff_only_and_uses_scoped_instance_lookup(self):
        instance = self.make_instance(
            self.superuser, name="admin-preset", is_admin_instance=True
        )
        path = f"/api/instances/{instance.pk}/admin_instance/change_preset/"

        for actor in (self.owner, self.other):
            with self.subTest(actor=actor.username):
                self.authenticate_as(actor)
                response = self.client.post(
                    path,
                    {"preset": SimpleUploadedFile("replacement.html", b"new preset")},
                    format="multipart",
                )
                self.assertEqual(response.status_code, 403)

        self.authenticate_as(self.staff)
        with (
            self.parser_context(return_value=[]),
            patch("main.views.config.get", return_value=str(self.arma_root / "mods")),
            patch("main.views.operation_loggers") as operation_loggers,
        ):
            operation_loggers.return_value.__enter__.return_value = {
                "change_preset": MagicMock()
            }
            operation_loggers.return_value.__exit__.return_value = False
            response = self.client.post(
                path,
                {"preset": SimpleUploadedFile("replacement.html", b"new preset")},
                format="multipart",
            )
        self.assertEqual(response.status_code, 200)
        instance.refresh_from_db()
        self.assertTrue(instance.preset.name.endswith("replacement.html"))

        self.authenticate_as(self.staff)
        missing_response = self.client.post(
            "/api/instances/999999/admin_instance/change_preset/",
            {"preset": SimpleUploadedFile("missing.html", b"new preset")},
            format="multipart",
        )
        self.assertEqual(missing_response.status_code, 404)

    def test_missing_instance_ids_are_404_for_owner_staff_and_detailed_actions(self):
        action_cases = (
            ("get", "/api/instances/999999/"),
            ("delete", "/api/instances/999999/"),
            ("post", "/api/instances/999999/start/"),
            ("post", "/api/instances/999999/stop/"),
            ("post", "/api/instances/999999/download_mods/"),
            ("get", "/api/instances/999999/logs/"),
            ("get", "/api/instances/999999/logs/download/"),
            ("delete", "/api/instances/999999/logs/delete/"),
        )
        for actor in (self.owner, self.other, self.staff):
            self.authenticate_as(actor)
            for method, path in action_cases:
                with self.subTest(actor=actor.username, path=path):
                    response = getattr(self.client, method)(path, format="json")
                    self.assertEqual(response.status_code, 404)

    def test_generic_put_and_patch_are_disabled(self):
        instance = self.make_instance(self.owner, name="immutable-instance")
        self.authenticate_as(self.owner)
        path = f"/api/instances/{instance.pk}/"

        put_response = self.client.put(path, {"name": "changed-name"}, format="json")
        patch_response = self.client.patch(path, {"name": "changed-name"}, format="json")

        self.assertEqual(put_response.status_code, 405)
        self.assertEqual(patch_response.status_code, 405)
        instance.refresh_from_db()
        self.assertEqual(instance.name, "immutable-instance")

    def test_create_ignores_server_controlled_field_injection(self):
        available_port = Ports.objects.create(port_number=self.next_port, is_available=True)
        self.next_port += 1
        self.authenticate_as(self.owner)

        @contextmanager
        def fake_operation_loggers(*args, **kwargs):
            yield {"create": MagicMock()}

        payload = {
            "id": 88001,
            "name": "safe-created",
            "preset": SimpleUploadedFile("safe-created.html", b"<html></html>"),
            "user": self.other.pk,
            "port": "9999",
            "pid": 123456,
            "is_ready": True,
            "is_running": True,
            "is_admin_instance": True,
            "lifecycle_state": "running",
            "shutdown_at": "2020-01-01T00:00:00Z",
            "start_file_path": "/attacker/start.sh",
            "log_file": "/attacker/server.log",
            "active_tasks": "attacker-task-id",
            "created_at": "2020-01-01T00:00:00Z",
        }

        with (
            self.parser_context(return_value=[]),
            patch("main.views.config.get", return_value=str(self.arma_root)),
            patch("main.views.operation_loggers", side_effect=fake_operation_loggers),
        ):
            response = self.client.post("/api/instances/", payload, format="multipart")

        self.assertEqual(response.status_code, 200, response.data)
        instance = Instances.objects.get(name="safe-created")
        self.assertEqual(instance.user, self.owner)
        self.assertEqual(instance.port, available_port)
        self.assertIsNone(instance.pid)
        self.assertFalse(instance.is_ready)
        self.assertFalse(instance.is_running)
        self.assertFalse(instance.is_admin_instance)
        self.assertEqual(instance.lifecycle_state, "stopped")
        self.assertIsNone(instance.shutdown_at)
        self.assertFalse(instance.log_file)
        self.assertEqual(
            instance.start_file_path,
            str(self.arma_root / "startsafe-created.sh"),
        )
        self.assertEqual(response.data["result"]["user"]["username"], "owner")
        self.assertEqual(response.data["result"]["port"], str(available_port.port_number))

    def test_incremental_log_returns_only_new_bytes(self):
        instance = self.make_instance(with_log=True)
        self.client.force_authenticate(self.owner)
        url = f"/api/instances/{instance.pk}/logs/incremental/"
        initial = self.client.get(url, {"tail": 100})
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.data["content"], "invented log line\n")
        with open(instance.log_file.path, "ab") as log:
            log.write(b"new line\n")
        updated = self.client.get(url, {"cursor": initial.data["cursor"]})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["content"], "new line\n")

    def test_incremental_log_resets_after_truncation(self):
        instance = self.make_instance(with_log=True)
        self.client.force_authenticate(self.owner)
        url = f"/api/instances/{instance.pk}/logs/incremental/"
        initial = self.client.get(url)
        with open(instance.log_file.path, "wb") as log:
            log.write(b"new\n")
        updated = self.client.get(url, {"cursor": initial.data["cursor"]})
        self.assertTrue(updated.data["reset"])
        self.assertEqual(updated.data["content"], "new\n")

    def test_incremental_log_waits_for_complete_utf8_character(self):
        instance = self.make_instance(with_log=True)
        self.client.force_authenticate(self.owner)
        url = f"/api/instances/{instance.pk}/logs/incremental/"
        initial = self.client.get(url)
        with open(instance.log_file.path, "ab") as log:
            log.write(b"\xc5")
        partial = self.client.get(url, {"cursor": initial.data["cursor"]})
        self.assertEqual(partial.data["content"], "")
        with open(instance.log_file.path, "ab") as log:
            log.write(b"\x82")
        complete = self.client.get(url, {"cursor": partial.data["cursor"]})
        self.assertEqual(complete.data["content"], "ł")
