# Backend API and data-model review

Scope: Django API views, routing, serializers, model lifecycle, relevant tasks, tests and repository documentation. Findings are based on static inspection; no production data, services, processes or real database were accessed. Line numbers refer to the reviewed working tree. Priorities use P0 (critical), P1 (high), and P2 (moderate).

## Findings

### P0 — Writable process and filesystem fields allow host-side damage

- Evidence: `backend/main/serializers.py:139` exposes `start_file_path`, `pid`, `is_running` and `is_admin_instance` as writable fields; `backend/main/views.py:179` exposes inherited `PATCH`/`PUT` actions on the unfiltered queryset. `backend/main/views.py:249` and `backend/main/models.py:69` subsequently delete the supplied start-file path. `backend/main/views.py:328` and `backend/main/tasks.py:153` use the supplied PID to terminate a process and its children.
- Scenario and impact: Any authenticated user can patch an instance with a path to a file accessible to the backend, mark it stopped, and delete the instance, removing that file. They can instead supply a live PID and set `is_running`, then request stop, causing the worker to terminate that process subject to OS permissions. Since the queryset is global, the attacker need not own the instance. The same writable admin flag can bypass normal temporary-instance restrictions.
- Recommendation: Make host paths, PID, state flags and administrative classification server-controlled; restrict or remove generic update endpoints and authorize each object. Validate process identity before termination and restrict file cleanup to trusted managed locations.

### P1 — Instance endpoints do not enforce ownership

- Evidence: `backend/main/views.py:182` requires only authentication, and the queryset at line 184 contains every instance. The inherited retrieve and update routes and `destroy` at line 252 use this queryset; `start`, `stop`, `download_mods`, and log actions retrieve by primary key directly (lines 270, 332, 405, 451, 492, and 510).
- Scenario and impact: A normal user who knows or guesses another instance ID can read or delete its logs, start or stop it, request downloads, inspect it, update it or delete it when marked stopped. Administrative instances are likewise not consistently restricted to staff. The UI's filtered listing does not protect direct API calls.
- Recommendation: Apply a consistent owner/staff object policy to all generic and custom actions, with explicit tests for cross-user and administrative access. This risk is also acknowledged in `docs/known-risks.md:7`.//

### P1 — Deleting a user bypasses instance cleanup and running-state safeguards

- Evidence: `backend/main/views.py:134` deletes the user directly; `backend/main/models.py:49` cascades instance rows, while port release and file cleanup exist only in the instance `delete()` override at lines 63–74. Django's cascade collector does not call individual related model `delete()` methods. The endpoint also does not check for running instances.
- Scenario and impact: Deleting a user with instances leaves their port reservations and generated/preset/log files behind. A running host process can outlive its database row, losing the PID and normal stop path. Repeated deletions can exhaust the pool of ports.
- Recommendation: Define and implement an explicit lifecycle for user deletion that rejects or safely stops running instances and cleans each instance and port, with failure handling and isolated tests.

### P1 — Preset replacement can delete the old file before validating its presence

- Evidence: `backend/main/views.py:367` uses a partial serializer, then deletes the old preset at lines 369–370 before indexing `validated_data["preset"]` at line 371.
- Scenario and impact: A staff request with an empty body, or one containing only another valid field, passes partial validation; the old file is deleted and the missing key raises an exception. The row still refers to a now-missing preset, preventing normal operations. A later write or generation failure can similarly leave partially updated state.
- Recommendation: Require a preset before changing anything, save and validate the replacement before removing the old file, and handle failures consistently.

### P2 — Creation failures strand instances and reserved ports

- Evidence: `backend/main/views.py:207` commits the row and reservation before parsing the preset and creating host files at lines 217–243, without compensating cleanup.
- Scenario and impact: If parsing or file generation fails, the request returns an error while the row and port remain committed, possibly without a start script. Retrying with the same name fails uniqueness validation and repeated failures consume available ports.
- Recommendation: Stage and validate host files, and compensate or mark the instance for repair on failure; cover failures after reservation.

## Coverage and limitations

Reviewed the API and model source, routes, relevant task behavior, existing backend tests and architecture/API/data-model/lifecycle documentation. Existing view tests focus on login and account endpoints and do not exercise the cross-user scenarios above. No application tests were executed for this review: the project explicitly prohibits running Django tests against a real or shared database and has no configured isolated test database. Findings should be confirmed with isolated test fixtures and mocked host operations; no exploit was executed.
