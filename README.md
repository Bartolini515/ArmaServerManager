# ArmaServerManager

ArmaServerManager is a web application for controlled administration of Arma 3 servers. Authenticated users can upload mission files, create temporary server instances from HTML mod presets, download missing Steam Workshop mods, start and stop their instances, and inspect server logs. Administrators can manage users and configuration and operate shared administrative instances.

The repository contains a Django/ Django REST Framework backend and a React/TypeScript frontend. Celery tasks coordinate long-running mod downloads and server process operations through Redis. MySQL stores application data, while SteamCMD, the Arma 3 installation, uploaded files, generated shell scripts, and logs remain external operational boundaries.

## Current status

This documentation describes the behavior currently present in the code. The runtime orchestration is not yet standardized and Dockerization is planned separately; this repository guide therefore does not present an unsupported production or local startup procedure.

The safe local verification command is:

```text
pipenv run check
```

It checks installed Python dependencies, documentation structure, backend syntax, frontend lint/build, and whitespace in the Git diff. Django tests that would require a configured isolated database are explicitly skipped.

## Main capabilities

- token-authenticated user and administrator access;
- user-owned and administrator-owned server instances;
- port reservation and release;
- HTML preset parsing and Steam Workshop mod discovery;
- asynchronous mod downloads with progress polling;
- generated Arma server configuration and start scripts;
- asynchronous server start, stop, status reconciliation, and one-hour timeout for temporary instances;
- server log viewing, download, and deletion;
- `.pbo` mission upload with replacement by filename;
- administrator user and configuration management;
- system resource monitoring and theme/settings screens.

## Repository map

- `backend/` — Django project, REST API, models, migrations, Celery tasks, SteamCMD integration, file handling, and tests.
- `frontend/` — Vite React application, routes, authentication context, UI components, and API calls.
- `Pipfile` / `Pipfile.lock` — Python dependency declarations and lockfile.
- `frontend/package.json` / `frontend/package-lock.json` — frontend dependencies and scripts.
- `docs/` — architecture, code map, API, data, configuration, domain flows, risks, and troubleshooting.
- `scripts/check.py` — portable safe verification entrypoint.
- `tests/` — dependency-free tests for documentation and the check runner.

## Documentation index

- [Architecture](docs/architecture.md)
- [Code map](docs/code-map.md)
- [API contract](docs/api.md)
- [Data model](docs/data-model.md)
- [Configuration](docs/configuration.md)
- [Instance lifecycle](docs/domain/instance-lifecycle.md)
- [Files and missions](docs/domain/files-and-missions.md)
- [Known risks and limitations](docs/known-risks.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture decision records](docs/decisions/README.md)

## Boundaries

Do not treat ignored `.env`, `backend/data/config.json`, uploaded media, logs, database contents, Steam credentials, or host process state as documentation fixtures. Never copy their real values into issues, tests, screenshots, or Markdown. See [repository instructions](AGENTS.md) and the backend-specific instructions in [backend/AGENTS.md](backend/AGENTS.md).
