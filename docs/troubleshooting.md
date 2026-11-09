# Troubleshooting

This is an operator-safe diagnostic guide. It does not authorize process termination, cache edits, workspace deletion, database repair, or production cleanup.

## Start with the source of truth

Use the [code map](code-map.md), [API contract](api.md), [data model](data-model.md), [configuration guide](configuration.md), and [instance lifecycle](domain/instance-lifecycle.md). For an active operation, inspect only approved API/status evidence and authorized logs. Do not inspect ignored credentials or production records to speed diagnosis.

## Repository checks

`pipenv run check` runs dependency, documentation, backend syntax, frontend lint/build, and whitespace checks. It explicitly skips Django behavior tests by default.

The isolated backend runner must be used with `PIPENV_DONT_LOAD_ENV=1`; do not use a bare/default `manage.py test` invocation as isolation evidence. The download API checks same-instance reuse and lifecycle/global conflicts before new-admission config/preset/disk preflight. A reused active download returns its task ID; a conflicting operation returns a static 409. Persisted attention is returned as static, nonterminal `REQUIRES_ATTENTION` before backend access. An unfinished DOWNLOAD paired with terminal Celery state becomes durable attention rather than inferred completion. The default `pipenv run check` explicitly skips Django behavior tests; run the guarded backend suite separately.

The tracked standalone frontend Node suites passed: from `frontend`, run `node --experimental-strip-types --no-warnings src/contexts/authSession.test.ts`, `node --experimental-strip-types --no-warnings src/components/moderatorPanel/configPayload.test.ts`, and `node --experimental-strip-types --no-warnings src/components/instances/taskState.test.ts`. A synthetic browser smoke with mocked responses observed staff main/own/other sections, per-card shutdown displays and controls, active-task transitions, and recovery after injected task-poll and instance-list errors. It was not a live-service test.

## Instance is not ready

Check serialized readiness/lifecycle values, current task state, and authorized operation/download logs. A successful enqueue does not prove installation completed. Preset, start-script and log paths are checked against the current snapshot and owner-specific trusted root; unsafe saved references fail closed for operator review. For download status, inspect whether the bound task is active or returns static `REQUIRES_ATTENTION`; attention is not a terminal failure or proof the process ended.

## Download is pending, duplicated, or requires attention

The worker uses a durable task binding, current claim, and renewable owner-token lease. A pending/null backend result can remain nonterminal. Persisted attention returns static `REQUIRES_ATTENTION`; it is nonterminal and does not prove the worker or process ended. Same-instance unfinished work reuses the existing task ID without preflight or redispatch; lifecycle/global conflicts return static HTTP 409; only new admissions run config/preset/disk preflight under the lifecycle mutex before binding creation. Any terminal backend state on an unfinished download is persisted as `worker_state_unverified` attention under the same coordinator used for worker claims. A later delivery is rejected before claim or readiness mutation. Do not clear Redis keys, reset claims, remove a workspace, or start another worker based on age, lease expiry, backend state, or a missing cache key.

## Server will not start

Confirm the instance is in `stopped`, has required installed mods, and has valid preset/start/log references. A start reservation may still fail because the configured Arma path, script permission, content, port, or log path is unavailable. If state is `unknown` or process identity is uncertain, do not retry starts or use a PID-only repair.

## Server will not stop

Before signaling the direct/discovered root, the lifecycle checks the run/task reservation, process marker, PID birth time, and persisted expected executable; UDP port discovery is only candidate evidence. Descendant candidates are checked by birth time, marker, and verified ancestry, not by comparing each child's executable with the root's. A root mismatch or unverifiable identity leaves the instance unknown and sends no signal. Legacy rows without verified identity remain unknown. Do not run ad-hoc kill commands; contact an authorized host operator.

Before any manual recovery, the operator must verify worker quiescence and the current run identity through approved operations. Do not reset a claim, remove cache state, or delete files while a matching process may still be alive. There is no reset endpoint.

## Instance deletion, preset replacement, or user deletion fails

The accepted storage service fails closed for active/uncertain work and avoids autostop. A preset replacement restores the previous file/metadata only when precommit compensation succeeds and verifies it. Failed or uncertain restoration/cleanup may leave unresolved public paths or private stage/backup evidence; the journal raises `ManagedFileRollbackError` and retains repair evidence where available. A single model deletion through the service is supported; bulk deletion is not. A cleanup-pending summary and static administrator warning are path-free. Verified successful compensation may leave an empty validated user log parent; regression results do not prove that every rollback succeeds cleanly. Escalate uncertain cleanup evidence to an authorized operator; do not manually remove files until database commit and process quiescence are verified.

## Logs are missing

The instance log is separate from application operation logs. Check the authorized API response, current instance binding, and whether a validated log exists. Unsafe saved references require operator review. First-start log bytes are journal-staged. Regression cases verified removal/restoration only after successful compensation; if compensation is uncertain, unresolved destinations or private stage/backup evidence may remain. An empty validated per-user log parent may remain after successful compensation. Operation logs are written immediately and have no automatic rotation; protect them and follow an authorized retention procedure.

## Mission replacement is unexpected

Uploading a `.pbo` with the same stored filename replaces the existing mission. There is no version history or uploader relation. Mission behavior is outside the Task 9 remediation scope.
