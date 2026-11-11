# Instance lifecycle

The instance log viewer uses bounded cursor-based polling while auto-refresh is enabled; its settings are stored in the browser. The existing complete-log download remains separate.

This lifecycle contract reflects the accepted source at commit 9da951f and guarded regression evidence (2026-11-09).

An instance is a logical Arma 3 server plus its managed files, reserved port, asynchronous tasks, and a verified host-process run. Database flags alone do not prove that a host process is alive or safe to signal.

## State and ownership

The public lifecycle states are `stopped`, `starting`, `running`, `stopping`, and `unknown`. The state and per-instance `shutdown_at` are read-only API fields. Internal run IDs, process birth/executable identity and task-binding paths are not public.

Task IDs are strings. A database `InstanceTask` binding is written before dispatch and ties each start, stop, or download task to one instance. Workers claim once. The task-status API checks that binding against the caller's ownership or staff access.

## Create and file publication

Creation, preset replacement, first start, and single-object deletion use the accepted managed-file service and shared lifecycle transaction. Create validates before reservation, stages preset/config/script, and publishes managed files with instance/profile/port rows. Admin preset replacement requires the file before side effects; old metadata/files are preserved only if precommit compensation succeeds and verifies restoration. Uncertain compensation may leave unresolved public destinations or private evidence for operator review. First-start log bytes are staged by `staged_first_instance_log`; the database `on_commit` callback marks that journal committed. After commit, private cleanup failure is `cleanup_pending`, with no rollback over committed metadata. The read-only `preflight_managed_root_layout(config_snapshot)` runs before operation logging for create, preset replacement, and first start using the same snapshot as the operation; its public-root roster includes `Missions.mission_file`.

Ordinary users are limited to five instances. Staff can operate all instances. User deletion is expected to preflight every owned instance and reject busy or uncertain work; it does not automatically stop a server.

## Download mods

The HTTP view authorizes the instance, then `reserve_download(instance_id)` checks same-instance reuse and lifecycle/global conflicts under the lifecycle mutex before invoking preflight for new admission. Reuse returns the existing string task ID with HTTP 202 and does not preflight or redispatch. A non-stopped instance or conflicting operation/download returns a static HTTP 409. New work checks the administrative superuser where required, one config snapshot, the preset and mods-directory setting, and at least 20 GiB free disk space before it creates a binding; preflight failure leaves no binding. A committed new binding is dispatched after the transaction with only `instance_id` and returns HTTP 202.

All downloads share SteamCMD cache/assets, so a new admission reserves one global unfinished-download slot. Same-instance requests reuse the existing task ID; another unfinished global download returns static HTTP 409. A healthy unclaimed, non-attention legacy DOWNLOAD does not alone block worker claim. A claimed or attention-required unfinished download does fence cross-instance claim/current-claim/readiness paths. No task age, TTL expiry, backend result, or missing cache key reclaims a reservation.

The worker takes one coherent configuration snapshot, claims the durable task binding once, and clears readiness in the claim transaction. It checks the current claim and renewable owner-token lease before every shared write/publication, and verifies required Workshop content before setting readiness true. Each job uses a unique worker-owned workspace. Cancellation is checked through SteamCMD output handling, retry/rate-limit waits, shared writes, and ghost-folder publication. The lease heartbeat continues during the 600-second rate-limit wait and 7,200-second SteamCMD execution window. The callback gate closes **admission** only: a callback admitted before cancellation can be suppressed or complete later; there is no promise about when its first physical instruction runs. Two daemon reader threads are used per cancellable supervisor. Cancellation adds no callback-monitor or cleanup thread and no callback queue; the lease uses its bounded heartbeat thread. If process cleanup or reader quiescence is uncertain, the worker retains the workspace and binding as unfinished attention. It closes no pipe while a reader may still be active. A filesystem-only postcommit cleanup-pending result is separate and does not itself mean process uncertainty.

Verification succeeds when the installed-mod check reports no missing Workshop IDs. The check confirms directory existence; it does not validate each mod's contents.

