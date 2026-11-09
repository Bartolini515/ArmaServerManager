# Known risks and limitations

This inventory records the remaining limits of the accepted source at commit 9da951f (2026-11-09).

## Lifecycle and process identity

Lifecycle admission is serialized through one database mutex row. This intentionally serializes lifecycle decisions across instances. SQLite tests do not prove MySQL row-lock behavior under concurrent production traffic.

Process signalling is fenced to lifecycle task/run state. Stop, compensation, and reconciliation check the persisted expected executable against the direct/discovered root together with its PID birth time and per-run marker. Descendant candidates use birth-time, marker, and verified-ancestry checks; the root executable is not compared with each child. A mismatched or unverifiable root remains unknown and is not signalled. Legacy rows without verified run identity remain unknown and are not signalled by PID or port alone. These checks reduce wrong-target signals but do not prove absolute process containment or prevent every host-level race.

## File storage

The accepted storage service requires local `FileSystemStorage`, administrator-managed trusted roots, safe path components, and same-filesystem hard links. For each unique trusted media/Arma-root parent, an existing sibling `.asm-private` directory must be provisioned outside every public/trusted root. Temporary tests provisioned sibling private directories; no production private roots were provisioned. The application never creates/chmods/chowns them. The read-only preflight inventories configured/static/media roots and the instance preset/log plus `Missions.mission_file` storage before loggers; unsupported/non-enumerable storage fails closed for file mutations.

The journal compensates handled runtime failures and preserves evidence when rollback cannot be verified. It does not guarantee distributed atomicity with MySQL, crash/power-loss recovery, or behavior on filesystems outside the tested local contract. A post-commit private cleanup failure is cleanup pending; committed database rows must not be rolled back.

Only single-model deletion routed through the managed service carries the intended cleanup behavior. Bulk QuerySet/admin deletion is unsupported for managed cleanup.

## Downloads and Redis

The worker combines an owner-token renewable lease with durable database claims and checks both before shared writes. The lease alone is not a durable task claim or fence. The download view reuses same-instance work and resolves lifecycle/global conflicts before new-admission config/preset/disk preflight. It records static attention when an unfinished download disagrees with terminal Celery state, and late delivery/claim/readiness paths honor that fence. Preparation failures before secret-redactor initialization use a fixed diagnostic and do not format the original exception.

The supported coordination boundary is one ASM installation using one database and Redis. External writers, additional databases, direct workspace edits, and other Redis clients do not participate. The worker resolves numeric DNS before lease acquisition, outside the bounded Redis I/O guarantee. Its client caps the pool at four connections with connect/socket timeouts at or below one second. Bounded fake/client tests do not establish live Redis/TLS behavior or bound operating-system DNS.

A static nonterminal attention state requires authorized operator review. Never treat task age, lease expiry, Celery backend state, or a missing cache key alone as proof that work stopped.

Cancellation closes callback admission at a short gate. Work admitted before cancellation may be suppressed or complete later; no physical callback-entry timing promise is made. The implementation reuses existing bounded resources and adds no monitor thread or callback queue. Two daemon reader threads are used per cancellable supervisor. A blocked callback/record can keep its existing reader or handler alive until it returns or the worker process restarts. Attention is persisted before logger teardown; an active opted-in FileHandler closes itself after the record unwinds. If the process or readers cannot be proven quiescent, pipes are not closed cross-thread and the durable task remains unfinished.

## Configuration and credentials

The accepted local configuration update uses snapshots, a stable sidecar lock, merge-under-lock and atomic replacement. POSIX existing-file readers are lock-free; Windows readers use the sidecar. The documented contract does not cover NFS/distributed filesystem semantics, ACL/xattr preservation, alternate-UID live deployment tests, manual writers that ignore the lock, or full power-loss durability. See [configuration.md](configuration.md) and ADR-0002.

Steam username, password, shared secret and generated Guard values must be redacted before stdout, callbacks, logs, task results, APIs, and exception serialization. Never publish raw command-bearing exceptions or their arguments. Runtime configuration and logs remain sensitive.

## Application operations and scope

Operation logs have no automatic retention or rotation and may contain operational data. They require an authorized retention procedure. The separate Arma process log follows instance media authorization and cleanup rules.

The generated known administrator password finding remains intentionally excluded by the user design. Mission behavior remains outside this remediation scope.

## Validation limits

The accepted regression suite covers the repair wave. Its evidence does not establish live MySQL row-lock timing, Redis/Lua/TLS/broker behavior, SteamCMD or Arma operation, alternate-UID permissions, production migrations, or hostile same-UID/root filesystem mutation. The isolated runner blocks socket.getaddrinfo, gethostbyname, gethostbyname_ex, gethostbyaddr, and getnameinfo; this does not prevent native-code bypasses.

The preparation-failure regression checks a fixed typed error, its diagnostic/result, task bookkeeping, and context-suppression flag. Its unittest assertion does not retain a full task-wrapper stack because `assertRaises` clears the traceback; the source explicitly raises the safe error from None. This is a test-evidence limit, not evidence of a remaining disclosure.

Windows-host guards or skips for POSIX ownership/umask/mode and unavailable symlink creation do not prove those cases (`backend/main/tests/test_config_consistency.py`, `test_instance_files.py`, `test_managed_files.py`). SQLite, fake Redis, temporary files, mocked processes, and synthetic browser smoke do not prove live MySQL locking, Redis/TLS, operating-system DNS behavior, SteamCMD, Arma, production migrations, Windows-only behavior not run, NFS/ACL/xattr preservation, alternate-UID deployment, power-loss durability, or privileged concurrent filesystem mutation.

The historical unguarded `manage.py test` invocation was excluded from isolated evidence because it used the default configured MySQL test settings. An unbounded bare `AsyncResult` mock escaped old HTTP rendering; that attempt was stopped and excluded. Corrected finite/fail-fast mocks produced an isolated RED baseline, not a pass. Runtime media and other data were not inspected or cleaned, so do not claim that none exist or that no services were contacted during the whole task.

The root source check before this documentation update reported zero lint errors, eleven existing ESLint warnings, and a successful build with a 741.86 kB main-chunk size advisory. These are maintenance warnings, not behavior-test results.
