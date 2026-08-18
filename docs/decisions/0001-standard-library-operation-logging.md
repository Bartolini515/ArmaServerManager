# ADR-0001: Use standard-library logging for application operations

- Status: accepted
- Date: 2026-08-18
- Owners: ArmaServerManager maintainers

## Context

The application used a custom `Logger` class that buffered messages in process-global collections, manually formatted timestamps, and flushed files only at selected call sites. Celery downloads needed two operation streams and a shared error stream, while request handlers needed timestamped files. Separately, `Instances.log_file` stores stdout/stderr from the Arma process and is exposed through the instance log API.

## Decision

Use Python's standard `logging` module and `LoggerAdapter` objects created by the `operation_loggers` context. The context owns per-operation `FileHandler`s, attaches a shared `ERROR` handler for the operation's error file, writes UTF-8 records immediately, and closes all handlers in a `finally` block. Existing callback-based helper interfaces remain callback-based; callers pass methods such as `logger.info` and `logger.exception`.

Create/start/preset-change requests keep timestamped files in `paths.logs_directory`. A Celery download creates one timestamped user directory with `log_operations.txt` and `log_download.txt`. Existing custom-logger files remain in place, and no automatic retention or rotation is introduced. The application operation logger does not replace the media-backed Arma process log.

## Alternatives considered

- Keep the custom class and replace only its file-writing code: rejected because it preserves global state, buffering, and a second logging abstraction.
- Use an external structured-logging dependency: rejected because the standard library meets the current file and callback contract without a production dependency.
- Configure only static Django `LOGGING` handlers: rejected because the required per-operation filenames and user-scoped Celery directory are created dynamically at operation start.

## Consequences

Positive effects include immediate persistence, standard levels and traceback handling, handler cleanup on every control-flow path, and isolation between concurrent operation sessions. The configured logs directory can accumulate files indefinitely, and dynamic handlers remain an operational filesystem concern. The Arma process log continues to have its existing media/API lifecycle.

## Verification and follow-up

`backend/main/tests/test_operation_logging.py` verifies file layout, formatting, error tracebacks, lazy error files, handler cleanup, session isolation, and Steam Guard error callbacks. The repository check continues to skip Django tests until an isolated database configuration exists.
