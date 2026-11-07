# Frontend code review

## Scope and method

Reviewed the React/TypeScript frontend, routing, authentication bootstrap, instance actions and task polling, mission and moderator forms, API client, shared components, and their relevant backend contracts. Findings are based on static code tracing; no live services, production data, or browser actions were used. No frontend test files or test script were found. This review does not establish exhaustive coverage or runtime behavior in every deployment.

## Findings

### P1 — Configuration edits can overwrite the Steam password or fail validation

- `frontend/src/components/moderatorPanel/modals/ConfigModeratorPanelModal.tsx:98` constructs a payload and deletes `payload.password` and `payload.shared_secret` when they contain the mask. However, `frontend/src/components/moderatorPanel/ModeratorPanelConfiguration.tsx:22` nests both values under `steam_auth`, so these deletes do nothing.
- `backend/main/serializers.py:80` rejects the masked shared secret as invalid base64. Consequently, when an existing shared secret is configured, editing an unrelated path and leaving the mask unchanged fails. If the shared secret is empty but a password exists, validation succeeds and the backend deep-merges the literal mask into the password (`backend/main/views.py:152`, `backend/main/utils/config.py:92`), breaking subsequent Steam authentication.
- Recommendation: remove unchanged masked values from the nested object before submission, and cover both the save-with-secret and save-with-password-only cases.

### P1 — A pending task is permanently misclassified as failed

- `frontend/src/components/instances/Instances.tsx:111` unconditionally reads `response.data.result.status`. The endpoint returns the Celery result as-is (`backend/main/views.py:436`), and a queued PENDING task can have a null result. Dereferencing null rejects the promise, and the catch at `Instances.tsx:132` sets the task state to FAILURE. The polling loop then skips it permanently at line 104 even though the worker may subsequently complete it.
- This can happen when a task waits in the queue longer than the polling interval. The UI stops tracking the operation, re-enables actions, and does not refresh when it eventually finishes. Treat missing metadata as a normal nonterminal state and distinguish transport failures from task failures.

### P2 — Administrator instances can disappear after a direct page load

- `frontend/src/components/ProtectedRoutes.tsx:13` starts the user lookup but immediately renders the child route. On a reload of `/instances`, `isAdmin` initially defaults to false and the instances request starts in parallel. If it finishes before the user lookup, `frontend/src/components/instances/Instances.tsx:156` discards `admin_instances`; the effect only depends on `refresh` at line 170 and is not rerun when `isAdmin` changes. The admin section appears after authentication but remains empty until another refresh.
- Wait for authentication before rendering protected content, or always store the response and react to authentication state changes.

### P2 — Temporary authentication errors discard a valid session

- The lookup catch in `frontend/src/components/ProtectedRoutes.tsx:23` removes the token for every failure, including a timeout or server error. The returned `<Navigate>` is inside a promise callback and is never rendered. On a transient outage the user loses their valid token and can remain on a stale protected page until another render or request occurs.
- Handle authentication and service failures separately, render a loading/error state, and redirect through component state only when the session is actually invalid.

### P2 — Displayed shutdown time is not tied to the running instance

- `frontend/src/components/instances/UserInstances.tsx:67` uses one browser-wide `timestamp`, sets it when any instance starts, and clears it when any stops. The same value is passed to every instance at line 204. It is neither user-scoped nor synchronized with the server. Starting from another browser or device, switching accounts, or a failed start leaves a missing or incorrect displayed shutdown time. The actual timeout is scheduled by the backend (`backend/main/views.py:323`).
- Return a server-authoritative deadline for each instance and display that value instead of a shared local timestamp.

## Verification and limitations

The scenarios were traced through source code and documented API behavior. No automated or interactive tests were run for this report. The frontend package provides lint and build scripts but no test script; this report does not claim the repository-wide check or Django tests passed.
