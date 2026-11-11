# Code map

This map was checked against the accepted source at commit 9da951f (2026-11-09). Code and tests remain the source of truth.

This document maps responsibilities without listing every file. Code and tests remain the source of truth. The [feature and function map](map/features.md) remains the detailed navigation index.

## Backend

| Area | Responsibility | Main locations |
| --- | --- | --- |
| Django project | Settings, URL mounting, WSGI/ASGI, Celery app | `backend/servermanager/` |
| REST API | Authentication, account, moderator, services, instance actions, missions | `backend/main/views.py`, `backend/main/urls.py` |
| Serialization | Request validation and response shaping, including masked Steam fields and safe lifecycle fields | `backend/main/serializers.py` |
| Data model | Profiles, instances, ports, missions, persisted task bindings and lifecycle lock | `backend/main/models.py`, `backend/main/migrations/` |
| Task ownership | Persist task/instance association, dispatch after persistence, terminal-state bookkeeping | `backend/main/instance_tasks.py` |
| Lifecycle coordination | Global database lock, start/stop reservations, claims, recovery and stale-attempt fencing | `backend/main/instance_lifecycle.py`, `backend/main/tasks.py`, `backend/main/views.py` |
| Process identity | PID birth time, run marker, executable verification and UDP discovery | `backend/main/serverhandling/process_identity.py`, `backend/main/serverhandling/start_server.py` |
| Lifecycle worker | Start/stop, per-run timeout, process output and reconciliation | `backend/main/tasks.py`, `backend/main/serverhandling/` |
| Managed file journal | Trusted roots, private stages/backups, no-clobber publication, rollback evidence | `backend/main/instance_files.py` |
| Instance file operations | Create, preset replacement, log preparation, instance/profile deletion | `backend/main/instance_management.py` — accepted in d2290e0 |
| Download lease primitive | Owner-token Redis lease, bounded client I/O, heartbeat and guarded release | `backend/main/download_leases.py` |
| Download worker and HTTP integration | Durable download claim, cancellation fencing, shared-cache protection, admission ordering and task-status attention mapping | `backend/main/download_jobs.py`, `backend/main/tasks.py`, `backend/main/views.py` — accepted worker and scoped API integration |
| Preset processing | Workshop ID extraction, installed-mod checks, generated paths | `backend/main/modpreset/` |
| Steam integration | Authentication, Steam Guard, download subprocess and output handling | `backend/main/steamcmd/`, `backend/main/utils/process_output.py` |
| Configuration | Fresh JSON snapshots, locked merge updates and atomic local replacement | `backend/main/utils/config.py`, `backend/main/utils/config_lock.py` |
| Logging | Per-operation files, grouped Celery logs, safe error records | `backend/main/utils/operation_logging.py`, `backend/main/tasks.py`, `backend/main/views.py` |
| Administration | Django admin registration and port population command | `backend/main/admin.py`, `backend/main/management/` |
| Offline path relocation | Dry-run and transactional update of validated instance/profile paths | `backend/main/management/commands/relocate_arma_paths.py` |
| Test isolation | Temporary settings, SQLite runner, fakes, runtime-path guards, and Python DNS resolver guards | `backend/testsupport/`, `scripts/test_backend.py` |

## Frontend

| Area | Responsibility | Main locations |
| --- | --- | --- |
| Route shell | Public/protected route tree and provider composition | `frontend/src/App.tsx`, `frontend/src/main.tsx` |
| API client | Base URL, JSON/multipart behavior, token header, CSRF mode, 401 handling | `frontend/src/components/AxiosInstance.tsx` |
| Authentication | Identity/admin bootstrap and retry state for transient failures | `frontend/src/contexts/AuthContext.tsx`, `frontend/src/components/ProtectedRoutes.tsx` |
| Dashboard | System resource polling | `frontend/src/components/dashboard/` |
| Instances | Staff/user sections, per-instance lifecycle display, task polling, create/delete/start/stop/download/log flows | `frontend/src/components/instances/` |
| Incremental instance logs | Bounded cursor-based log reads and browser-side polling | `backend/main/views.py`, `frontend/src/components/instances/modals/ServerLogsModal.tsx` |
| Missions | `.pbo` upload form and staff deletion | `frontend/src/components/missions/`, `frontend/src/components/moderatorPanel/MissionManagement.tsx` |
| Moderator panel | User management and configuration editing with masked payload handling | `frontend/src/components/moderatorPanel/` |
| Account/settings | Password change, theme, and appearance preferences | `frontend/src/components/account/`, `frontend/src/components/settings/` |
| Shared UI | MUI forms, dialogs, alerts, buttons, dropzones, and hooks | `frontend/src/UI/`, `frontend/src/hooks/` |

## Change navigation

- API path, permission, or response changes start in `views.py` and `serializers.py`, then require matching Axios calls and UI state updates.
- Instance lifecycle changes cross the viewset, lifecycle/task registries, process helpers, model/migration, and instance task UI.
- Managed file changes cross `instance_files.py`, accepted `instance_management.py`, single-object model deletion, and storage tests.
- Download changes cross the lease primitive, accepted `download_jobs.py`/task integration, SteamCMD process handling, the `download_mods`/`task_status` actions in `views.py`, and task UI. Admission/status order and durable attention mapping are accepted in the scoped Task 7c3 integration.
- Configuration changes cross `utils/config.py`, `ConfigSerializer`, moderator forms, and [configuration.md](configuration.md).

## Verified safeguards

Lifecycle stop, compensation, and reconciliation compare the persisted expected executable with the direct/discovered root in addition to checking its birth time and run marker. Descendant candidates use birth-time, run-marker, and verified-ancestry checks; their executable is not compared with the root's. An identity mismatch leaves the instance unknown and does not authorize a signal. New start-task messages contain only the instance and run identifiers; a legacy queued `arma3_dir` value is discarded. The isolated backend runner blocks socket.getaddrinfo, gethostbyname, gethostbyname_ex, gethostbyaddr, and getnameinfo. Frontend shutdown-time parsing accepts valid UTC offsets with fractional-hour minutes.
