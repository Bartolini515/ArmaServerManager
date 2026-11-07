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
