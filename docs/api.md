# API contract

This contract records behavior verified against the accepted source at commit 9da951f (2026-11-09). Routes, serializers, views, and tests define exact behavior.

The backend mounts a Django REST Framework router below `/api/` with trailing slashes. Knox token authentication and DRF session authentication protect authenticated endpoints. Field validation errors use serializer-shaped JSON; action errors commonly use a JSON `message`. Long-running instance actions return HTTP `202` with a string `task_id`.

## Authentication and account

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| POST | `/api/login/` | Public | Validates username/password and returns the user, token, admin flag, and message. |
| POST | `/api/logout/` | Authenticated | Revokes the current Knox token. |
| GET | `/api/account/` | Authenticated | Lists the configured profile queryset. |
| GET | `/api/account/get_user/` | Authenticated | Returns the current serialized profile and admin flag. |
| POST | `/api/account/change_password/` | Authenticated | Validates and changes the current password. |

Moderator-panel profile endpoints require staff access:

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/moderator_panel/user/` | List profiles. |
| GET | `/api/moderator_panel/<id>/user/` | Read one profile. |
| PUT | `/api/moderator_panel/<id>/user/update/` | Update permitted profile fields. |
| POST | `/api/moderator_panel/user/create/` | Create a profile through the user manager. |
| DELETE | `/api/moderator_panel/<id>/user/delete/` | Preflights every owned instance and file; never autostops. Success returns HTTP 200 `{"message":"Użytkownik usunięty"}`. If post-commit private-file cleanup is pending, HTTP 200 returns `{"message":"Operacja zakończona; czyszczenie plików wymaga uwagi administratora.","cleanup_pending":{"pending":true,"reason":"private_cleanup_failed","pending_count":1}}`; `pending_count` is the number of private stage/backup files still awaiting cleanup. Missing profile returns 404 `Użytkownik nie istnieje`; busy work returns 400 `Nie można usunąć użytkownika podczas aktywnej operacji instancji`; unsafe paths return 400 `Pliki instancji wymagają weryfikacji przez administratora.`; storage/management failures return static HTTP 500 `Nie udało się wykonać operacji na plikach instancji.` |

## Services

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| GET | `/api/services/get_system_info/` | Authenticated | Returns system resource and operating-system information from `psutil`. |

## Instances and access

Instance access is owner-or-staff. A non-staff user can see and operate only their own non-administrative instances. Staff can operate all instances. Generic instance `PUT` and `PATCH` are disabled; creation accepts `name` and `preset`, while server-controlled values such as owner, port, host paths, process state, readiness, and administrative classification are read-only.

`GET /api/instances/` returns these arrays:

- `user_instances`: the caller's non-administrative instances.
- `admin_instances`: shared administrative instances for staff; empty for ordinary users.
- `other_instances`: non-administrative instances owned by other profiles for staff; empty for ordinary users.

Staff UI sections are displayed as main instances, the staff member's own instances, then other users' instances. Owner data is read-only. The serialized `port` is a string. `active_tasks` maps operation kinds to string Celery task IDs; it does not grant access to those tasks.

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| GET | `/api/instances/` | Authenticated | Returns the three instance arrays described above. |
| POST | `/api/instances/` | Authenticated | Accepts `name` and exactly one preset upload or `prepared_preset_id`; the serializer does not require a filename extension. Ordinary users are limited to five instances. Creation uses the managed-file journal and shared lifecycle transaction. Precommit failures trigger compensation; clean rollback is reported only when restoration/removal is verified, while uncertain rollback may leave repair evidence. Postcommit private cleanup failure is reported as cleanup-pending. |
| GET | `/api/instances/<id>/` | Owner or staff | Reads one authorized instance. |
| DELETE | `/api/instances/<id>/` | Owner or staff | Deletes a stopped instance and its managed files and port. Deletes only stopped/safe instances through the journal; validates owned paths, preserves per-user config, and releases the port on the guarded service path. |
| POST | `/api/instances/<id>/start/` | Owner or staff | Reserves a lifecycle run and enqueues start work with only `instance_id` and `run_id`; returns `202 {task_id}`. Repeated start while the same run is starting returns its task ID. |
| POST | `/api/instances/<id>/stop/` | Owner or staff | Reserves a verified stop attempt and returns `202 {task_id}`. |
| POST | `/api/instances/<id>/download_mods/` | Owner or staff | Reuses an unfinished same-instance download with HTTP 202 and its string `task_id`. A conflicting global download or active operation returns a static HTTP 409. A new admission runs config/preset/disk preflight under the lifecycle mutex before creating its durable task binding; successful dispatch returns HTTP 202 `{task_id}`. |
| GET | `/api/instances/task_status/<task_id>/` | Bound owner or staff | Returns `{id, state, result}`; non-staff access requires a persisted task-to-instance binding for an owned non-administrative instance. Unknown or unowned task IDs return 404. Superseded tasks return static `REVOKED`; persisted attention returns static, nonterminal `REQUIRES_ATTENTION`. |
| GET | `/api/instances/<id>/logs/` | Owner or staff | Returns log text; optional `tail` limits the returned lines. |
| GET | `/api/instances/<id>/logs/incremental/` | Owner or staff | Returns a bounded log tail or newly appended content with a signed cursor; reports reset, truncation, and remaining data. |
| GET | `/api/instances/<id>/logs/download/` | Owner or staff | Downloads the complete log. |
| DELETE | `/api/instances/<id>/logs/delete/` | Owner or staff | Deletes a log only when the instance is not busy. |
| POST | `/api/instances/<id>/admin_instance/change_preset/` | Staff | Replaces the preset of a stopped administrative instance using exactly one uploaded preset or `prepared_preset_id`; stages and validates replacement files. Prior files and metadata are restored only when precommit compensation succeeds and verifies the restoration; uncertain rollback may leave paths or private evidence for operator review. |

The instance serializer validates names as ASCII letters, digits, underscores, and hyphens; preset filenames permit ASCII letters, digits, underscores, dots, and hyphens, with no extension requirement. Workshop-ID parsing runs only for the exact lowercase `.html` suffix; other accepted filenames produce no Workshop IDs. See Managed file behavior below.

## Download admission and task status

`reserve_download` executes under the lifecycle mutex. It checks same-instance reuse first and returns the existing task ID without preflight or redispatch. It then rejects a non-stopped instance, another active download, or another unfinished operation before running new-admission preflight. Only genuinely new work checks for an administrative superuser where needed, snapshots configuration, validates the target preset and mods-directory setting, and requires at least 20 GiB free disk space. Failed preflight creates no binding. A valid new binding is committed before dispatch; download dispatch passes only `instance_id`. Reuse and new dispatch both return HTTP 202 with a string `task_id`.

Download conflict responses are static: a non-stopped instance returns HTTP 409 with `Serwer musi być zatrzymany przed pobraniem modów.`, and an active global download or other operation returns HTTP 409 with `Inna operacja serwera lub pobierania jest już w toku.` A missing target returns 404. Preset/path, missing-superuser, disk, and enqueue failures use the static 400/404/500 responses defined by the view; raw exceptions and host paths are not returned.

The status endpoint authorizes its persisted binding before reading Celery. Superseded bindings return HTTP 200 `REVOKED`; persisted attention returns HTTP 200 `REQUIRES_ATTENTION` with a fixed status message, before backend access. If any terminal backend state (`SUCCESS`, `FAILURE`, or `REVOKED`) disagrees with an unfinished DOWNLOAD binding, the view takes the lifecycle mutex, rereads the binding, and persists `worker_state_unverified` attention unless completion or supersession won concurrently. It does not infer a claim or finish, return the backend payload, requeue work, or release the global fence. A later queued delivery is rejected before claim/readiness mutation. Pending/null backend results remain nonterminal; finished stale-PENDING bindings are retired and return 404. Generic task failures are presented with a static error message.

## Managed file behavior

The accepted storage service validates preset, start-script, and log references against the current configuration snapshot, expected per-user path leaves, local FileField storage paths, and regular-file/symlink checks. Create, preset replacement, and first-start admission perform a read-only `preflight_managed_root_layout(config_snapshot)` before opening operation loggers, using the same snapshot as the operation and logger. It compares the derived private siblings with configured, static, media, instance preset/log, and mission-upload storage roots. The public-root enumeration includes `Missions.mission_file`; unsupported/non-enumerable FileField storage fails closed for mutations that rely on private staging. Mission upload/replacement behavior itself is unchanged.

Creation stages the uploaded preset and generated configuration/start files, then publishes them with database state and port reservation under the lifecycle transaction. Precommit failures trigger compensation; clean restoration/removal is established only when compensation succeeds and verifies it. A failed or uncertain restore, destination cleanup, or private-stage cleanup can raise `ManagedFileRollbackError` and leave unresolved public paths or private stage/backup evidence, retained for repair where available. Preset replacement preserves the previous file/readiness/metadata only after verified successful compensation. User deletion preflights every owned instance and file, rejects busy or unknown work without autostopping, releases ports only after the guarded cleanup path, and deletes a profile configuration only when no other row references it. Instance deletion retains that per-user configuration.

The first start log is staged in the same lifecycle/database transaction with `staged_first_instance_log`; it is not written with an unjournaled `FileField.save()`. The journal is marked committed only by the database `on_commit` callback. After commit, a private cleanup failure produces a path-free `cleanup_pending` summary and administrator-attention message; the system does not roll back committed metadata. Before commit, failures trigger compensation. Only verified successful compensation establishes that published bytes were restored or removed; an uncertain restore or cleanup may leave unresolved destinations or private stage/backup evidence, with repair evidence retained where available. An expected empty per-user log parent may remain.

Single `Profile.delete()` and `Instances.delete()` calls delegate through the managed service, preserve Django’s `(count, per_model_count)` return, and attach a path-free `_asm_cleanup_pending` summary to the caller object. Successful deletion clears that caller’s primary key; pending cleanup also emits a static administrator warning. QuerySet and admin bulk deletion bypass the model override and are unsupported for managed cleanup.
## Lifecycle and task fields

`InstanceSerializer` returns `id`, `name`, `user`, `preset`, `log_file`, `start_file_path`, `port`, `pid`, `is_ready`, `is_running`, `is_admin_instance`, `created_at`, `lifecycle_state`, `shutdown_at`, and `active_tasks`. The response includes read-only `pid` and `start_file_path`; generic instance updates cannot write them. The private run ID, process birth time (`process_created_at`), expected executable (`process_executable`), and persisted task-binding rows are not serialized. `active_tasks` maps operation kinds to string task IDs.

Start/stop task ownership is persisted before dispatch. Task IDs are identifiers, not permissions. Task failure responses use safe, allowlisted messages rather than serializing arbitrary exception text. A superseded stop binding is represented as a revoked task result. Download reuse, conflict, and attention response mapping is accepted in Task 7c3. See the next section for the ordering and terminal-state rules.

## Configuration

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| GET | `/api/moderator_panel/config/` | Staff | Returns `paths` and `steam_auth`; `steam_auth.username` is unchanged, while each non-empty `password` and `shared_secret` is replaced by exactly eight asterisks. |
| PUT | `/api/moderator_panel/config/update/` | Staff | Validates and persists a configuration update. Omitted masked credentials preserve the saved value; an explicitly empty value clears it. |

The staff configuration response masks only non-empty `steam_auth.password` and `steam_auth.shared_secret`; it leaves `steam_auth.username` intact. Failed task results use safe allowlisted messages rather than arbitrary exception text. SteamCMD authentication errors do not expose raw command arguments or generated Guard codes. These task/error protections do not change which configuration fields staff can see. See the configuration guide for file-level locking and platform limits.

## Missions

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| POST | `/api/missions/` | Authenticated | Accepts one `.pbo` upload; a same-stored-filename mission is replaced. |
| DELETE | `/api/missions/<id>/` | Staff | Removes the mission and managed file; returns 409 while any instance has `is_running=true`. A missing file does not prevent deletion; unsafe references are rejected. Private cleanup failure is reported. |

Missions do not have an uploader relation or version history. Upload replacement behavior is unchanged.

## Prepared presets

`GET /api/prepared-presets/` lists the shared catalog for authenticated users. Staff can submit multipart `name` and `preset_file` to `POST /api/prepared-presets/` and delete a catalog entry with `DELETE /api/prepared-presets/<id>/`. Display names are unique after case folding; conflicting file names receive distinct stored names. Selecting a prepared preset captures a separate copy for the instance. Removing an entry leaves existing copies unchanged. File writes and removals use the managed-file journal and may report private cleanup pending.
