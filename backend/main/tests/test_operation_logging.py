import re
import tempfile
import unittest
from pathlib import Path

from main.steamcmd.steam_auth import generate_steam_guard_code
from main.utils.operation_logging import operation_loggers


class OperationLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.logs_directory = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_writes_individual_operation_file_with_context(self) -> None:
        with operation_loggers(self.logs_directory, "alice", ("create",)) as loggers:
            logger = loggers["create"]
            underlying_logger = logger.logger
            logger.info("Created instance")
            handlers = tuple(underlying_logger.handlers)

        files = list(self.logs_directory.glob("log_create_*.txt"))
        self.assertEqual(len(files), 1)
        self.assertEqual(len(underlying_logger.handlers), 0)
        self.assertTrue(all(handler.stream is None for handler in handlers))
        self.assertRegex(
            files[0].read_text(encoding="utf-8"),
            r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2} - INFO \[create\] \[user=alice\] - Created instance\n",
        )
        self.assertEqual(list(self.logs_directory.glob("errors*.txt")), [])

    def test_grouped_session_separates_operation_files(self) -> None:
        with operation_loggers(
            self.logs_directory,
            "alice",
            ("operations", "download"),
            grouped=True,
        ) as loggers:
            loggers["operations"].info("Parsed preset")
            loggers["download"].warning("Retrying download")

        run_directories = list(self.logs_directory.glob("*_user_alice"))
        self.assertEqual(len(run_directories), 1)
        run_directory = run_directories[0]
        self.assertEqual(
            {path.name for path in run_directory.iterdir()},
            {"log_operations.txt", "log_download.txt"},
        )
        self.assertIn("[operations] [user=alice] - Parsed preset", (run_directory / "log_operations.txt").read_text(encoding="utf-8"))
        self.assertIn("WARNING [download] [user=alice] - Retrying download", (run_directory / "log_download.txt").read_text(encoding="utf-8"))

    def test_error_is_written_to_operation_and_error_files_with_traceback(self) -> None:
        with operation_loggers(
            self.logs_directory,
            "alice",
            ("download",),
            grouped=True,
        ) as loggers:
            try:
                raise ValueError("invalid Steam Guard secret")
            except ValueError:
                loggers["download"].exception("Could not generate Steam Guard code")

        run_directory = next(self.logs_directory.glob("*_user_alice"))
        operation_content = (run_directory / "log_download.txt").read_text(encoding="utf-8")
        error_content = (run_directory / "errors.txt").read_text(encoding="utf-8")
        self.assertIn("ERROR [download] [user=alice] - Could not generate Steam Guard code", operation_content)
        self.assertIn("Traceback (most recent call last)", operation_content)
        self.assertIn("ValueError: invalid Steam Guard secret", error_content)
        self.assertNotIn("INFO", error_content)

    def test_error_file_is_lazy_and_sessions_do_not_share_records(self) -> None:
        with operation_loggers(self.logs_directory, "alice", ("create",)) as alice_loggers:
            alice_loggers["create"].info("Alice record")
            with operation_loggers(self.logs_directory, "bob", ("create",)) as bob_loggers:
                bob_loggers["create"].info("Bob record")

        files = sorted(self.logs_directory.glob("log_create_*.txt"))
        self.assertEqual(len(files), 2)
        contents = [path.read_text(encoding="utf-8") for path in files]
        self.assertTrue(any("user=alice" in content and "Alice record" in content for content in contents))
        self.assertTrue(any("user=bob" in content and "Bob record" in content for content in contents))
        self.assertEqual(list(self.logs_directory.glob("errors*.txt")), [])

    def test_handlers_close_when_operation_raises(self) -> None:
        logger = None
        handlers = ()
        with self.assertRaises(RuntimeError):
            with operation_loggers(self.logs_directory, "alice", ("start",)) as loggers:
                logger = loggers["start"]
                handlers = tuple(logger.logger.handlers)
                raise RuntimeError("operation failed")

        self.assertIsNotNone(logger)
        self.assertEqual(len(logger.logger.handlers), 0)
        self.assertTrue(all(handler.stream is None for handler in handlers))
        self.assertTrue(list(self.logs_directory.glob("log_start_*.txt")))

    def test_steam_guard_generation_reports_error_through_callback(self) -> None:
        errors: list[str] = []

        result = generate_steam_guard_code("a", error_callback=errors.append)

        self.assertEqual(result, "")
        self.assertEqual(len(errors), 1)
        self.assertIn("Failed to generate Steam Guard code", errors[0])


if __name__ == "__main__":
    unittest.main()