A persisted attention row returns the static HTTP 200 `REQUIRES_ATTENTION` response before Celery backend access. A terminal backend state (`SUCCESS`, `FAILURE`, or `REVOKED`) on any unfinished download is treated as unverified worker state and persisted as attention under the lifecycle coordinator; completion or supersession reread wins. A later queued delivery is rejected before claim/readiness mutation. Global attention fences block other worker claims/current-claim/readiness checks. A blocked record/handler can remain alive until it unwinds or the process restarts. The opted-in operation logger seals new handler admission and defers only an actively used handler's close to that record's return; it adds no monitor/cleanup thread. Normal logger teardown remains synchronous. Filesystem-only postcommit cleanup pending is separate from process/reader uncertainty.

The worker resolves numeric DNS before lease acquisition; that OS DNS work is outside the bounded lease I/O. The Redis client uses a maximum of four connections and connect/socket timeouts no longer than one second. One ASM database/Redis installation is the supported coordination boundary.

## Start

The API validates that the instance is stopped, ready, and has required installed mods plus valid preset/start/log inputs. One lifecycle mutex serializes admission. Starting reservations count toward owner/global limits. A duplicate start while the current reservation is active returns the existing task ID rather than enqueuing a second run.

The start worker claims the bound task and launches with a per-run UUID marker. During bounded startup identity resolution, it accepts the Popen root when its PID/birth identity and run marker are valid and its executable matches the persisted expected Arma executable, then rechecks that identity. If the Popen root is a shell or otherwise has a different executable, the worker examines its descendants and accepts only one candidate whose PID/birth identity, run marker, executable, and verified ancestry match; it rechecks the selected candidate and root before returning it. No unique verified match by the deadline leaves lifecycle state uncertain. This startup descendant selection is distinct from stop-time descendant signaling, described below. The persisted expected executable is checked for the direct/discovered root during later stop, compensation, and reconciliation. Failure before process creation can compensate the reservation; failure after launch may leave a live process and requires verified cleanup before reset.

## Stop and timeout

A stop reservation is tied to the current run and one task attempt. Before signaling the direct/discovered root, the worker checks the current reservation, run marker, PID birth time, and persisted expected executable. UDP discovery supplies candidate identities only and is never sufficient authority by itself. Descendant candidates are checked against birth time, run marker, and verified ancestry; the root's expected executable is not compared to each child. Stop and compensation recheck the direct process executable; reconciliation also checks it. A root mismatch or unverifiable identity leaves the instance unknown and sends no signal. A legacy row without verified process identity remains unknown and receives no signal. If a launched shell cannot be identified as the expected Arma executable, it remains unverified rather than inheriting the expected identity from its PID or marker.

Recovery preserves claimed/unclaimed stop state and may supersede prior attempts only after exit is confirmed. A per-instance one-hour deadline is scheduled after a successful user-server launch; old timeout deliveries cannot stop a later run. This is an instance deadline, not a browser-wide timestamp.

Process identity checks are fail-closed, but they do not establish absolute process containment. SQLite regression runs do not prove MySQL lock serialization or live host behavior.

## Delete and logs

The accepted service rejects running, starting, stopping, downloading, and unknown instances; user deletion preflights every owned instance/file before mutation, never autostops, and releases ports only through the guarded successful cleanup. Saved preset/start/log/config references are validated under trusted roots. A private cleanup failure returns static `cleanup_pending` metadata and warning without exposing repair paths. Only one `Instances.delete()` or `Profile.delete()` call through the service is supported; queryset/admin bulk deletion bypasses the overrides. Verified successful compensation may leave an empty validated per-user log parent and no remaining file bytes/private stages in the regression-covered cases. Unsuccessful or uncertain rollback may leave unresolved public paths or private stage/backup evidence; preserve it for operator review.

## Operator recovery

Before manual repair, an authorized operator must verify worker quiescence and confirm the current run ID, process birth time, run marker, and canonical executable through approved operations. Never blind-kill a PID, delete cache data, reset a claim, or remove a workspace while a process may still be alive. No reset endpoint is added. Preserve the structured attention evidence and use it to make the manual decision.
## Start task compatibility

New start tasks publish only the instance and run identifiers after the reservation transaction commits. The task accepts a legacy `arma3_dir` keyword for already queued messages, discards it immediately, and derives the launch directory from persisted process data.
