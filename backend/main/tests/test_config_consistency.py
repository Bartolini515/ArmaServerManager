"""Regression tests for safe, current configuration reads and updates."""

from __future__ import annotations

import importlib
import errno
import json
import os
import stat
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from main.utils.config import Config
from main.utils.config_lock import exclusive_file_lock

config_module = importlib.import_module("main.utils.config")
lock_module = importlib.import_module("main.utils.config_lock")


class ConfigConsistencyTests(SimpleTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="asm-config-consistency-",
            dir=os.environ["ASM_TEST_ROOT"],
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = Path(self.temporary_directory.name) / "config.json"
        self.initial_config = {
            "paths": {
                "steamcmd": "invented/steamcmd",
                "arma3": "invented/arma3-old",
                "mods_directory": "invented/mods",
            },
            "steam_auth": {
                "username": "invented-user",
                "password": "invented-password",
                "shared_secret": "aW52ZW50ZWQ=",
            },
        }
        self._write_json(self.initial_config)

    def _write_json(self, value):
        self.config_path.write_text(
            json.dumps(value, indent=2),
            encoding="utf-8",
        )

    def _mock_config_stat(self, *, owner, group, mode):
        real_stat = os.stat
        target_path = os.path.normcase(os.path.abspath(self.config_path))
        result = SimpleNamespace(
            st_uid=owner,
            st_gid=group,
            st_mode=stat.S_IFREG | mode,
        )

        def stat_for_config(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                candidate = os.path.normcase(
                    os.path.abspath(os.fsdecode(os.fspath(path)))
                )
                if candidate == target_path:
                    return result
            return real_stat(path, *args, **kwargs)

        return patch.object(config_module.os, "stat", side_effect=stat_for_config)

    def test_independent_instances_read_current_disk_state(self):
        writer = Config(str(self.config_path))
        reader = Config(str(self.config_path))

        writer.update({"paths": {"arma3": "invented/arma3-new"}})

        self.assertEqual(reader.get("paths.arma3"), "invented/arma3-new")

    def test_disjoint_concurrent_updates_merge_and_preserve_secret_values(self):
        first = Config(str(self.config_path))
        second = Config(str(self.config_path))
        start_updates = threading.Barrier(2)
        errors = []

        def update(config, changes):
            try:
                start_updates.wait(timeout=5)
                config.update(changes)
            except Exception as error:  # surfaced in the test thread
                errors.append(error)

        threads = [
            threading.Thread(
                target=update,
                args=(first, {"paths": {"arma3": "invented/arma3-concurrent"}}),
            ),
            threading.Thread(
                target=update,
                args=(second, {"steam_auth": {"username": "invented-new-user"}}),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        result = Config(str(self.config_path))
        self.assertEqual(result.get("paths.arma3"), "invented/arma3-concurrent")
        self.assertEqual(result.get("steam_auth.username"), "invented-new-user")
        self.assertEqual(result.get("steam_auth.password"), "invented-password")
        self.assertEqual(result.get("steam_auth.shared_secret"), "aW52ZW50ZWQ=")

    def test_snapshot_and_config_property_are_safe_copies(self):
        config = Config(str(self.config_path))

        snapshot = config.snapshot()
        snapshot["paths"]["arma3"] = "invented/snapshot-mutation"
        paths_from_get = config.get("paths")
        paths_from_get["arma3"] = "invented/get-mutation"
        config_view = config.config
        config_view["steam_auth"]["password"] = "invented/property-mutation"

        self.assertEqual(config.get("paths.arma3"), "invented/arma3-old")
        self.assertEqual(config.get("steam_auth.password"), "invented-password")
        stored = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(stored, self.initial_config)

    def test_failed_atomic_publication_keeps_previous_file_and_view_usable(self):
        config = Config(str(self.config_path))
        previous_bytes = self.config_path.read_bytes()

        with patch("os.replace", side_effect=OSError("injected publication failure")):
            with self.assertRaisesRegex(OSError, "injected publication failure"):
                config.update({"paths": {"arma3": "invented/unpublished"}})

        self.assertEqual(self.config_path.read_bytes(), previous_bytes)
        self.assertEqual(config.get("paths.arma3"), "invented/arma3-old")

    def test_snapshot_reader_and_update_share_the_publication_lock(self):
        if os.name != "nt":
            self.skipTest("This overlap test covers Windows file sharing behavior")

        config = Config(str(self.config_path))
        reader_opened_file = threading.Event()
        release_reader = threading.Event()
        writer_lock_attempted = threading.Event()
        writer_lock_acquired = threading.Event()
        replacement_attempted = threading.Event()
        errors = []
        real_lock = config_module.exclusive_file_lock
        real_replace = os.replace

        def controlled_read():
            with open(config.file_path, "r", encoding="utf-8") as config_file:
                contents = config_file.read()
                if threading.current_thread().name == "config-reader":
                    reader_opened_file.set()
                    if not release_reader.wait(timeout=5):
                        raise TimeoutError("test reader was not released")
                return json.loads(contents)

        @contextmanager
        def observed_lock(file_path):
            is_writer = threading.current_thread().name == "config-writer"
            if is_writer:
                writer_lock_attempted.set()
            with real_lock(file_path):
                if is_writer:
                    writer_lock_acquired.set()
                yield

        def observed_replace(source, destination):
            replacement_attempted.set()
            return real_replace(source, destination)

        def read_snapshot():
            try:
                config.snapshot()
            except Exception as error:
                errors.append(error)

        def publish_update():
            try:
                config.update({"paths": {"arma3": "invented/after-reader"}})
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=read_snapshot, name="config-reader")
        writer = threading.Thread(target=publish_update, name="config-writer")
        with (
            patch.object(config, "_read_file", side_effect=controlled_read),
            patch.object(config_module, "exclusive_file_lock", observed_lock),
            patch.object(config_module.os, "replace", observed_replace),
        ):
            reader.start()
            self.assertTrue(reader_opened_file.wait(timeout=5))
            writer.start()
            try:
                self.assertTrue(writer_lock_attempted.wait(timeout=5))
                self.assertFalse(writer_lock_acquired.wait(timeout=0.5))
                self.assertFalse(replacement_attempted.is_set())
            finally:
                release_reader.set()
                reader.join(timeout=10)
                writer.join(timeout=10)

        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(replacement_attempted.is_set())
        self.assertEqual(config.get("paths.arma3"), "invented/after-reader")

    def test_existing_posix_mode_and_group_are_preserved(self):
        if os.name != "posix":
            self.skipTest("POSIX ownership and mode metadata are unavailable")

        config = Config(str(self.config_path))
        os.chmod(self.config_path, 0o640)
        previous_stat = os.stat(self.config_path)

        config.update({"paths": {"arma3": "invented/permission-preserved"}})

        published_stat = os.stat(self.config_path)
        self.assertEqual(stat.S_IMODE(published_stat.st_mode), 0o640)
        self.assertEqual(published_stat.st_uid, previous_stat.st_uid)
        self.assertEqual(published_stat.st_gid, previous_stat.st_gid)

    def test_metadata_preservation_failure_keeps_previous_file_before_replace(self):
        config = Config(str(self.config_path))
        previous_bytes = self.config_path.read_bytes()
        replace = Mock()

        with (
            patch.object(config_module, "_IS_POSIX", True),
            patch.object(
                config_module.os,
                "chown",
                side_effect=PermissionError("injected ownership failure"),
                create=True,
            ),
            patch.object(config_module.os, "replace", replace),
        ):
            with self.assertRaisesRegex(
                PermissionError,
                "Cannot preserve existing config owner/group",
            ):
                config.update({"paths": {"arma3": "invented/unpublished-permissions"}})

        self.assertEqual(self.config_path.read_bytes(), previous_bytes)
        replace.assert_not_called()

    def test_root_owned_shared_group_config_can_fallback_to_writer_owner(self):
        config = Config(str(self.config_path))
        chown_calls = []
        metadata_order = []
        replace_real = os.replace
        original_stat = SimpleNamespace(st_uid=0, st_gid=456, st_mode=stat.S_IFREG | 0o660)

        def chown(path, owner, group):
            chown_calls.append((path, owner, group))
            metadata_order.append("chown")
            if len(chown_calls) == 1:
                raise PermissionError(errno.EPERM, "injected root-owner permission failure")

        def chmod(path, mode):
            metadata_order.append(("chmod", mode))

        def replace(source, destination):
            metadata_order.append("replace")
            return replace_real(source, destination)

        with (
            patch.object(config_module, "_IS_POSIX", True),
            self._mock_config_stat(owner=0, group=456, mode=0o660),
            patch.object(config_module.os, "chown", side_effect=chown, create=True),
            patch.object(config_module.os, "chmod", side_effect=chmod),
            patch.object(config_module.os, "geteuid", return_value=1001, create=True),
            patch.object(config_module.os, "getegid", return_value=456, create=True),
            patch.object(config_module.os, "getgroups", return_value=[456], create=True),
            patch.object(config_module.os, "replace", side_effect=replace),
        ):
            config.update({"paths": {"arma3": "invented/root-owner-fallback"}})

        temporary_path = chown_calls[0][0]
        self.assertEqual(chown_calls, [(temporary_path, 0, 456), (temporary_path, 1001, 456)])
        self.assertEqual(metadata_order, ["chown", "chown", ("chmod", 0o660), "replace"])
        self.assertEqual(config.get("paths.arma3"), "invented/root-owner-fallback")

    def test_nonroot_owner_cannot_silently_change_to_writer_owner(self):
        config = Config(str(self.config_path))
        previous_bytes = self.config_path.read_bytes()
        replace = Mock()
        chown = Mock(side_effect=PermissionError(errno.EPERM, "injected ownership failure"))

        with (
            patch.object(config_module, "_IS_POSIX", True),
            self._mock_config_stat(owner=2002, group=456, mode=0o660),
            patch.object(config_module.os, "chown", chown, create=True),
            patch.object(config_module.os, "geteuid", return_value=1001, create=True),
            patch.object(config_module.os, "getegid", return_value=456, create=True),
            patch.object(config_module.os, "getgroups", return_value=[456], create=True),
            patch.object(config_module.os, "replace", replace),
        ):
            with self.assertRaisesRegex(PermissionError, "Cannot preserve existing config owner/group"):
                config.update({"paths": {"arma3": "invented/denied-owner-fallback"}})

        self.assertEqual(chown.call_count, 1)
        self.assertEqual(self.config_path.read_bytes(), previous_bytes)
        replace.assert_not_called()

    def test_root_group_preservation_failure_keeps_previous_config(self):
        config = Config(str(self.config_path))
        previous_bytes = self.config_path.read_bytes()
        replace = Mock()
        chown = Mock(side_effect=PermissionError(errno.EPERM, "injected group failure"))

        with (
            patch.object(config_module, "_IS_POSIX", True),
            self._mock_config_stat(owner=0, group=456, mode=0o660),
            patch.object(config_module.os, "chown", chown, create=True),
            patch.object(config_module.os, "geteuid", return_value=1001, create=True),
            patch.object(config_module.os, "getegid", return_value=456, create=True),
            patch.object(config_module.os, "getgroups", return_value=[456], create=True),
            patch.object(config_module.os, "replace", replace),
        ):
            with self.assertRaisesRegex(PermissionError, "Cannot preserve existing config group"):
                config.update({"paths": {"arma3": "invented/unpublished-group"}})

        self.assertEqual(chown.call_count, 2)
        self.assertEqual(self.config_path.read_bytes(), previous_bytes)
        replace.assert_not_called()

    def test_mode_preservation_failure_keeps_previous_config(self):
        config = Config(str(self.config_path))
        previous_bytes = self.config_path.read_bytes()
        replace = Mock()

        with (
            patch.object(config_module, "_IS_POSIX", True),
            self._mock_config_stat(owner=1001, group=456, mode=0o660),
            patch.object(config_module.os, "chown", Mock(), create=True),
            patch.object(
                config_module.os,
                "chmod",
                side_effect=PermissionError(errno.EPERM, "injected mode failure"),
            ),
            patch.object(config_module.os, "replace", replace),
        ):
            with self.assertRaisesRegex(PermissionError, "Cannot preserve existing config mode"):
                config.update({"paths": {"arma3": "invented/unpublished-mode"}})

        self.assertEqual(self.config_path.read_bytes(), previous_bytes)
        replace.assert_not_called()

    def test_posix_existing_config_reader_does_not_require_sidecar_access(self):
        with (
            patch.object(config_module, "_IS_POSIX", True),
            patch.object(
                config_module,
                "exclusive_file_lock",
                side_effect=AssertionError("POSIX existing-file reads must not require the lock file"),
            ),
        ):
            config = Config(str(self.config_path))
            self.assertEqual(config.get("paths.arma3"), "invented/arma3-old")

    def test_posix_lock_uses_read_only_descriptor_and_preserves_provisioned_sidecar(self):
        lock_target = Path(self.temporary_directory.name) / "invented-provisioned.json"
        lock_path = Path(f"{lock_target}.lock")
        lock_path.write_bytes(b"provisioned nonsecret lock")
        os.chmod(lock_path, 0o444)
        self.addCleanup(lock_path.chmod, 0o644)
        before = os.stat(lock_path)
        before_content = lock_path.read_bytes()
        open_flags = []
        real_open = os.open
        fake_fcntl = SimpleNamespace(LOCK_EX=object(), LOCK_UN=object(), flock=Mock())

        def record_open(path, flags, *args, **kwargs):
            if os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(path)))) == os.path.normcase(os.path.abspath(lock_path)):
                open_flags.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with (
            patch.object(lock_module, "_IS_WINDOWS", False),
            patch.dict(sys.modules, {"fcntl": fake_fcntl}),
            patch.object(lock_module.os, "open", side_effect=record_open),
        ):
            with lock_module.exclusive_file_lock(str(lock_target)):
                pass

        after = os.stat(lock_path)
        self.assertEqual(open_flags, [os.O_RDONLY])
        self.assertEqual(lock_path.read_bytes(), before_content)
        self.assertEqual((after.st_ino, after.st_mode), (before.st_ino, before.st_mode))

    def test_new_posix_lock_is_made_readable_before_becoming_stable(self):
        lock_target = Path(self.temporary_directory.name) / "invented-new-lock.json"
        lock_path = Path(f"{lock_target}.lock")
        operations = []
        open_flags = []
        real_open = os.open
        fake_fcntl = SimpleNamespace(LOCK_EX=object(), LOCK_UN=object(), flock=Mock())

        def record_fchmod(file_descriptor, mode):
            operations.append(("fchmod", mode))

        def record_link(source, destination):
            operations.append(("link", os.path.basename(source)))
            with open(destination, "wb"):
                pass

        def record_open(path, flags, *args, **kwargs):
            if os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(path)))) == os.path.normcase(os.path.abspath(lock_path)):
                open_flags.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with (
            patch.object(lock_module, "_IS_WINDOWS", False),
            patch.dict(sys.modules, {"fcntl": fake_fcntl}),
            patch.object(lock_module.os, "fchmod", side_effect=record_fchmod, create=True),
            patch.object(lock_module.os, "link", side_effect=record_link),
            patch.object(lock_module.os, "open", side_effect=record_open),
        ):
            with lock_module.exclusive_file_lock(str(lock_target)):
                pass

        self.assertEqual(operations[0], ("fchmod", 0o644))
        self.assertEqual(operations[1][0], "link")
        self.assertEqual(open_flags, [os.O_RDONLY])
        self.assertTrue(lock_path.is_file())

    def test_new_posix_lock_mode_ignores_restrictive_umask(self):
        if os.name != "posix":
            self.skipTest("POSIX umask and mode bits are unavailable")

        lock_target = Path(self.temporary_directory.name) / "invented-umask-lock.json"
        previous_umask = os.umask(0o077)
        try:
            with exclusive_file_lock(str(lock_target)):
                pass
        finally:
            os.umask(previous_umask)

        self.assertEqual(
            stat.S_IMODE(os.stat(f"{lock_target}.lock").st_mode),
            0o644,
        )

    def test_posix_metadata_platform_mock_runs_before_replace(self):
        config = Config(str(self.config_path))
        previous_stat = os.stat(self.config_path)
        operation_order = []
        real_replace = os.replace

        def record_replace(source, destination):
            operation_order.append("replace")
            return real_replace(source, destination)

        def record_chown(path, owner, group):
            operation_order.append(("chown", owner, group))

        def record_chmod(path, mode):
            operation_order.append(("chmod", mode))

        with (
            patch.object(config_module, "_IS_POSIX", True),
            patch.object(config_module.os, "chown", side_effect=record_chown, create=True),
            patch.object(config_module.os, "chmod", side_effect=record_chmod),
            patch.object(config_module.os, "replace", record_replace),
        ):
            config.update({"paths": {"arma3": "invented/platform-mock"}})

        self.assertEqual(
            operation_order[0],
            ("chown", previous_stat.st_uid, previous_stat.st_gid),
        )
        self.assertEqual(
            operation_order[1],
            ("chmod", stat.S_IMODE(previous_stat.st_mode)),
        )
        self.assertEqual(operation_order[2], "replace")

    def test_new_bootstrap_file_stays_owner_only_on_posix(self):
        if os.name != "posix":
            self.skipTest("POSIX mode bits are unavailable")

        self.config_path.unlink()
        Config(str(self.config_path))

        self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o600)

    def test_omitted_secrets_are_preserved_and_explicit_empty_values_clear(self):
        config = Config(str(self.config_path))

        config.update({"steam_auth": {"username": "invented-updated-user"}})
        self.assertEqual(config.get("steam_auth.password"), "invented-password")
        self.assertEqual(config.get("steam_auth.shared_secret"), "aW52ZW50ZWQ=")

        config.update({"steam_auth": {"password": "", "shared_secret": ""}})
        self.assertEqual(config.get("steam_auth.password"), "")
        self.assertEqual(config.get("steam_auth.shared_secret"), "")

    def test_missing_file_bootstrap_is_complete_for_multiple_instances(self):
        self.config_path.unlink()
        start_bootstrap = threading.Barrier(2)
        configs = []
        errors = []

        def bootstrap():
            try:
                start_bootstrap.wait(timeout=5)
                configs.append(Config(str(self.config_path)))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=bootstrap) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0].config, configs[1].config)
        self.assertEqual(
            json.loads(self.config_path.read_text(encoding="utf-8")),
            configs[0].config,
        )

    def test_posix_file_lock_releases_after_operation_errors(self):
        lock_target = Path(self.temporary_directory.name) / "invented-posix.json"
        fake_fcntl = SimpleNamespace(LOCK_EX=object(), LOCK_UN=object(), flock=Mock())

        with (
            patch("main.utils.config_lock._IS_WINDOWS", False),
            patch.dict(sys.modules, {"fcntl": fake_fcntl}),
            patch("main.utils.config_lock.os.fchmod", Mock(), create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected operation failure"):
                with exclusive_file_lock(str(lock_target)):
                    raise RuntimeError("injected operation failure")

        calls = fake_fcntl.flock.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0], calls[1].args[0])
        self.assertEqual(
            [entry.args[1] for entry in calls],
            [fake_fcntl.LOCK_EX, fake_fcntl.LOCK_UN],
        )

    def test_windows_file_lock_releases_after_operation_errors(self):
        lock_target = Path(self.temporary_directory.name) / "invented-windows.json"
        fake_msvcrt = SimpleNamespace(
            LK_NBLCK=object(),
            LK_UNLCK=object(),
            locking=Mock(),
        )

        with (
            patch("main.utils.config_lock._IS_WINDOWS", True),
            patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected operation failure"):
                with exclusive_file_lock(str(lock_target)):
                    raise RuntimeError("injected operation failure")

        self.assertEqual(fake_msvcrt.locking.call_count, 2)
        self.assertEqual(
            [entry.args[1:] for entry in fake_msvcrt.locking.call_args_list],
            [
                (fake_msvcrt.LK_NBLCK, 1),
                (fake_msvcrt.LK_UNLCK, 1),
            ],
        )
