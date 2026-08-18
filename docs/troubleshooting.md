# Troubleshooting

This guide is limited to safe, read-only repository diagnosis. It intentionally does not define deployment, Docker, service restart, SteamCMD execution, process termination, database repair, or production cleanup.

## Start with the source of truth

Check the relevant flow in [the code map](code-map.md), then compare API behavior with [api.md](api.md), data relationships with [data-model.md](data-model.md), and runtime keys with [configuration.md](configuration.md). Do not inspect ignored secrets or production data as a shortcut.

## `pipenv run check` fails

- `dependencies`: repair the local Python environment from the committed lockfile; do not change production dependencies casually.
- `documentation tests`: inspect the missing file or relative-link target named by the test.
- `backend syntax`: inspect the reported Python source; this stage does not import Django settings.
- `frontend lint`: fix a real type/error contract; do not disable a rule. Warnings are non-blocking and remain documented in [known risks](known-risks.md).
- `frontend build`: inspect TypeScript/Vite output and keep API shapes unchanged.
- `git diff check`: inspect whitespace in the diff, including unrelated changes.

The runner explicitly prints that Django tests were skipped because no isolated database configuration exists.

## Instance is not ready

The create path may have succeeded while preset parsing, generated-file creation, or mod installation failed. Verify the serialized `is_ready` flag, the task result, and the operation/download log in an approved test environment. Check configured paths only through safe, non-secret test values.

## Mod download is stuck or duplicated

The task uses Redis cache keys and Celery result state. Compare the frontend task ID with `instances/task_status/<task_id>/` and inspect the task result/log. Do not delete cache entries or kill workers manually without an approved operational procedure.

## Server will not start

The API refuses to enqueue a start when required mods are missing, the instance is already running, or the generated start script is absent. A queued task can still fail because the Arma path, script permissions, Steam content, port, or log path is unavailable.

## Server will not stop

The stop task relies on the recorded PID and UDP connections for the instance port. A stale PID, changed port, missing permissions, or another process using the port can produce a failure. Do not use ad-hoc kill commands; escalate to an approved host operator.

## Logs are missing

Logs are created lazily on first start and stored through the instance `log_file` field. A missing file, deleted instance, unavailable media path, or failed process startup can produce a `404`. The model deletion path removes logs when an instance is deleted.

## Mission replacement is unexpected

Uploading a `.pbo` with the same stored filename deletes the existing mission before creating the new record. There is no version history or uploader relation in the current schema.
