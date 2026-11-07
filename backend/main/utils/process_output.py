import threading
from .operation_logging import LogCallback
from .steam_errors import (
    SteamSecretRedactor,
    safe_steam_diagnostic,
    steam_command_error,
)


def reader_thread(
    pipe,
    log_callback: LogCallback | None = None,
    secret_redactor: SteamSecretRedactor | None = None,
) -> None:
    safe_log_callback = (
        secret_redactor.callback(log_callback)
        if secret_redactor is not None
        else log_callback
    )
    try:
        while True:
            line = pipe.readline()
            if not line:
                break
            
            processed_line = ""
            if isinstance(line, bytes):
                processed_line = line.decode('utf-8', errors='ignore').strip() + '\n'
            else: # It's a string
                processed_line = line.strip() + '\n'

            if secret_redactor is not None:
                processed_line = secret_redactor.redact(processed_line)

            if safe_log_callback:
                print(processed_line)
                safe_log_callback(processed_line)
            else:
                print(processed_line)
    finally:
        pipe.close()


def stream_process_output(
    process,
    log_callback: LogCallback | None = None,
    secret_redactor: SteamSecretRedactor | None = None,
) -> None:
    if secret_redactor is None:
        try:
            configured_redactor = SteamSecretRedactor.from_config()
        except Exception:
            configured_redactor = None
        if configured_redactor is not None and configured_redactor.has_secrets:
            secret_redactor = configured_redactor

    if secret_redactor is None:
        stdout_thread = threading.Thread(
            target=reader_thread,
            args=(process.stdout, log_callback),
        )
        stderr_thread = threading.Thread(
            target=reader_thread,
            args=(process.stderr, log_callback),
        )
        stdout_thread.start()
        stderr_thread.start()
        stdout_thread.join()
        stderr_thread.join()
        process.wait()
        return

    reader_errors: list[BaseException] = []

    def run_reader(pipe) -> None:
        try:
            reader_thread(pipe, log_callback, secret_redactor)
        except Exception as exc:
            reader_errors.append(exc)

    stdout_thread = threading.Thread(target=run_reader, args=(process.stdout,))
    stderr_thread = threading.Thread(target=run_reader, args=(process.stderr,))

    stdout_thread.start()
    stderr_thread.start()

    stdout_thread.join()
    stderr_thread.join()

    if reader_errors:
        exception = reader_errors[0]
        if secret_redactor is None:
            raise exception
        diagnostic = safe_steam_diagnostic(exception, secret_redactor)
        safe_log_callback = secret_redactor.callback(log_callback)
        if safe_log_callback:
            safe_log_callback(f"SteamCMD output error: {diagnostic}")
        raise steam_command_error(
            exception,
            secret_redactor,
            message="Nie udało się odczytać wyjścia SteamCMD.",
        ) from None

    wait_error: Exception | None = None
    try:
        process.wait()
    except Exception as exc:
        if secret_redactor is None:
            raise
        wait_error = exc
    if wait_error is not None:
        diagnostic = safe_steam_diagnostic(wait_error, secret_redactor)
        safe_log_callback = secret_redactor.callback(log_callback)
        if safe_log_callback:
            safe_log_callback(f"SteamCMD output error: {diagnostic}")
        raise steam_command_error(wait_error, secret_redactor) from None
