# Backend-specific agent guide

## Scope

The backend is a Django 5.x project with Django REST Framework, Knox token authentication, MySQL, Celery, Redis, `django-redis`, SteamCMD integration, and filesystem-backed uploads/logs. The Django project entrypoint is `backend/manage.py`; the API is mounted below `/api/`.

Read the root [AGENTS.md](../AGENTS.md) first, then use [architecture](../docs/architecture.md), [API](../docs/api.md), [data model](../docs/data-model.md), [configuration](../docs/configuration.md), and [instance lifecycle](../docs/domain/instance-lifecycle.md) as detailed references.

## Backend rules

- Treat `backend/main/migrations/` as immutable history. Add a new migration for schema changes; never rewrite an applied migration.
- Do not run Django tests against a real or shared database, through default `manage.py test`, or with production settings. Run relevant backend behavior tests separately from the repository root with `PIPENV_DONT_LOAD_ENV=1 pipenv run python scripts/test_backend.py` in POSIX shells, or `$env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py` in PowerShell. Both commands prevent Pipenv from loading `.env` and select the isolated runner.
- Do not invoke SteamCMD, generated shell scripts, Arma processes, `psutil` termination, or production filesystem cleanup during automated checks.
- Do not read real `backend/.env`, `backend/data/config.json`, media, logs, or credentials. Use invented temporary values and files.
- Preserve API response shapes, Polish error messages, trailing-slash routing, and Knox authentication unless a behavior change is explicitly requested.
- Document filesystem side effects, port reservation/release, task IDs, cache keys, and permission changes when touching related code.

## Verification

Use `pipenv run check` from the repository root. It performs dependency validation, documentation tests, syntax compilation, frontend checks, and `git diff --check`; it does not connect to MySQL, Redis, Steam, or Arma.

The default safe check skips Django tests. For relevant backend behavior tests, run one of these commands from the repository root.

POSIX shells:

```sh
PIPENV_DONT_LOAD_ENV=1 pipenv run python scripts/test_backend.py
```

PowerShell:

```powershell
$env:PIPENV_DONT_LOAD_ENV = '1'; pipenv run python scripts/test_backend.py
```

The runner uses an in-memory SQLite database and disposable configuration and filesystem paths under a temporary directory. It guards protected repository runtime data paths and blocks external network, subprocess, and host process operations. Do not use default `manage.py test` or production settings for backend tests.
