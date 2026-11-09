# Files and missions

This document reflects the accepted storage, lifecycle, and download contracts at source commit 9da951f (2026-11-09).

The application combines uploads, generated files, host process logs, and temporary download workspaces. Filesystem work is not one transaction with the relational database.

## Presets and generated files

An instance preset is stored below an owner-specific preset area. The serializer accepts a filename of at least three characters containing only ASCII letters, digits, underscores, dots, and hyphens; it does not require a particular extension. The parser extracts Workshop IDs only when the staged filename ends in the exact lowercase suffix `.html`. Uppercase `.HTML`, `.htm`, and extensionless names are accepted as safe filenames but produce no Workshop IDs. The private stage preserves the relevant filename suffix so parsing follows the submitted filename behavior.

Host-side outputs include a per-user server configuration, instance start script, instance process log, and downloaded Workshop content. These may include operational identifiers or paths and do not belong in public fixtures or examples.

Preset/start/log/config references are validated against the current operation snapshot, expected owner-specific relative leaves, local FileField root, path components, and regular-file/no-symlink rules. Unsafe legacy references are rejected for operator review. Required preset handling and original filename extension semantics follow the accepted serializer/storage service.

## Managed-file journal

The accepted `backend/main/instance_files.py` journal takes explicitly provisioned trusted public roots and private same-filesystem staging roots. Each root has allowed relative prefixes/names. It stages regular files privately, verifies file identity, supports no-clobber publication and replacement backup, and removes only caller-proven owned files.

Rollback attempts to restore the previous file if a pre-commit operation fails. Clean restoration/removal is established only when compensation succeeds and verifies it. If a restore, public-destination cleanup, or private stage/backup cleanup fails or cannot be verified, the journal raises `ManagedFileRollbackError`; unresolved paths or private evidence may remain, with structured repair evidence retained where available. After the actual database commit callback, private-stage cleanup failure is reported as `cleanup_pending`; committed database state is not rolled back.

The journal does not make database and filesystem writes atomic across process/power loss. It is not a general-purpose cleanup tool and does not authorize traversal outside configured roots.

### Provisioning boundary

A private staging directory must be a pre-existing private sibling on the same filesystem, outside all public/trusted roots. It is administrator-managed. Application startup and request handling do not create it or change ownership/permissions with `mkdir`, `chmod`, or `chown`. Operators must provision and verify permissions outside this application contract.

## Create, replacement, and deletion

The accepted service performs create, preset replacement, log deletion, instance deletion, and profile deletion through the journal and shared lifecycle transaction. It requires autocommit, takes the lifecycle mutex before row locks, and marks the journal committed only from the database `on_commit` callback. Precommit failure triggers compensation; only verified successful compensation establishes a clean rollback. Uncertain rollback may leave unresolved public paths or private evidence and requires operator review. Postcommit cleanup failure is `cleanup_pending` and never restores old files over committed metadata. User deletion preflights every owned instance/file, rejects busy/unknown work without autostopping, releases owned ports through guarded cleanup, and deletes the profile config only when no row still references it.

The expected safe operations are:

- Create validates inputs before publication, stages the uploaded preset and generated config/start files, and commits managed files with instance/profile/port rows. On verified successful precommit compensation, the operation's own staged/published file bytes are removed and its database reservation is rolled back; a validated empty public per-user log parent may remain. If compensation is unsuccessful or uncertain, unresolved public destinations or private stage/backup files may remain, with repair evidence retained where available; do not assume there are no orphaned artifacts.
- Preset replacement stages and parses the uploaded file and generated script before publication. A missing replacement is a validation failure with no side effects. The prior file/readiness/metadata are preserved only when precommit compensation succeeds and verifies restoration. If rollback is uncertain, a public destination or private stage/backup may remain and requires operator review. An existing unrecorded generated script target is refused.
- User deletion preflights all owned instances and files before removing anything. Running, starting, stopping, downloading, or unknown state rejects deletion. No automatic stop is attempted.
- Cleanup covers only files proven owned under trusted roots. A symlink, path escape, changed file identity, or uncertain rollback prevents destructive cleanup and leaves evidence for operator review.
- A single `Instances.delete()` or `Profile.delete()` through the service preserves Django’s `(count, per_model_count)` return and sets a path-free `_asm_cleanup_pending` summary. Pending cleanup emits a static administrator warning; successful deletion clears the caller object’s primary key. Bulk queryset/admin deletion bypasses the model overrides and is unsupported for managed cleanup.

## Root preflight and provisioning

`preflight_managed_root_layout(config_snapshot)` is read-only and runs before operation loggers for create, admin preset replacement, and first-start admission. It uses the same snapshot as the operation. It compares derived media/Arma private siblings with configured, static, media, and FileField storage roots. Root enumeration includes `Instances.preset`, `Instances.log_file`, and `Missions.mission_file`. Every enumerated FileField must use supported local `FileSystemStorage`; unsupported or unenumerable storage fails closed for mutations that rely on private staging. The preflight does not require private directories to exist; the mutating journal boundary rechecks and requires them.

For each unique parent of a trusted media or Arma root, an administrator must provision an existing sibling directory named `.asm-private`. A shared parent can share one directory. It must be outside every public/trusted root, including static roots, and on the same filesystem as its associated trusted root. The application does not create, chmod, or chown it. The Django service account needs access for exclusive stage files and hard-link backups. If the root is missing, overlaps, is a reparse/symlink, is on another filesystem, or does not support required hard links, file mutations fail safely while read-only validation remains available. The regression suite provisions actual sibling directories under temporary test roots; no production roots were provisioned. Fake layout example only: `C:/example/arma/media` and sibling `C:/example/arma/.asm-private`.

## Staged first-start log

When the instance has no saved log, start admission creates the validated per-user log parent and stages the first log bytes with `staged_first_instance_log` inside the lifecycle/database transaction. The journal publishes the staged file before saving the `FileField` name; the transaction’s `on_commit` callback marks the journal committed. The regular `FileField.save()` storage path is not used for this first log. On handled precommit failure, compensation attempts to restore the database/file state. Regression cases verify clean restoration only when compensation succeeds; if a restore or cleanup step is uncertain, unresolved destinations or private stage/backup evidence may remain. An expected empty public per-user log parent may remain after successful compensation. After commit, private cleanup failure is reported as `cleanup_pending` rather than rolling back committed state.

## Missions

Missions are `.pbo` uploads stored under `missions/`. The current serializer validates a safe basename and extension, and replaces an existing record with the same stored path. There is no uploader relation or version history. Mission upload/replacement behavior is unchanged and outside Task 9 remediation; the `Missions.mission_file` storage is enumerated only when checking private-root overlap.
