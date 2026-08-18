# Frontend-specific agent guide

## Scope

The frontend is a Vite React application written in TypeScript and styled with Material UI. `src/App.tsx` defines the route tree, `src/components/AxiosInstance.tsx` defines the API client and token/CSRF behavior, and contexts provide authentication, alerts, and theme state.

Read the root [AGENTS.md](../AGENTS.md) and the [code map](../docs/code-map.md) before editing. The backend contract is documented in [docs/api.md](../docs/api.md).

## Frontend rules

- Preserve existing Polish user-facing strings unless translation or copy changes are explicitly requested.
- Keep API paths, trailing slashes, multipart uploads, task polling, and Knox token handling compatible with the backend.
- Prefer precise TypeScript types, `unknown` plus explicit narrowing for caught errors, and existing React Hook Form/MUI types over `any`.
- Do not weaken ESLint rules to hide type or hook problems. The required result is zero ESLint errors; existing non-blocking warnings must remain visible and documented.
- Keep authentication redirects and local-storage keys stable unless the API contract is intentionally changed.
- Do not add a frontend dependency without approval.

## Verification

Run `pipenv run check` from the repository root. The frontend stages run `npm run lint` and `npm run build` through a PATH-resolved `npm`/`npm.cmd`; no backend service is required.
