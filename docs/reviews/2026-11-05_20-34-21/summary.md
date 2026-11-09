# Repository code review summary

Review date: 2026-11-05. This is a static, risk-focused review of the working tree, divided into [API and data model](backend-api.md), [backend operations](backend-operations.md), and [frontend](frontend.md). The review also inspected the repository check runner, test inventory, project settings, and documentation. It is not proof that all defects have been found. No application source was changed.

## Priority findings

The reports contain 19 entries, representing 18 distinct findings: one critical (P0), eight high (P1), and nine moderate (P2). The failed-instance-creation finding appears in both backend reports and is counted once here. Priorities indicate reviewer judgment, not confirmed exploitation.

1. **P0 — Host file deletion and process termination through writable API fields.** Authenticated users can update instance process state and paths, then invoke lifecycle actions using those values. The operating system privileges of the application and workers bound the impact. See [API report](backend-api.md#p0--writable-process-and-filesystem-fields-allow-host-side-damage).
2. **P1 — Missing instance ownership checks.** Instance operations use a global queryset or direct primary-key lookup, allowing cross-user access and operations. See [API report](backend-api.md#p1--instance-endpoints-do-not-enforce-ownership).
3. **P1 — Unsafe lifecycle and process handling.** User deletion can bypass instance cleanup; preset replacement can remove the existing file before validating the replacement; recycled PIDs can target unrelated processes; and concurrent starts can leave untracked servers. See the [API](backend-api.md) and [operations](backend-operations.md) reports.
4. **P1 — Predictable server administrator password.** Generated user-server configurations use a fixed password. See the [operations report](backend-operations.md#p1--generated-servers-use-a-known-administrator-password).
5. **P1 — Configuration and task-tracking failures in the UI.** Masked Steam credentials can prevent saving or overwrite a password, while queued tasks can be permanently shown as failed. See the [frontend report](frontend.md).

The remaining P2 findings concern incomplete cleanup on creation failure, stale configuration across processes, UDP fallback, SteamCMD error and concurrency handling, authentication and instance display state, and shutdown-time accuracy. Details, scenarios, locations, and suggested fixes are in the individual reports.

## Suggested sequence

First restrict writable server-controlled fields and enforce object authorization on every instance action. Address process identity and the fixed password alongside those boundaries. Next repair lifecycle cleanup and concurrency, credential editing, and task polling. Finally address the remaining operational and display defects, adding isolated regression coverage for each scenario.

## Verification and limits

`pipenv run check` completed successfully after rerunning with access to the parent directory: dependency checks, four documentation/check-runner tests, backend compilation, frontend lint, frontend build, and Git diff whitespace check passed. Lint reported 12 warnings and the build reported a large-chunk warning. Django tests were explicitly skipped because an isolated test database is not configured; the frontend has no test script. Findings were traced statically and were not reproduced against a live database, Steam, Redis, host processes, or production data. The check's Git diff stage does not inspect untracked report files.


## Remediation status

This appendix records the accepted remediation as of 2026-11-09. The historical review and its 2026-11-05 verification section remain unchanged. The review lists 19 report entries representing 18 distinct findings. This work resolves 17 selected original findings plus the separate user-reported SteamCMD credential-disclosure finding, for 18 distinct fixes. The failed-creation/file-generation issue appears twice in the original reports and is one fix. P1 — Generated servers use a known administrator password remains excluded by user direction and is not Resolved; mission behavior remains outside scope.

### API and data model

- **P0 — Writable process and filesystem fields allow host-side damage - Resolved** — commit c6651c8; regression backend/main/tests/test_instance_permissions.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [API report](backend-api.md#p0--writable-process-and-filesystem-fields-allow-host-side-damage).
- **P1 — Instance endpoints do not enforce ownership - Resolved** — commit c6651c8; regressions backend/main/tests/test_instance_permissions.py and backend/main/tests/test_task_permissions.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [API report](backend-api.md#p1--instance-endpoints-do-not-enforce-ownership).
- **P1 — Deleting a user bypasses instance cleanup and running-state safeguards - Resolved** — commit d2290e0; regression backend/main/tests/test_user_deletion.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [API report](backend-api.md#p1--deleting-a-user-bypasses-instance-cleanup-and-running-state-safeguards).
- **P1 — Preset replacement can delete the old file before validating its presence - Resolved** — commit d2290e0; regression backend/main/tests/test_instance_files.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [API report](backend-api.md#p1--preset-replacement-can-delete-the-old-file-before-validating-its-presence).
- **P2 — Creation failures strand instances and reserved ports - Resolved** / **P2 — A failed file-generation step leaves a reserved, broken instance - Resolved** — one fix, commit d2290e0; regression backend/main/tests/test_instance_files.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [API report](backend-api.md#p2--creation-failures-strand-instances-and-reserved-ports), [operations report](backend-operations.md#p2--a-failed-file-generation-step-leaves-a-reserved-broken-instance).

### Backend operations

- **P1 — Stopping an instance can terminate an unrelated process - Resolved** — commits a5a870b, a8eaad4, and 8632dd68d06de429660cdf607ee1756ef40f0cf4; regressions backend/main/tests/test_process_identity.py and backend/main/tests/test_instance_lifecycle.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [operations report](backend-operations.md#p1--stopping-an-instance-can-terminate-an-unrelated-process).
- **P1 — Duplicate starts are not serialized - Resolved** — commits c6651c8 and a8eaad4; regression backend/main/tests/test_instance_lifecycle.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [operations report](backend-operations.md#p1--duplicate-starts-are-not-serialized).
- **P2 — Configuration edits do not propagate to other processes - Resolved** — commit 4a3605c; regression backend/main/tests/test_config_consistency.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [operations report](backend-operations.md#p2--configuration-edits-do-not-propagate-to-other-processes).
- **P2 — UDP fallback compares a port number to a model object - Resolved** — commit a5a870b; regression backend/main/tests/test_process_identity.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [operations report](backend-operations.md#p2--udp-fallback-compares-a-port-number-to-a-model-object).
- **P2 — Rate-limit handling raises instead of waiting - Resolved** — commit 4146478; regression backend/main/tests/test_download_lifecycle.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [operations report](backend-operations.md#p2--rate-limit-handling-raises-instead-of-waiting).
- **P2 — Concurrent download guard is not atomic - Resolved** — commits 5c4bc77, 2ae07e8, 0841b91e3aab580431ed22cdb6810e6cbaae7f7d, and 40c344bb7662ede7b56f41106d1439fa07ef8ddc; regressions backend/main/tests/test_download_jobs.py, backend/main/tests/test_download_leases.py, backend/main/tests/test_download_lifecycle.py, and backend/main/tests/test_operation_logging.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [operations report](backend-operations.md#p2--concurrent-download-guard-is-not-atomic).
- **P2 — Failed Steam connection checks can leave a child running - Resolved** — commit 4146478; regression backend/main/tests/test_download_lifecycle.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests; [operations report](backend-operations.md#p2--failed-steam-connection-checks-can-leave-a-child-running).

### Frontend

- **P1 — Configuration edits can overwrite the Steam password or fail validation - Resolved** — commits df957da and 523ecf8; regression frontend/src/components/moderatorPanel/configPayload.test.ts; command from frontend: node --experimental-strip-types --no-warnings src/components/moderatorPanel/configPayload.test.ts; [frontend report](frontend.md#p1--configuration-edits-can-overwrite-the-steam-password-or-fail-validation).
- **P1 — A pending task is permanently misclassified as failed - Resolved** — commit 523ecf8; regression frontend/src/components/instances/taskState.test.ts; command from frontend: node --experimental-strip-types --no-warnings src/components/instances/taskState.test.ts; [frontend report](frontend.md#p1--a-pending-task-is-permanently-misclassified-as-failed).
- **P2 — Administrator instances can disappear after a direct page load - Resolved** — commit 523ecf8; synthetic browser smoke with mocked responses observed a fresh staff identity loading main/own/other sections with owner labels; no live-service claim; [frontend report](frontend.md#p2--administrator-instances-can-disappear-after-a-direct-page-load).
- **P2 — Temporary authentication errors discard a valid session - Resolved** — commit 4687e20; regression frontend/src/contexts/authSession.test.ts; command from frontend: node --experimental-strip-types --no-warnings src/contexts/authSession.test.ts; [frontend report](frontend.md#p2--temporary-authentication-errors-discard-a-valid-session).
- **P2 — Displayed shutdown time is not tied to the running instance - Resolved** — commits 523ecf8, a8eaad4, and 9da951f3b5c5cc4a1b1edce314ce98bf68c4eb1f; regression frontend/src/components/instances/taskState.test.ts; command from frontend: node --experimental-strip-types --no-warnings src/components/instances/taskState.test.ts; synthetic browser smoke observed separate running-card shutdown displays that disappeared when stop completed; no live-service claim; [frontend report](frontend.md#p2--displayed-shutdown-time-is-not-tied-to-the-running-instance).

### Additional user-reported credential disclosure

- **P1 — Failed SteamCMD authentication exposes login credentials - Resolved** — commit ce87d74; regression backend/main/tests/test_steamcmd_secrets.py; full-run command: $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests. This finding came from the user and has no historical review-report anchor.

### Final source verification

Root accepted source head 9da951f3b5c5cc4a1b1edce314ce98bf68c4eb1f. Terra Spec and Quality review of the six-finding source-fix wave passed. The focused guarded backend run passed 123 tests in 4.459 seconds with clean Django checks. The full guarded backend command, $env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py main.tests, ran 382 tests in 17.866 seconds: five skipped and the remaining 377 passed; Django checks were clean. The taskState Node regression passed, including +05:30, +05:45, and -03:30; the authSession and configPayload Node regressions also passed.

On 2026-11-09, root ran `$env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run check` against the R1-corrected documentation freeze on accepted source. It exited 0 and all stages passed, including four documentation/check-runner tests. Lint reported zero errors and eleven existing warnings; the build passed with a 741.86 kB chunk advisory. The safe check skips Django behavior tests. Separately, the guarded backend run reported 382 tests total, five skipped and 377 passed, with clean Django checks.

The source-wave review noted one test-evidence limit: unittest.assertRaises clears the exception traceback, so the preparation-failure regression checks the safe typed error and suppression flag rather than retaining a full task-wrapper stack. The source explicitly raises from None. The resolver guard covers five patched Python entry points, not native-code bypasses. SQLite/fakes/temporary files do not prove live MySQL locks, Redis/TLS/broker behavior, SteamCMD or Arma operation, alternate-UID deployment, production migrations, power-loss durability, or hostile privileged filesystem behavior.

The earlier unguarded default-database manage.py test and the stopped unbounded mock-render attempt are excluded from isolated RED/GREEN evidence. No protected runtime records were inspected to investigate them. This report does not claim that no service was contacted during the full earlier task.
