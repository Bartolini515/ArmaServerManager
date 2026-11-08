import os
import subprocess
import threading
from collections.abc import Mapping

from .process_identity import (
    RUN_ID_ENVIRONMENT_KEY,
    ProcessIdentity,
    UnverifiedProcessError,
    capture_process_identity,
    get_verified_process,
)
from ..utils.process_output import stream_process_output


class ProcessCreationFailedError(RuntimeError):
    """Popen failed before returning a child process."""


class PostLaunchMayBeLiveError(RuntimeError):
    """A failure after Popen returned; the child may still be running."""

    def __init__(self, launch_identity: ProcessIdentity | None = None) -> None:
        super().__init__("The launched process may still be live.")
        self.launch_identity = launch_identity


def _capture_launch_identity(process, run_id: str):
    """Capture the child birth time while Popen still anchors its process handle."""
    if process.poll() is not None:
        raise UnverifiedProcessError("Launched process exited before identity capture")

    try:
        identity = capture_process_identity(process.pid)
    except Exception:
        raise UnverifiedProcessError("Launched process identity could not be captured") from None

    # Popen.poll() observes the process through the original child handle. If it
    # reports that child exited, a subsequent psutil lookup could already see a
    # reused PID; never attach that lookup result to the launch.
    if process.poll() is not None:
        raise UnverifiedProcessError("Launched process exited during identity capture")
    process._asm_process_identity = identity

    verified = get_verified_process(identity)
    if verified is None:
        raise UnverifiedProcessError("Launched process disappeared during identity capture")
    try:
        environment = verified.environ()
    except Exception:
        raise UnverifiedProcessError("Launched process marker could not be verified") from None
    if (
        not isinstance(environment, Mapping)
        or environment.get(RUN_ID_ENVIRONMENT_KEY) != run_id
    ):
        raise UnverifiedProcessError("Launched process marker does not match this run")

    if process.poll() is not None or get_verified_process(identity) is None:
        raise UnverifiedProcessError("Launched process is no longer anchored to Popen")
    return identity


def start_server(
    arma3_dir: str,
    start_file_path: str,
    log_file_path: str,
    *,
    run_id: str | None = None,
    launch_signal_guard=None,
) -> subprocess.Popen:
    """
    Start an Arma 3 server instance and log its output.
    
    Args:
        arma3_dir: Arma 3 directory path
        start_file_path: Path to the server start file
        log_file_path: Path to the log file (optional)
    
    Returns:
        subprocess.Popen: The running process object
    """
    args = [start_file_path]

    popen_options = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": arma3_dir,
        "text": True,
        "bufsize": 1,
        "universal_newlines": True,
    }
    if run_id is not None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be non-empty")
        if not callable(launch_signal_guard):
            raise ValueError("launch_signal_guard is required for managed starts")
        child_environment = os.environ.copy()
        child_environment["ASM_INSTANCE_RUN_ID"] = run_id
        popen_options["env"] = child_environment

    try:
        process = subprocess.Popen(args, **popen_options)
    except Exception:
        raise ProcessCreationFailedError(
            "Popen failed before returning a child process."
        ) from None

    try:
        if run_id is not None:
            _capture_launch_identity(process, run_id)

        def active_log_callback(line: str) -> None:
            try:
                os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

                with open(log_file_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(line)
                    log_file.flush()
            except Exception as e:
                raise e

        # Start streaming process output in a separate thread.
        output_thread = threading.Thread(
            target=stream_process_output,
            args=(process, active_log_callback),
            daemon=True,
        )
        output_thread.start()
    except Exception:
        try:
            identity = getattr(process, "_asm_process_identity", None)
        except Exception:
            identity = None
        if not isinstance(identity, ProcessIdentity):
            identity = None
        # No cleanup is safe here: after Popen returned, any of these failures
        # can leave its child live. The task retains UNKNOWN for review.
        raise PostLaunchMayBeLiveError(identity) from None

    return process
