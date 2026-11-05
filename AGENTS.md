# ArmaServerManager agent guide

ArmaServerManager is a Django/DRF and React/TypeScript application for managing Arma 3 servers. Code and tests define current behavior; verify documentation against them.

## Start here

- **Every task:** read [repository-wide working rules](docs/agents/repository-rules.md), including safety, approval and verification requirements.
- **Backend or frontend work:** also read [backend instructions](backend/AGENTS.md) or [frontend instructions](frontend/AGENTS.md), respectively.
- **Navigate a change:** use the [system map](docs/map/AGENTS.md) and [code map](docs/code-map.md).
- **API, data, operations or configuration:** consult the relevant [architecture](docs/architecture.md), [API](docs/api.md), [data model](docs/data-model.md), [configuration](docs/configuration.md), [domain](docs/domain/), [risks](docs/known-risks.md) and [troubleshooting](docs/troubleshooting.md) documents.
- **Local issues and specs:** see the [issue tracker](docs/agents/issue-tracker.md) and [triage labels](docs/agents/triage-labels.md).
- **Domain vocabulary and decisions:** see [domain documentation rules](docs/agents/domain.md) and [ADRs](docs/decisions/README.md).

## Sources of truth

- Backend models and migrations define data; URLs, views and serializers define the API; tasks define asynchronous operations; configuration code and Django settings define runtime shapes.
- Frontend routes, API client, contexts and components define UI behavior and API coupling. Follow the relevant links in the system map to the implementation.
- Existing documentation explains verified behavior. When it disagrees with code or tests, inspect the implementation and update affected sources together.
