# Backend-specific agent guide

## Scope

The backend is a Django 5.x project with Django REST Framework, Knox token authentication, MySQL, Celery, Redis, `django-redis`, SteamCMD integration, and filesystem-backed uploads/logs. The Django project entrypoint is `backend/manage.py`; the API is mounted below `/api/`.

Read the root [AGENTS.md](../AGENTS.md) first, then use [architecture](../docs/architecture.md), [API](../docs/api.md), [data model](../docs/data-model.md), [configuration](../docs/configuration.md), and [instance lifecycle](../docs/domain/instance-lifecycle.md) as detailed references.

## Backend rules

- Treat `backend/main/migrations/` as immutable history. Add a new migration for schema changes; never rewrite an applied migration.
- Do not run Django tests against a real or shared MySQL database. The safe check intentionally skips them until an isolated test configuration exists.
- Do not invoke SteamCMD, generated shell scripts, Arma processes, `psutil` termination, or production filesystem cleanup during automated checks.
- Do not read real `backend/.env`, `backend/data/config.json`, media, logs, or credentials. Use invented temporary values and files.
- Preserve API response shapes, Polish error messages, trailing-slash routing, and Knox authentication unless a behavior change is explicitly requested.
- Document filesystem side effects, port reservation/release, task IDs, cache keys, and permission changes when touching related code.

## Verification

Use `pipenv run check` from the repository root. It performs dependency validation, documentation tests, syntax compilation, frontend checks, and `git diff --check`; it does not connect to MySQL, Redis, Steam, or Arma.
