from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.test import APIClient

from main.models import InstanceTask, Instances, Missions, Ports
from main.serializers import InstanceSerializer
from main.utils.operation_logging import operation_loggers as real_operation_loggers


User = get_user_model()


class PartialUpload(SimpleUploadedFile):
    def chunks(self, chunk_size=None):
        yield b"<html>partial upload"
        raise OSError("invented partial upload failure")


class PartialFirstLogContentFile(ContentFile):
    def chunks(self, chunk_size=None):
        yield b"invented partial first-start log"
        raise OSError("invented partial first-start log failure")


class RuntimeErrorFirstLogContentFile(ContentFile):
    def chunks(self, chunk_size=None):
        yield b"invented partial runtime-error first-start log"
        raise RuntimeError("invented first-start writer runtime error")


class InstanceFileApiTests(TransactionTestCase):
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

        self.owner = User.objects.create_user(username="file-owner", password="test-password")
        self.staff = User.objects.create_user(
            username="file-staff", password="test-password", is_staff=True
        )
        self.superuser = User.objects.create_superuser(
            username="file-root", password="test-password"
        )
        self.client = APIClient()
        self.next_port = 25100
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

    def config_context(self, snapshot=None):
        snapshot = snapshot or self.snapshot

        def get_value(key, default=None):
            value = snapshot
            try:
                for component in key.split("."):
                    value = value[component]
                return value
            except (KeyError, TypeError):
                return default

        stack = ExitStack()
        stack.enter_context(patch("main.views.config.snapshot", return_value=snapshot))
        stack.enter_context(patch("main.views.config.get", side_effect=get_value))
        return stack

    def filesystem_state(self, root):
        paths = tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*")))
        contents = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        return paths, contents

    @contextmanager
    def fake_operation_loggers(self, *args, **kwargs):
        yield {
            "create": MagicMock(),
            "start": MagicMock(),
            "change_preset": MagicMock(),
        }

    def parser_context(self, *, return_value=None, side_effect=None):
        patch_kwargs = (
            {"side_effect": side_effect}
            if side_effect is not None
            else {"return_value": return_value}
        )
        stack = ExitStack()
        parser_mocks = [
            stack.enter_context(patch("main.views.preset_parser", **patch_kwargs))
        ]
        service_module = sys.modules.get("main.instance_management")
        if service_module is not None and hasattr(service_module, "preset_parser"):
            parser_mocks.append(
                stack.enter_context(
                    patch.object(service_module, "preset_parser", **patch_kwargs)
                )
            )
        stack.parser_mocks = parser_mocks
        return stack

    def add_port(self, *, available=False):
        port = Ports.objects.create(port_number=self.next_port, is_available=available)
        self.next_port += 1
        return port

    def write_start_file(self, name, content=b"invented start script\n"):
        path = self.arma_root / f"start{name}.sh"
        path.write_bytes(content)
        return path

    def make_instance(self, name, *, owner=None, admin=False, with_log=False):
        owner = owner or self.owner
        port = self.add_port()
        instance = Instances.objects.create(
            name=name,
            user=owner,
            preset=SimpleUploadedFile(f"{name}.html", b"<html></html>"),
            port=port,
            is_admin_instance=admin,
            is_ready=True,
            start_file_path=str(self.write_start_file(name)),
        )
        if with_log:
            instance.log_file.save(
                f"{name}-log.txt",
                SimpleUploadedFile(f"{name}-log.txt", b"invented log line\n"),
                save=True,
            )
        return instance

    def attach_log_from_storage(self, instance, storage, filename="external-log.txt"):
        log_field = Instances._meta.get_field("log_file")
        with patch.object(log_field, "storage", storage):
            instance.log_file.storage = storage
            instance.log_file.save(
                filename,
                SimpleUploadedFile(filename, b"invented actual log bytes\n"),
                save=True,
            )
            relative_name = instance.log_file.name
        actual_path = Path(storage.path(relative_name))
        sentinel_path = self.media_root / relative_name
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.write_bytes(b"invented media-root sentinel bytes\n")
        return relative_name, actual_path, sentinel_path

    def post_create(self, name, upload=None):
        payload = {"name": name}
        if upload is not None:
            payload["preset"] = upload
        return self.client.post("/api/instances/", payload, format="multipart")

    def test_create_rejects_missing_preset_without_reserving_a_port(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with self.config_context():
            response = self.post_create("missing-preset")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Instances.objects.filter(name="missing-preset").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_create_parser_failure_leaves_no_row_port_or_uploaded_preset(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        def fail_parser(path, **kwargs):
            self.assertEqual(Path(path).suffix, ".html")
            self.assertIn(b"invented preset", Path(path).read_bytes())
            raise ValueError("invented parser failure")

        with (
            self.config_context(),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(side_effect=fail_parser),
        ):
            response = self.post_create(
                "parser-failure",
                SimpleUploadedFile("parser-failure.html", b"invented preset"),
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("invented parser failure", str(response.data))
        self.assertFalse(Instances.objects.filter(name="parser-failure").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])

    def test_partial_upload_failure_leaves_no_public_partial_file_or_reserved_port(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)
        original_is_valid = InstanceSerializer.is_valid

        def inject_server_side_upload(serializer, *args, **kwargs):
            is_valid = original_is_valid(serializer, *args, **kwargs)
            if is_valid:
                serializer._validated_data["preset"] = PartialUpload(
                    "partial-upload.html",
                    b"unused initial bytes",
                )
            return is_valid

        with (
            self.config_context(),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch.object(InstanceSerializer, "is_valid", new=inject_server_side_upload),
        ):
            response = self.post_create(
                "partial-upload",
                PartialUpload("partial-upload.html", b"unused initial bytes"),
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("invented partial upload failure", str(response.data))
        self.assertFalse(Instances.objects.filter(name="partial-upload").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])

    def test_create_uses_one_snapshot_and_keeps_success_response_shape(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)
        with (
            patch("main.views.config.snapshot", return_value=self.snapshot) as snapshot,
            patch("main.views.config.get", side_effect=lambda key, default=None: default),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
        ):
            response = self.post_create(
                "created-instance",
                SimpleUploadedFile("created-instance.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["message"], "Instancja została utworzona")
        self.assertEqual(response.data["result"]["name"], "created-instance")
        self.assertEqual(response.data["result"]["port"], str(port.port_number))
        snapshot.assert_called_once_with()
        instance = Instances.objects.get(name="created-instance")
        self.owner.refresh_from_db()
        self.assertEqual(instance.start_file_path, str(self.arma_root / "startcreated-instance.sh"))
        self.assertTrue(Path(instance.start_file_path).is_file())
        self.assertTrue(Path(self.owner.server_config).is_file())
        port.refresh_from_db()
        self.assertFalse(port.is_available)

    def test_same_client_filename_gets_a_unique_owned_preset_leaf(self):
        first_port = self.add_port(available=True)
        second_port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
        ):
            first = self.post_create(
                "duplicate-name-first",
                SimpleUploadedFile("shared-name.html", b"<html>first</html>"),
            )
            self.owner.refresh_from_db()
            config_path = Path(self.owner.server_config) if self.owner.server_config else None
            config_bytes = config_path.read_bytes() if config_path else None
            second = self.post_create(
                "duplicate-name-second",
                SimpleUploadedFile("shared-name.html", b"<html>second</html>"),
            )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        first_instance = Instances.objects.get(name="duplicate-name-first")
        second_instance = Instances.objects.get(name="duplicate-name-second")
        self.assertNotEqual(first_instance.preset.name, second_instance.preset.name)
        self.assertTrue(first_instance.preset.name.endswith(".html"))
        self.assertTrue(second_instance.preset.name.endswith(".html"))
        self.assertEqual(Path(first_instance.preset.path).read_bytes(), b"<html>first</html>")
        self.assertEqual(Path(second_instance.preset.path).read_bytes(), b"<html>second</html>")
        first_port.refresh_from_db()
        second_port.refresh_from_db()
        self.assertFalse(first_port.is_available)
        self.assertFalse(second_port.is_available)
        self.owner.refresh_from_db()
        self.assertEqual(Path(self.owner.server_config).read_bytes(), config_bytes)

    def test_create_refuses_preexisting_unowned_start_target_without_clobbering(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)
        target = self.write_start_file("preexisting-target", b"keep these bytes\n")

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
        ):
            response = self.post_create(
                "preexisting-target",
                SimpleUploadedFile("preexisting-target.html", b"<html></html>"),
            )

        self.assertIn(response.status_code, (400, 409))
        self.assertEqual(target.read_bytes(), b"keep these bytes\n")
        self.assertFalse(Instances.objects.filter(name="preexisting-target").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_create_checks_missing_start_target_references_before_publication(self):
        target_name = "startreferenced-target.sh"
        target_path = self.arma_root / target_name
        legacy = self.make_instance("legacy-start-reference")
        legacy.start_file_path = str(target_path)
        legacy.save(update_fields=["start_file_path"])
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
        ):
            response = self.post_create(
                "referenced-target",
                SimpleUploadedFile("referenced-target.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Instances.objects.filter(pk=legacy.pk).exists())
        self.assertEqual(Instances.objects.get(pk=legacy.pk).start_file_path, str(target_path))
        self.assertFalse(target_path.exists())
        self.assertFalse(Instances.objects.filter(name="referenced-target").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.private_root.iterdir()], [])

    def test_create_checks_missing_profile_config_references_before_publication(self):
        other = User.objects.create_user(username="legacy-config-ref", password="test-password")
        target_path = self.arma_root / "startprofile-reference.sh"
        other.server_config = str(target_path)
        other.save(update_fields=["server_config"])
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
        ):
            response = self.post_create(
                "profile-reference",
                SimpleUploadedFile("profile-reference.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 400)
        other.refresh_from_db()
        self.assertEqual(other.server_config, str(target_path))
        self.assertFalse(target_path.exists())
        self.assertFalse(Instances.objects.filter(name="profile-reference").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.private_root.iterdir()], [])

    def test_create_rejects_configured_roots_that_contain_private_staging(self):
        sentinel = self.private_root / "invented-private-sentinel"
        sentinel.write_bytes(b"preserve private sentinel\n")
        self.client.force_authenticate(user=self.owner)

        for key in ("mods_directory", "download_directory", "logs_directory", "steamcmd"):
            with self.subTest(key=key):
                port = self.add_port(available=True)
                paths = dict(self.snapshot["paths"])
                paths[key] = str(self.root)
                snapshot = {**self.snapshot, "paths": paths}
                with (
                    self.config_context(snapshot),
                    patch("main.views.preset_parser", return_value=[]),
                    patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
                ):
                    response = self.post_create(
                        f"root-overlap-{key.replace('_', '-')}",
                        SimpleUploadedFile(f"root-overlap-{key}.html", b"<html></html>"),
                    )

                self.assertEqual(response.status_code, 400)
                self.assertFalse(
                    Instances.objects.filter(name=f"root-overlap-{key.replace('_', '-')}").exists()
                )
                self.assertEqual(sentinel.read_bytes(), b"preserve private sentinel\n")
                self.assertEqual(list(self.private_root.iterdir()), [sentinel])
                self.assertEqual(
                    [path for path in self.media_root.rglob("*") if path.is_file()],
                    [],
                )
                port.refresh_from_db()
                self.assertTrue(port.is_available)

    def test_create_preflights_private_log_root_before_opening_real_operation_loggers(self):
        sentinel = self.private_root / "invented-private-sentinel"
        sentinel.write_bytes(b"preserve private sentinel\n")
        paths = dict(self.snapshot["paths"])
        paths["logs_directory"] = str(self.private_root)
        snapshot = {**self.snapshot, "paths": paths}
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)
        before = self.filesystem_state(self.root)

        with (
            self.config_context(snapshot),
            patch("main.views.operation_loggers", wraps=real_operation_loggers) as logger_spy,
            self.parser_context(return_value=[]),
        ):
            response = self.post_create(
                "private-log-root-create",
                SimpleUploadedFile("private-log-root-create.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 400)
        logger_spy.assert_not_called()
        self.assertEqual(self.filesystem_state(self.root), before)
        self.assertEqual(sentinel.read_bytes(), b"preserve private sentinel\n")
        self.assertFalse(Instances.objects.filter(name="private-log-root-create").exists())
        self.assertEqual(list(InstanceTask.objects.all()), [])
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_create_preflights_mission_storage_root_before_logging_or_mutation(self):
        sentinel = self.private_root / "invented-mission-root-sentinel"
        sentinel.write_bytes(b"preserve mission storage sentinel\n")
        mission_field = Missions._meta.get_field("mission_file")
        mission_storage = FileSystemStorage(location=str(self.root))
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)
        before = self.filesystem_state(self.root)

        with (
            patch.object(mission_field, "storage", mission_storage),
            self.config_context(),
            patch("main.views.operation_loggers", wraps=real_operation_loggers) as logger_spy,
            self.parser_context(return_value=[]),
        ):
            response = self.post_create(
                "mission-root-overlap",
                SimpleUploadedFile("mission-root-overlap.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 400)
        logger_spy.assert_not_called()
        self.assertEqual(self.filesystem_state(self.root), before)
        self.assertEqual(sentinel.read_bytes(), b"preserve mission storage sentinel\n")
        self.assertFalse(Instances.objects.filter(name="mission-root-overlap").exists())
        self.assertFalse(Missions.objects.exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_layout_preflight_does_not_require_private_root_to_exist(self):
        self.private_root.rmdir()
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.operation_loggers", wraps=real_operation_loggers) as logger_spy,
            self.parser_context(return_value=[]),
        ):
            response = self.post_create(
                "missing-private-root",
                SimpleUploadedFile("missing-private-root.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 400)
        logger_spy.assert_called_once()
        self.assertFalse(self.private_root.exists())
        self.assertFalse(Instances.objects.filter(name="missing-private-root").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_admin_replacement_preflights_containing_log_root_before_real_logging(self):
        instance = self.make_instance("private-log-root-replacement", owner=self.superuser, admin=True)
        original_preset = Path(instance.preset.path).read_bytes()
        original_script = Path(instance.start_file_path).read_bytes()
        sentinel = self.private_root / "invented-replacement-sentinel"
        sentinel.write_bytes(b"preserve replacement sentinel\n")
        paths = dict(self.snapshot["paths"])
        paths["logs_directory"] = str(self.root)
        snapshot = {**self.snapshot, "paths": paths}
        self.client.force_authenticate(user=self.staff)
        before = self.filesystem_state(self.root)

        with (
            self.config_context(snapshot),
            patch("main.views.operation_loggers", wraps=real_operation_loggers) as logger_spy,
            self.parser_context(return_value=[]),
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"preset": SimpleUploadedFile("replacement.html", b"new invented preset")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        logger_spy.assert_not_called()
        self.assertEqual(self.filesystem_state(self.root), before)
        self.assertEqual(sentinel.read_bytes(), b"preserve replacement sentinel\n")
        instance.refresh_from_db()
        self.assertTrue(instance.is_ready)
        self.assertEqual(Path(instance.preset.path).read_bytes(), original_preset)
        self.assertEqual(Path(instance.start_file_path).read_bytes(), original_script)
        instance.port.refresh_from_db()
        self.assertFalse(instance.port.is_available)

    def test_create_rejects_other_filefield_storage_containing_private_staging(self):
        sentinel = self.private_root / "invented-storage-root-sentinel"
        sentinel.write_bytes(b"preserve storage sentinel\n")
        alternate_storage = FileSystemStorage(location=str(self.root))
        log_field = Instances._meta.get_field("log_file")
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            patch.object(log_field, "storage", alternate_storage),
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
        ):
            response = self.post_create(
                "storage-root-overlap",
                SimpleUploadedFile("storage-root-overlap.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Instances.objects.filter(name="storage-root-overlap").exists())
        self.assertEqual(sentinel.read_bytes(), b"preserve storage sentinel\n")
        self.assertEqual(list(self.private_root.iterdir()), [sentinel])
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        port.refresh_from_db()
        self.assertTrue(port.is_available)

    def test_create_rejects_unsupported_log_field_storage_before_mutation(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)
        log_field = Instances._meta.get_field("log_file")

        with (
            patch.object(log_field, "storage", object()),
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
        ):
            response = self.post_create(
                "unsupported-field-storage",
                SimpleUploadedFile("unsupported-field-storage.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Instances.objects.filter(name="unsupported-field-storage").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_create_partial_config_writer_failure_leaves_no_public_files_or_reservation(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        def fail_config_writer(*args, output_path=None, **kwargs):
            Path(output_path).write_bytes(b"invented partial cfg bytes")
            raise OSError("invented config writer failure")

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch("main.instance_management.generate_server_config", side_effect=fail_config_writer),
        ):
            response = self.post_create(
                "partial-config-writer",
                SimpleUploadedFile("partial-config-writer.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Instances.objects.filter(name="partial-config-writer").exists())
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.server_config)
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        self.assertEqual(
            [path.name for path in self.arma_root.iterdir()],
            ["arma3server_x64"],
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_create_partial_script_writer_failure_leaves_no_public_files_or_reservation(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        def fail_script_writer(*args, output_path=None, **kwargs):
            Path(output_path).write_bytes(b"invented partial script bytes")
            raise OSError("invented script writer failure")

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch("main.instance_management.generate_sh_file", side_effect=fail_script_writer),
        ):
            response = self.post_create(
                "partial-script-writer",
                SimpleUploadedFile("partial-script-writer.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Instances.objects.filter(name="partial-script-writer").exists())
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.server_config)
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        self.assertEqual(
            [path.name for path in self.arma_root.iterdir()],
            ["arma3server_x64"],
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_create_script_chmod_failure_leaves_no_public_files_or_reservation(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch("main.instance_management.os.chmod", side_effect=OSError("invented chmod failure")),
        ):
            response = self.post_create(
                "chmod-failure",
                SimpleUploadedFile("chmod-failure.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Instances.objects.filter(name="chmod-failure").exists())
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.server_config)
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        self.assertEqual(
            [path.name for path in self.arma_root.iterdir()],
            ["arma3server_x64"],
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_create_second_publication_failure_compensates_first_publication(self):
        original_link = os.link
        calls = 0

        def fail_second_link(source, destination, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("invented second publication failure")
            return original_link(source, destination, *args, **kwargs)

        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)
        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch("main.instance_files.os.link", side_effect=fail_second_link),
        ):
            response = self.post_create(
                "second-publication-failure",
                SimpleUploadedFile("second-publication-failure.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Instances.objects.filter(name="second-publication-failure").exists())
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.server_config)
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        self.assertEqual(
            [path.name for path in self.arma_root.iterdir()],
            ["arma3server_x64"],
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_create_known_precommit_failure_compensates_files_rows_and_port(self):
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch.object(
                connection,
                "commit",
                side_effect=DatabaseError("invented precommit failure"),
            ),
        ):
            response = self.post_create(
                "precommit-failure",
                SimpleUploadedFile("precommit-failure.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Instances.objects.filter(name="precommit-failure").exists())
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.server_config)
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        self.assertEqual(
            [path.name for path in self.arma_root.iterdir()],
            ["arma3server_x64"],
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_create_failure_reusing_shared_config_preserves_config_bytes_and_reference(self):
        config_path = self.arma_root / f"server_{self.owner.username}.cfg"
        config_bytes = b"invented shared config bytes\n"
        config_path.write_bytes(config_bytes)
        self.owner.server_config = str(config_path)
        self.owner.save(update_fields=["server_config"])
        port = self.add_port(available=True)
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.preset_parser", return_value=[]),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            patch.object(Instances, "save", autospec=True, side_effect=OSError("invented row save failure")),
        ):
            response = self.post_create(
                "shared-config-failure",
                SimpleUploadedFile("shared-config-failure.html", b"<html></html>"),
            )

        self.assertEqual(response.status_code, 500)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.server_config, str(config_path))
        self.assertEqual(config_path.read_bytes(), config_bytes)
        self.assertFalse(Instances.objects.filter(name="shared-config-failure").exists())
        port.refresh_from_db()
        self.assertTrue(port.is_available)
        self.assertEqual([path for path in self.media_root.rglob("*") if path.is_file()], [])
        self.assertEqual(
            [path.name for path in self.arma_root.iterdir()],
            ["arma3server_x64", config_path.name],
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_admin_replacement_requires_a_preset_before_any_change(self):
        instance = self.make_instance("admin-required-preset", owner=self.superuser, admin=True)
        old_preset_name = instance.preset.name
        old_preset_bytes = Path(instance.preset.path).read_bytes()
        old_script_bytes = Path(instance.start_file_path).read_bytes()
        self.client.force_authenticate(user=self.staff)

        with self.config_context():
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"name": "ignored-name"},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        instance.refresh_from_db()
        self.assertEqual(instance.preset.name, old_preset_name)
        self.assertEqual(Path(instance.preset.path).read_bytes(), old_preset_bytes)
        self.assertEqual(Path(instance.start_file_path).read_bytes(), old_script_bytes)
        self.assertTrue(instance.is_ready)

    def test_admin_replacement_parser_failure_preserves_old_preset_script_and_readiness(self):
        instance = self.make_instance("admin-parse-failure", owner=self.superuser, admin=True)
        old_preset_name = instance.preset.name
        old_preset_path = Path(instance.preset.path)
        old_preset_bytes = old_preset_path.read_bytes()
        old_script_path = Path(instance.start_file_path)
        old_script_bytes = old_script_path.read_bytes()
        self.client.force_authenticate(user=self.staff)

        def fail_parser(path, **kwargs):
            self.assertEqual(Path(path).suffix, ".html")
            raise ValueError("invented replacement parser failure")

        with (
            self.config_context(),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(side_effect=fail_parser),
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"preset": SimpleUploadedFile("replacement.html", b"new invented preset")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("invented replacement parser failure", str(response.data))
        instance.refresh_from_db()
        self.assertEqual(instance.preset.name, old_preset_name)
        self.assertTrue(old_preset_path.is_file())
        self.assertEqual(old_preset_path.read_bytes(), old_preset_bytes)
        self.assertEqual(instance.start_file_path, str(old_script_path))
        self.assertTrue(old_script_path.is_file())
        self.assertEqual(old_script_path.read_bytes(), old_script_bytes)
        self.assertTrue(instance.is_ready)

    def test_admin_replacement_does_not_overwrite_unrecorded_existing_script(self):
        instance = self.make_instance("admin-unowned-script", owner=self.superuser, admin=True)
        script_path = Path(instance.start_file_path)
        script_bytes = b"invented unowned script bytes\n"
        script_path.write_bytes(script_bytes)
        old_preset_name = instance.preset.name
        old_preset_path = Path(instance.preset.path)
        old_preset_bytes = old_preset_path.read_bytes()
        instance.start_file_path = None
        instance.save(update_fields=["start_file_path"])
        self.client.force_authenticate(user=self.staff)

        with (
            self.config_context(),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"preset": SimpleUploadedFile("unowned-replacement.html", b"replacement")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        instance.refresh_from_db()
        self.assertEqual(instance.preset.name, old_preset_name)
        self.assertEqual(old_preset_path.read_bytes(), old_preset_bytes)
        self.assertIsNone(instance.start_file_path)
        self.assertTrue(instance.is_ready)
        self.assertEqual(script_path.read_bytes(), script_bytes)

    def test_admin_replacement_metadata_save_failure_restores_old_files_and_row(self):
        instance = self.make_instance("admin-metadata-save-failure", owner=self.superuser, admin=True)
        old_preset_name = instance.preset.name
        old_preset_path = Path(instance.preset.path)
        old_preset_bytes = old_preset_path.read_bytes()
        old_script_path = Path(instance.start_file_path)
        old_script_bytes = old_script_path.read_bytes()
        original_save = Instances.save
        failure_injected = False

        def save_then_fail_after_write(model, *args, **kwargs):
            nonlocal failure_injected
            result = original_save(model, *args, **kwargs)
            if not failure_injected and "preset" in kwargs.get("update_fields", ()):
                failure_injected = True
                raise OSError("invented replacement metadata save failure")
            return result

        self.client.force_authenticate(user=self.staff)
        with (
            self.config_context(),
            patch.object(Instances, "save", new=save_then_fail_after_write),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"preset": SimpleUploadedFile("metadata-failure.html", b"new replacement bytes")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 500)
        self.assertTrue(failure_injected)
        instance.refresh_from_db()
        self.assertEqual(instance.preset.name, old_preset_name)
        self.assertEqual(instance.start_file_path, str(old_script_path))
        self.assertTrue(instance.is_ready)
        self.assertEqual(old_preset_path.read_bytes(), old_preset_bytes)
        self.assertEqual(old_script_path.read_bytes(), old_script_bytes)
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_admin_replacement_known_precommit_failure_restores_old_files_and_row(self):
        instance = self.make_instance("admin-precommit-failure", owner=self.superuser, admin=True)
        old_preset_name = instance.preset.name
        old_preset_path = Path(instance.preset.path)
        old_preset_bytes = old_preset_path.read_bytes()
        old_script_path = Path(instance.start_file_path)
        old_script_bytes = old_script_path.read_bytes()
        self.client.force_authenticate(user=self.staff)

        with (
            self.config_context(),
            patch.object(
                connection,
                "commit",
                side_effect=DatabaseError("invented replacement precommit failure"),
            ),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"preset": SimpleUploadedFile("precommit-failure.html", b"new replacement bytes")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 500)
        instance.refresh_from_db()
        self.assertEqual(instance.preset.name, old_preset_name)
        self.assertEqual(instance.start_file_path, str(old_script_path))
        self.assertTrue(instance.is_ready)
        self.assertEqual(old_preset_path.read_bytes(), old_preset_bytes)
        self.assertEqual(old_script_path.read_bytes(), old_script_bytes)
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_admin_replacement_postcommit_cleanup_failure_keeps_new_files_and_reports_pending(self):
        from main.instance_files import ManagedFileError

        instance = self.make_instance("admin-cleanup-pending", owner=self.superuser, admin=True)
        old_preset_path = Path(instance.preset.path)
        old_script_path = Path(instance.start_file_path)
        old_script_bytes = old_script_path.read_bytes()
        self.client.force_authenticate(user=self.staff)

        with (
            self.config_context(),
            patch("main.instance_files._unlink_private_file", side_effect=ManagedFileError()),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {"preset": SimpleUploadedFile("cleanup-pending.html", b"new committed preset bytes")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["cleanup_pending"]["pending"])
        self.assertEqual(response.data["cleanup_pending"]["reason"], "private_cleanup_failed")
        self.assertNotIn(str(self.private_root), str(response.data))
        instance.refresh_from_db()
        self.assertNotEqual(instance.preset.path, str(old_preset_path))
        self.assertEqual(Path(instance.preset.path).read_bytes(), b"new committed preset bytes")
        self.assertEqual(instance.start_file_path, str(old_script_path))
        self.assertNotEqual(old_script_path.read_bytes(), old_script_bytes)
        self.assertFalse(instance.is_ready)
        self.assertFalse(old_preset_path.exists())
        self.assertTrue(list(self.private_root.iterdir()))

    def test_start_refuses_saved_script_outside_configured_root_before_parsing_or_dispatch(self):
        instance = self.make_instance("outside-start-path")
        outside = self.root / "outside-start.sh"
        outside.write_bytes(b"invented outside script\n")
        instance.start_file_path = str(outside)
        instance.save(update_fields=["start_file_path"])
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]) as parser,
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.check_sh_file_exists", return_value=True),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("invented outside script", str(response.data))
        self.assertFalse(any(parser_mock.called for parser_mock in parser.parser_mocks))
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertFalse(instance.log_file)

    def test_start_refuses_symlink_alias_to_expected_script(self):
        instance = self.make_instance("symlink-start-alias")
        trusted_script = Path(instance.start_file_path)
        alias_script = self.root / "outside-start-alias.sh"
        try:
            alias_script.symlink_to(trusted_script)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlink creation is unavailable: {error}")
        instance.start_file_path = str(alias_script)
        instance.save(update_fields=["start_file_path"])
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]) as parser,
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(any(parser_mock.called for parser_mock in parser.parser_mocks))
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())

    def test_first_start_rejects_divergent_log_storage_before_calling_save(self):
        instance = self.make_instance("first-start-divergent-storage")
        external_root = self.root / "first-start-external-logs"
        external_root.mkdir()
        external_storage = FileSystemStorage(location=str(external_root))
        log_field = Instances._meta.get_field("log_file")
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch.object(log_field, "storage", external_storage),
            patch.object(external_storage, "save", wraps=external_storage.save) as storage_save,
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertIn(response.status_code, (400, 500))
        storage_save.assert_not_called()
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertFalse(instance.log_file)
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
        self.assertEqual([path for path in external_root.rglob("*") if path.is_file()], [])
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_first_start_partial_log_writer_failure_leaves_no_public_or_private_orphan(self):
        instance = self.make_instance("first-start-partial-log")
        preset_path = Path(instance.preset.path)
        existing_media_files = {path for path in self.media_root.rglob("*") if path.is_file()}
        self.assertEqual(existing_media_files, {preset_path})
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.ContentFile", new=PartialFirstLogContentFile),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 500)
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertFalse(instance.log_file)
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
        self.assertEqual(
            {path for path in self.media_root.rglob("*") if path.is_file()},
            existing_media_files,
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_first_start_log_metadata_save_failure_rolls_back_file_and_start_state(self):
        instance = self.make_instance("first-start-log-save-failure")
        existing_media_files = {path for path in self.media_root.rglob("*") if path.is_file()}
        original_save = Instances.save
        failure_injected = False

        def save_then_fail_after_write(model, *args, **kwargs):
            nonlocal failure_injected
            result = original_save(model, *args, **kwargs)
            if not failure_injected:
                failure_injected = True
                raise OSError("invented first-start metadata save failure")
            return result

        self.client.force_authenticate(user=self.owner)
        with (
            self.config_context(),
            patch.object(Instances, "save", new=save_then_fail_after_write),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 500)
        self.assertTrue(failure_injected)
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertFalse(instance.log_file)
        self.assertTrue(instance.is_ready)
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
        self.assertEqual(
            {path for path in self.media_root.rglob("*") if path.is_file()},
            existing_media_files,
        )
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_first_start_runtime_writer_failure_returns_static_error_after_compensation(self):
        instance = self.make_instance("first-start-runtime-writer-failure")
        existing_state = self.filesystem_state(self.root)
        self.client.force_authenticate(user=self.owner)
        self.client.raise_request_exception = False

        with (
            self.config_context(),
            patch("main.views.ContentFile", new=RuntimeErrorFirstLogContentFile),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 500)
        body = response.content.decode("utf-8", errors="replace")
        self.assertIn("Nie udało się przygotować pliku logu.", body)
        self.assertNotIn(str(self.root), body)
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertFalse(instance.log_file)
        self.assertTrue(instance.is_ready)
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
        before_paths, before_files = existing_state
        after_paths, after_files = self.filesystem_state(self.root)
        self.assertEqual(after_files, before_files)
        expected_log_directory = Path("media") / f"user_{instance.user_id}" / "logs"
        self.assertEqual(
            set(after_paths) - set(before_paths),
            {expected_log_directory.as_posix()},
        )
        self.assertTrue((self.root / expected_log_directory).is_dir())
        self.assertEqual(list((self.root / expected_log_directory).iterdir()), [])
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_first_start_path_bearing_api_exception_after_publication_is_sanitized(self):
        instance = self.make_instance("first-start-path-api-exception")
        before_paths, before_files = self.filesystem_state(self.root)
        secret_path = str(self.private_root / "invented-path-bearing-error.txt")
        original_save = Instances.save
        failure_injected = False
        self.client.force_authenticate(user=self.owner)

        def save_then_raise_path_api_exception(model, *args, **kwargs):
            nonlocal failure_injected
            result = original_save(model, *args, **kwargs)
            if not failure_injected and "log_file" in kwargs.get("update_fields", ()):
                failure_injected = True
                raise APIException(f"invented writer failure at {secret_path}")
            return result

        with (
            self.config_context(),
            patch.object(Instances, "save", new=save_then_raise_path_api_exception),
            patch("main.views.operation_loggers", side_effect=self.fake_operation_loggers),
            self.parser_context(return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertTrue(failure_injected)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(secret_path, str(response.data))
        self.assertEqual(
            response.data,
            {"message": "Nie udało się przygotować pliku logu."},
        )
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertFalse(instance.log_file)
        self.assertTrue(instance.is_ready)
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertIsNone(instance.lifecycle_task_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
        after_paths, after_files = self.filesystem_state(self.root)
        self.assertEqual(after_files, before_files)
        expected_log_directory = Path("media") / f"user_{instance.user_id}" / "logs"
        self.assertEqual(
            set(after_paths) - set(before_paths),
            {expected_log_directory.as_posix()},
        )
        self.assertEqual(list((self.root / expected_log_directory).iterdir()), [])
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_start_permission_exception_is_preserved_before_any_file_or_logger_side_effect(self):
        instance = self.make_instance("start-permission-exception")
        before = self.filesystem_state(self.root)
        port = instance.port
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch("main.views.operation_loggers", wraps=real_operation_loggers) as logger_spy,
            patch(
                "main.views.InstancesViewset.check_object_permissions",
                side_effect=PermissionDenied("invented permission denial"),
            ),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data, {"detail": "invented permission denial"})
        logger_spy.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual(self.filesystem_state(self.root), before)
        instance.refresh_from_db()
        self.assertFalse(instance.log_file)
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
        port.refresh_from_db()
        self.assertFalse(port.is_available)

    def test_start_preflights_private_log_root_before_opening_real_operation_loggers(self):
        instance = self.make_instance("private-log-root-start")
        sentinel = self.private_root / "invented-start-sentinel"
        sentinel.write_bytes(b"preserve start sentinel\n")
        paths = dict(self.snapshot["paths"])
        paths["logs_directory"] = str(self.private_root)
        snapshot = {**self.snapshot, "paths": paths}
        self.client.force_authenticate(user=self.owner)
        before = self.filesystem_state(self.root)

        with (
            self.config_context(snapshot),
            patch("main.views.operation_loggers", wraps=real_operation_loggers) as logger_spy,
            self.parser_context(return_value=[]),
            patch("main.views.check_installed", return_value=([], [])),
            patch("main.views.dispatch_instance_task", return_value="unexpected-task-id") as dispatch,
        ):
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/", {}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        logger_spy.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual(self.filesystem_state(self.root), before)
        self.assertEqual(sentinel.read_bytes(), b"preserve start sentinel\n")
        instance.refresh_from_db()
        self.assertFalse(instance.log_file)
        self.assertTrue(instance.is_ready)
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertIsNone(instance.run_id)
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
        instance.port.refresh_from_db()
        self.assertFalse(instance.port.is_available)

    def test_instance_delete_refuses_foreign_start_path_before_any_cleanup(self):
        instance = self.make_instance("foreign-delete-path", with_log=True)
        foreign_path = self.root / "other-owner-start.sh"
        foreign_path.write_bytes(b"keep foreign script bytes\n")
        instance.start_file_path = str(foreign_path)
        instance.save(update_fields=["start_file_path"])
        owned_files = [Path(instance.preset.path), Path(instance.log_file.path)]
        expected_bytes = {path: path.read_bytes() for path in owned_files}
        expected_bytes[foreign_path] = foreign_path.read_bytes()
        port = instance.port
        self.client.force_authenticate(user=self.owner)

        with self.config_context():
            response = self.client.delete(f"/api/instances/{instance.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Instances.objects.filter(pk=instance.pk).exists())
        port.refresh_from_db()
        self.assertFalse(port.is_available)
        for path, content in expected_bytes.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), content)

    def test_logs_refuse_cross_user_storage_path_for_read_and_download(self):
        other = User.objects.create_user(username="file-other", password="test-password")
        instance = self.make_instance("cross-user-log", with_log=True)
        forged_name = f"user_{other.pk}/logs/instance_{instance.pk}_invented.txt"
        forged_path = self.media_root / forged_name
        forged_path.parent.mkdir(parents=True)
        forged_path.write_text("cross-user secret marker\n", encoding="utf-8")
        instance.log_file.name = forged_name
        instance.save(update_fields=["log_file"])
        self.client.force_authenticate(user=self.owner)

        with self.config_context():
            read_response = self.client.get(f"/api/instances/{instance.pk}/logs/")
            download_response = self.client.get(
                f"/api/instances/{instance.pk}/logs/download/"
            )

        self.assertEqual(read_response.status_code, 400)
        self.assertEqual(download_response.status_code, 400)
        self.assertNotIn("cross-user secret marker", str(read_response.data))
        self.assertNotIn("cross-user secret marker", str(download_response.content))
        self.assertEqual(forged_path.read_text(encoding="utf-8"), "cross-user secret marker\n")

    def test_valid_log_reads_do_not_require_private_staging_directory(self):
        instance = self.make_instance("read-without-stage", with_log=True)
        self.private_root.rmdir()
        self.client.force_authenticate(user=self.owner)

        with self.config_context():
            response = self.client.get(f"/api/instances/{instance.pk}/logs/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("invented log line", response.content.decode())

    def test_log_delete_refuses_divergent_log_storage_and_preserves_same_name_sentinel(self):
        instance = self.make_instance("divergent-log-only", with_log=False)
        external_root = self.root / "actual-log-storage"
        external_root.mkdir()
        external_storage = FileSystemStorage(location=str(external_root))
        _relative_name, actual_path, sentinel_path = self.attach_log_from_storage(
            instance,
            external_storage,
        )
        actual_bytes = actual_path.read_bytes()
        sentinel_bytes = sentinel_path.read_bytes()
        old_name = instance.log_file.name
        self.client.force_authenticate(user=self.owner)
        log_field = Instances._meta.get_field("log_file")

        with (
            self.config_context(),
            patch.object(log_field, "storage", external_storage),
        ):
            response = self.client.delete(f"/api/instances/{instance.pk}/logs/delete/")

        self.assertEqual(response.status_code, 400)
        instance.refresh_from_db()
        self.assertEqual(instance.log_file.name, old_name)
        self.assertTrue(actual_path.is_file())
        self.assertEqual(actual_path.read_bytes(), actual_bytes)
        self.assertTrue(sentinel_path.is_file())
        self.assertEqual(sentinel_path.read_bytes(), sentinel_bytes)

    def test_instance_delete_refuses_divergent_log_storage_before_any_cleanup(self):
        instance = self.make_instance("divergent-instance-log", with_log=False)
        external_root = self.root / "actual-instance-logs"
        external_root.mkdir()
        external_storage = FileSystemStorage(location=str(external_root))
        _relative_name, actual_path, sentinel_path = self.attach_log_from_storage(
            instance,
            external_storage,
        )
        actual_bytes = actual_path.read_bytes()
        sentinel_bytes = sentinel_path.read_bytes()
        owned_files = [Path(instance.preset.path), Path(instance.start_file_path)]
        expected_bytes = {path: path.read_bytes() for path in owned_files}
        port = instance.port
        self.client.force_authenticate(user=self.owner)
        log_field = Instances._meta.get_field("log_file")

        with (
            self.config_context(),
            patch.object(log_field, "storage", external_storage),
        ):
            response = self.client.delete(f"/api/instances/{instance.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Instances.objects.filter(pk=instance.pk).exists())
        port.refresh_from_db()
        self.assertFalse(port.is_available)
        self.assertEqual(actual_path.read_bytes(), actual_bytes)
        self.assertEqual(sentinel_path.read_bytes(), sentinel_bytes)
        for path, content in expected_bytes.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), content)

    def test_profile_delete_refuses_divergent_log_storage_before_any_cleanup(self):
        instance = self.make_instance("divergent-profile-log", with_log=False)
        external_root = self.root / "actual-profile-logs"
        external_root.mkdir()
        external_storage = FileSystemStorage(location=str(external_root))
        _relative_name, actual_path, sentinel_path = self.attach_log_from_storage(
            instance,
            external_storage,
        )
        actual_bytes = actual_path.read_bytes()
        sentinel_bytes = sentinel_path.read_bytes()
        owned_files = [Path(instance.preset.path), Path(instance.start_file_path)]
        expected_bytes = {path: path.read_bytes() for path in owned_files}
        port = instance.port
        self.client.force_authenticate(user=self.staff)
        log_field = Instances._meta.get_field("log_file")

        with (
            self.config_context(),
            patch.object(log_field, "storage", external_storage),
        ):
            response = self.client.delete(f"/api/moderator_panel/{self.owner.pk}/user/delete/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(Instances.objects.filter(pk=instance.pk).exists())
        port.refresh_from_db()
        self.assertFalse(port.is_available)
        self.assertEqual(actual_path.read_bytes(), actual_bytes)
        self.assertEqual(sentinel_path.read_bytes(), sentinel_bytes)
        for path, content in expected_bytes.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), content)

    def test_log_delete_refuses_unsupported_storage_before_any_file_or_metadata_change(self):
        instance = self.make_instance("unsupported-log-storage", with_log=True)
        old_name = instance.log_file.name
        log_path = Path(instance.log_file.path)
        log_bytes = log_path.read_bytes()
        self.client.force_authenticate(user=self.owner)
        log_field = Instances._meta.get_field("log_file")

        with (
            self.config_context(),
            patch.object(log_field, "storage", object()),
        ):
            response = self.client.delete(f"/api/instances/{instance.pk}/logs/delete/")

        self.assertEqual(response.status_code, 400)
        instance.refresh_from_db()
        self.assertEqual(instance.log_file.name, old_name)
        self.assertTrue(log_path.is_file())
        self.assertEqual(log_path.read_bytes(), log_bytes)

    def test_log_deletion_storage_failure_keeps_file_and_database_metadata(self):
        instance = self.make_instance("log-delete-failure", with_log=True)
        old_name = instance.log_file.name
        old_path = Path(instance.log_file.path)
        old_bytes = old_path.read_bytes()
        self.client.force_authenticate(user=self.owner)

        with (
            self.config_context(),
            patch.object(Instances, "save", autospec=True, side_effect=OSError("invented DB save failure")),
        ):
            response = self.client.delete(f"/api/instances/{instance.pk}/logs/delete/")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("invented DB save failure", str(response.data))
        instance.refresh_from_db()
        self.assertEqual(instance.log_file.name, old_name)
        self.assertTrue(old_path.is_file())
        self.assertEqual(old_path.read_bytes(), old_bytes)
