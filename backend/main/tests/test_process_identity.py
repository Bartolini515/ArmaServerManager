from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psutil

from main.serverhandling import start_server as start_server_module
from main.serverhandling import process_identity


@dataclass
class FakeProcessState:
    create_times: list[float | BaseException]
    executable: str = r"C:\Invented\arma3server_x64.exe"
    environment: dict[str, str] | BaseException = field(default_factory=dict)
    executable_error: BaseException | None = None
    udp_ports: list[int] = field(default_factory=list)
    udp_connections_error: BaseException | None = None


class FakeProcess:
    def __init__(self, state: FakeProcessState) -> None:
        self.state = state

    def create_time(self) -> float:
        value = (
            self.state.create_times.pop(0)
            if len(self.state.create_times) > 1
            else self.state.create_times[0]
        )
        if isinstance(value, BaseException):
            raise value
        return value

    def exe(self) -> str:
        if self.state.executable_error is not None:
            raise self.state.executable_error
        return self.state.executable

    def environ(self) -> dict[str, str]:
        if isinstance(self.state.environment, BaseException):
            raise self.state.environment
        return self.state.environment or {}

    def net_connections(self, kind: str = "inet") -> list[SimpleNamespace]:
        if self.state.udp_connections_error is not None:
            raise self.state.udp_connections_error
        return [
            SimpleNamespace(laddr=SimpleNamespace(port=port))
            for port in self.state.udp_ports
        ]


