# Backend operations review

Scope: Celery lifecycle tasks, SteamCMD integration, generated files, process output, configuration, and their API call sites. Findings are based on static inspection of the current working tree; host processes, Steam, Redis, and databases were not exercised. Priorities express impact and urgency, not proof of exploitation.

## Findings

### P1 — Stopping an instance can terminate an unrelated process

`backend/main/tasks.py:154-160` accepts any live process with the stored PID, without checking process identity or creation time; `:175-193` terminates it and its descendants. If an Arma process has exited and the OS has recycled its PID before reconciliation or a stop request, a different host process can be killed. The same unchecked PID is also considered evidence of a running server at `:117-121` and `:220-224`. Persist and verify a stable process identity before signalling it.

### P1 — Generated servers use a known administrator password

`backend/main/views.py:224-230` supplies the literal `test` as the password for every newly generated user server configuration. `backend/main/modpreset/start_files.py:17-20` assigns that value both to `passwordAdmin` and to the server password. Anyone who can reach such a server and knows this fixed value can join and potentially use its administrator access. Require a unique, securely supplied password for each configuration.

### P1 — Duplicate starts are not serialized

`backend/main/views.py:278-285` checks persisted running flags before queuing, but `backend/main/tasks.py:114-132` sets those flags only after launching. Two requests or workers can pass the checks concurrently, start two processes, and overwrite the recorded PID; the same gap bypasses the per-user and global running limits. A subsequent stop may leave an untracked process alive. Reserve the start atomically and serialize launches per instance and across enforced limits.

### P2 — Configuration edits do not propagate to other processes

`backend/main/utils/config.py:19-23,63-79,83-98` loads a module-global object once, and `update` reloads only the writer's copy. The API and Celery workers normally run in separate processes, so editing paths or Steam credentials through `backend/main/views.py:150-155` leaves other workers using their old values until restart. Multiple API workers can likewise display inconsistent settings or overwrite one another's changes. Reload or distribute configuration changes with concurrency control.

### P2 — UDP fallback compares a port number to a model object

`backend/main/tasks.py:162-165` compares `conn.laddr.port` (an integer) with `instance.port` (a `Ports` model, `backend/main/models.py:53`), so a matching UDP port will not be collected. When the recorded PID is missing, the task can report failure, clear the running state, and leave the real process running. Compare against `instance.port.port_number`, while also checking process identity before termination.

### P2 — Rate-limit handling raises instead of waiting

`backend/main/steamcmd/mods_download.py:4` imports `time` as a function, but the return-code-84 branch calls `time.sleep(600)` at `:123-127`. That raises `AttributeError` whenever this branch is reached. The task fails immediately, does not process remaining batches, and skips the cleanup at `:160-161`. Call the imported `sleep` and guarantee cleanup on failures.

### P2 — Concurrent download guard is not atomic

`backend/main/tasks.py:30-38` performs separate cache `get` and `set` operations, so two workers can both observe no task and download the same instance concurrently. They share the deterministic ghost directory (`backend/main/steamcmd/mods_download.py:94`, `backend/main/workaround/ghost_folder.py:17-24`), and either can move or delete files while the other is using them. The four-hour expiry can also admit another download while an earlier long retry is active. Use an atomic, ownership-aware lock with an appropriate lifetime and isolated temporary directories.

### P2 — Failed Steam connection checks can leave a child running

`backend/main/steamcmd/mods_download.py:279-287` starts SteamCMD with piped output and calls `wait(timeout=10)` without draining either pipe. A verbose child can block on a full pipe; a slow login can also exceed the timeout. In both cases `TimeoutExpired` propagates without terminating or reaping the child, and the ghost-folder cleanup in `:160-161` is bypassed. Consume output and terminate/reap on timeout, with cleanup in a `finally` block.

### P2 — A failed file-generation step leaves a reserved, broken instance

`backend/main/views.py:207-215` commits the instance and port reservation before parsing the preset and generating files at `:217-243`. If parsing or filesystem creation fails, the request errors while the row and reserved port remain, potentially with no start script; retrying creation with the same name then fails. Compensate on failure or represent and recover incomplete setup explicitly.

## Coverage and limitations

Reviewed the paths above together with `process_output.py`, Steam Guard generation, preset parsing, mod path checks, ghost-folder helpers, configuration and lifecycle documentation, and existing test locations. No runtime host operation, service connection, or Django test was performed. Existing logging tests cover Steam Guard error reporting, but the reviewed tests do not establish the lifecycle concurrency, PID reuse, timeout, or SteamCMD rate-limit scenarios above. Other repository areas are covered by separate reports.
