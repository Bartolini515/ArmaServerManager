# Repository-wide working rules

These rules apply to all work in ArmaServerManager. Read the nearest backend or frontend `AGENTS.md` for additional rules.

## Working rules

- Inspect relevant code, tests, documentation, and repository status before editing.
- Preserve unrelated and user-authored changes. Do not alter `.vscode/settings.json` unless explicitly requested.
- For behavioral changes, use RED → GREEN → REFACTOR and add a focused regression test first.
- Keep identifiers and source comments in English unless preserving an existing Polish user-facing string. New repository documentation is English.
- Do not add production dependencies without explicit approval. Do not add Docker, deployment, or external integrations as part of documentation work.
- Keep documentation current when changing routes, API responses, models, migrations, tasks, configuration keys, file contracts, or security boundaries.

## Sensitive data and approval gates

- Never read, copy, log, or test against real `.env`, `backend/data/config.json`, database contents, uploaded media, production logs, or Steam credentials.
- Treat usernames, file names, paths, server logs, database records, and host process identifiers as operational data; anonymize them in fixtures and documentation.
- Do not start/stop/kill Arma or SteamCMD processes, modify production data, run migrations against a real database, or contact external services without explicit approval.
- Do not perform deployment, branch creation, commits, pushes, releases, or pull requests unless the user explicitly requests that exact action.

## Verification and Definition of Done

Run the safe repository check:

```text
pipenv run check
```

It runs dependency, documentation, backend syntax, frontend lint/build, and diff-whitespace checks. It explicitly skips Django tests because an isolated test database is not configured. Report that omission instead of implying full application-test coverage.

A change is complete only when its relevant checks were actually run, the affected documentation is updated, no secrets or personal paths entered the diff, and the final report names changed files, checks, limitations, and any required follow-up.

## Documentation duties

- API, permission, or route changes → `docs/api.md` and `docs/code-map.md`.
- Model, migration, file-storage, or cascade changes → `docs/data-model.md` and the relevant domain document.
- Celery, process, mod, timeout, or log changes → `docs/architecture.md`, `docs/domain/instance-lifecycle.md`, and `docs/known-risks.md`.
- Configuration changes → `docs/configuration.md` and `docs/known-risks.md` when sensitivity or operational safety changes.
- New recurring diagnostic behavior → `docs/troubleshooting.md`.
- Durable cross-cutting choices → a new ADR under `docs/decisions/`.
