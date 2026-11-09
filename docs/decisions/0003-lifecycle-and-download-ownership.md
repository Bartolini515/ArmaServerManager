# ADR-0003: Durable instance lifecycle and download ownership

- Status: accepted
- Date: 2026-11-09
- Owners: ArmaServerManager maintainers

## Context

Instance tasks cross database rows, Celery delivery, Redis coordination, managed files, and host processes. A queued task ID alone cannot establish the owning instance, current attempt, process identity, or whether work is still active after a timeout or delivery ambiguity. File and process side effects also outlive an HTTP request and cannot join one database transaction.

This ADR records the accepted lifecycle, managed-storage, and download-worker decisions, their API mapping, and their operational costs. The final source is committed at 9da951f.

## Decision

- Persist each start, stop, and download task's owning instance before dispatch. Expose task IDs as identifiers only; API status access still uses owner-or-staff checks.
- Serialize lifecycle reservations through a single database lock row and acquire it before owner, instance, or port locks. Count starting reservations toward existing limits.
- Associate a server run with a run ID and verified process observations. For the direct/discovered root, check the current run/task attempt, marker, birth time, and persisted canonical executable before signaling. Descendant candidates use birth-time, marker, and verified-ancestry checks; the root executable is not compared to each child. Keep legacy or uncertain rows in `unknown` for operator attention.
- Use a per-instance shutdown deadline after successful launch and fence stale timeout/stop deliveries to the current run and attempt.
- Use the managed-file journal for regular files under configured trusted roots, with private same-filesystem stages, rollback evidence, and an explicit database commit marker.
- Combine a durable database task claim with an ownership-aware renewable Redis lease for shared download work. Check the claim and lease before shared writes/publication, including retry waits and cancellation paths. Retain unfinished attention when process/reader quiescence is uncertain. Cancellation closes callback admission only; already-admitted work may be suppressed or finish later, with no promise about its first physical instruction. Reuse the two bounded daemon reader threads; do not add a monitor thread or callback queue. The HTTP endpoint checks persisted ownership before backend access, reuses same-instance unfinished work before preflight, serializes global conflicts, and persists nonterminal attention when an unfinished download disagrees with terminal backend state.

## Alternatives considered

Existing source and decision records do not document a historical alternatives discussion for this decision. This ADR does not represent alternatives as previously considered or tested.

## Consequences

- The global lifecycle row deliberately serializes admission and lifecycle transitions across instances.
- Unknown or ambiguous process/worker state requires operator attention; the application does not add a reset endpoint or automatically kill uncertain processes.
- For each unique parent of a trusted media or Arma root, private sibling `.asm-private` directories must already be provisioned outside all public/trusted roots on the same filesystem. Tests provisioned temporary siblings; no production roots were provisioned. The application does not create or chmod/chown these directories. The read-only preflight includes static/media/configured roots and the `Missions.mission_file` FileField storage.
- The managed-file journal does not guarantee database/filesystem atomicity across power loss or a distributed filesystem.
- SQLite/fake-process/fake-Redis evidence does not prove live MySQL lock behavior, Redis/TLS behavior, SteamCMD, Arma, or production process signaling.
- The supported download coordination boundary is one ASM installation with one database and Redis. External writers and additional databases are outside the contract.

## Verification and follow-up

The accepted source includes storage d2290e0, worker 2ae07e8, API integration 0841b91e3aab580431ed22cdb6810e6cbaae7f7d, and final source-fix commits 8632dd68d06de429660cdf607ee1756ef40f0cf4, 40c344bb7662ede7b56f41106d1439fa07ef8ddc, 030fa33f9da2e71af80d12d1af05a0ed929c3144, and 9da951f3b5c5cc4a1b1edce314ce98bf68c4eb1f. Terra Spec and Quality review of the source-fix wave passed.

The focused final-wave backend run passed 123 tests in 4.459 seconds with clean Django checks. The full isolated backend run reported 382 tests in 17.866 seconds, five skipped and the remaining 377 passing, with clean Django checks. The Node taskState regression passed for positive and negative fractional-hour offsets. The authentication-session and configuration-payload helper suites also passed. A synthetic browser smoke with mocked responses observed staff main/own/other sections, per-card shutdown displays and controls, active-task transitions, and recovery after injected task-poll and instance-list errors; it is not live-service evidence.

On 2026-11-09, root ran `$env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run check` against the R1-corrected documentation freeze on accepted source. It exited 0 and all stages passed: dependencies, backend syntax, four documentation/check-runner tests, frontend lint/build, and whitespace. Lint reported zero errors and eleven existing warnings; the build passed with a 741.86 kB main JavaScript chunk advisory. The safe check skips Django behavior tests. Separately, the guarded backend run reported 382 tests total, five skipped and 377 passed, with clean Django checks.

The no-redactor regression verifies the fixed typed diagnostic, task result, context-suppression flag, and absence of downstream work. Its unittest assertion does not retain a full task-wrapper stack because assertRaises clears the traceback; the source explicitly raises from None. This is a test-evidence limit, not evidence of a remaining disclosure. The isolated runner blocks five patched Python resolver entry points, not native-code bypasses.

SQLite, fake Redis, temporary files, mocked processes, and synthetic browser tests do not prove live MySQL row-lock timing, Redis/Lua/TLS/broker behavior, SteamCMD or Arma operation, production migrations, alternate-UID permissions, unsupported filesystem/ACL conditions, hostile same-UID/root changes, or power-loss/distributed database-filesystem atomicity.
