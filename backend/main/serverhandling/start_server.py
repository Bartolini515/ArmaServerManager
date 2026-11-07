import os
import subprocess
import threading
from ..utils.process_output import stream_process_output


def start_server(
    arma3_dir: str,
    start_file_path: str,
    log_file_path: str,
    *,
    run_id: str | None = None,
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
        child_environment = os.environ.copy()
        child_environment["ASM_INSTANCE_RUN_ID"] = run_id
        popen_options["env"] = child_environment

    process = subprocess.Popen(args, **popen_options)
    
    def active_log_callback(line: str) -> None:
            try:
                os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
                
                with open(log_file_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(line)
                    log_file.flush()
            except Exception as e:
                raise e
    
    # Start streaming process output in a separate thread
    output_thread = threading.Thread(
        target=stream_process_output,
        args=(process, active_log_callback),
        daemon=True  # Thread will die when main program exits
    )
    output_thread.start()
    
    return process
