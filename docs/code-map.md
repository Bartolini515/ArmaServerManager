# Code map

This document maps responsibilities without attempting to list every file. The code and tests remain the source of truth for exact behavior.

## Backend

| Area | Responsibility | Main locations |
| --- | --- | --- |
| Django project | Settings, URL mounting, WSGI/ASGI, Celery app | `backend/servermanager/` |
| REST API | Authentication, account, moderator, services, instances, and missions viewsets | `backend/main/views.py`, `backend/main/urls.py` |
| Serialization | Request validation and response shaping, including masked Steam fields | `backend/main/serializers.py` |
| Data model | Custom user profile, instances, ports, missions, and file cleanup | `backend/main/models.py` |
| Asynchronous work | Mod downloads, server start/stop, status reconciliation, timeout | `backend/main/tasks.py` |
| Preset processing | HTML link/workshop ID extraction, installed-mod checks, generated paths | `backend/main/modpreset/` |
| Host integration | SteamCMD login/guard handling and subprocess download | `backend/main/steamcmd/` |
| Server integration | Start script execution and output streaming | `backend/main/serverhandling/` |
| Configuration | JSON bootstrap, dot-path lookup, deep updates | `backend/main/utils/config.py` |
| Logging | Standard-library per-operation files, grouped Celery logs, and error records | `backend/main/utils/operation_logging.py`, `backend/main/tasks.py`, `backend/main/views.py` |
| Workarounds | Ghost folders, SteamCMD cache deletion, addon name normalization | `backend/main/workaround/` |
| Administration | Django admin registration and port population command | `backend/main/admin.py`, `backend/main/management/` |
| Schema history | Applied database migrations | `backend/main/migrations/` |

## Frontend

| Area | Responsibility | Main locations |
| --- | --- | --- |
| Route shell | Public/protected route tree and provider composition | `frontend/src/App.tsx`, `frontend/src/main.tsx` |
| API client | Base URL, JSON/multipart behavior, token header, CSRF mode, 401 redirect | `frontend/src/components/AxiosInstance.tsx` |
| Authentication | User/admin state and protected route bootstrap | `frontend/src/contexts/AuthContext.tsx`, `frontend/src/components/ProtectedRoutes.tsx` |
| Dashboard | System resource polling | `frontend/src/components/dashboard/` |
| Instances | User/admin cards, task polling, create/delete/start/stop/download/log flows | `frontend/src/components/instances/` |
| Missions | `.pbo` upload form | `frontend/src/components/missions/` |
| Moderator panel | User management and JSON path/Steam configuration editing | `frontend/src/components/moderatorPanel/` |
| Account/settings | Password change, theme, and appearance preferences | `frontend/src/components/account/`, `frontend/src/components/settings/` |
| Shared UI | MUI forms, dialogs, alerts, buttons, dropzones, and hooks | `frontend/src/UI/`, `frontend/src/hooks/` |

## Change navigation

- An API path or response change starts in `backend/main/views.py` and `serializers.py`, then requires updates to the matching Axios call and UI state.
- An instance lifecycle change crosses `views.py`, `tasks.py`, `models.py`, generated-file helpers, and the task polling code in `frontend/src/components/instances/`.
- A configuration key change crosses `utils/config.py`, `ConfigSerializer`, moderator configuration forms, and [configuration.md](configuration.md).
- A schema change starts with a new migration and updates [data-model.md](data-model.md); applied migrations are not rewritten.
