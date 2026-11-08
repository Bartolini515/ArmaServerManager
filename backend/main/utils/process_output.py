from __future__ import annotations

import threading
import time
from typing import Callable
from .operation_logging import LogCallback
from .steam_errors import (
    SteamSecretRedactor,
    safe_steam_diagnostic,
    steam_command_error,
)


class ProcessOutputCancelledError(RuntimeError):
    """Static cancellation marker carrying bounded reader-thread evidence."""

    def __init__(self, readers=(), *, original_exception: BaseException | None = None) -> None:
        super().__init__("SteamCMD output was cancelled after lease loss.")
        self._readers = tuple(readers)
        self.original_exception = original_exception

    def readers_quiesced(self, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        for reader in self._readers:
            if reader.ident is not None:
                reader.join(max(0.0, deadline - time.monotonic()))
        return not any(reader.is_alive() for reader in self._readers)


def reader_thread(
    pipe,
    log_callback: LogCallback | None = None,
    secret_redactor: SteamSecretRedactor | None = None,
    *,
    cancel_event: threading.Event | None = None,
    callback_lock: threading.Lock | None = None,
    callback_gate: threading.Lock | None = None,
) -> None:
    safe_log_callback = (
        secret_redactor.callback(log_callback)
        if secret_redactor is not None
        else log_callback
    )
    admission_gate = callback_gate or threading.Lock()
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
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

            if cancel_event is None:
                if safe_log_callback:
                    print(processed_line)
                    safe_log_callback(processed_line)
                else:
                    print(processed_line)
                continue

            lock = callback_lock or threading.Lock()
            with lock:
                if cancel_event.is_set():
                    break
                # Reserve callback admission in a short critical section. The
                # passing reader remains the in-flight accounting token until
                # callback dispatch completes. Cancellation never waits on this
                # gate or callback lock to reach owned-process cleanup.
                with admission_gate:
                    if cancel_event.is_set():
                        break
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
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    if secret_redactor is None:
        try:
            configured_redactor = SteamSecretRedactor.from_config()
        except Exception:
            configured_redactor = None
        if configured_redactor is not None and configured_redactor.has_secrets:
            secret_redactor = configured_redactor

    if cancel_check is not None:
        _stream_process_output_cancellable(
            process,
            log_callback,
            secret_redactor,
            cancel_check,
        )
        return

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


def _stream_process_output_cancellable(
    process,
    log_callback: LogCallback | None,
    secret_redactor: SteamSecretRedactor | None,
    cancel_check: Callable[[], bool],
) -> None:
    """Supervise output and cancellation independently of blocked readline calls."""
    cancelled = threading.Event()
    callback_lock = threading.Lock()
    callback_gate = threading.Lock()
    reader_errors: list[BaseException] = []

    def stop_readers() -> None:
        # Readers only hold this gate while admitting a callback, never while
        # running it, so this cannot wait on blocked logging or database I/O.
        with callback_gate:
            cancelled.set()

    def run_reader(pipe) -> None:
        try:
            reader_thread(
                pipe,
                log_callback,
                secret_redactor,
                cancel_event=cancelled,
                callback_lock=callback_lock,
                callback_gate=callback_gate,
            )
        except BaseException as exc:
            if not cancelled.is_set():
                reader_errors.append(exc)

    readers = [
        threading.Thread(target=run_reader, args=(process.stdout,), daemon=True),
        threading.Thread(target=run_reader, args=(process.stderr,), daemon=True),
    ]
    try:
        for reader in readers:
            reader.start()
        while True:
            if _cancellation_requested(cancel_check):
                # Return directly to the owned-process supervisor. A reader may
                # be blocked inside buffered readline/close or user logging; the
                # owner must first terminate and reap its child without waiting
                # for either reader.
                stop_readers()
                raise ProcessOutputCancelledError(readers) from None

            readers_alive = any(reader.is_alive() for reader in readers)
            try:
                process_alive = process.poll() is None
            except Exception:
                process_alive = True
            if not readers_alive and not process_alive:
                break
            cancelled.wait(0.05)
    except ProcessOutputCancelledError:
        raise
    except BaseException as exc:
        stop_readers()
        raise ProcessOutputCancelledError(
            readers,
            original_exception=exc,
        ) from None

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


def _cancellation_requested(cancel_check: Callable[[], bool]) -> bool:
    try:
        return bool(cancel_check())
    except Exception:
        return True
