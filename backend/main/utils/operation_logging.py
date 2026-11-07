"""Per-operation logging built on Python's standard :mod:`logging` package."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import copy
from datetime import datetime
import logging
from pathlib import Path
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


@contextmanager
def operation_loggers(
    logs_directory: str | Path,
    user: str,
    operations: Sequence[str],
    *,
    grouped: bool = False,
    secret_redactor: SteamSecretRedactor | None = None,
) -> Iterator[dict[str, logging.LoggerAdapter]]:
    """Create and close standard-library loggers for one application operation.

    ``grouped`` creates a run directory shared by all requested operation
    loggers. Otherwise each operation writes directly below ``logs_directory``.
    Operation files are created when the context opens; the shared error file
    is opened lazily only after an error record is emitted.
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

    error_handler = logging.FileHandler(
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
            operation_handler = logging.FileHandler(
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
        for logger in loggers:
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
        for handler in handlers:
            handler.close()
        error_handler.close()
