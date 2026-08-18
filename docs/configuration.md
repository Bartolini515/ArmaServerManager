# Configuration

Configuration has three sources: Django environment variables, frontend Vite variables, and the JSON object loaded by `backend/main/utils/config.py`. This document records names and shapes only; it never contains real secrets or host paths.

## Django environment variables

`backend/servermanager/settings.py` reads:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Required Django signing key. |
| `DEBUG` | Enables Django debug behavior; defaults to false. |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts; defaults to `127.0.0.1`. |
| `DATABASE_NAME` | MySQL database name. |
| `DATABASE_USER` | MySQL user. |
| `DATABASE_PASSWORD` | MySQL password. |
| `DATABASE_HOST` | MySQL host. |
| `DATABASE_PORT` | MySQL port. |
| `CORS_ALLOWED_ORIGINS` | Allowed browser origins; defaults to `http://localhost:5173`. |
| `CSRF_TRUSTED_ORIGINS` | Trusted origins for CSRF checks. |

The settings also configure Knox authentication, media/static roots, secure-cookie behavior outside debug mode, Redis cache, Celery broker/result URLs, queues, task routing, and the periodic server-status task. The actual Redis URL is currently defined in settings rather than sourced from an environment variable.

## Frontend Vite variables

`frontend/src/components/AxiosInstance.tsx` reads:

| Variable | Purpose |
| --- | --- |
| `VITE_BASE_URL` | API base URL; defaults to `http://127.0.0.1:8000/api/`. |
| `VITE_DEBUG` | Any value other than the exact string `false` keeps debug-style Axios behavior. |

The client stores a Knox token in browser local storage under `Token`. Do not put a token in source, examples, screenshots, or documentation.

## `backend/data/config.json`

The `Config` class creates a blank file when it does not exist. The expected top-level shape is:

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

Dot-separated lookups include `paths.arma3`, `paths.mods_directory`, `paths.logs_directory`, `paths.download_directory`, `paths.steamcmd`, and the three `steam_auth` values. `Config.update` performs a recursive merge and writes the whole file. `ConfigSerializer` validates the shared secret as base64 when supplied and masks `password` and `shared_secret` in responses.

This file is runtime state and may contain credentials and host paths. It is ignored by Git and must be supplied only through an approved environment.
