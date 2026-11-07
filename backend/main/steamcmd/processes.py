"""Owned SteamCMD subprocess startup and best-effort tree cleanup."""

from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
from typing import Any

import psutil
from ..utils.steam_errors import SteamCommandError


@dataclass(frozen=True)
class ProcessIdentity:
    """A process handle paired with the OS lifetime value that owns its PID."""

    process: Any
    pid: int
    create_time: float


class CleanupUncertainError(SteamCommandError):
    """Trusted internal marker that a child may still use the download workspace.

    Public text and diagnostics are deliberately static. ``original_exception``
    is retained only for trusted in-process error handling; callers must not
    serialize or log it.
    """

    cleanup_uncertain = True
    public_message = (
        "Nie udało się potwierdzić zakończenia procesu SteamCMD; "
        "pliki robocze zostały zachowane."
    )
    public_diagnostic = "SteamCMD process cleanup is unconfirmed"

    def __init__(self, original_exception: BaseException) -> None:
        super().__init__(self.public_message, diagnostic=self.public_diagnostic)
        self.original_exception = original_exception
        self.workspace_path: str | None = None
        self.diagnostic = self.public_diagnostic


def start_process(*args: Any, **kwargs: Any) -> subprocess.Popen:
    """Start a process in an isolated group and retain its initial identity."""
    if os.name == "posix":
        kwargs.setdefault("start_new_session", True)
    elif os.name == "nt":
        flags = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = flags | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    process = subprocess.Popen(*args, **kwargs)
    try:
        identity = _identity_for_pid(process.pid)
    except Exception:
        identity = None
    process._steamcmd_process_identity = identity
    process._steamcmd_owned_identities = []
    process._steamcmd_cleanup_incomplete = identity is None
    if os.name == "posix":
        process._steamcmd_process_group = process.pid
    elif os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP does not contain or account for reparented
        # descendants. Without an assigned Job Object, cleanup must fail closed.
        process._steamcmd_process_tree_contained = False
    return process


def capture_owned_processes(process: subprocess.Popen) -> list[ProcessIdentity]:
    """Capture descendants only while the original Popen lifetime is proven.

    On POSIX, the isolated group is enumerated only while its original leader
    is alive and matches the identity captured at spawn. Once that proof is
    lost, no process is adopted from the numeric group ID.
    """
    owned_identities = _metadata(process, "_steamcmd_owned_identities", ())
    identities = list(owned_identities) if isinstance(owned_identities, (list, tuple)) else []
    root_identity = _metadata(process, "_steamcmd_process_identity", None)
    group_id = _metadata(process, "_steamcmd_process_group", None)
    if not _root_lifetime_is_proven(process, root_identity):
        if group_id is not None:
            process._steamcmd_cleanup_incomplete = True
        if os.name == "nt" and _metadata(process, "_steamcmd_process_tree_contained", None) is False:
            process._steamcmd_cleanup_incomplete = True
        return _deduplicate(identities)

    try:
        root = psutil.Process(process.pid)
        root_create_time = root.create_time()
        if not _same_creation_time(root_create_time, root_identity.create_time):
            process._steamcmd_cleanup_incomplete = True
            return _deduplicate(identities)
        for child in root.children(recursive=True):
            identity = _identity_for_psutil(child)
            if identity is not None and identity.pid != process.pid:
                identities.append(identity)
    except (psutil.Error, OSError, AttributeError):
        process._steamcmd_cleanup_incomplete = True

    if group_id is not None and os.name == "posix":
        try:
            # The root is still alive and identity-checked, so this is a live
            # run membership snapshot, not adoption from a recycled PGID.
            for candidate in psutil.process_iter(attrs=["pid", "create_time"]):
                try:
                    pid = int(candidate.info["pid"])
                    if pid == process.pid or os.getpgid(pid) != group_id:
                        continue
                    create_time = float(candidate.info["create_time"])
                    identities.append(ProcessIdentity(candidate, pid, create_time))
                except (KeyError, TypeError, ValueError, OSError, psutil.Error):
                    continue
        except (OSError, psutil.Error):
            process._steamcmd_cleanup_incomplete = True

    identities = _deduplicate(identities)
    process._steamcmd_owned_identities = identities
    return identities


