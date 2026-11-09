# Configuration

This document covers the Django environment, Vite variables, and the JSON
object loaded by [`Config`](../backend/main/utils/config.py). It records names
and shapes without real credentials or host paths.

## Django environment variables

`backend/servermanager/settings.py` loads `.env` through `django-environ` and
reads these values:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Required Django signing key. |
| `DEBUG` | Django debug setting; defaults to false. |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts; defaults to `127.0.0.1`. |
| `DATABASE_NAME` | MySQL database name. |
| `DATABASE_USER` | MySQL user. |
| `DATABASE_PASSWORD` | MySQL password. |
| `DATABASE_HOST` | MySQL host. |
| `DATABASE_PORT` | MySQL port. |
| `CORS_ALLOWED_ORIGINS` | Allowed browser origins; defaults to `http://localhost:5173`. |
| `CSRF_TRUSTED_ORIGINS` | Trusted origins for CSRF checks; defaults to an empty list. |

Settings also configure Knox authentication, media/static roots, secure-cookie
behavior outside debug mode, Redis cache, Celery broker/result URLs, queues,
task routing, and the periodic server-status task. Redis URLs are currently
defined in settings rather than read from environment variables.

## Frontend Vite variables

[`AxiosInstance.tsx`](../frontend/src/components/AxiosInstance.tsx) reads:

| Variable | Purpose |
| --- | --- |
| `VITE_BASE_URL` | API base URL; defaults to `http://127.0.0.1:8000/api/`. |
| `VITE_DEBUG` | When set to the exact string `false`, Axios enables `withCredentials` and sets the `csrftoken` / `X-CSRFToken` names. Any other value, including an unset value, keeps the default debug-style Axios configuration. |

The client stores the Knox token in browser local storage under the key
`Token` and sends it as an `Authorization: Token ...` header for authenticated
requests. Do not put tokens in source, examples, screenshots, or documentation.

## Server JSON configuration

The default file is `backend/data/config.json`. Its top-level shape is:

```json
{
  "paths": {
    "steamcmd": "",
    "arma3": "",
    "mods_directory": "",
    "logs_directory": "",
    "download_directory": ""
  },
  "steam_auth": {
    "username": "",
    "password": "",
    "shared_secret": ""
  }
}
```

Dot-path reads include `paths.steamcmd`, `paths.arma3`,
`paths.mods_directory`, `paths.logs_directory`, `paths.download_directory`,
and the three `steam_auth` values. `Config.get()` reads a fresh snapshot for
each call; `Config.snapshot()` returns one deep-copied view for code that needs
several related values from the same read. The legacy `config.config` property
also returns a fresh copy. Use one `snapshot()` for a multi-field operation;
separate `get()` calls can observe different updates.

`Config.update()` reloads the latest file, recursively merges the supplied
mapping, and writes the complete JSON document. Omitted keys keep their
current values, including credentials. An explicitly supplied empty string
clears that value. A missing file is initialized with the shape above. These
read/update semantics are implemented in [`config.py`](../backend/main/utils/config.py)
and covered by the isolated configuration regressions in
[`test_config_consistency.py`](../backend/main/tests/test_config_consistency.py).

The config API serializer requires `paths` and `steam_auth` maps whose values
are validated with DRF `CharField`s. Strings, integers, and floats are
accepted; numeric values are converted to strings, while booleans and
composite values are rejected. `steam_auth` allows blank strings, while
`paths` uses nonblank validation. A nonempty `steam_auth.shared_secret` must
be valid base64. The config GET response masks nonempty `password` and
`shared_secret` values as exactly eight asterisks; `username` remains present.
In the moderator form,
[`configPayload.ts`](../frontend/src/components/moderatorPanel/configPayload.ts)
omits only those exact masked placeholders from the nested `steam_auth`
payload. An empty string remains present and clears the stored value. This
combines with the recursive merge so an unrelated edit leaves a masked
credential unchanged. See [`serializers.py`](../backend/main/serializers.py),
[`views.py`](../backend/main/views.py), and the
[`configPayload` regressions](../frontend/src/components/moderatorPanel/configPayload.test.ts).

