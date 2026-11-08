# ADR-0002: Atomic local configuration snapshots and updates

- Status: accepted
- Date: 2026-11-08
- Owners: ArmaServerManager maintainers

## Context

The JSON configuration is used by more than one application process. A
process-local cached copy could remain stale after another process updated the
file. Concurrent read/modify/write cycles could also replace the whole file
with changes based on stale data, losing a disjoint update. The existing JSON
shape and default location are retained.

## Decision

Keep the JSON file as the local configuration store and make reads and writes
coherent for processes that use the application configuration API:

- `Config.snapshot()`, `Config.get()`, and the legacy `config.config` property
  read the current JSON and return deep copies. A caller that needs related
  fields from one file version takes one snapshot for that operation.
- Writers coordinate through the stable `<config file>.lock` sidecar. Under
  that lock, `Config.update()` reloads the latest JSON, recursively merges the
  supplied fields, writes a temporary sibling, flushes and `fsync`s the file,
  then publishes it with `os.replace`. Missing-file bootstrap rechecks under
  the same lock before publishing the default or merged document.
- POSIX reads of an existing file do not require the sidecar; an open file
  handle reads one complete old or new inode. Missing-file bootstrap and all
  writes use the sidecar. Windows readers and writers use the sidecar because
  an open destination handle can block replacement. POSIX uses `fcntl.flock`;
  Windows uses `msvcrt.locking`.
- On POSIX, replacement preserves the existing config owner, group, and mode.
  A failed ownership change may fall back only for an `EPERM` failure on a
  root-owned file with exact mode `0660`, and only when the non-root writer is
  already a member of its group. The fallback keeps the group and mode but
  makes the writer the new owner. Other owner changes, or any group/mode
  preservation failure, abort before replacement. A new POSIX config is
  owner-only (`0600`).

This contract is for cooperating processes using the same local configuration
directory and sidecar. It does not coordinate manual edits or other programs
that replace the JSON without taking the sidecar lock.

## Alternatives considered

- Keep process-local cached JSON and add only an in-process mutex: rejected
  because separate server or worker processes would still read stale state
  and overwrite one another's updates.
- Rewrite the JSON directly without a sibling temporary and atomic replace:
  rejected because readers could observe a partial document and a failed
  write could damage the current file.
- Replace the local JSON file with a central database or distributed
  coordination service: not selected because the accepted change is limited
  to the existing local configuration contract and adds no production
  service or dependency.

## Consequences

Fresh reads remove stale process-local configuration views. Reloading under
the sidecar lock prevents lost merges among cooperating writers, and whole-file
replacement keeps each local read on a complete JSON version. A multi-field
operation must use one `snapshot()`; several `get()` calls are separate reads
and can observe different versions.

The scheme adds one local file read per snapshot and requires writers to have
directory replacement access plus access to the stable sidecar. POSIX readers
of an existing file need no sidecar access; Windows readers also take the
sidecar lock and therefore need the Windows sidecar open permissions. Existing
POSIX sidecars are not changed or removed, and a newly created sidecar is
published as a readable non-secret file with mode `0644`.

For shared POSIX provisioning, the existing JSON owner, group, and mode must
remain preservable. The narrow root-owned `0660` fallback may change ownership
to the writer UID, so deployments should keep writer identity stable and
provision group access intentionally. A different non-root writer is not
silently granted ownership. The POSIX metadata contract preserves owner,
group, and mode only; custom ACLs and extended attributes are not copied.

The lock and replacement assumptions require a trusted local configuration
directory and cooperating application processes. NFS and other network or
distributed filesystems with different `flock`, link, or replacement
semantics are outside the verified contract. The temporary file is
`fsync`ed, but the containing directory is not, so no complete power-loss
durability guarantee is made.

## Verification and follow-up

[`test_config_consistency.py`](../../backend/main/tests/test_config_consistency.py)
uses disposable files to cover fresh reads across `Config` instances,
concurrent thread updates, copy-safe snapshots, merge/clear behavior,
publication failure, permission preservation, sidecar locking, and Windows
reader/update overlap. POSIX behavior is partly platform-mocked on Windows;
three POSIX-only cases are skipped there. No real child-process lock
contention, alternate-UID execution, NFS behavior, or live deployment was
verified. This test evidence describes the reviewed local contract; operators
must provision and verify deployment ownership, group access, and filesystem
behavior themselves.

See the [configuration guide](../configuration.md) for the current file shape,
API behavior, and operational notes. The implementation is in
[`config.py`](../../backend/main/utils/config.py) and
[`config_lock.py`](../../backend/main/utils/config_lock.py).
