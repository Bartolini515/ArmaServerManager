# API contract

The backend mounts the DRF router below `/api/` and uses trailing slashes. Knox token authentication and DRF session authentication protect authenticated endpoints. Error payloads are either serializer field errors or a JSON object containing `message`; long-running actions return `202` with a Celery `task_id`.

## Authentication and account

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| POST | `api/login/` | Public | Validates username/password and returns `user`, `token`, `isAdmin`, and `message`. |
| POST | `api/logout/` | Authenticated | Revokes the current Knox token. |
| GET | `api/account/` | Authenticated | Returns the configured account queryset (currently all profiles). |
| GET | `api/account/get_user/` | Authenticated | Returns the current serialized user and `isAdmin`. |
| POST | `api/account/change_password/` | Authenticated | Validates and replaces the current password. |

## Services

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| GET | `api/services/get_system_info/` | Authenticated | Returns CPU, memory, disk, CPU count, and operating-system information from `psutil`. |

## Instances

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| GET | `api/instances/` | Authenticated | Returns `user_instances` and, for staff, `admin_instances`. |
| POST | `api/instances/` | Authenticated | Accepts `name` and an HTML `preset`; reserves an available port and generates server files. Non-staff users are limited to five instances. |
| DELETE | `api/instances/<id>/` | Authenticated | Deletes a stopped instance and releases its port and files. |
| POST | `api/instances/<id>/start/` | Authenticated | Validates readiness and enqueues `start_server_task`; temporary instances receive a one-hour timeout. |
| POST | `api/instances/<id>/stop/` | Authenticated | Enqueues `stop_server_task`. |
| POST | `api/instances/<id>/download_mods/` | Authenticated | Enqueues SteamCMD mod download and returns its task ID. |
| GET | `api/instances/task_status/<task_id>/` | Authenticated | Returns `{id, state, result}` for a Celery task. |
| GET | `api/instances/<id>/logs/` | Authenticated | Returns log text; optional `tail` limits the returned lines. |
| GET | `api/instances/<id>/logs/download/` | Authenticated | Downloads the complete log as `logs_<id>.txt`. |
| DELETE | `api/instances/<id>/logs/delete/` | Authenticated | Deletes a stopped instance log. |
| POST | `api/instances/<id>/admin_instance/change_preset/` | Staff | Replaces the preset of a stopped administrative instance and regenerates its start file. |

`InstanceSerializer` validates instance names as ASCII letters/digits/underscore/hyphen and preset names as safe ASCII filenames. The backend returns serialized `port` as a string and nests a read-only user profile.

## Moderator panel

All moderator-panel endpoints require `IsAdminUser`.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `api/moderator_panel/user/` | List profiles. |
| GET | `api/moderator_panel/<id>/user/` | Read one profile. |
| PUT | `api/moderator_panel/<id>/user/update/` | Partially update username/password. |
| POST | `api/moderator_panel/user/create/` | Create a profile through `create_user`. |
| DELETE | `api/moderator_panel/<id>/user/delete/` | Delete a profile and model-owned files. |
| GET | `api/moderator_panel/config/` | Read JSON path and Steam configuration with password/shared-secret masking. |
| PUT | `api/moderator_panel/config/update/` | Validate and persist the JSON configuration. |

## Missions

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| POST | `api/missions/` | Authenticated | Accepts one `.pbo` upload; an existing mission with the same stored filename is deleted first. |

The router also exposes DRF-generated routes for viewsets. Their practical behavior should be checked against `backend/main/views.py`; this document focuses on the routes used by the frontend and the custom actions.

## Task states

The frontend treats `PENDING`, `STARTED`, and `PROGRESS` as active work and refreshes instance data after `SUCCESS` or `FAILURE`. Progress text is stored in `result.status`. A task ID is not a permission grant; endpoint authorization and object lookup behavior remain backend responsibilities. See [known risks](known-risks.md).