class ProcessIdentityTests(unittest.TestCase):
    def _patch_processes(self, states: dict[int, FakeProcessState]):
        def make_process(pid: int) -> FakeProcess:
            return FakeProcess(states[pid])

        return patch.object(process_identity.psutil, "Process", side_effect=make_process)

    @staticmethod
    def _connection(pid: int | None, port: int) -> SimpleNamespace:
        return SimpleNamespace(pid=pid, laddr=SimpleNamespace(port=port))

    def test_capture_and_verified_lookup_accept_matching_creation_identity(self) -> None:
        state = FakeProcessState(create_times=[1720000000.25])
        with self._patch_processes({23: state}):
            identity = process_identity.capture_process_identity(23)
            verified = process_identity.get_verified_process(identity)

        self.assertEqual(identity, process_identity.ProcessIdentity(23, 1720000000.25))
        self.assertIsInstance(verified, FakeProcess)

    def test_verified_lookup_returns_none_for_confirmed_missing_process(self) -> None:
        with patch.object(
            process_identity.psutil,
            "Process",
            side_effect=psutil.NoSuchProcess(23),
        ):
            self.assertIsNone(
                process_identity.get_verified_process(
                    process_identity.ProcessIdentity(23, 1720000000.25)
                )
            )

    def test_verified_lookup_handles_disappearance_during_birth_time_check(self) -> None:
        state = FakeProcessState(
            create_times=[psutil.NoSuchProcess(23)],
        )
        with self._patch_processes({23: state}):
            self.assertIsNone(
                process_identity.get_verified_process(
                    process_identity.ProcessIdentity(23, 1720000000.25)
                )
            )

    def test_verified_lookup_rejects_recycled_pid_and_access_denied(self) -> None:
        recycled = FakeProcessState(create_times=[1720000001.0])
        with self._patch_processes({23: recycled}):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.get_verified_process(
                    process_identity.ProcessIdentity(23, 1720000000.25)
                )

        with patch.object(
            process_identity.psutil,
            "Process",
            side_effect=psutil.AccessDenied(23),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.get_verified_process(
                    process_identity.ProcessIdentity(23, 1720000000.25)
                )

    def test_finds_only_matching_udp_port_marker_and_executable(self) -> None:
        state = FakeProcessState(
            create_times=[1720000000.25, 1720000000.25],
            environment={"ASM_INSTANCE_RUN_ID": "run-23"},
            udp_ports=[2402],
        )
        connections = [
            self._connection(23, 2402),
            self._connection(23, 2402),
            self._connection(24, 2403),
        ]
        with (
            self._patch_processes({23: state}),
            patch.object(process_identity.psutil, "net_connections", return_value=connections) as net_connections,
        ):
            matches = process_identity.find_udp_processes(
                2402,
                "run-23",
                r"C:\Invented\arma3server_x64.exe",
            )

        self.assertEqual(matches, [process_identity.ProcessIdentity(23, 1720000000.25)])
        net_connections.assert_called_once_with(kind="udp")

    def test_excludes_wrong_run_marker_executable_and_missing_marker(self) -> None:
        states = {
            23: FakeProcessState(
                create_times=[1720000000.25],
                environment={"ASM_INSTANCE_RUN_ID": "another-run"},
                udp_ports=[2402],
            ),
            24: FakeProcessState(
                create_times=[1720000001.25],
                executable=r"C:\Invented\other.exe",
                environment={"ASM_INSTANCE_RUN_ID": "run-24"},
                udp_ports=[2402],
            ),
            25: FakeProcessState(
                create_times=[1720000002.25],
                environment={},
                udp_ports=[2402],
            ),
        }
        connections = [
            self._connection(23, 2402),
            self._connection(24, 2402),
            self._connection(25, 2402),
        ]
        with (
            self._patch_processes(states),
            patch.object(process_identity.psutil, "net_connections", return_value=connections),
        ):
            matches = process_identity.find_udp_processes(
                2402,
                "run-24",
                r"C:\Invented\arma3server_x64.exe",
            )

        self.assertEqual(matches, [])

    def test_pid_reuse_before_marker_mismatch_is_uncertain(self) -> None:
        state = FakeProcessState(
            create_times=[100.0, 100.0, 200.0],
            environment={"ASM_INSTANCE_RUN_ID": "other-run"},
            udp_ports=[2402],
        )
        with (
            self._patch_processes({23: state}),
            patch.object(
                process_identity.psutil,
                "net_connections",
                return_value=[self._connection(23, 2402)],
            ),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.find_udp_processes(
                    2402,
                    "run-23",
                    r"C:\Invented\arma3server_x64.exe",
                )

    def test_pid_reuse_before_executable_mismatch_is_uncertain(self) -> None:
        state = FakeProcessState(
            create_times=[100.0, 100.0, 100.0, 200.0],
            executable=r"C:\Invented\other.exe",
            environment={"ASM_INSTANCE_RUN_ID": "run-23"},
            udp_ports=[2402],
        )
        with (
            self._patch_processes({23: state}),
            patch.object(
                process_identity.psutil,
                "net_connections",
                return_value=[self._connection(23, 2402)],
            ),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.find_udp_processes(
                    2402,
                    "run-23",
                    r"C:\Invented\arma3server_x64.exe",
                )

    def test_stale_socket_snapshot_for_recycled_pid_preserves_uncertainty(self) -> None:
        state = FakeProcessState(
            create_times=[200.0],
            environment={"ASM_INSTANCE_RUN_ID": "run-23"},
            udp_ports=[2403],
        )
        with (
            self._patch_processes({23: state}),
            patch.object(
                process_identity.psutil,
                "net_connections",
                return_value=[self._connection(23, 2402)],
            ),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.find_udp_processes(
                    2402,
                    "run-23",
                    r"C:\Invented\arma3server_x64.exe",
                )

    def test_live_original_owner_with_conflicting_socket_views_is_uncertain(self) -> None:
        state = FakeProcessState(
            create_times=[100.0],
            environment={"ASM_INSTANCE_RUN_ID": "run-23"},
            udp_ports=[2403],
        )
        with (
            self._patch_processes({23: state}),
            patch.object(
                process_identity.psutil,
                "net_connections",
                return_value=[self._connection(23, 2402)],
            ),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.find_udp_processes(
                    2402,
                    "run-23",
                    r"C:\Invented\arma3server_x64.exe",
                )

    def test_access_denied_reading_current_udp_ownership_is_uncertain(self) -> None:
        state = FakeProcessState(
            create_times=[200.0],
            environment={"ASM_INSTANCE_RUN_ID": "run-23"},
            udp_connections_error=psutil.AccessDenied(23),
        )
        with (
            self._patch_processes({23: state}),
            patch.object(
                process_identity.psutil,
                "net_connections",
                return_value=[self._connection(23, 2402)],
            ),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.find_udp_processes(
                    2402,
                    "run-23",
                    r"C:\Invented\arma3server_x64.exe",
                )

    def test_empty_run_marker_is_rejected_before_discovery(self) -> None:
        with patch.object(process_identity.psutil, "net_connections") as net_connections:
            with self.assertRaises(ValueError):
                process_identity.find_udp_processes(
                    2402,
                    "",
                    r"C:\Invented\arma3server_x64.exe",
                )
        net_connections.assert_not_called()

    def test_pid_reuse_during_discovery_is_uncertain(self) -> None:
        state = FakeProcessState(
            create_times=[1720000000.25, 1720000100.25],
            environment={"ASM_INSTANCE_RUN_ID": "run-23"},
        )
        with (
            self._patch_processes({23: state}),
            patch.object(
                process_identity.psutil,
                "net_connections",
                return_value=[self._connection(23, 2402)],
            ),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError):
                process_identity.find_udp_processes(
                    2402,
                    "run-23",
                    r"C:\Invented\arma3server_x64.exe",
                )

    def test_access_denied_during_discovery_preserves_uncertainty(self) -> None:
        denied = psutil.AccessDenied(23)
        state = FakeProcessState(
            create_times=[1720000000.25],
            environment={"ASM_INSTANCE_RUN_ID": "run-23"},
            executable_error=denied,
            udp_ports=[2402],
        )
        with (
            self._patch_processes({23: state}),
            patch.object(
                process_identity.psutil,
                "net_connections",
                return_value=[self._connection(23, 2402)],
            ),
        ):
            with self.assertRaises(process_identity.UnverifiedProcessError) as raised:
                process_identity.find_udp_processes(
                    2402,
                    "run-23",
                    r"C:\Invented\arma3server_x64.exe",
                )

        self.assertNotIn("run-23", str(raised.exception))
        self.assertNotIn("arma3server_x64.exe", str(raised.exception))

    def test_start_server_copies_fake_environment_for_child_marker(self) -> None:
        fake_parent_environment = {
            "PATH": "invented-path",
            "ASM_INSTANCE_RUN_ID": "prior-invented-run",
        }
        fake_process = Mock()
        fake_process.pid = 12345
        fake_process.poll.return_value = None
        fake_thread = Mock()
        identity = process_identity.ProcessIdentity(pid=12345, created_at=100.5)
        verified_process = Mock()
        verified_process.environ.return_value = {"ASM_INSTANCE_RUN_ID": "run-23"}
        with (
            patch.object(
                start_server_module,
                "os",
                SimpleNamespace(environ=fake_parent_environment),
            ),
            patch.object(
                start_server_module.subprocess,
                "Popen",
                return_value=fake_process,
            ) as popen,
            patch.object(
                start_server_module,
                "capture_process_identity",
                return_value=identity,
            ),
            patch.object(
                start_server_module,
                "get_verified_process",
                return_value=verified_process,
            ),
            patch.object(start_server_module.threading, "Thread", return_value=fake_thread),
        ):
            returned = start_server_module.start_server(
                "invented-arma-dir",
                "invented-start-file",
                "invented-log-file",
                run_id="run-23",
                launch_signal_guard=Mock(return_value=True),
            )

        self.assertIs(returned, fake_process)
        self.assertEqual(returned._asm_process_identity, identity)
        child_environment = popen.call_args.kwargs["env"]
        self.assertIsNot(child_environment, fake_parent_environment)
        self.assertEqual(child_environment["PATH"], "invented-path")
        self.assertEqual(child_environment["ASM_INSTANCE_RUN_ID"], "run-23")
        self.assertEqual(
            fake_parent_environment["ASM_INSTANCE_RUN_ID"],
            "prior-invented-run",
        )
        fake_thread.start.assert_called_once_with()

    def test_marker_mismatch_does_not_signal_through_popen_handle(self) -> None:
        fake_process = Mock()
        fake_process.pid = 12345
        fake_process.poll.return_value = None
        identity = process_identity.ProcessIdentity(pid=12345, created_at=100.5)
        verified_process = Mock()
        verified_process.environ.return_value = {"ASM_INSTANCE_RUN_ID": "wrong-run"}
        launch_signal_guard = Mock(return_value=False)

        with (
            patch.object(start_server_module.subprocess, "Popen", return_value=fake_process),
            patch.object(start_server_module, "capture_process_identity", return_value=identity),
            patch.object(start_server_module, "get_verified_process", return_value=verified_process),
        ):
            with self.assertRaises(Exception) as raised:
                start_server_module.start_server(
                    "invented-arma-dir",
                    "invented-start-file",
                    "invented-log-file",
                    run_id="run-23",
                    launch_signal_guard=launch_signal_guard,
                )

        self.assertEqual(
            raised.exception.__class__.__name__,
            "PostLaunchMayBeLiveError",
        )
        self.assertEqual(raised.exception.launch_identity, identity)
        launch_signal_guard.assert_not_called()
        fake_process.terminate.assert_not_called()
        fake_process.wait.assert_not_called()

    def test_output_reader_start_failure_does_not_assume_child_is_stopped(self) -> None:
        fake_process = Mock()
        fake_process.pid = 12345
        fake_process.poll.return_value = None
        identity = process_identity.ProcessIdentity(pid=12345, created_at=100.5)
        verified_process = Mock()
        verified_process.environ.return_value = {"ASM_INSTANCE_RUN_ID": "run-23"}
        output_thread = Mock()
        output_thread.start.side_effect = RuntimeError("invented reader startup failure")
        launch_signal_guard = Mock(return_value=True)

        def capture_anchored_process(process, run_id):
            process._asm_process_identity = identity
            return identity

        with (
            patch.object(start_server_module.subprocess, "Popen", return_value=fake_process),
            patch.object(
                start_server_module,
                "_capture_launch_identity",
                side_effect=capture_anchored_process,
            ),
            patch.object(start_server_module, "get_verified_process", return_value=verified_process),
            patch.object(start_server_module.threading, "Thread", return_value=output_thread),
        ):
            with self.assertRaises(Exception) as raised:
                start_server_module.start_server(
                    "invented-arma-dir",
                    "invented-start-file",
                    "invented-log-file",
                    run_id="run-23",
                    launch_signal_guard=launch_signal_guard,
                )

        self.assertEqual(
            raised.exception.__class__.__name__,
            "PostLaunchMayBeLiveError",
        )
        self.assertEqual(raised.exception.launch_identity, identity)
        fake_process.terminate.assert_not_called()
        fake_process.kill.assert_not_called()
        launch_signal_guard.assert_not_called()

    def test_start_server_without_marker_keeps_legacy_popen_environment_behavior(self) -> None:
        fake_process = object()
        fake_thread = Mock()
        with (
            patch.object(
                start_server_module,
                "os",
                SimpleNamespace(environ={"PATH": "invented-path"}),
            ),
            patch.object(
                start_server_module.subprocess,
                "Popen",
                return_value=fake_process,
            ) as popen,
            patch.object(start_server_module.threading, "Thread", return_value=fake_thread),
        ):
            returned = start_server_module.start_server(
                "invented-arma-dir",
                "invented-start-file",
                "invented-log-file",
            )

        self.assertIs(returned, fake_process)
        self.assertNotIn("env", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
