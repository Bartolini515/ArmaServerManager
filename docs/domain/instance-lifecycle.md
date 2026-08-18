# Instance lifecycle

An instance represents one Arma 3 server definition and its host-side files. The browser uses the REST endpoints in [the API contract](../api.md); the server-side lifecycle crosses models, generated files, Celery, Redis, SteamCMD, and host processes.

## Create

1. An authenticated user submits a name and HTML preset.
2. `InstanceSerializer` validates the safe name and preset filename.
3. The backend locks and selects the first available `Ports` row.
4. The instance is saved and the port is marked unavailable in one database transaction.
5. The preset parser extracts Workshop IDs.
6. A per-user server configuration is generated when needed, followed by an instance start script.
7. The generated path is stored on the instance and the creation operation log is written immediately through the standard-library logging context.

Non-staff users may create at most five instances. The instance is not ready until its required mods have been downloaded.

## Download mods

`download_mods` enqueues `download_mods_task`. The task:

- prevents duplicate active downloads through a cache key;
- extracts Workshop IDs and filters already-installed mods;
- invokes SteamCMD for missing mods;
- reports `PROGRESS` status to the frontend;
- normalizes addon directory casing, moves content from a temporary ghost folder, and cleans it;
- marks `is_ready=True` only after success;
- writes separate operation/download logs through one user-scoped logging context, records handled failures with a traceback, and clears its cache key on completion or failure.

The task requires configured Steam credentials and host paths. It must not run in repository-only checks.

## Start

Before enqueueing `start_server_task`, the API checks that the instance is not already running, that required mods are installed, and that its generated start script exists. It creates an initial log file when needed. A non-administrative user cannot have more than one of their instances running, and a global limit of five running non-administrative instances is enforced for non-staff users.

The task verifies the recorded PID is not already alive, launches the generated script from the Arma directory, stores the child PID, sets `is_running=True`, and streams output to the log. A temporary instance receives an `instance_timeout_task` scheduled one hour later; a new start revokes the previous timeout task ID stored in cache.

## Stop and timeout

`stop_server_task` finds the recorded PID and UDP-bound processes for the instance port. It terminates child processes first, waits up to 30 seconds, kills timed-out processes, then clears `pid` and `is_running`. It also revokes the cached timeout task. If no process can be found, it clears the flags and reports failure.

`instance_timeout_task` is intentionally small: it looks up the instance and delegates to the stop task only when the instance still reports running. Missing instances are ignored.

## Delete and logs

Running instances cannot be deleted. Deleting a stopped instance removes its preset, log, generated start file, and releases its port through the model override. Logs can be read completely or by tail count, downloaded, and deleted only while the instance is stopped.

## Administrative instances

Staff can see shared `is_admin_instance` rows, start/stop them, and replace a stopped administrative preset. Administrative operations use the superuser's identity for generated files/logging. Administrative instances do not receive the one-hour user timeout. Application operation logs are separate from the instance `log_file` streamed from the Arma process.

## Failure boundaries

- A Celery task can fail after the database row or log has already changed.
- A missing generated script, SteamCMD path, mod directory, log path, or process can leave an instance not ready or not running.
- The task result backend and cache are required for progress polling and duplicate-work prevention.
- Process termination is host-wide and must be verified manually in an approved test environment.
