"""Fail-closed discovery and verification for Arma server processes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import os

import psutil


RUN_ID_ENVIRONMENT_KEY = "ASM_INSTANCE_RUN_ID"


class UnverifiedProcessError(RuntimeError):
    """Raised when process identity or discovery cannot be verified safely."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created_at: float


def _validate_pid(pid: int) -> None:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")


def _read_creation_time(process: psutil.Process) -> float:
    try:
        created_at = float(process.create_time())
    except psutil.NoSuchProcess:
        raise
    except Exception:
        raise UnverifiedProcessError(
            "Process creation time could not be verified"
        ) from None

    if not math.isfinite(created_at) or created_at < 0:
        raise UnverifiedProcessError("Process creation time is invalid")
    return created_at


def _canonical_executable(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise UnverifiedProcessError("Executable path could not be verified")
    try:
        return os.path.normcase(os.path.realpath(path))
    except Exception:
        raise UnverifiedProcessError("Executable path could not be verified") from None


def capture_process_identity(pid: int) -> ProcessIdentity:
    """Capture the PID and process creation time, rejecting uncertain metadata."""
    _validate_pid(pid)
    try:
        process = psutil.Process(pid)
        created_at = _read_creation_time(process)
    except psutil.NoSuchProcess:
        raise UnverifiedProcessError(
            "Process disappeared while its identity was captured"
        ) from None
    except UnverifiedProcessError:
        raise
    except Exception:
        raise UnverifiedProcessError(
            "Process identity could not be captured"
        ) from None
    return ProcessIdentity(pid=pid, created_at=created_at)


def get_verified_process(identity: ProcessIdentity) -> psutil.Process | None:
    """Return the process only when its current PID birth time still matches."""
    if not isinstance(identity, ProcessIdentity):
        raise TypeError("identity must be a ProcessIdentity")
    _validate_pid(identity.pid)
    if not math.isfinite(identity.created_at) or identity.created_at < 0:
        raise ValueError("created_at must be a finite non-negative timestamp")

    try:
        process = psutil.Process(identity.pid)
        created_at = _read_creation_time(process)
    except psutil.NoSuchProcess:
        return None
    except UnverifiedProcessError:
        raise
    except Exception:
        raise UnverifiedProcessError(
            "Process identity could not be checked"
        ) from None

    if created_at != identity.created_at:
        raise UnverifiedProcessError("Process identity no longer matches")
    return process


def _connection_port(connection: object) -> int:
    try:
        local_address = connection.laddr  # type: ignore[attr-defined]
        if hasattr(local_address, "port"):
            port = local_address.port
        else:
            port = local_address[1]
    except Exception:
        raise UnverifiedProcessError(
            "UDP connection metadata could not be verified"
        ) from None

    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise UnverifiedProcessError("UDP connection port is invalid")
    return port


def _identity_is_current(pid: int, expected_created_at: float) -> bool:
    """Freshly check a PID birth time; return False only when it is gone."""
    try:
        process = psutil.Process(pid)
        created_at = _read_creation_time(process)
    except psutil.NoSuchProcess:
        return False
    except UnverifiedProcessError:
        raise
    except Exception:
        raise UnverifiedProcessError(
            "Process identity could not be rechecked"
        ) from None

    if created_at != expected_created_at:
        raise UnverifiedProcessError("Process identity changed during discovery")
    return True


def find_udp_processes(
    port: int,
    run_id: str,
    executable_path: str,
) -> list[ProcessIdentity]:
    """Find verified UDP owners for one port, marker, and executable.

    An empty list is only a socket-discovery observation, not proof that an
    instance has stopped. Callers must retain lifecycle uncertainty unless
    process identity and independent state checks confirm it.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be non-empty")
    if not isinstance(executable_path, str) or not executable_path:
        raise ValueError("executable_path must be non-empty")

    expected_executable = _canonical_executable(executable_path)
    try:
        connections = psutil.net_connections(kind="udp")
        candidate_pids: set[int] = set()
        for connection in connections:
            if _connection_port(connection) != port:
                continue
            pid = connection.pid  # type: ignore[attr-defined]
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise UnverifiedProcessError(
                    "UDP process ownership could not be verified"
                )
            candidate_pids.add(pid)
    except UnverifiedProcessError:
        raise
    except Exception:
        raise UnverifiedProcessError(
            "UDP process discovery could not be completed"
        ) from None

    identities: list[ProcessIdentity] = []
    for pid in sorted(candidate_pids):
        try:
            process = psutil.Process(pid)
            created_at = _read_creation_time(process)
        except psutil.NoSuchProcess:
            # A connection-table PID that has disappeared is confirmed absent.
            continue
        except UnverifiedProcessError:
            raise
        except Exception:
            raise UnverifiedProcessError(
                "UDP process identity could not be captured"
            ) from None

        try:
            current_connections = process.net_connections(kind="udp")
            owns_port = any(
                _connection_port(connection) == port
                for connection in current_connections
            )
        except psutil.NoSuchProcess:
            if not _identity_is_current(pid, created_at):
                continue
            raise UnverifiedProcessError(
                "UDP process ownership became unavailable"
            ) from None
        except Exception:
            raise UnverifiedProcessError(
                "UDP process ownership could not be verified"
            ) from None

        # The per-process socket read must belong to the birth time captured
        # above. This fresh Process prevents reuse of the first object's cache.
        if not _identity_is_current(pid, created_at):
            continue
        if not owns_port:
            raise UnverifiedProcessError("UDP process ownership observations conflict")

        try:
            environment = process.environ()
        except psutil.NoSuchProcess:
            if not _identity_is_current(pid, created_at):
                continue
            raise UnverifiedProcessError(
                "UDP process marker became unavailable"
            ) from None
        except Exception:
            raise UnverifiedProcessError(
                "UDP process marker could not be verified"
            ) from None

        if not isinstance(environment, Mapping):
            raise UnverifiedProcessError("UDP process marker could not be verified")
        try:
            marker = environment.get(RUN_ID_ENVIRONMENT_KEY)
        except Exception:
            raise UnverifiedProcessError(
                "UDP process marker could not be verified"
            ) from None
        if not _identity_is_current(pid, created_at):
            continue
        if marker != run_id:
            continue

        try:
            executable = process.exe()
        except psutil.NoSuchProcess:
            if not _identity_is_current(pid, created_at):
                continue
            raise UnverifiedProcessError(
                "UDP process executable became unavailable"
            ) from None
        except Exception:
            raise UnverifiedProcessError(
                "UDP process executable could not be verified"
            ) from None
        if not _identity_is_current(pid, created_at):
            continue
        if _canonical_executable(executable) != expected_executable:
            continue
        identities.append(ProcessIdentity(pid=pid, created_at=created_at))

    return identities
