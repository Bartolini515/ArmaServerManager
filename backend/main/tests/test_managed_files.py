import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main.modpreset.preset_extraction import preset_parser
from main.instance_files import (
    CleanupPending,
    ManagedFileJournal,
    ManagedFileRollbackError,
    ManagedFileRoot,
    ManagedFileSetupError,
    ManagedFileStateError,
)


class ManagedFileJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        self.public_root = self.base / "media"
        self.private_root = self.base / "private"
        self.outside_root = self.base / "outside"
        self.public_root.mkdir()
        self.private_root.mkdir()
        self.outside_root.mkdir()
        (self.public_root / "user_17" / "presets").mkdir(parents=True)
        (self.public_root / "user_17" / "logs").mkdir(parents=True)
        self.root = ManagedFileRoot(
            trusted_root=self.public_root,
            private_root=self.private_root,
            allowed_prefixes=("user_17/presets/", "user_17/logs/"),
        )

    def journal(self):
        return ManagedFileJournal({"media": self.root})

    def target(self, relative_name="user_17/presets/server.html"):
        return self.public_root.joinpath(*relative_name.split("/"))

    @staticmethod
    def write_bytes(path, content):
        Path(path).write_bytes(content)

    def test_stage_is_registered_before_writer_and_partial_write_is_removed(self):
        target = self.target()
        observed = []

        def fail_after_partial_write(stage_path):
            stage_path = Path(stage_path)
            observed.append(stage_path)
            self.assertEqual(stage_path.parent, self.private_root)
            self.assertTrue(stage_path.is_file())
            stage_path.write_bytes(b"partial upload")
            raise OSError("simulated upload failure")

        with self.assertRaisesRegex(OSError, "simulated upload failure"):
            with self.journal() as journal:
                journal.stage("media", "user_17/presets/server.html", fail_after_partial_write)

        self.assertEqual(len(observed), 1)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_parser_extension_semantics_are_preserved_in_private_stages(self):
        html_content = (
            '<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=123456">'
        ).encode("utf-8")
        cases = (
            ("user_17/presets/server.html", ["123456"], True),
            ("user_17/presets/.html", ["123456"], True),
            ("user_17/presets/server.HTML", [], False),
            ("user_17/presets/server.htm", [], False),
        )

        for relative_name, expected_ids, should_end_with_html in cases:
            with self.subTest(relative_name=relative_name):
                actual_ids = []
                stage_names = []

                def write_and_parse(stage_path):
                    stage_path.write_bytes(html_content)
                    stage_names.append(stage_path.name)
                    actual_ids.extend(preset_parser(str(stage_path)))

                with self.journal() as journal:
                    journal.stage("media", relative_name, write_and_parse)

                self.assertEqual(actual_ids, expected_ids)
                self.assertEqual(stage_names[0].endswith(".html"), should_end_with_html)
                self.assertEqual(list(self.private_root.iterdir()), [])

    def test_caught_writer_failure_cannot_publish_partial_stage(self):
        target = self.target()

        with self.journal() as journal:
            try:
                journal.stage(
                    "media",
                    "user_17/presets/server.html",
                    lambda path: self._write_then_fail(path),
                )
            except OSError:
                pass
            with self.assertRaises(ManagedFileStateError):
                journal.publish()

        self.assertFalse(target.exists())
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_unowned_preexisting_target_is_never_clobbered(self):
        target = self.target()
        target.write_bytes(b"caller-owned bytes")
        writer_called = []

        with self.assertRaises(Exception):
            with self.journal() as journal:
                journal.stage(
                    "media",
                    "user_17/presets/server.html",
                    lambda path: writer_called.append(path),
                )

        self.assertEqual(target.read_bytes(), b"caller-owned bytes")
        self.assertEqual(writer_called, [])
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_publish_failure_rolls_back_earlier_new_publication(self):
        first_name = "user_17/presets/first.html"
        second_name = "user_17/presets/second.html"
        first = self.target(first_name)
        second = self.target(second_name)
        original_link = os.link

        def fail_second_link(source, destination, *args, **kwargs):
            if Path(destination) == second:
                raise OSError("simulated publication failure")
            return original_link(source, destination, *args, **kwargs)

        with self.assertRaises(Exception):
            with self.journal() as journal:
                journal.stage("media", first_name, lambda path: self.write_bytes(path, b"first"))
                journal.stage("media", second_name, lambda path: self.write_bytes(path, b"second"))
                with patch("main.instance_files.os.link", side_effect=fail_second_link):
                    journal.publish()

        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_replacement_rollback_restores_old_bytes_and_metadata(self):
        target = self.target()
        target.write_bytes(b"old preset bytes")
        if os.name == "posix":
            target.chmod(0o640)
        before = target.stat()

        with self.assertRaisesRegex(RuntimeError, "force metadata rollback"):
            with self.journal() as journal:
                journal.stage(
                    "media",
                    "user_17/presets/server.html",
                    lambda path: self.write_bytes(path, b"replacement bytes"),
                    replace=True,
                )
                journal.publish()
                self.assertEqual(target.read_bytes(), b"replacement bytes")
                raise RuntimeError("force metadata rollback")

        after = target.stat()
        self.assertEqual(target.read_bytes(), b"old preset bytes")
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
            self.assertEqual(after.st_ino, before.st_ino)
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_backup_link_raising_after_creation_is_registered_and_rolled_back(self):
        target = self.target()
        target.write_bytes(b"old bytes")
        original_link = os.link

        def link_then_fail_backup(source, destination, *args, **kwargs):
            original_link(source, destination, *args, **kwargs)
            if "asm-backup-" in str(destination):
                raise OSError("simulated ambiguous backup creation")

        with self.assertRaises(Exception):
            with self.journal() as journal:
                journal.stage(
                    "media",
                    "user_17/presets/server.html",
                    lambda path: self.write_bytes(path, b"new bytes"),
                    replace=True,
                )
                with patch("main.instance_files.os.link", side_effect=link_then_fail_backup):
                    journal.publish()

        self.assertEqual(target.read_bytes(), b"old bytes")
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_ambiguous_backup_link_with_unavailable_probe_preserves_private_state(self):
        target = self.target()
        target.write_bytes(b"old backup bytes")
        original_link = os.link
        original_lstat = os.lstat
        ambiguous_destination = []

        def link_then_fail_backup(source, destination, *args, **kwargs):
            original_link(source, destination, *args, **kwargs)
            if "asm-backup-" in str(destination):
                ambiguous_destination.append(Path(destination))
                raise OSError("simulated post-link failure")

        def fail_backup_probe(path, *args, **kwargs):
            if ambiguous_destination and Path(path) == ambiguous_destination[0]:
                raise PermissionError("simulated backup identity probe failure")
            return original_lstat(path, *args, **kwargs)

        with self.assertRaises(ManagedFileRollbackError) as caught:
            with patch("main.instance_files.os.link", side_effect=link_then_fail_backup):
                with patch("main.instance_files.os.lstat", side_effect=fail_backup_probe):
                    with self.journal() as journal:
                        journal.stage(
                            "media",
                            "user_17/presets/server.html",
                            lambda path: self.write_bytes(path, b"new stage bytes"),
                            replace=True,
                        )
                        journal.publish()

        error = caught.exception
        self.assertEqual(error.code, "rollback_uncertain")
        self.assertEqual(target.read_bytes(), b"old backup bytes")
        self.assertEqual(len(ambiguous_destination), 1)
        backup_path = ambiguous_destination[0]
        self.assertTrue(backup_path.is_file())
        self.assertEqual(backup_path.read_bytes(), b"old backup bytes")
        self.assertIn(backup_path, error.unresolved_paths)
        self.assertTrue(all(Path(path).is_file() for path in error.repair_evidence))

    def test_ambiguous_public_link_with_unavailable_probe_keeps_stage_evidence(self):
        target = self.target()
        original_link = os.link
        original_lstat = os.lstat
        ambiguous_destination = []

        def link_then_fail_public(source, destination, *args, **kwargs):
            original_link(source, destination, *args, **kwargs)
            if Path(destination) == target:
                ambiguous_destination.append(Path(destination))
                raise OSError("simulated post-link failure")

        def fail_public_probe(path, *args, **kwargs):
            if ambiguous_destination and Path(path) == target:
                raise PermissionError("simulated target identity probe failure")
            return original_lstat(path, *args, **kwargs)

        with self.assertRaises(ManagedFileRollbackError) as caught:
            with patch("main.instance_files.os.link", side_effect=link_then_fail_public):
                with patch("main.instance_files.os.lstat", side_effect=fail_public_probe):
                    with self.journal() as journal:
                        journal.stage(
                            "media",
                            "user_17/presets/server.html",
                            lambda path: self.write_bytes(path, b"published stage bytes"),
                        )
                        journal.publish()

        error = caught.exception
        self.assertEqual(error.code, "rollback_uncertain")
        self.assertEqual(target.read_bytes(), b"published stage bytes")
        self.assertIn(target, error.unresolved_paths)
        self.assertTrue(error.repair_evidence)
        self.assertTrue(all(Path(path).is_file() for path in error.repair_evidence))
        self.assertIn(b"published stage bytes", {Path(path).read_bytes() for path in error.repair_evidence})

    def test_deletion_rollback_restores_file(self):
        target = self.target("user_17/logs/instance_17_server.txt")
        target.write_bytes(b"log bytes")
        before = target.stat()

        with self.assertRaisesRegex(RuntimeError, "force deletion rollback"):
            with self.journal() as journal:
                journal.remove("media", "user_17/logs/instance_17_server.txt")
                journal.publish()
                self.assertFalse(target.exists())
                raise RuntimeError("force deletion rollback")

        self.assertEqual(target.read_bytes(), b"log bytes")
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
        if os.name == "posix":
            self.assertEqual(target.stat().st_ino, before.st_ino)
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_postcommit_backup_cleanup_failure_reports_pending_without_rollback(self):
        target = self.target()
        target.write_bytes(b"old bytes")
        journal = self.journal()
        journal.__enter__()
        journal.stage(
            "media",
            "user_17/presets/server.html",
            lambda path: self.write_bytes(path, b"committed bytes"),
            replace=True,
        )
        journal.publish()
        journal.mark_committed()
        original_unlink = os.unlink

        def fail_backup_cleanup(path, *args, **kwargs):
            if "asm-backup-" in str(path):
                raise PermissionError("simulated private cleanup failure")
            return original_unlink(path, *args, **kwargs)

        with patch("main.instance_files.os.unlink", side_effect=fail_backup_cleanup):
            pending = journal.finalize()
        journal.__exit__(None, None, None)

        self.assertIsInstance(pending, CleanupPending)
        self.assertTrue(pending.pending)
        self.assertEqual(pending.reason, "private_cleanup_failed")
        self.assertEqual(target.read_bytes(), b"committed bytes")
        leftovers = list(self.private_root.iterdir())
        self.assertTrue(any("asm-backup-" in path.name for path in leftovers))

    def test_traversal_names_are_rejected_before_writer_runs(self):
        writer_called = []
        for unsafe_name in (
            "../outside/escape.html",
            "user_17/presets/../../escape.html",
            "user_17\\presets\\escape.html",
            "/absolute/escape.html",
            "C:/absolute/escape.html",
        ):
            with self.subTest(name=unsafe_name):
                with self.assertRaises(Exception):
                    with self.journal() as journal:
                        journal.stage("media", unsafe_name, lambda path: writer_called.append(path))
        self.assertEqual(writer_called, [])
        self.assertEqual(list(self.outside_root.iterdir()), [])
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_symlink_component_and_leaf_are_rejected_without_chasing(self):
        outside = self.outside_root / "target.html"
        outside.write_bytes(b"outside bytes")
        link_dir = self.public_root / "user_17" / "presets" / "linked-dir"
        try:
            link_dir.symlink_to(self.outside_root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("This platform does not permit creating a symlink")

        with self.assertRaises(Exception):
            with self.journal() as journal:
                journal.stage(
                    "media",
                    "user_17/presets/linked-dir/escape.html",
                    lambda path: self.write_bytes(path, b"bad"),
                )
        self.assertEqual(outside.read_bytes(), b"outside bytes")

        link_dir.unlink()
        link_leaf = self.public_root / "user_17" / "presets" / "linked.html"
        link_leaf.symlink_to(outside)
        with self.assertRaises(Exception):
            with self.journal() as journal:
                journal.remove("media", "user_17/presets/linked.html")
        self.assertEqual(outside.read_bytes(), b"outside bytes")
        self.assertTrue(link_leaf.is_symlink())

    def test_private_root_must_be_outside_trusted_public_root(self):
        nested_private_root = self.public_root / ".private"
        nested_private_root.mkdir()

        with self.assertRaises(Exception):
            ManagedFileRoot(
                trusted_root=self.public_root,
                private_root=nested_private_root,
                allowed_prefixes=("user_17/presets/",),
            )

    def test_rollback_uncertainty_uses_static_error_and_keeps_private_evidence(self):
        target = self.target()
        target.write_bytes(b"old preset bytes")
        original_unlink = os.unlink
        rollback_started = False

        def fail_target_cleanup(path, *args, **kwargs):
            if rollback_started and Path(path) == target:
                raise PermissionError("simulated target cleanup failure")
            return original_unlink(path, *args, **kwargs)

        with patch("main.instance_files.os.unlink", side_effect=fail_target_cleanup):
            with self.assertRaises(ManagedFileRollbackError) as caught:
                with self.journal() as journal:
                    journal.stage(
                        "media",
                        "user_17/presets/server.html",
                        lambda path: self.write_bytes(path, b"partial published file"),
                        replace=True,
                    )
                    journal.publish()
                    rollback_started = True
                    raise RuntimeError("force uncertain rollback")

        error = caught.exception
        self.assertEqual(error.code, "rollback_uncertain")
        self.assertNotIn(str(self.base), str(error))
        self.assertTrue(error.repair_evidence)
        self.assertTrue(all(Path(path).parent == self.private_root for path in error.repair_evidence))
        self.assertEqual(target.read_bytes(), b"partial published file")
        evidence = {Path(path): Path(path).read_bytes() for path in error.repair_evidence}
        self.assertEqual(set(evidence.values()), {b"partial published file", b"old preset bytes"})
        self.assertTrue(all(path.is_file() for path in error.repair_evidence))

    def test_stage_setup_identity_failure_does_not_leave_an_unregistered_file(self):
        journal = self.journal()
        observed_registrations = []

        def fail_fstat(descriptor):
            registrations = [
                operation.stage_path
                for operation in journal._operations
                if operation.stage_path is not None and operation.stage_path.is_file()
            ]
            observed_registrations.append(registrations)
            raise OSError("simulated identity setup failure")

        with self.assertRaises(Exception):
            with journal:
                with patch("main.instance_files.os.fstat", side_effect=fail_fstat):
                    journal.stage(
                        "media",
                        "user_17/presets/server.html",
                        lambda path: self.write_bytes(path, b"never reached"),
                    )

        self.assertEqual(len(observed_registrations), 1)
        self.assertEqual(len(observed_registrations[0]), 1)
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_stage_close_failure_is_reported_without_retry_or_orphan(self):
        journal = self.journal()
        original_close = os.close
        close_calls = []

        def close_then_fail(descriptor):
            close_calls.append(descriptor)
            original_close(descriptor)
            raise OSError("simulated ambiguous close failure")

        with self.assertRaises(ManagedFileSetupError) as caught:
            with journal:
                with patch("main.instance_files.os.close", side_effect=close_then_fail):
                    journal.stage(
                        "media",
                        "user_17/presets/server.html",
                        lambda path: self.write_bytes(path, b"never reached"),
                    )

        self.assertEqual(len(close_calls), 1)
        self.assertTrue(caught.exception.descriptor_uncertain)
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_stage_final_identity_check_failure_is_compensated(self):
        journal = self.journal()
        identity_calls = []

        def fail_final_identity(path):
            identity_calls.append(Path(path))
            raise OSError("simulated final identity check failure")

        with self.assertRaises(Exception):
            with journal:
                with patch("main.instance_files._regular_file_identity", side_effect=fail_final_identity):
                    journal.stage(
                        "media",
                        "user_17/presets/server.html",
                        lambda path: self.write_bytes(path, b"never reached"),
                    )

        self.assertEqual(len(identity_calls), 1)
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_reparse_point_attribute_is_treated_as_link(self):
        class ReparseStat:
            st_mode = stat.S_IFDIR | 0o700
            st_file_attributes = 0x400

        reparse_parent = self.public_root / "user_17" / "presets"
        original_lstat = os.lstat

        def return_reparse_parent(path):
            if Path(path) == reparse_parent:
                return ReparseStat()
            return original_lstat(path)

        with patch("main.instance_files.os.lstat", side_effect=return_reparse_parent):
            with self.assertRaises(Exception):
                with self.journal() as journal:
                    journal.stage(
                        "media",
                        "user_17/presets/server.html",
                        lambda path: self.write_bytes(path, b"bad"),
                    )

    @staticmethod
    def _write_then_fail(path):
        Path(path).write_bytes(b"partial")
        raise OSError("simulated writer failure")


if __name__ == "__main__":
    unittest.main()