def terminate_owned_process_tree(
    process: subprocess.Popen,
    *,
    grace_seconds: float = 1.0,
) -> bool:
    """Terminate only verified process identities and report uncertain trees.

    Individual identities are checked immediately before each signal. Numeric
    process-group signaling is deliberately avoided because a PGID has no
    durable handle and may be recycled after its original leader exits.
    """
    if process is None:
        return False

    group_id = _metadata(process, "_steamcmd_process_group", None)
    root_identity = _metadata(process, "_steamcmd_process_identity", None)
    root_was_proven_live = _root_lifetime_is_proven(process, root_identity)
    identities = capture_owned_processes(process)
    complete = not bool(_metadata(process, "_steamcmd_cleanup_incomplete", False))

    # A POSIX process group is not a durable handle. If the root exits, late
    # descendants cannot be enumerated safely by PGID. We clean captured
    # identities but retain the workspace whenever that ownership is uncertain.
    if group_id is not None and not root_was_proven_live:
        complete = False
    if os.name == "nt" and _metadata(process, "_steamcmd_process_tree_contained", None) is False:
        # Windows process groups do not contain descendants. A Job Object would
        # be required to prove that late/reparented children are gone.
        complete = False
    if os.name == "nt" and getattr(process, "returncode", None) is not None:
        # A completed root cannot prove that a late or reparented child is gone.
        complete = False

    for identity in reversed(identities):
        current_process = _current_process(identity)
        if current_process is None:
            continue
        try:
            current_process.terminate()
        except (psutil.Error, OSError):
            complete = False

    for identity in reversed(identities):
        if not _wait_for_identity(identity, grace_seconds):
            try:
                current_process = _current_process(identity)
                if current_process is not None:
                    current_process.kill()
            except (psutil.Error, OSError):
                complete = False
            if not _wait_for_identity(identity, grace_seconds):
                complete = False

    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=grace_seconds)
    except (subprocess.TimeoutExpired, OSError, ProcessLookupError):
        complete = False
    except Exception:
        complete = False
    finally:
        for stream_name in ("stdout", "stderr"):
            try:
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    stream.close()
            except Exception:
                complete = False

    # Recheck captured identities after the Popen has been reaped. Do not
    # rescan by group number after losing the root lifetime proof.
    for identity in identities:
        if _identity_is_current(identity):
            complete = False

    if group_id is not None and os.name == "posix":
        # The captured identities are safe to signal, but without a kernel
        # group handle we cannot prove that no unobserved child appeared during
        # teardown. This is intentionally fail-closed for workspace retention.
        complete = False

    process._steamcmd_cleanup_incomplete = not complete
    return complete


def _root_lifetime_is_proven(process: subprocess.Popen, identity: ProcessIdentity | None) -> bool:
    if identity is None or getattr(process, "returncode", None) is not None:
        return False
    try:
        if process.poll() is not None:
            return False
    except Exception:
        return False
    return _identity_is_current(identity)


def _metadata(process: Any, name: str, default: Any) -> Any:
    try:
        return vars(process).get(name, default)
    except TypeError:
        return default


def _identity_for_pid(pid: int) -> ProcessIdentity:
    process = psutil.Process(pid)
    return ProcessIdentity(process, int(pid), float(process.create_time()))


def _identity_for_psutil(process: Any) -> ProcessIdentity | None:
    try:
        return ProcessIdentity(process, int(process.pid), float(process.create_time()))
    except (psutil.Error, OSError, AttributeError, TypeError, ValueError):
        return None


def _identity_is_current(identity: ProcessIdentity) -> bool:
    return _current_process(identity) is not None


def _current_process(identity: ProcessIdentity) -> Any | None:
    try:
        current = psutil.Process(identity.pid)
        if not current.is_running():
            return None
        if not _same_creation_time(float(current.create_time()), identity.create_time):
            return None
        return current
    except (psutil.Error, OSError, AttributeError, TypeError, ValueError):
        return None


def _same_creation_time(left: float, right: float) -> bool:
    return abs(left - right) < 0.000001


def _wait_for_identity(identity: ProcessIdentity, grace_seconds: float) -> bool:
    try:
        current = _current_process(identity)
        if current is None:
            # Waiting does not signal a recycled PID and can still reap the
            # process object whose lifetime this identity captured.
            identity.process.wait(timeout=grace_seconds)
        else:
            current.wait(timeout=grace_seconds)
    except psutil.TimeoutExpired:
        return not _identity_is_current(identity)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return True
    except psutil.Error:
        return not _identity_is_current(identity)
    return not _identity_is_current(identity)


def _deduplicate(identities: list[ProcessIdentity]) -> list[ProcessIdentity]:
    unique: dict[tuple[int, float], ProcessIdentity] = {}
    for identity in identities:
        unique[(identity.pid, identity.create_time)] = identity
    return list(unique.values())
