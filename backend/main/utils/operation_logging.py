"""Per-operation logging built on Python's standard :mod:`logging` package."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import copy
from datetime import datetime
import logging
from pathlib import Path
import sys
import threading
import traceback
from typing import Callable, TypeAlias
from uuid import uuid4

from .steam_errors import (
    SteamSecretRedactor,
    safe_exception_details,
    safe_steam_diagnostic,
    safe_exception_type,
)


LogCallback: TypeAlias = Callable[[str], None]

_LOG_FORMAT = "%(asctime)s - %(levelname)s [%(operation)s] [user=%(user)s] - %(message)s"
_DATE_FORMAT = "%Y-%m-%d_%H-%M-%S"


class _SecretRedactingFormatter(logging.Formatter):
    def __init__(self, secret_redactor: SteamSecretRedactor) -> None:
        super().__init__(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        self.secret_redactor = secret_redactor

    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy(record)
        if record.exc_info:
            safe_record.msg = "Operation failed"
            safe_record.args = ()
        elif isinstance(record.msg, BaseException):
            safe_record.msg = safe_steam_diagnostic(
                record.msg,
                self.secret_redactor,
            )
            safe_record.args = ()
        elif isinstance(record.args, tuple):
            safe_record.args = tuple(
                safe_steam_diagnostic(argument, self.secret_redactor)
                if isinstance(argument, BaseException)
                else argument
                for argument in record.args
            )
        safe_record.exc_info = None
        safe_record.exc_text = None
        formatted = self.secret_redactor.redact(super().format(safe_record))

        if not record.exc_info:
            return formatted

        _exception_type, exception, traceback_object = record.exc_info
        exception_name = (
            safe_exception_type(exception)
            if exception is not None
            else "Exception"
        )
        details = (
            safe_exception_details(exception, self.secret_redactor)
            if exception is not None
            else None
        )
        summary = f"Exception: {exception_name}"
        if details:
            summary = f"{summary}: {details}"

        locations = []
        if traceback_object is not None:
            for frame in traceback.extract_tb(traceback_object):
                locations.append(
                    self.secret_redactor.redact(
                        f"  at {frame.filename}:{frame.lineno} in {frame.name}"
                    )
                )

        return "\n".join((formatted, self.secret_redactor.redact(summary), *locations))


def _formatter(
    secret_redactor: SteamSecretRedactor | None = None,
) -> logging.Formatter:
    if secret_redactor is not None:
        return _SecretRedactingFormatter(secret_redactor)
    return logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)


class _DeferredCloseFileHandler(logging.FileHandler):
    """Close only after every already-admitted record has left the handler."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._deferred_state_lock = threading.Lock()
        self._deferred_close_requested = False
        self._deferred_active_records = 0
        self._deferred_close_started = False

    def handle(self, record: logging.LogRecord) -> bool:
        accepted = self.filter(record)
        if not accepted:
            return accepted

        with self._deferred_state_lock:
            if self._deferred_close_requested:
                return False
            # Admission is the linearization point. The call remains accounted
            # for even if it waits on the FileHandler lock or its emit blocks.
            self._deferred_active_records += 1

        try:
            self.acquire()
            try:
                self.emit(record)
            finally:
                self.release()
        finally:
            close_now = False
            with self._deferred_state_lock:
                self._deferred_active_records -= 1
                if (
                    self._deferred_close_requested
                    and self._deferred_active_records == 0
                    and not self._deferred_close_started
                ):
                    self._deferred_close_started = True
                    close_now = True
            if close_now:
                self._close_deferred()
        return accepted

    def defer_close(self) -> None:
        close_now = False
        with self._deferred_state_lock:
            self._deferred_close_requested = True
            if self._deferred_active_records == 0 and not self._deferred_close_started:
                self._deferred_close_started = True
                close_now = True

        # Remove the detached handler from logging.shutdown's registry too;
        # shutdown must not reintroduce a wait on its active emit lock.
        remove_handler_ref = getattr(logging, "_removeHandlerRef", None)
        if callable(remove_handler_ref):
            for handler_ref in tuple(getattr(logging, "_handlerList", ())):
                try:
                    if handler_ref() is self:
                        remove_handler_ref(handler_ref)
                except Exception:
                    continue

        if close_now:
            self._close_deferred()

    def _close_deferred(self) -> None:
        try:
            super().close()
        except Exception:
            # Deferred close must not replace the task's already-recorded
            # cleanup uncertainty or escape from a reader callback thread.
            pass


@contextmanager
def operation_loggers(
    logs_directory: str | Path,
    user: str,
    operations: Sequence[str],
    *,
    grouped: bool = False,
    secret_redactor: SteamSecretRedactor | None = None,
    defer_close_on_exception: bool | Callable[[BaseException], bool] = False,
) -> Iterator[dict[str, logging.LoggerAdapter]]:
    """Create and close standard-library loggers for one application operation.

    ``grouped`` creates a run directory shared by all requested operation
    loggers. Otherwise each operation writes directly below ``logs_directory``.
    Operation files are created when the context opens; the shared error file
    is opened lazily only after an error record is emitted. Explicit deferred
    close mode applies only when an exception exits the context; admitted
    records finish on their existing caller threads, without a cleanup thread.
    """
    base_directory = Path(logs_directory)
    base_directory.mkdir(parents=True, exist_ok=True)
    timestamp = (
        f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')}-{uuid4().hex}"
    )
    safe_user = (
        secret_redactor.redact(user)
        if secret_redactor is not None
        else user
    )

    if grouped:
        output_directory = base_directory / f"{timestamp}_user_{safe_user}"
        output_directory.mkdir(parents=True, exist_ok=True)
        error_path = output_directory / "errors.txt"
    else:
        output_directory = base_directory
        error_path = output_directory / f"errors_{timestamp}.txt"

    handler_type = (
        _DeferredCloseFileHandler
        if defer_close_on_exception is not False
        else logging.FileHandler
    )
    error_handler = handler_type(
        error_path,
        mode="a",
        encoding="utf-8",
        delay=True,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(_formatter(secret_redactor))

    adapters: dict[str, logging.LoggerAdapter] = {}
    loggers: list[logging.Logger] = []
    handlers: list[logging.Handler] = []
    try:
        for operation in operations:
            operation_path = output_directory / (
                f"log_{operation}.txt"
                if grouped
                else f"log_{operation}_{timestamp}.txt"
            )
            operation_handler = handler_type(
                operation_path,
                mode="a",
                encoding="utf-8",
            )
            operation_handler.setLevel(logging.INFO)
            operation_handler.setFormatter(_formatter(secret_redactor))
            handlers.append(operation_handler)

            logger = logging.getLogger(f"main.operation.{uuid4().hex}.{operation}")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            logger.addHandler(operation_handler)
            logger.addHandler(error_handler)
            loggers.append(logger)
            adapters[operation] = logging.LoggerAdapter(
                logger,
                {"operation": operation, "user": safe_user},
            )

        yield adapters
    finally:
        current_exception = sys.exc_info()[1]
        defer_close = False
        if current_exception is not None:
            if callable(defer_close_on_exception):
                try:
                    defer_close = bool(defer_close_on_exception(current_exception))
                except Exception:
                    defer_close = False
            else:
                defer_close = bool(defer_close_on_exception)

        for logger in loggers:
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
        if defer_close:
            for handler in (*handlers, error_handler):
                if isinstance(handler, _DeferredCloseFileHandler):
                    handler.defer_close()
                else:
                    handler.close()
        else:
            for handler in handlers:
                handler.close()
            error_handler.close()
