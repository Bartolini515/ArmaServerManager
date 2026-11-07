import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory

from main.steamcmd.mods_download import assign_new_steamguard, steamcmd_download, test_connection
from main.steamcmd.steam_auth import generate_steam_guard_code
from main.tasks import download_mods_task
from main.utils.config import config
from main.utils.operation_logging import operation_loggers
from main.utils.steam_errors import (
    SteamCommandError,
    SteamSecretRedactor,
    safe_steam_diagnostic,
)
from main.utils.process_output import stream_process_output
from main.views import InstancesViewset


STEAM_USERNAME = "steam-user-secret-7f3"
STEAM_PASSWORD = "steam-password-secret-2e9"
STEAM_SHARED_SECRET = "steam-shared-secret-81c"
STEAM_GUARD_CODE = "Q7W3P"
ESCAPED_STEAM_USERNAME = 'steam"user-secret'
ESCAPED_STEAM_PASSWORD = "pw'\\segment\nquoted"
ONE_CHARACTER_SECRET = "x"


def credential_spellings(secret: str) -> set[str]:
    return {
        secret,
        repr(secret)[1:-1],
        ascii(secret)[1:-1],
    }


class FakePipe:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = iter(lines)
        self.closed = False

    def readline(self) -> bytes:
        return next(self.lines, b"")

    def close(self) -> None:
        self.closed = True


class SteamCmdSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redactor = SteamSecretRedactor(
            (
                STEAM_USERNAME,
                STEAM_PASSWORD,
                STEAM_SHARED_SECRET,
                STEAM_GUARD_CODE,
            )
        )

    def assert_secrets_absent(self, value: str) -> None:
        for secret in (
            STEAM_USERNAME,
            STEAM_PASSWORD,
            STEAM_SHARED_SECRET,
            STEAM_GUARD_CODE,
        ):
            self.assertNotIn(secret, value)

    def test_process_output_is_redacted_before_print_and_callback(self) -> None:
        stdout = FakePipe(
            [
                (
                    f"login={STEAM_USERNAME} password={STEAM_PASSWORD} "
                    f"secret={STEAM_SHARED_SECRET} guard={STEAM_GUARD_CODE}\n"
                ).encode()
            ]
        )
        stderr = FakePipe([])
        process = Mock(stdout=stdout, stderr=stderr)
        callback_messages: list[str] = []
        printed = io.StringIO()

        with (
            redirect_stdout(printed),
            patch(
                "main.utils.process_output.SteamSecretRedactor.from_config",
                return_value=self.redactor,
            ),
        ):
            stream_process_output(process, callback_messages.append)

        output = printed.getvalue()
        self.assert_secrets_absent(output)
        self.assertEqual(len(callback_messages), 1)
        self.assert_secrets_absent(callback_messages[0])
        self.assertIn(self.redactor.marker, output)

    def test_one_character_is_redacted_inside_stdout_stderr_and_callbacks(self) -> None:
        redactor = SteamSecretRedactor((ONE_CHARACTER_SECRET,))
        stdout = FakePipe([b"prefixxpostfix\n"])
        stderr = FakePipe([b"endxmarker\n"])
        process = Mock(stdout=stdout, stderr=stderr)
        callback_messages: list[str] = []
        printed = io.StringIO()

        with redirect_stdout(printed):
            stream_process_output(
                process,
                callback_messages.append,
                secret_redactor=redactor,
            )

        self.assertNotIn(ONE_CHARACTER_SECRET, printed.getvalue())
        self.assertEqual(len(callback_messages), 2)
        self.assertTrue(all(ONE_CHARACTER_SECRET not in message for message in callback_messages))
        self.assertTrue(all(redactor.marker in message for message in callback_messages))

    def test_operation_logs_redact_messages_and_exception_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_directory = Path(temp_dir)
            with operation_loggers(
                logs_directory,
                "alice",
                ("download",),
                grouped=True,
                secret_redactor=self.redactor,
            ) as loggers:
                loggers["download"].info(
                    f"Steam output {STEAM_USERNAME} {STEAM_PASSWORD} "
                    f"{STEAM_SHARED_SECRET} {STEAM_GUARD_CODE}"
                )
                try:
                    raise RuntimeError(
                        f"failure {STEAM_USERNAME} {STEAM_PASSWORD} "
                        f"{STEAM_SHARED_SECRET} {STEAM_GUARD_CODE}"
                    )
                except RuntimeError:
                    loggers["download"].exception("Steam download failed")

            contents = "\n".join(
                path.read_text(encoding="utf-8")
                for path in logs_directory.rglob("*.txt")
            )

        self.assert_secrets_absent(contents)
        self.assertIn("Exception: RuntimeError", contents)
        self.assertIn("at ", contents)
        self.assertNotIn("failure ", contents)
        self.assertIn(self.redactor.marker, contents)

    def test_operation_log_filenames_stay_unique_when_clock_collides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = Mock()
            clock.now.return_value.strftime.return_value = "2026-11-07_12-00-00-000001"
            unique_ids = [
                SimpleNamespace(hex="session-a"),
                SimpleNamespace(hex="logger-a"),
                SimpleNamespace(hex="session-b"),
                SimpleNamespace(hex="logger-b"),
            ]

            with (
                patch("main.utils.operation_logging.datetime", clock),
                patch(
                    "main.utils.operation_logging.uuid4",
                    side_effect=unique_ids,
                ),
            ):
                for user in ("alice", "bob"):
                    with operation_loggers(temp_dir, user, ("create",)) as loggers:
                        loggers["create"].info(f"{user} record")

            files = sorted(Path(temp_dir).glob("log_create_*.txt"))
            self.assertEqual(len(files), 2)
            contents = [path.read_text(encoding="utf-8") for path in files]
            self.assertTrue(any("user=alice" in item and "alice record" in item for item in contents))
            self.assertTrue(any("user=bob" in item and "bob record" in item for item in contents))

    def test_one_character_is_redacted_inside_operation_log_messages(self) -> None:
        redactor = SteamSecretRedactor((ONE_CHARACTER_SECRET,))
        with tempfile.TemporaryDirectory() as temp_dir:
            with operation_loggers(
                temp_dir,
                "alice",
                ("download",),
                secret_redactor=redactor,
            ) as loggers:
                loggers["download"].info("prefixxpostfix")

            content = next(Path(temp_dir).glob("log_download_*.txt")).read_text(
                encoding="utf-8"
            )

        self.assertNotIn(ONE_CHARACTER_SECRET, content)
        self.assertIn(redactor.marker, content)

    def test_marker_avoids_known_credentials_and_survives_nested_callbacks(self) -> None:
        redactor = SteamSecretRedactor(("E", "D"))
        marker = redactor.marker
        self.assertNotIn("E", marker)
        self.assertNotIn("D", marker)

        captured: list[str] = []
        nested_callback = redactor.callback(redactor.callback(captured.append))
        nested_callback("prefixEDsuffix")

        self.assertEqual(captured, [f"prefix{marker}{marker}suffix"])
        self.assertEqual(redactor.redact(captured[0]), captured[0])

    def test_structured_numeric_diagnostics_are_redacted(self) -> None:
        redactor = SteamSecretRedactor(("1",))
        timeout_diagnostic = safe_steam_diagnostic(
            subprocess.TimeoutExpired("fake command", timeout=13),
            redactor,
        )
        returncode_diagnostic = safe_steam_diagnostic(
            subprocess.CalledProcessError(17, "fake command"),
            redactor,
        )
        errno_diagnostic = safe_steam_diagnostic(OSError(13, "fake error"), redactor)

        self.assertNotIn("1", timeout_diagnostic)
        self.assertNotIn("1", returncode_diagnostic)
        self.assertNotIn("1", errno_diagnostic)
        self.assertIn(redactor.marker, timeout_diagnostic)
        self.assertIn(redactor.marker, returncode_diagnostic)
        self.assertIn(redactor.marker, errno_diagnostic)

        credential_redactor = SteamSecretRedactor(
            (ESCAPED_STEAM_USERNAME, ESCAPED_STEAM_PASSWORD)
        )
        unsafe_timeout = subprocess.TimeoutExpired("fake command", timeout=13)
        unsafe_timeout.timeout = f"+login {ESCAPED_STEAM_PASSWORD}"
        unsafe_returncode = subprocess.CalledProcessError(17, "fake command")
        unsafe_returncode.returncode = f"+login {ESCAPED_STEAM_PASSWORD}"
        unsafe_os_error = OSError(13, "fake error")
        unsafe_os_error.errno = f"+login {ESCAPED_STEAM_PASSWORD}"
        unsafe_diagnostics = (
            safe_steam_diagnostic(unsafe_timeout, credential_redactor),
            safe_steam_diagnostic(unsafe_returncode, credential_redactor),
            safe_steam_diagnostic(unsafe_os_error, credential_redactor),
        )
        for diagnostic in unsafe_diagnostics:
            self.assertNotIn("+login", diagnostic)
            for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD):
                self.assertNotIn(spelling, diagnostic)

        for diagnostic in (
            timeout_diagnostic,
            returncode_diagnostic,
            errno_diagnostic,
        ):
            self.assertNotIn("+login", diagnostic)
            for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD):
                self.assertNotIn(spelling, diagnostic)

    def test_steam_guard_callback_does_not_include_shared_secret(self) -> None:
        errors: list[tuple[str, bool]] = []

        def capture_error(message: str) -> None:
            errors.append((message, sys.exc_info()[0] is not None))

        with patch(
            "main.steamcmd.steam_auth.base64.b64decode",
            side_effect=ValueError(f"invalid input {STEAM_SHARED_SECRET}"),
        ):
            result = generate_steam_guard_code(
                STEAM_SHARED_SECRET,
                error_callback=capture_error,
            )

        self.assertEqual(result, "")
        self.assertEqual(len(errors), 1)
        self.assertNotIn(STEAM_SHARED_SECRET, errors[0][0])
        self.assertFalse(errors[0][1])

    def test_generated_guard_code_is_registered_for_redaction(self) -> None:
        redactor = SteamSecretRedactor(
            (STEAM_USERNAME, STEAM_PASSWORD, STEAM_SHARED_SECRET)
        )

        with patch(
            "main.steamcmd.mods_download.generate_steam_guard_code",
            return_value=STEAM_GUARD_CODE,
        ):
            code = assign_new_steamguard(None, secret_redactor=redactor)

        self.assertEqual(code, STEAM_GUARD_CODE)
        self.assertEqual(redactor.redact(f"guard={code}"), f"guard={redactor.marker}")

    def test_steamcmd_timeout_is_a_safe_typed_error(self) -> None:
        command = [
            "bash",
            "steamcmd.sh",
            f"+set_steam_guard_code {STEAM_GUARD_CODE}",
            f"+login {ESCAPED_STEAM_USERNAME} {ESCAPED_STEAM_PASSWORD}",
        ]

        logged_messages: list[str] = []
        exception_contexts: list[bool] = []

        def capture_log(message: str) -> None:
            logged_messages.append(message)
            exception_contexts.append(sys.exc_info()[0] is not None)

        with patch(
            "main.steamcmd.mods_download.subprocess.Popen",
            side_effect=subprocess.TimeoutExpired(command, timeout=10),
        ):
            with self.assertRaises(SteamCommandError) as raised:
                steamcmd_download(
                    mod="123456",
                    appid=107410,
                    login=ESCAPED_STEAM_USERNAME,
                    password=ESCAPED_STEAM_PASSWORD,
                    steamcmd_dir="/fake/steamcmd",
                    ghost_folder_path="/fake/download",
                    steamguard=STEAM_GUARD_CODE,
                    log_callback=capture_log,
                )

        self.assertEqual(
            str(raised.exception),
            "Nie udało się uruchomić SteamCMD. Sprawdź logi operacji.",
        )
        self.assert_secrets_absent(str(raised.exception))
        self.assert_secrets_absent(raised.exception.diagnostic)
        self.assertIn("TimeoutExpired", raised.exception.diagnostic)
        for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
            self.assertNotIn(spelling, raised.exception.diagnostic)
        self.assertEqual(len(logged_messages), 1)
        self.assertIn("TimeoutExpired", logged_messages[0])
        self.assert_secrets_absent(logged_messages[0])
        for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
            self.assertNotIn(spelling, logged_messages[0])
        self.assertNotIn("+login", logged_messages[0])
        self.assertEqual(exception_contexts, [False])

    def test_test_connection_timeout_is_safe(self) -> None:
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired(
            ["steamcmd.sh", f"+login {STEAM_USERNAME} {STEAM_PASSWORD}"],
            timeout=10,
        )

        with patch("main.steamcmd.mods_download.subprocess.Popen", return_value=process):
            with self.assertRaises(SteamCommandError) as raised:
                test_connection(
                    "/fake/steamcmd",
                    STEAM_USERNAME,
                    STEAM_PASSWORD,
                    STEAM_GUARD_CODE,
                )

        self.assert_secrets_absent(str(raised.exception))
        self.assert_secrets_absent(raised.exception.diagnostic)
        self.assertIn("TimeoutExpired", raised.exception.diagnostic)

    def test_called_process_error_command_is_safe(self) -> None:
        command = [
            "bash",
            "steamcmd.sh",
            f"+set_steam_guard_code {STEAM_GUARD_CODE}",
            f"+login {ESCAPED_STEAM_USERNAME} {ESCAPED_STEAM_PASSWORD}",
        ]

        callback_messages: list[str] = []
        with patch(
            "main.steamcmd.mods_download.subprocess.Popen",
            side_effect=subprocess.CalledProcessError(1, command),
        ):
            with self.assertRaises(SteamCommandError) as raised:
                steamcmd_download(
                    mod="123456",
                    appid=107410,
                    login=ESCAPED_STEAM_USERNAME,
                    password=ESCAPED_STEAM_PASSWORD,
                    steamcmd_dir="/fake/steamcmd",
                    ghost_folder_path="/fake/download",
                    steamguard=STEAM_GUARD_CODE,
                    log_callback=callback_messages.append,
                )

        self.assert_secrets_absent(str(raised.exception))
        self.assert_secrets_absent(raised.exception.diagnostic)
        self.assertIn("CalledProcessError", raised.exception.diagnostic)
        self.assertEqual(len(callback_messages), 1)
        for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
            self.assertNotIn(spelling, callback_messages[0])
        self.assertNotIn("+login", callback_messages[0])

    def test_callback_subprocess_exceptions_use_structured_diagnostics(self) -> None:
        command = [
            "+login",
            ESCAPED_STEAM_USERNAME,
            ESCAPED_STEAM_PASSWORD,
        ]
        failures = (
            subprocess.TimeoutExpired(command, timeout=19),
            subprocess.CalledProcessError(23, command),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                redactor = SteamSecretRedactor(
                    (ESCAPED_STEAM_USERNAME, ESCAPED_STEAM_PASSWORD)
                )

                def failed_callback(_message: str) -> None:
                    raise failure

                safe_callback = redactor.callback(failed_callback)
                with self.assertRaises(SteamCommandError) as raised:
                    safe_callback("starting download")

                diagnostic = raised.exception.diagnostic
                self.assertIn(type(failure).__name__, diagnostic)
                self.assertNotIn("+login", diagnostic)
                for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
                    self.assertNotIn(spelling, diagnostic)

    def test_callback_wrapped_subprocess_exceptions_do_not_serialize_messages(self) -> None:
        command = [
            "+login",
            ESCAPED_STEAM_USERNAME,
            ESCAPED_STEAM_PASSWORD,
        ]
        failures = (
            subprocess.TimeoutExpired(command, timeout=29),
            subprocess.CalledProcessError(31, command),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                redactor = SteamSecretRedactor(
                    (ESCAPED_STEAM_USERNAME, ESCAPED_STEAM_PASSWORD)
                )

                def failed_callback(_message: str) -> None:
                    raise RuntimeError(str(failure))

                safe_callback = redactor.callback(failed_callback)
                with self.assertRaises(SteamCommandError) as raised:
                    safe_callback("starting download")

                diagnostic = raised.exception.diagnostic
                self.assertIn("RuntimeError", diagnostic)
                self.assertNotIn("+login", diagnostic)
                for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
                    self.assertNotIn(spelling, diagnostic)

    def test_callback_does_not_trust_arbitrary_diagnostic_or_str(self) -> None:
        class DiagnosticError(Exception):
            diagnostic = (
                f"command +login {ESCAPED_STEAM_USERNAME} "
                f"{ESCAPED_STEAM_PASSWORD}"
            )

        class MaliciousStringError(Exception):
            def __str__(self) -> str:
                return (
                    f"command +login {ESCAPED_STEAM_USERNAME} "
                    f"{ESCAPED_STEAM_PASSWORD}"
                )

        arbitrary_steam_error = SteamCommandError(
            f"command +login {ESCAPED_STEAM_USERNAME} {ESCAPED_STEAM_PASSWORD}",
            diagnostic=(
                f"command +login {ESCAPED_STEAM_USERNAME} "
                f"{ESCAPED_STEAM_PASSWORD}"
            ),
        )

        for failure in (
            DiagnosticError(),
            MaliciousStringError(),
            arbitrary_steam_error,
        ):
            with self.subTest(failure=type(failure).__name__):
                redactor = SteamSecretRedactor(
                    (ESCAPED_STEAM_USERNAME, ESCAPED_STEAM_PASSWORD)
                )

                def failed_callback(_message: str) -> None:
                    raise failure

                safe_callback = redactor.callback(failed_callback)
                with self.assertRaises(SteamCommandError) as raised:
                    safe_callback("starting download")

                diagnostic = raised.exception.diagnostic
                self.assertNotIn("+login", diagnostic)
                self.assertIn(
                    "SteamCommandError"
                    if isinstance(failure, SteamCommandError)
                    else "Exception",
                    diagnostic,
                )
                for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
                    self.assertNotIn(spelling, diagnostic)

    def test_task_status_does_not_return_generic_exception_text(self) -> None:
        failed_task = Mock()
        failed_task.status = "FAILURE"
        failed_task.result = RuntimeError(
            f"command failed with {STEAM_USERNAME} {STEAM_PASSWORD} "
            f"{STEAM_SHARED_SECRET} {STEAM_GUARD_CODE}"
        )
        request = RequestFactory().get("/api/instances/task_status/fake-task/")

        with patch("main.views.AsyncResult", return_value=failed_task):
            response = InstancesViewset().task_status(request, task_id="fake-task")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"], {"status": "Nie udało się wykonać zadania."})
        self.assert_secrets_absent(str(response.data))

        failed_task.result = SteamCommandError(
            f"failed {STEAM_USERNAME} {STEAM_PASSWORD} "
            f"{STEAM_SHARED_SECRET} {STEAM_GUARD_CODE}"
        )
        with patch("main.views.AsyncResult", return_value=failed_task):
            response = InstancesViewset().task_status(request, task_id="fake-task")

        self.assertEqual(response.data["result"], {"status": "Nie udało się wykonać zadania."})
        self.assert_secrets_absent(str(response.data))

        failed_task.result = {
            "status": f"failed {STEAM_USERNAME} {STEAM_PASSWORD} "
            f"{STEAM_SHARED_SECRET} {STEAM_GUARD_CODE}"
        }
        with patch("main.views.AsyncResult", return_value=failed_task):
            response = InstancesViewset().task_status(request, task_id="fake-task")

        self.assertEqual(response.data["result"], {"status": "Nie udało się wykonać zadania."})
        self.assert_secrets_absent(str(response.data))

    def test_download_enqueue_error_does_not_echo_exception_text(self) -> None:
        instance = Mock(
            id=1,
            is_admin_instance=False,
            name="unit",
            preset=Mock(path="fake-preset"),
        )
        viewset = InstancesViewset()
        viewset.request = Mock(user=Mock(username="alice"))
        failure = RuntimeError(
            f"enqueue failed with {STEAM_USERNAME} {STEAM_PASSWORD} "
            f"{STEAM_SHARED_SECRET} {STEAM_GUARD_CODE}"
        )

        with (
            patch("main.views.Instances.objects.get", return_value=instance),
            patch("main.views.psutil.disk_usage", return_value=Mock(free=10**12)),
            patch("main.views.config.get", return_value="/fake/mods"),
            patch("main.views.download_mods_task.delay", side_effect=failure),
        ):
            response = viewset.download_mods(Mock(), pk=1)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.data["message"],
            "Nie udało się rozpocząć zadania pobierania.",
        )
        self.assert_secrets_absent(str(response.data))

    def test_celery_failure_result_and_logs_use_safe_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_directory = Path(temp_dir)

            def config_value(key: str, default=None):
                values = {
                    "paths.logs_directory": str(logs_directory),
                    "steam_auth.username": STEAM_USERNAME,
                    "steam_auth.password": STEAM_PASSWORD,
                    "steam_auth.shared_secret": STEAM_SHARED_SECRET,
                }
                return values.get(key, default)

            raw_failure = RuntimeError(
                f"worker failed with {STEAM_USERNAME} {STEAM_PASSWORD} "
                f"{STEAM_SHARED_SECRET} {STEAM_GUARD_CODE}"
            )

            def fail_download(**kwargs):
                kwargs["secret_redactor"].add_secret(STEAM_GUARD_CODE)
                raise raw_failure

            with (
                patch.object(config, "get", side_effect=config_value),
                patch("main.tasks.cache.get", return_value=None),
                patch("main.tasks.cache.set"),
                patch("main.tasks.cache.delete"),
                patch("main.tasks.preset_parser", return_value=["123456"]),
                patch("main.tasks.check_installed", return_value=([], ["123456"])),
                patch("main.tasks.download_mods", side_effect=fail_download),
                patch.object(download_mods_task, "update_state"),
            ):
                result = download_mods_task.apply(
                    args=(1, "unit", "alice", "fake-preset", "fake-mods"),
                    throw=False,
                )

            self.assertTrue(result.failed())
            self.assertIsInstance(result.result, SteamCommandError)
            self.assertEqual(
                str(result.result),
                "Nie udało się pobrać modów. Sprawdź logi operacji.",
            )
            self.assert_secrets_absent(str(result.result))
            self.assert_secrets_absent(result.result.diagnostic)

            log_contents = "\n".join(
                path.read_text(encoding="utf-8")
                for path in logs_directory.rglob("*.txt")
            )
            self.assert_secrets_absent(log_contents)
            self.assertIn("Steam download diagnostic", log_contents)

    def test_escaped_subprocess_failures_are_safe_in_celery_results_and_logs(self) -> None:
        command = [
            "bash",
            "steamcmd.sh",
            f"+login {ESCAPED_STEAM_USERNAME} {ESCAPED_STEAM_PASSWORD}",
        ]
        failures = (
            subprocess.TimeoutExpired(
                command,
                timeout=13,
                output=ESCAPED_STEAM_PASSWORD,
                stderr=ESCAPED_STEAM_PASSWORD,
            ),
            subprocess.CalledProcessError(
                17,
                command,
                output=ESCAPED_STEAM_PASSWORD,
                stderr=ESCAPED_STEAM_PASSWORD,
            ),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as temp_dir:
                    logs_directory = Path(temp_dir)

                    def config_value(key: str, default=None):
                        values = {
                            "paths.logs_directory": str(logs_directory),
                            "steam_auth.username": ESCAPED_STEAM_USERNAME,
                            "steam_auth.password": ESCAPED_STEAM_PASSWORD,
                            "steam_auth.shared_secret": STEAM_SHARED_SECRET,
                        }
                        return values.get(key, default)

                    def fail_download(**kwargs):
                        kwargs["secret_redactor"].add_secret(STEAM_GUARD_CODE)
                        try:
                            raise RuntimeError(
                                f"nested cause {ESCAPED_STEAM_PASSWORD}"
                            )
                        except RuntimeError as cause:
                            if isinstance(failure, subprocess.TimeoutExpired):
                                raise failure
                            raise failure from cause

                    with (
                        patch.object(config, "get", side_effect=config_value),
                        patch("main.tasks.cache.get", return_value=None),
                        patch("main.tasks.cache.set"),
                        patch("main.tasks.cache.delete"),
                        patch("main.tasks.preset_parser", return_value=["123456"]),
                        patch("main.tasks.check_installed", return_value=([], ["123456"])),
                        patch("main.tasks.download_mods", side_effect=fail_download),
                        patch.object(download_mods_task, "update_state"),
                    ):
                        result = download_mods_task.apply(
                            args=(1, "unit", "alice", "fake-preset", "fake-mods"),
                            throw=False,
                        )

                    self.assertTrue(result.failed())
                    self.assertIsInstance(result.result, SteamCommandError)
                    diagnostic = result.result.diagnostic
                    self.assertIn("SteamCommandError", diagnostic)
                    self.assertNotIn("+login", diagnostic)
                    for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
                        self.assertNotIn(spelling, diagnostic)
                    self.assertNotIn(STEAM_USERNAME, diagnostic)
                    self.assertNotIn(STEAM_GUARD_CODE, diagnostic)

                    log_contents = "\n".join(
                        path.read_text(encoding="utf-8")
                        for path in logs_directory.rglob("*.txt")
                    )
                    log_paths = list(logs_directory.rglob("*.txt"))
                    self.assertTrue(any(path.name == "log_download.txt" for path in log_paths))
                    self.assertTrue(any(path.name == "errors.txt" for path in log_paths))
                    for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
                        self.assertNotIn(spelling, log_contents)
                    self.assertNotIn(STEAM_USERNAME, log_contents)
                    self.assertNotIn(STEAM_GUARD_CODE, log_contents)
                    self.assertIn("Steam download diagnostic", log_contents)
                    self.assertIn(
                        "TimeoutExpired: timeout="
                        if isinstance(failure, subprocess.TimeoutExpired)
                        else "CalledProcessError: returncode=",
                        log_contents,
                    )
                    self.assertNotIn("nested cause", log_contents)
                    self.assertNotIn("Command ", log_contents)

    def test_wrapped_subprocess_failures_are_type_only_in_celery_results_and_logs(self) -> None:
        command = [
            "+login",
            ESCAPED_STEAM_USERNAME,
            ESCAPED_STEAM_PASSWORD,
        ]
        timeout_error = subprocess.TimeoutExpired(command, timeout=37)
        process_error = subprocess.CalledProcessError(41, command)

        class DiagnosticError(Exception):
            diagnostic = (
                f"command +login {ESCAPED_STEAM_USERNAME} "
                f"{ESCAPED_STEAM_PASSWORD}"
            )

        class MaliciousStringError(Exception):
            def __str__(self) -> str:
                return (
                    f"command +login {ESCAPED_STEAM_USERNAME} "
                    f"{ESCAPED_STEAM_PASSWORD}"
                )

        failures = (
            RuntimeError(str(timeout_error)),
            RuntimeError(str(process_error)),
            DiagnosticError(),
            MaliciousStringError(),
            SteamCommandError(
                f"command +login {ESCAPED_STEAM_USERNAME} "
                f"{ESCAPED_STEAM_PASSWORD}",
                diagnostic=(
                    f"command +login {ESCAPED_STEAM_USERNAME} "
                    f"{ESCAPED_STEAM_PASSWORD}"
                ),
            ),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as temp_dir:
                    logs_directory = Path(temp_dir)

                    def config_value(key: str, default=None):
                        values = {
                            "paths.logs_directory": str(logs_directory),
                            "steam_auth.username": ESCAPED_STEAM_USERNAME,
                            "steam_auth.password": ESCAPED_STEAM_PASSWORD,
                            "steam_auth.shared_secret": STEAM_SHARED_SECRET,
                        }
                        return values.get(key, default)

                    def fail_download(**kwargs):
                        kwargs["secret_redactor"].add_secret(STEAM_GUARD_CODE)
                        raise failure

                    with (
                        patch.object(config, "get", side_effect=config_value),
                        patch("main.tasks.cache.get", return_value=None),
                        patch("main.tasks.cache.set"),
                        patch("main.tasks.cache.delete"),
                        patch("main.tasks.preset_parser", return_value=["123456"]),
                        patch("main.tasks.check_installed", return_value=([], ["123456"])),
                        patch("main.tasks.download_mods", side_effect=fail_download),
                        patch.object(download_mods_task, "update_state"),
                    ):
                        result = download_mods_task.apply(
                            args=(1, "unit", "alice", "fake-preset", "fake-mods"),
                            throw=False,
                        )

                    self.assertTrue(result.failed())
                    self.assertIsInstance(result.result, SteamCommandError)
                    diagnostic = result.result.diagnostic
                    self.assertIn("SteamCommandError", diagnostic)
                    self.assertNotIn("+login", diagnostic)
                    for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
                        self.assertNotIn(spelling, diagnostic)

                    log_contents = "\n".join(
                        path.read_text(encoding="utf-8")
                        for path in logs_directory.rglob("*.txt")
                    )
                    expected_type = (
                        "RuntimeError"
                        if type(failure) is RuntimeError
                        else "Exception"
                    )
                    if isinstance(failure, SteamCommandError):
                        expected_type = "SteamCommandError"
                    self.assertIn(f"{expected_type}: details omitted", log_contents)
                    self.assertNotIn("+login", log_contents)
                    self.assertNotIn("Command ", log_contents)
                    for spelling in credential_spellings(ESCAPED_STEAM_PASSWORD) | credential_spellings(ESCAPED_STEAM_USERNAME):
                        self.assertNotIn(spelling, log_contents)


if __name__ == "__main__":
    unittest.main()