The JSON file stores its values as ordinary JSON text; `Config` does not
encrypt Steam credentials. It can contain credentials and host paths, so treat
it as sensitive runtime state. The `backend/data/*` files are ignored by Git
except for `.gitkeep`. The isolated backend test runner sets `ASM_CONFIG_PATH`
to a disposable test file so tests do not target the default runtime
location. That is test-runner routing, not an instruction to read or redirect
real runtime configuration.

### Local file coordination and limits

Writers use the stable sibling sidecar `<config file>.lock`. They lock it,
reload the current JSON while holding the lock, merge, write a temporary
sibling file, flush and `fsync` that file, then publish it with `os.replace`.
Missing-file bootstrap also takes the lock and rechecks before publishing.
This gives cooperating `Config.update()` writers serialized whole-file
updates on a local filesystem. A manual editor or another program that writes
without the sidecar is outside that coordination protocol.

| Platform | Snapshot reads | Writers and bootstrap |
| --- | --- | --- |
| POSIX | An existing file is read directly; its open handle sees one complete old or new file, and the read does not require access to the sidecar. A missing-file bootstrap rechecks under the lock. | Writers use `fcntl.flock` on the sidecar. A new sidecar is published with mode `0644`; an existing sidecar is opened read-only and is never changed or removed. |
| Windows | Readers take the sidecar lock because an open destination handle can block replacement. | Readers, writers, and bootstrap coordinate through the sidecar using `msvcrt.locking`. |

Processes that update the file need write/replace access to its directory and
access to the sidecar. POSIX readers of an existing config file need no
sidecar access; Windows readers open the sidecar for read/write. Provision
the directory, JSON file, and sidecar for the service identities that must
use them. On POSIX, replacing an existing config preserves its owner, group,
and permission bits. If ownership preservation fails with `EPERM`, the only
fallback is a root-owned file with exact mode `0660`, written by a non-root
process already in the file's group. That fallback retains the group and mode
but makes the writer the new owner; it is not a general transfer between
non-root owners. Group or mode preservation failures abort before replacement
and leave the old file in place. A new POSIX config starts with owner-only
permissions (`0600`).

This is cooperative locking inside a trusted local configuration directory,
not a security boundary against a process that can replace the directory,
config file, or sidecar. NFS and other filesystems with different locking,
link, or replacement guarantees are not covered. Replacing an inode does not
copy custom ACLs or extended attributes. Although the temporary file is
flushed and `fsync`ed before replacement, the containing directory is not
`fsync`ed; this is not a power-loss durability guarantee.

[`test_config_consistency.py`](../backend/main/tests/test_config_consistency.py)
uses temporary files, independent `Config` objects, concurrent threads, and
platform mocks. It does not prove real child-process lock contention,
alternate-UID ownership behavior, or a live deployment. Three POSIX-only
cases are skipped by the suite on Windows.

### Operation logs and Steam secrets

`paths.logs_directory` is the root for application operation logs. Create,
start, and preset-change operations write UTF-8 files there; Celery mod
downloads use a user-scoped directory. Files are written as records arrive,
and records include operation and user context. The separate error file is
opened only after an `ERROR` record. Names use a timestamp plus a unique
suffix to prevent same-tick collisions. The application does not automatically
delete or rotate these files. This path
does not control `Instances.log_file`, which remains media-backed Arma process
output.

SteamCMD handling builds a redactor from the configured Steam username,
password, and shared secret, and registers generated Steam Guard codes before
they reach covered sinks. In those Steam-sensitive flows, process output,
callbacks, operation logs, and task/API failure reporting pass through that
handling. Generic operation logging without a Steam redactor keeps its normal
formatter. Safe Steam diagnostics omit subprocess command arguments and raw
exception text. The scope and regression coverage are in
[`steam_errors.py`](../backend/main/utils/steam_errors.py) and
[`test_steamcmd_secrets.py`](../backend/main/tests/test_steamcmd_secrets.py).

For the rationale and tradeoffs behind these local snapshot and update rules,
see [ADR-0002](decisions/0002-atomic-local-configuration.md).
