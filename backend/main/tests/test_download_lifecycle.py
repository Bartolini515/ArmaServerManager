from contextlib import ExitStack
import ctypes
import errno
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil

from main.steamcmd import processes
from main.steamcmd.mods_download import download_mods, test_connection
from main.utils.config import config
from main.utils.steam_errors import SteamCommandError, SteamSecretRedactor
import main.workaround.ghost_folder as ghost_folder_module
from main.workaround.ghost_folder import GhostFolder, _atomic_publish


class DownloadLifecycleTests(unittest.TestCase):
    def test_rate_limited_batch_fails_then_later_batch_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            download_root = Path(temp_dir) / "downloads"
            mods_root = Path(temp_dir) / "mods"
            snapshot = {
                "paths": {
                    "steamcmd": str(Path(temp_dir) / "steamcmd"),
                    "download_directory": str(download_root),
                    "mods_directory": str(mods_root),
                },
                "steam_auth": {
                    "username": "fake-user",
                    "password": "fake-password",
                    "shared_secret": "ZmFrZS1zZWNyZXQ=",
                },
            }
            calls: list[list[str]] = []
            sleeps: list[int] = []

            def download_batch(**kwargs):
                calls.append(list(kwargs["mod"]))
                if len(calls) == 1:
                    return 84
                content_root = Path(kwargs["ghost_folder_path"]) / "steamapps/workshop/content/107410"
                for mod in kwargs["mod"]:
                    (content_root / mod).mkdir(parents=True, exist_ok=True)
                return 0

            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.test_connection", return_value=True),
                patch(
                    "main.steamcmd.mods_download.generate_steam_guard_code",
                    side_effect=("ABCDE", "BCDEF", "CDEFG"),
                ),
                patch("main.steamcmd.mods_download.steamcmd_download", side_effect=download_batch),
                patch("main.steamcmd.mods_download.lowercase_addons_directory"),
                patch("main.steamcmd.mods_download.sleep", side_effect=sleeps.append),
            ):
                failed = download_mods(["111", "222"], "same-name", lim=1)

            self.assertEqual(failed, ["111"])
            self.assertEqual(calls, [["111"], ["222"]])
            self.assertEqual(sleeps, [600])
            self.assertEqual(list(download_root.iterdir()), [])

    def test_all_rate_limited_batches_return_failures_and_clean_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(temp_dir)
            created: list[GhostFolder] = []
            real_folder_type = GhostFolder

            def make_folder(*args, **kwargs):
                folder = real_folder_type(*args, **kwargs)
                created.append(folder)
                return folder

            sleeps: list[int] = []
            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.GhostFolder", side_effect=make_folder),
                patch("main.steamcmd.mods_download.test_connection", return_value=True),
                patch(
                    "main.steamcmd.mods_download.generate_steam_guard_code",
                    side_effect=("ABCDE", "BCDEF", "CDEFG"),
                ),
                patch("main.steamcmd.mods_download.steamcmd_download", return_value=84),
                patch("main.steamcmd.mods_download.lowercase_addons_directory"),
                patch("main.steamcmd.mods_download.sleep", side_effect=sleeps.append),
            ):
                failed = download_mods(["111", "222"], "same-name", lim=1)

            self.assertEqual(failed, ["111", "222"])
            self.assertEqual(sleeps, [600, 600])
            self.assertEqual(len(created), 1)
            self.assertFalse(Path(created[0].ghost_folder_path).exists())

    def test_success_without_workshop_content_remains_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(temp_dir)
            created: list[GhostFolder] = []
            real_folder_type = GhostFolder

            def make_folder(*args, **kwargs):
                folder = real_folder_type(*args, **kwargs)
                created.append(folder)
                return folder

            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.GhostFolder", side_effect=make_folder),
                patch("main.steamcmd.mods_download.test_connection", return_value=True),
                patch(
                    "main.steamcmd.mods_download.generate_steam_guard_code",
                    side_effect=("ABCDE", "BCDEF"),
                ),
                patch("main.steamcmd.mods_download.steamcmd_download", return_value=0),
                patch("main.steamcmd.mods_download.lowercase_addons_directory"),
            ):
                with self.assertRaises(FileNotFoundError):
                    download_mods(["123"], "same-name", lim=1)

            self.assertEqual(len(created), 1)
            self.assertFalse(Path(created[0].ghost_folder_path).exists())

    def test_same_name_gets_distinct_owned_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = GhostFolder("same-name", temp_dir)
            second = GhostFolder("same-name", temp_dir)
            first_path = Path(first.ghost_folder_path)
            second_path = Path(second.ghost_folder_path)

            self.assertNotEqual(first_path, second_path)
            self.assertTrue(first_path.is_dir())
            self.assertTrue(second_path.is_dir())

            first.cleanup()
            self.assertFalse(first_path.exists())
            self.assertTrue(second_path.is_dir())
            second.cleanup()
            self.assertFalse(second_path.exists())

    def test_download_failure_always_cleans_only_its_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            download_root = Path(temp_dir) / "downloads"
            download_root.mkdir()
            unrelated = download_root / "same-name_download_folder"
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("preserve", encoding="utf-8")
            snapshot = self._snapshot(temp_dir, download_root=download_root)
            created_workspaces: list[Path] = []
            real_ghost_folder = GhostFolder

            def make_ghost_folder(*args, **kwargs):
                folder = real_ghost_folder(*args, **kwargs)
                created_workspaces.append(Path(folder.ghost_folder_path))
                return folder

            failure_steps = (
                "connection",
                "generation",
                "batch",
                "normalization",
                "publication",
            )
            for failure_step in failure_steps:
                with self.subTest(failure_step=failure_step):
                    created_workspaces.clear()
                    if failure_step == "generation":
                        guard_patch = patch(
                            "main.steamcmd.mods_download.generate_steam_guard_code",
                            side_effect=("ABCDE", RuntimeError("fake guard failure")),
                        )
                    else:
                        guard_patch = patch(
                            "main.steamcmd.mods_download.generate_steam_guard_code",
                            return_value="ABCDE",
                        )
                    patches = [
                        patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                        patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                        patch(
                            "main.steamcmd.mods_download.test_connection",
                            return_value=failure_step != "connection",
                        ),
                        guard_patch,
                        patch("main.steamcmd.mods_download.sleep"),
                        patch("main.steamcmd.mods_download.GhostFolder", side_effect=make_ghost_folder),
                    ]
                    if failure_step == "batch":
                        patches.append(
                            patch("main.steamcmd.mods_download.steamcmd_download", side_effect=RuntimeError("fake batch failure"))
                        )
                    else:
                        patches.append(patch("main.steamcmd.mods_download.steamcmd_download", return_value=0))
                    if failure_step == "normalization":
                        patches.append(
                            patch("main.steamcmd.mods_download.lowercase_addons_directory", side_effect=RuntimeError("fake normalization failure"))
                        )
                    else:
                        patches.append(patch("main.steamcmd.mods_download.lowercase_addons_directory"))
                    if failure_step == "publication":
                        patches.append(
                            patch.object(real_ghost_folder, "move_files", side_effect=RuntimeError("fake publication failure"))
                        )

                    with ExitStack() as stack:
                        for active_patch in patches:
                            stack.enter_context(active_patch)
                        with self.assertRaises(Exception):
                            download_mods(["123"], "same-name", lim=1)

                    self.assertEqual(len(created_workspaces), 1)
                    self.assertFalse(created_workspaces[0].exists())
                    self.assertEqual((unrelated / "keep.txt").read_text(encoding="utf-8"), "preserve")

    def test_cleanup_failure_does_not_mask_primary_download_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(temp_dir)
            ghost = Mock(ghost_folder_path="/fake/workspace")
            ghost.cleanup.return_value = False
            primary_error = RuntimeError("primary download failure")

            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.GhostFolder", return_value=ghost),
                patch("main.steamcmd.mods_download.generate_steam_guard_code", return_value="ABCDE"),
                patch("main.steamcmd.mods_download.test_connection", side_effect=primary_error),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    download_mods(["123"], "same-name", lim=1)

            self.assertIs(raised.exception, primary_error)
            ghost.cleanup.assert_called_once_with()

    def test_unconfirmed_process_cleanup_retains_workspace_through_outer_finally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(temp_dir)
            created: list[GhostFolder] = []
            real_folder_type = GhostFolder

            def make_folder(*args, **kwargs):
                folder = real_folder_type(*args, **kwargs)
                created.append(folder)
                return folder

            process = Mock(pid=5544, returncode=0)
            stream_failure = RuntimeError("synthetic output reader failure")
            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.GhostFolder", side_effect=make_folder),
                patch("main.steamcmd.mods_download.test_connection", return_value=True),
                patch(
                    "main.steamcmd.mods_download.generate_steam_guard_code",
                    side_effect=("ABCDE", "BCDEF"),
                ),
                patch("main.steamcmd.mods_download.sleep"),
                patch("main.steamcmd.processes.subprocess.Popen", return_value=process),
                patch("main.steamcmd.mods_download.stream_process_output", side_effect=stream_failure),
                patch("main.steamcmd.mods_download.terminate_owned_process_tree", return_value=False),
            ):
                with self.assertRaises(Exception) as raised:
                    download_mods(["123"], "same-name", lim=1)

            error = raised.exception
            self.assertEqual(type(error).__name__, "CleanupUncertainError")
            self.assertTrue(getattr(error, "cleanup_uncertain", False))
            self.assertIs(getattr(error, "original_exception", None), stream_failure)
            self.assertEqual(error.diagnostic, "SteamCMD process cleanup is unconfirmed")
            self.assertEqual(len(created), 1)
            self.assertEqual(error.workspace_path, created[0].ghost_folder_path)
            self.assertTrue(Path(error.workspace_path).is_dir())
            self.assertNotIn("synthetic output reader failure", str(error))
            self.assertNotIn("synthetic output reader failure", error.diagnostic)

    def test_unconfirmed_connection_cleanup_survives_logging_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(temp_dir)
            created: list[GhostFolder] = []
            real_folder_type = GhostFolder
            process = Mock(pid=5544, returncode=None)
            timeout = subprocess.TimeoutExpired(["bash", "fake-login"], timeout=10)
            process.communicate.side_effect = timeout
            callback_failures: list[str] = []

            def make_folder(*args, **kwargs):
                folder = real_folder_type(*args, **kwargs)
                created.append(folder)
                return folder

            def failing_log(message: str) -> None:
                if "connection error" in message or "process cleanup is unconfirmed" in message:
                    callback_failures.append(message)
                    raise OSError("synthetic log sink failure")

            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.GhostFolder", side_effect=make_folder),
                patch("main.steamcmd.mods_download.generate_steam_guard_code", return_value="ABCDE"),
                patch("main.steamcmd.processes.subprocess.Popen", return_value=process),
                patch("main.steamcmd.mods_download.terminate_owned_process_tree", return_value=False),
            ):
                with self.assertRaises(processes.CleanupUncertainError) as raised:
                    download_mods(["123"], "same-name", lim=1, log_callback=failing_log)

            self.assertTrue(callback_failures)
            self.assertIs(raised.exception.original_exception, timeout)
            self.assertEqual(raised.exception.diagnostic, "SteamCMD process cleanup is unconfirmed")
            self.assertEqual(len(created), 1)
            self.assertEqual(raised.exception.workspace_path, created[0].ghost_folder_path)
            self.assertTrue(Path(raised.exception.workspace_path).is_dir())

    def test_unconfirmed_batch_cleanup_survives_logging_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(temp_dir)
            created: list[GhostFolder] = []
            real_folder_type = GhostFolder
            process = Mock(pid=5544, returncode=None)
            stream_failure = RuntimeError("synthetic output failure")
            callback_failures: list[str] = []

            def make_folder(*args, **kwargs):
                folder = real_folder_type(*args, **kwargs)
                created.append(folder)
                return folder

            def failing_log(message: str) -> None:
                if "SteamCMD error" in message or "process cleanup is unconfirmed" in message:
                    callback_failures.append(message)
                    raise OSError("synthetic log sink failure")

            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.GhostFolder", side_effect=make_folder),
                patch("main.steamcmd.mods_download.test_connection", return_value=True),
                patch("main.steamcmd.mods_download.generate_steam_guard_code", side_effect=("ABCDE", "BCDEF")),
                patch("main.steamcmd.processes.subprocess.Popen", return_value=process),
                patch("main.steamcmd.mods_download.stream_process_output", side_effect=stream_failure),
                patch("main.steamcmd.mods_download.terminate_owned_process_tree", return_value=False),
                patch("main.steamcmd.mods_download.sleep"),
            ):
                with self.assertRaises(processes.CleanupUncertainError) as raised:
                    download_mods(["123"], "same-name", lim=1, log_callback=failing_log)

            self.assertTrue(callback_failures)
            self.assertIs(raised.exception.original_exception, stream_failure)
            self.assertEqual(raised.exception.diagnostic, "SteamCMD process cleanup is unconfirmed")
            self.assertEqual(len(created), 1)
            self.assertEqual(raised.exception.workspace_path, created[0].ghost_folder_path)
            self.assertTrue(Path(raised.exception.workspace_path).is_dir())

    def test_filesystem_cleanup_pending_preserves_failed_list_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(temp_dir)
            ghost = Mock(ghost_folder_path="/fake/workspace")
            ghost.cleanup.return_value = False

            with (
                patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                patch("main.steamcmd.mods_download.GhostFolder", return_value=ghost),
                patch("main.steamcmd.mods_download.generate_steam_guard_code", return_value="ABCDE"),
                patch("main.steamcmd.mods_download.test_connection", return_value=True),
                patch("main.steamcmd.mods_download.steamcmd_download", return_value=84),
                patch("main.steamcmd.mods_download.sleep"),
            ):
                result = download_mods(["123"], "same-name", lim=1)

            self.assertIsInstance(result, list)
            self.assertEqual(result, ["123"])
            self.assertTrue(result)
            self.assertTrue(result.cleanup_pending)
            self.assertEqual(result.cleanup_reason, "filesystem_cleanup_pending")
            self.assertFalse(getattr(result, "cleanup_uncertain", False))

    def test_posix_owned_directory_cleanup_uses_private_atomic_claim(self) -> None:
        remover = getattr(ghost_folder_module, "_remove_posix_owned_directory", None)
        self.assertTrue(callable(remover))

    def test_posix_claim_moves_only_the_original_when_source_name_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "workspace")
            ownership = self._fake_posix_ownership(temp_dir)
            operations = _FakePosixCleanupOperations(source_identity=(7, 11))
            operations.on_claim = lambda: operations.parent_entries.__setitem__(
                "workspace", (7, 99)
            )

            cleaned = ghost_folder_module._remove_posix_owned_directory(
                path,
                ownership,
                operations=operations,
            )

            self.assertTrue(cleaned)
            self.assertEqual(operations.cleaned_identities, [(7, 11)])
            self.assertEqual(operations.parent_entries["workspace"], (7, 99))
            self.assertIn((operations.quarantine_fd, operations.claim_name), operations.removed_names)

    def test_posix_preclaim_swap_is_moved_but_never_traversed_or_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "workspace")
            ownership = self._fake_posix_ownership(temp_dir)
            operations = _FakePosixCleanupOperations(source_identity=(7, 99))

            cleaned = ghost_folder_module._remove_posix_owned_directory(
                path,
                ownership,
                operations=operations,
            )

            self.assertFalse(cleaned)
            self.assertEqual(operations.cleaned_identities, [])
            self.assertEqual(
                operations.claims[operations.claim_name],
                (7, 99),
            )
            self.assertNotIn((operations.quarantine_fd, operations.claim_name), operations.removed_names)

    def test_posix_postclaim_swap_is_not_deleted_at_final_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "workspace")
            ownership = self._fake_posix_ownership(temp_dir)
            operations = _FakePosixCleanupOperations(source_identity=(7, 11))
            operations.on_remove_tree = lambda: operations.claims.__setitem__(
                operations.claim_name, (7, 99)
            )

            cleaned = ghost_folder_module._remove_posix_owned_directory(
                path,
                ownership,
                operations=operations,
            )

            self.assertFalse(cleaned)
            self.assertEqual(operations.cleaned_identities, [(7, 11)])
            self.assertEqual(operations.claims[operations.claim_name], (7, 99))
            self.assertNotIn((operations.quarantine_fd, operations.claim_name), operations.removed_names)

    def test_posix_quarantine_replacement_is_not_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "workspace")
            ownership = self._fake_posix_ownership(temp_dir)
            operations = _FakePosixCleanupOperations(source_identity=(7, 11))
            operations.quarantine_path_identity = (7, 99)

            cleaned = ghost_folder_module._remove_posix_owned_directory(
                path,
                ownership,
                operations=operations,
            )

            self.assertFalse(cleaned)
            self.assertEqual(operations.parent_entries["workspace"], (7, 11))
            self.assertEqual(operations.parent_entries[operations.quarantine_name], (7, 99))
            self.assertEqual(operations.rename_calls, 0)
            self.assertEqual(operations.cleaned_identities, [])

    def test_posix_unsafe_parent_quarantine_and_rename_primitive_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "workspace")
            ownership = self._fake_posix_ownership(temp_dir)
            unsafe_parent = _FakePosixCleanupOperations(source_identity=(7, 11), parent_safe=False)
            replaced_parent = _FakePosixCleanupOperations(
                source_identity=(7, 11),
                parent_identity=(7, 99),
            )
            unsafe_quarantine = _FakePosixCleanupOperations(
                source_identity=(7, 11),
                quarantine_safe=False,
            )
            unsupported = _FakePosixCleanupOperations(
                source_identity=(7, 11),
                rename_error=OSError(errno.ENOTSUP, "unsupported"),
            )

            for operations in (unsafe_parent, replaced_parent, unsafe_quarantine, unsupported):
                with self.subTest(operations=operations):
                    cleaned = ghost_folder_module._remove_posix_owned_directory(
                        path,
                        ownership,
                        operations=operations,
                    )
                    self.assertFalse(cleaned)
                    self.assertEqual(operations.cleaned_identities, [])
                    self.assertEqual(operations.parent_entries.get("workspace"), (7, 11))

    def test_posix_permission_and_acl_checks_reject_unsafe_ancestry(self) -> None:
        trusted = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        world_writable = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o777)
        private = SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o700, st_dev=7)
        wrong_owner = SimpleNamespace(st_uid=1001, st_mode=stat.S_IFDIR | 0o700, st_dev=7)
        group_accessible = SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o750, st_dev=7)

        with (
            patch.object(ghost_folder_module.os, "geteuid", return_value=1000, create=True),
            patch.object(ghost_folder_module.os, "listxattr", return_value=[], create=True),
        ):
            self.assertTrue(ghost_folder_module._trusted_posix_ancestor(1, trusted))
            self.assertFalse(ghost_folder_module._trusted_posix_ancestor(1, world_writable))
            self.assertTrue(ghost_folder_module._private_posix_quarantine_is_safe(1, private, 7))
            self.assertFalse(ghost_folder_module._private_posix_quarantine_is_safe(1, wrong_owner, 7))
            self.assertFalse(ghost_folder_module._private_posix_quarantine_is_safe(1, group_accessible, 7))

        with (
            patch.object(ghost_folder_module.os, "geteuid", return_value=1000, create=True),
            patch.object(
                ghost_folder_module.os,
                "listxattr",
                return_value=["system.posix_acl_access"],
                create=True,
            ),
        ):
            self.assertFalse(ghost_folder_module._trusted_posix_ancestor(1, trusted))
            self.assertFalse(ghost_folder_module._private_posix_quarantine_is_safe(1, private, 7))

    def test_posix_missing_noreplace_primitive_fails_closed(self) -> None:
        with (
            patch.object(ghost_folder_module.sys, "platform", "linux"),
            patch.object(ghost_folder_module.ctypes, "CDLL", return_value=SimpleNamespace()),
        ):
            with self.assertRaises(OSError):
                ghost_folder_module._renameat2_noreplace(1, "workspace", 2, "claim")

    def test_posix_recursive_cleanup_unlinks_nested_symlink_without_following_it(self) -> None:
        class FakeEntry:
            def __init__(self, name: str, mode: int, inode: int) -> None:
                self.name = name
                self._metadata = SimpleNamespace(st_mode=mode, st_dev=7, st_ino=inode)

            def stat(self, *, follow_symlinks: bool):
                self.asserted_no_follow = not follow_symlinks
                return self._metadata

        class FakeScanner:
            def __init__(self, entries) -> None:
                self.entries = entries

            def __enter__(self):
                return iter(self.entries)

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as temp_dir:
            outside_marker = Path(temp_dir) / "outside.txt"
            outside_marker.write_text("preserve", encoding="utf-8")
            nested_directory = FakeEntry("nested", stat.S_IFDIR | 0o700, 12)
            nested_link = FakeEntry("escape", stat.S_IFLNK | 0o777, 13)
            unlinked: list[tuple[str, int]] = []
            removed_directories: list[tuple[str, int]] = []

            def fake_unlink(name: str, *, dir_fd: int) -> None:
                unlinked.append((name, dir_fd))

            def fake_rmdir(name: str, *, dir_fd: int) -> None:
                removed_directories.append((name, dir_fd))

            with (
                patch.object(
                    ghost_folder_module.os,
                    "scandir",
                    side_effect=(FakeScanner([nested_directory]), FakeScanner([nested_link])),
                ),
                patch.object(
                    ghost_folder_module.os,
                    "fstat",
                    side_effect=(SimpleNamespace(st_dev=7), SimpleNamespace(st_dev=7)),
                ),
                patch.object(ghost_folder_module.os, "unlink", side_effect=fake_unlink),
                patch.object(ghost_folder_module.os, "rmdir", side_effect=fake_rmdir),
                patch.object(ghost_folder_module.os, "close"),
                patch.object(
                    ghost_folder_module,
                    "_open_owned_directory",
                    return_value=2,
                ),
                patch.object(
                    ghost_folder_module.os,
                    "stat",
                    return_value=SimpleNamespace(st_dev=7, st_ino=12, st_mode=stat.S_IFDIR),
                ),
            ):
                self.assertTrue(ghost_folder_module._remove_posix_tree(1))

            self.assertTrue(nested_link.asserted_no_follow)
            self.assertEqual(unlinked, [("escape", 2)])
            self.assertEqual(removed_directories, [("nested", 1)])
            self.assertEqual(outside_marker.read_text(encoding="utf-8"), "preserve")

    def test_successful_posix_stage_cleanup_unregisters_owned_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = GhostFolder("same-name", temp_dir)
            stage_root = Path(temp_dir) / "mods"
            stage_root.mkdir()
            stage_path = folder._create_owned_directory(str(stage_root), prefix=".mod.stage-")
            removed: list[str] = []

            def fake_posix_remove(path, _ownership):
                os.rmdir(path)
                removed.append(path)
                return True

            with (
                patch.object(ghost_folder_module.sys, "platform", "linux"),
                patch.object(ghost_folder_module, "_open_owned_directory", return_value=42),
                patch.object(ghost_folder_module, "_close_directory_handle"),
                patch.object(
                    ghost_folder_module,
                    "_remove_posix_owned_directory",
                    side_effect=fake_posix_remove,
                ),
            ):
                self.assertTrue(folder._remove_owned_directory(stage_path))
                self.assertNotIn(stage_path, folder._owned_directories)
                self.assertTrue(folder.cleanup())

            self.assertEqual(removed.count(stage_path), 1)
            self.assertEqual(folder._owned_directories, {})
            self.assertFalse(Path(stage_path).exists())
            self.assertFalse(Path(folder.ghost_folder_path).exists())

    def test_successful_publication_with_stage_cleanup_failure_returns_pending_list(self) -> None:
        for scenario in ("persistent", "clears_on_outer_retry"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp_dir:
                snapshot = self._snapshot(temp_dir)
                download_root = Path(snapshot["paths"]["download_directory"])
                mods_root = Path(snapshot["paths"]["mods_directory"])
                created: list[GhostFolder] = []
                real_folder_type = GhostFolder
                batch_calls = 0

                def make_folder(*args, **kwargs):
                    folder = real_folder_type(*args, **kwargs)
                    original_remove = folder._remove_owned_directory
                    stage_attempts = 0

                    def controlled_remove(path):
                        nonlocal stage_attempts
                        if ".stage-" in os.path.basename(path):
                            stage_attempts += 1
                            if scenario == "persistent" or stage_attempts == 1:
                                return False
                        return original_remove(path)

                    folder._remove_owned_directory = controlled_remove
                    created.append(folder)
                    return folder

                def download_batch(**kwargs):
                    nonlocal batch_calls
                    batch_calls += 1
                    if scenario == "persistent" and batch_calls == 1:
                        return 84
                    content = (
                        Path(kwargs["ghost_folder_path"])
                        / "steamapps/workshop/content/107410"
                        / ("222" if scenario == "persistent" else "333")
                    )
                    content.mkdir(parents=True, exist_ok=True)
                    (content / "complete.txt").write_text("complete", encoding="utf-8")
                    return 0

                mods = ["111", "222"] if scenario == "persistent" else ["333"]
                guard_codes = ("ABCDE", "BCDEF", "CDEFG")
                with (
                    patch("main.steamcmd.mods_download.config.snapshot", return_value=snapshot),
                    patch("main.steamcmd.mods_download.delete_steamcmd_appcache"),
                    patch("main.steamcmd.mods_download.GhostFolder", side_effect=make_folder),
                    patch("main.steamcmd.mods_download.test_connection", return_value=True),
                    patch("main.steamcmd.mods_download.generate_steam_guard_code", side_effect=guard_codes),
                    patch("main.steamcmd.mods_download.steamcmd_download", side_effect=download_batch),
                    patch("main.steamcmd.mods_download.lowercase_addons_directory"),
                    patch("main.steamcmd.mods_download.sleep"),
                ):
                    result = download_mods(mods, "same-name", lim=1)

                installed_mod = "222" if scenario == "persistent" else "333"
                self.assertEqual(
                    (mods_root / installed_mod / "complete.txt").read_text(encoding="utf-8"),
                    "complete",
                )
                if scenario == "persistent":
                    self.assertEqual(result, ["111"])
                    self.assertTrue(result.cleanup_pending)
                    self.assertEqual(result.cleanup_reason, "filesystem_cleanup_pending")
                    self.assertFalse(getattr(result, "cleanup_uncertain", False))
                    self.assertTrue(list(mods_root.glob(".*.stage-*")))
                else:
                    self.assertIsNone(result)
                    self.assertFalse(list(mods_root.glob(".*.stage-*")))
                    self.assertEqual(created[0]._owned_directories, {})

    def test_primary_publication_exception_survives_stage_cleanup_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = GhostFolder("same-name", str(Path(temp_dir) / "downloads"))
            source = Path(folder.ghost_folder_path) / "steamapps/workshop/content/107410/123"
            source.mkdir(parents=True)
            (source / "mod.cpp").write_text("source", encoding="utf-8")
            mods_root = Path(temp_dir) / "mods"
            primary_failure = OSError("synthetic copy failure")

            with (
                patch("main.workaround.ghost_folder.shutil.copytree", side_effect=primary_failure),
                patch.object(folder, "_remove_owned_directory", return_value=False),
            ):
                with self.assertRaises(OSError) as raised:
                    folder.move_files(str(mods_root), "steamapps/workshop/content/107410")

            self.assertIs(raised.exception, primary_failure)

    def test_posix_parent_chain_checks_root_acl_before_accepting_ancestry(self) -> None:
        root_metadata = SimpleNamespace(
            st_dev=7,
            st_ino=99,
            st_uid=0,
            st_mode=stat.S_IFDIR | 0o755,
        )

        with (
            patch.object(ghost_folder_module.os.path, "abspath", return_value=ghost_folder_module.os.path.sep),
            patch.object(ghost_folder_module.os, "open", return_value=55),
            patch.object(ghost_folder_module.os, "fstat", return_value=root_metadata),
            patch.object(ghost_folder_module.os, "geteuid", return_value=1000, create=True),
            patch.object(
                ghost_folder_module.os,
                "listxattr",
                return_value=["system.posix_acl_access"],
                create=True,
            ),
            patch.object(ghost_folder_module.os, "close") as close_fd,
        ):
            self.assertIsNone(ghost_folder_module._open_posix_parent_chain("/", 7, 99))

        close_fd.assert_called_once_with(55)

    def test_posix_safe_root_is_accepted_by_parent_chain(self) -> None:
        root_metadata = SimpleNamespace(
            st_dev=7,
            st_ino=99,
            st_uid=0,
            st_mode=stat.S_IFDIR | 0o755,
        )

        with (
            patch.object(ghost_folder_module.os.path, "abspath", return_value=ghost_folder_module.os.path.sep),
            patch.object(ghost_folder_module.os, "open", return_value=56),
            patch.object(ghost_folder_module.os, "fstat", return_value=root_metadata),
            patch.object(ghost_folder_module.os, "geteuid", return_value=1000, create=True),
            patch.object(ghost_folder_module.os, "listxattr", return_value=[], create=True),
            patch.object(ghost_folder_module.os, "close"),
        ):
            descriptors = ghost_folder_module._open_posix_parent_chain("/", 7, 99)

        self.assertEqual(descriptors, [56])

    @staticmethod
    def _fake_posix_ownership(root: str) -> tuple[str, int, int, str, int, int]:
        return root, 7, 11, root, 7, 2

    def test_connection_drains_both_pipes(self) -> None:
        process = Mock(returncode=0)
        process.communicate.return_value = (b"verbose stdout", b"verbose stderr")
        redactor = SteamSecretRedactor(("fake-user", "fake-password", "ABCDE"))

        with patch("main.steamcmd.processes.subprocess.Popen", return_value=process):
            connected = test_connection(
                "/fake/steamcmd",
                "fake-user",
                "fake-password",
                "ABCDE",
                secret_redactor=redactor,
            )

        self.assertTrue(connected)
        process.communicate.assert_called_once_with(timeout=10)

    def test_completed_nonzero_login_returns_false(self) -> None:
        process = Mock(returncode=5)
        process.communicate.return_value = (b"login rejected", b"details")

        with patch("main.steamcmd.processes.subprocess.Popen", return_value=process):
            connected = test_connection(
                "/fake/steamcmd",
                "fake-user",
                "fake-password",
                "ABCDE",
                secret_redactor=SteamSecretRedactor(
                    ("fake-user", "fake-password", "ABCDE")
                ),
            )

        self.assertFalse(connected)
        process.communicate.assert_called_once_with(timeout=10)

    def test_connection_timeout_reaps_owned_tree_and_keeps_diagnostics_safe(self) -> None:
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired(
            ["bash", "+login", "fake-user", "fake-password"], timeout=10
        )
        redactor = SteamSecretRedactor(("fake-user", "fake-password", "ABCDE"))

        with (
            patch("main.steamcmd.processes.subprocess.Popen", return_value=process),
            patch("main.steamcmd.mods_download.terminate_owned_process_tree", return_value=True) as cleanup,
        ):
            with self.assertRaises(SteamCommandError) as raised:
                test_connection(
                    "/fake/steamcmd",
                    "fake-user",
                    "fake-password",
                    "ABCDE",
                    secret_redactor=redactor,
                )

        cleanup.assert_called_once_with(process)
        self.assertIn("TimeoutExpired", raised.exception.diagnostic)
        self.assertNotIn("fake-user", raised.exception.diagnostic)
        self.assertNotIn("fake-password", raised.exception.diagnostic)
        process.communicate.assert_called_once_with(timeout=10)

    def test_timeout_escalates_and_reaps_owned_child_tree(self) -> None:
        child = _FakeOwnedProcess(
            pid=43,
            create_time=15.0,
            alive=True,
            terminate_exits=False,
        )
        child.wait.side_effect = [psutil.TimeoutExpired(0.01), None]
        root = Mock(pid=42, returncode=None)
        root.poll.side_effect = [None, None, 1]
        root.wait.side_effect = [subprocess.TimeoutExpired("owned-process", 1), 1]
        identities = [processes.ProcessIdentity(child, pid=43, create_time=15.0)]

        with (
            patch("main.steamcmd.processes.capture_owned_processes", return_value=identities),
            patch("main.steamcmd.processes.psutil.Process", return_value=child),
        ):
            cleaned = processes.terminate_owned_process_tree(root, grace_seconds=0.01)

        self.assertTrue(cleaned)
        child.terminate.assert_called_once_with()
        child.kill.assert_called_once_with()
        self.assertEqual(child.wait.call_count, 2)
        root.terminate.assert_called_once_with()
        root.kill.assert_called_once_with()
        root.wait.assert_called_with(timeout=0.01)

    def test_pid_reuse_does_not_signal_unrelated_process(self) -> None:
        unrelated = _FakeOwnedProcess(pid=43, create_time=99.0, alive=True)
        root = Mock(pid=42, returncode=None)
        root.poll.side_effect = [None, 0]
        root.wait.return_value = 0
        identity = processes.ProcessIdentity(unrelated, pid=43, create_time=15.0)

        with (
            patch("main.steamcmd.processes.capture_owned_processes", return_value=[identity]),
            patch("main.steamcmd.processes.psutil.Process", return_value=unrelated),
        ):
            cleaned = processes.terminate_owned_process_tree(root, grace_seconds=0.01)

        self.assertTrue(cleaned)
        unrelated.terminate.assert_not_called()
        unrelated.kill.assert_not_called()
        root.terminate.assert_called_once_with()

    def test_recycled_posix_group_is_not_adopted_by_real_capture_logic(self) -> None:
        old_root = _FakeOwnedProcess(pid=42, create_time=10.0, alive=False)
        recycled_group_member = _FakeOwnedProcess(pid=999, create_time=50.0, alive=True)
        recycled_group_member.info = {"pid": 999}
        process = Mock(pid=42, returncode=0)
        process._steamcmd_process_identity = processes.ProcessIdentity(
            old_root,
            pid=42,
            create_time=10.0,
        )
        process._steamcmd_process_group = 42

        with (
            patch.object(processes.os, "name", "posix"),
            patch("main.steamcmd.processes.psutil.process_iter", return_value=[recycled_group_member]),
            patch("main.steamcmd.processes.os.getpgid", return_value=42, create=True),
        ):
            captured = processes.capture_owned_processes(process)

        self.assertFalse(any(identity.pid == recycled_group_member.pid for identity in captured))
        self.assertTrue(process._steamcmd_cleanup_incomplete)

    def test_windows_late_reparented_child_makes_cleanup_unconfirmed(self) -> None:
        root = Mock(pid=42, returncode=0)
        root.poll.return_value = 0
        root.wait.return_value = 0
        root._steamcmd_process_tree_contained = False
        root._steamcmd_process_identity = None
        root._steamcmd_owned_identities = []
        late_child = _FakeOwnedProcess(pid=43, create_time=15.0, alive=True)

        with patch.object(processes.os, "name", "nt"):
            cleaned = processes.terminate_owned_process_tree(root, grace_seconds=0.01)

        self.assertFalse(cleaned)
        self.assertTrue(late_child.is_running())
        self.assertTrue(root._steamcmd_cleanup_incomplete)

    def test_publication_preserves_existing_mod_and_renames_complete_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = GhostFolder("same-name", str(root / "downloads"))
            source_root = Path(folder.ghost_folder_path) / "steamapps/workshop/content/107410"
            existing_source = source_root / "123"
            existing_source.mkdir(parents=True)
            (existing_source / "new.txt").write_text("new", encoding="utf-8")
            new_source = source_root / "456"
            new_source.mkdir()
            (new_source / "complete.txt").write_text("complete", encoding="utf-8")
            mods_root = root / "mods"
            existing_destination = mods_root / "123"
            existing_destination.mkdir(parents=True)
            (existing_destination / "installed.txt").write_text("keep", encoding="utf-8")

            folder.move_files(str(mods_root), "steamapps/workshop/content/107410")

            self.assertEqual(
                {path.name for path in existing_destination.iterdir()},
                {"installed.txt"},
            )
            self.assertEqual(
                (mods_root / "456" / "complete.txt").read_text(encoding="utf-8"),
                "complete",
            )
            self.assertEqual(list(mods_root.glob(".*.stage-*")), [])
            folder.cleanup()

    def test_failed_copy_or_rename_never_exposes_partial_destination(self) -> None:
        for failed_action in ("copy", "rename"):
            with self.subTest(failed_action=failed_action), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                folder = GhostFolder("same-name", str(root / "downloads"))
                source = Path(folder.ghost_folder_path) / "steamapps/workshop/content/107410/789"
                source.mkdir(parents=True)
                (source / "mod.cpp").write_text("complete source", encoding="utf-8")
                mods_root = root / "mods"
                existing_source = Path(folder.ghost_folder_path) / "steamapps/workshop/content/107410/123"
                existing_source.mkdir()
                (existing_source / "downloaded.txt").write_text("new version", encoding="utf-8")
                existing_destination = mods_root / "123"
                existing_destination.mkdir(parents=True)
                (existing_destination / "installed.txt").write_text("keep", encoding="utf-8")

                if failed_action == "copy":
                    failure_patch = patch(
                        "main.workaround.ghost_folder.shutil.copytree",
                        side_effect=OSError("fake copy failure"),
                    )
                else:
                    failure_patch = patch(
                        "main.workaround.ghost_folder._atomic_publish",
                        side_effect=OSError("fake rename failure"),
                    )

                with failure_patch, self.assertRaises(OSError):
                    folder.move_files(str(mods_root), "steamapps/workshop/content/107410")

                self.assertEqual(
                    {path.name for path in existing_destination.iterdir()},
                    {"installed.txt"},
                )
                self.assertFalse((mods_root / "789").exists())
                self.assertEqual(list(mods_root.glob(".*.stage-*")), [])
                folder.cleanup()

    def test_atomic_publication_preserves_empty_destination_created_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = GhostFolder("same-name", str(root / "downloads"))
            source = Path(folder.ghost_folder_path) / "steamapps/workshop/content/107410/123"
            source.mkdir(parents=True)
            (source / "complete.txt").write_text("complete", encoding="utf-8")
            mods_root = root / "mods"
            mods_root.mkdir()
            empty_install = mods_root / "123"
            real_publish = _atomic_publish

            def race_install(staged_path: str, destination_path: str) -> None:
                empty_install.mkdir()
                real_publish(staged_path, destination_path)

            with patch("main.workaround.ghost_folder._atomic_publish", side_effect=race_install):
                folder.move_files(str(mods_root), "steamapps/workshop/content/107410")

            self.assertTrue(empty_install.is_dir())
            self.assertEqual(list(empty_install.iterdir()), [])
            self.assertEqual(list(mods_root.glob(".*.stage-*")), [])
            folder.cleanup()

    def test_posix_without_atomic_noreplace_fails_closed_on_raced_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "stage" / "payload"
            source.parent.mkdir()
            source.mkdir()
            destination = root / "installed"
            destination.mkdir()
            (destination / "winner.txt").write_text("winner", encoding="utf-8")
            winner_inode = destination.stat().st_ino
            rename = Mock()
            with (
                patch.object(ghost_folder_module.sys, "platform", "darwin"),
                patch.object(ghost_folder_module.os, "rename", rename),
            ):
                with self.assertRaises(OSError):
                    _atomic_publish(str(source), str(destination))

            rename.assert_not_called()
            self.assertEqual(
                (destination / "winner.txt").read_text(encoding="utf-8"),
                "winner",
            )
            self.assertEqual(destination.stat().st_ino, winner_inode)
            self.assertTrue(source.is_dir())

            race_destination = root / "raced-install"
            raced_winner_inode: list[int] = []

            def atomic_race(*_args) -> int:
                race_destination.mkdir()
                (race_destination / "winner.txt").write_text("winner", encoding="utf-8")
                raced_winner_inode.append(race_destination.stat().st_ino)
                ctypes.set_errno(errno.EEXIST)
                return -1

            fake_libc = Mock()
            fake_libc.renameat2 = Mock(side_effect=atomic_race)
            with (
                patch.object(ghost_folder_module.sys, "platform", "linux"),
                patch.object(ghost_folder_module.ctypes, "CDLL", return_value=fake_libc),
            ):
                with self.assertRaises(FileExistsError):
                    _atomic_publish(str(source), str(race_destination))

            self.assertEqual(len(raced_winner_inode), 1)
            self.assertEqual(
                (race_destination / "winner.txt").read_text(encoding="utf-8"),
                "winner",
            )
            self.assertEqual(race_destination.stat().st_ino, raced_winner_inode[0])
            self.assertTrue(source.is_dir())

    def test_workspace_cleanup_rejects_path_replaced_before_handle_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = GhostFolder("same-name", temp_dir)
            owned_path = Path(folder.ghost_folder_path)
            replacement_sibling = Path(temp_dir) / "displaced-owned-workspace"
            replacement_file = owned_path / "replacement.txt"
            swapped = False
            open_directory = getattr(ghost_folder_module, "_open_owned_directory", None)

            def swap_then_open(path, *args, **kwargs):
                nonlocal swapped
                candidate = Path(path)
                if not candidate.is_absolute() and kwargs.get("dir_fd") is not None:
                    candidate = owned_path.parent / candidate
                if candidate == owned_path and not swapped:
                    owned_path.rename(replacement_sibling)
                    owned_path.mkdir()
                    replacement_file.write_text("preserve", encoding="utf-8")
                    swapped = True
                if open_directory is None:
                    return None
                return open_directory(path, *args, **kwargs)

            with patch.object(
                ghost_folder_module,
                "_open_owned_directory",
                side_effect=swap_then_open,
                create=True,
            ):
                cleaned = folder.cleanup()

            self.assertTrue(swapped)
            self.assertFalse(cleaned)
            self.assertEqual(replacement_file.read_text(encoding="utf-8"), "preserve")

    def test_destination_stage_cleanup_rejects_replaced_stage_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = GhostFolder("same-name", str(root / "downloads"))
            destination_root = root / "mods"
            destination_root.mkdir()
            stage_path = Path(
                folder._create_owned_directory(
                    str(destination_root),
                    prefix=".123.stage-",
                )
            )
            displaced_stage = destination_root / "displaced-stage"
            marker = stage_path / "replacement.txt"
            swapped = False
            open_directory = getattr(ghost_folder_module, "_open_owned_directory", None)

            def swap_then_open(path, *args, **kwargs):
                nonlocal swapped
                candidate = Path(path)
                if not candidate.is_absolute() and kwargs.get("dir_fd") is not None:
                    candidate = destination_root / candidate
                if candidate == stage_path and not swapped:
                    stage_path.rename(displaced_stage)
                    stage_path.mkdir()
                    marker.write_text("preserve", encoding="utf-8")
                    swapped = True
                if open_directory is None:
                    return None
                return open_directory(path, *args, **kwargs)

            with patch.object(
                ghost_folder_module,
                "_open_owned_directory",
                side_effect=swap_then_open,
                create=True,
            ):
                cleaned = folder.cleanup()

            self.assertTrue(swapped)
            self.assertFalse(cleaned)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_download_uses_one_snapshot_for_paths_credentials_and_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old = self._snapshot(temp_dir)
            newer = {
                "paths": {
                    "steamcmd": "/other/steamcmd",
                    "download_directory": "/other/downloads",
                    "mods_directory": "/other/mods",
                },
                "steam_auth": {
                    "username": "new-user",
                    "password": "new-password",
                    "shared_secret": "bmV3LXNlY3JldA==",
                },
            }
            config_snapshots = [old, newer]
            observed: dict[str, object] = {}
            ghost = Mock(ghost_folder_path="/fake/workspace")
            redactors: list[SteamSecretRedactor] = []

            def observe_download(**kwargs):
                observed.update(kwargs)
                redactors.append(kwargs["secret_redactor"])
                return 1

            with (
                patch("main.steamcmd.mods_download.config.snapshot", side_effect=config_snapshots) as snapshot_call,
                patch("main.steamcmd.mods_download.delete_steamcmd_appcache") as delete_cache,
                patch("main.steamcmd.mods_download.GhostFolder", return_value=ghost),
                patch(
                    "main.steamcmd.mods_download.generate_steam_guard_code",
                    side_effect=("ABCDE", "BCDEF", "CDEFG"),
                ) as make_guard,
                patch("main.steamcmd.mods_download.sleep"),
                patch("main.steamcmd.mods_download.test_connection", return_value=True) as test_connect,
                patch("main.steamcmd.mods_download.steamcmd_download", side_effect=observe_download),
                patch("main.steamcmd.mods_download.download_fallback", return_value=None) as fallback,
                patch("main.steamcmd.mods_download.lowercase_addons_directory"),
            ):
                self.assertIsNone(download_mods(["123"], "same-name", lim=1))

            self.assertEqual(snapshot_call.call_count, 1)
            delete_cache.assert_called_once_with(old["paths"]["steamcmd"], unittest.mock.ANY)
            test_connect.assert_called_once()
            self.assertEqual(test_connect.call_args.args[:3], (
                old["paths"]["steamcmd"],
                old["steam_auth"]["username"],
                old["steam_auth"]["password"],
            ))
            self.assertTrue(
                all(
                    guard_call.args[0] == old["steam_auth"]["shared_secret"]
                    for guard_call in make_guard.call_args_list
                )
            )
            self.assertEqual(observed["login"], old["steam_auth"]["username"])
            self.assertEqual(observed["password"], old["steam_auth"]["password"])
            self.assertEqual(observed["steamcmd_dir"], old["paths"]["steamcmd"])
            self.assertEqual(fallback.call_args.args[:5], (
                "123",
                107410,
                old["steam_auth"]["username"],
                old["steam_auth"]["password"],
                old["paths"]["steamcmd"],
            ))
            self.assertEqual(fallback.call_args.kwargs["config_snapshot"], old)
            self.assertIn("ABCDE", redactors[0]._secret_spellings())
            ghost.move_files.assert_called_once_with(
                destination_path=old["paths"]["mods_directory"],
                internal_path="steamapps/workshop/content/107410",
            )
            ghost.cleanup.assert_called_once_with()

    @staticmethod
    def _snapshot(temp_dir: str, *, download_root: Path | None = None) -> dict:
        return {
            "paths": {
                "steamcmd": str(Path(temp_dir) / "steamcmd"),
                "download_directory": str(download_root or Path(temp_dir) / "downloads"),
                "mods_directory": str(Path(temp_dir) / "mods"),
            },
            "steam_auth": {
                "username": "fake-user",
                "password": "fake-password",
                "shared_secret": "ZmFrZS1zZWNyZXQ=",
            },
        }


class _FakeOwnedProcess:
    def __init__(
        self,
        *,
        pid: int,
        create_time: float,
        alive: bool,
        terminate_exits: bool = True,
    ) -> None:
        self.pid = pid
        self._create_time = create_time
        self._alive = alive
        self._terminate_exits = terminate_exits
        self.terminate = Mock(side_effect=self._terminate)
        self.kill = Mock(side_effect=self._kill)
        self.wait = Mock(side_effect=self._wait)

    def create_time(self) -> float:
        return self._create_time

    def is_running(self) -> bool:
        return self._alive

    def _terminate(self) -> None:
        if self._terminate_exits:
            self._alive = False

    def _kill(self) -> None:
        self._alive = False

    def _wait(self, timeout=None):
        return None


class _FakePosixDirectoryHandle:
    def __init__(self, identity: tuple[int, int]) -> None:
        self.identity = identity


class _FakePosixCleanupOperations:
    def __init__(
        self,
        *,
        source_identity: tuple[int, int],
        parent_safe: bool = True,
        parent_identity: tuple[int, int] = (7, 2),
        quarantine_safe: bool = True,
        rename_error: OSError | None = None,
    ) -> None:
        self.parent_fd = object()
        self.quarantine_fd = object()
        self.source_identity = source_identity
        self.parent_safe = parent_safe
        self.actual_parent_identity = parent_identity
        self.quarantine_safe = quarantine_safe
        self.rename_error = rename_error
        self.quarantine_name = ".steam-cleanup-test"
        self.claim_name = ".claimed-test"
        self.quarantine_identity = (7, 90)
        self.quarantine_path_identity = self.quarantine_identity
        self.parent_entries: dict[str, tuple[int, int]] = {"workspace": source_identity}
        self.claims: dict[str, tuple[int, int]] = {}
        self.cleaned_identities: list[tuple[int, int]] = []
        self.removed_names: list[tuple[object, str]] = []
        self.rename_calls = 0
        self.on_claim = None
        self.on_remove_tree = None

    def open_parent(self, _path: str, device: int, inode: int):
        if not self.parent_safe or (device, inode) != self.actual_parent_identity:
            return None
        return [self.parent_fd]

    def create_quarantine(self, _parent_fd, device: int):
        if not self.quarantine_safe or device != 7:
            return None
        self.parent_entries[self.quarantine_name] = self.quarantine_path_identity
        return self.quarantine_name, self.quarantine_fd, self.quarantine_identity

    def identity_at(self, directory_fd, name: str):
        if directory_fd is self.parent_fd:
            return self.parent_entries.get(name)
        if directory_fd is self.quarantine_fd:
            return self.claims.get(name)
        return None

    def rename_noreplace(self, _source_fd, source_name: str, _target_fd, target_name: str):
        self.rename_calls += 1
        if self.rename_error is not None:
            raise self.rename_error
        self.claim_name = target_name
        identity = self.parent_entries.pop(source_name)
        self.claims[target_name] = identity
        if self.on_claim is not None:
            self.on_claim()

    def open_directory(self, _parent_fd, name: str, device: int, inode: int):
        identity = self.claims.get(name)
        if identity != (device, inode):
            return None
        return _FakePosixDirectoryHandle(identity)

    @staticmethod
    def identity_fd(directory_handle):
        return directory_handle.identity

    def remove_tree(self, directory_handle) -> bool:
        self.cleaned_identities.append(directory_handle.identity)
        if self.on_remove_tree is not None:
            self.on_remove_tree()
        return True

    def rmdir(self, directory_fd, name: str) -> None:
        self.removed_names.append((directory_fd, name))
        if directory_fd is self.parent_fd:
            self.parent_entries.pop(name, None)
        elif directory_fd is self.quarantine_fd:
            self.claims.pop(name, None)

    @staticmethod
    def close(_descriptor) -> None:
        pass

    @staticmethod
    def close_parent(_descriptors) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
