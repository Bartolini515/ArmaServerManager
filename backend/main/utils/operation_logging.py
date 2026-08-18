"""Per-operation logging built on Python's standard :mod:`logging` package."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
import logging
from pathlib import Path
from typing import Callable, TypeAlias
from uuid import uuid4


LogCallback: TypeAlias = Callable[[str], None]

_LOG_FORMAT = "%(asctime)s - %(levelname)s [%(operation)s] [user=%(user)s] - %(message)s"
_DATE_FORMAT = "%Y-%m-%d_%H-%M-%S"


def _formatter() -> logging.Formatter:
    return logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)


@contextmanager
def operation_loggers(
    logs_directory: str | Path,
    user: str,
    operations: Sequence[str],
    *,
    grouped: bool = False,
) -> Iterator[dict[str, logging.LoggerAdapter]]:
    """Create and close standard-library loggers for one application operation.

    ``grouped`` creates a run directory shared by all requested operation
    loggers. Otherwise each operation writes directly below ``logs_directory``.
    Operation files are created when the context opens; the shared error file
    is opened lazily only after an error record is emitted.
    """
    base_directory = Path(logs_directory)
    base_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")

    if grouped:
        output_directory = base_directory / f"{timestamp}_user_{user}"
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
    error_handler.setFormatter(_formatter())

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
            operation_handler.setFormatter(_formatter())
            handlers.append(operation_handler)

            logger = logging.getLogger(f"main.operation.{uuid4().hex}.{operation}")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            logger.addHandler(operation_handler)
            logger.addHandler(error_handler)
            loggers.append(logger)
            adapters[operation] = logging.LoggerAdapter(
                logger,
                {"operation": operation, "user": user},
            )

        yield adapters
    finally:
        for logger in loggers:
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
        for handler in handlers:
            handler.close()
        error_handler.close()
