# Known risks and limitations

This is a factual inventory of behavior observed in the current repository. It is not a security fix list, and none of the items below should be silently “corrected” while editing documentation.

## Authorization and object scope

Several instance actions look up an object by primary key after authenticating the request, without a consistent owner filter. The list and create paths distinguish user instances, but start, stop, download, log, and delete paths should be reviewed separately before treating the UI as an authorization boundary. Mission upload is authenticated but has no uploader relation. Evidence: `backend/main/views.py` and `backend/main/models.py`.

## Host-side process and filesystem control

Celery tasks invoke `bash`, SteamCMD, generated shell scripts, `subprocess.Popen`, `psutil` process discovery, termination, and file deletion. Incorrect configuration or permissions can affect host processes and files beyond one database row. These tasks require an approved isolated host and are excluded from the safe repository check.

## Secrets and runtime state

`backend/data/config.json` can contain Steam username, password, and shared secret values. Django environment variables contain signing/database credentials. Responses mask two Steam fields, but local files, logs, and process arguments remain sensitive. Never copy runtime configuration into fixtures or Markdown.

## External service dependency

Normal instance operation requires MySQL, Redis, Celery workers, SteamCMD, an Arma 3 installation, writable media paths, and available host ports. The repository has no standardized Docker/runtime contract yet.

## Test isolation

The existing Django tests use Django's configured database settings and are not part of `pipenv run check` until an isolated test configuration is established. The check reports this skip explicitly. The dependency-free documentation/check tests do not exercise API, database, Celery, or host behavior.

## Frontend lint debt

The blocking ESLint errors are addressed as type-safety cleanup. Non-blocking warnings about hook dependencies and Fast Refresh remain visible after lint passes and should be handled in a focused frontend maintenance task.

## Consistency boundaries

Database updates, file cleanup, task state, and host process state are not one transaction. Repeated attendance-like operations are not relevant here, but the same general rule applies: a successful HTTP response or task enqueue does not prove that the external operation completed. Use task status and logs as evidence.
