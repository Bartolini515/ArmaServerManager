---
type: object
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Authentication and profile

The `Profile` model represents a user; authentication and frontend session state are separate concerns.

## Why this shape

The API enforces access while the UI maintains the authenticated user's presentation and routing state.

## Shape and connections

- [Profile](../../../backend/main/models.py), [login and account API](../../../backend/main/views.py), [API client](../../../frontend/src/components/AxiosInstance.tsx), [auth context](../../../frontend/src/contexts/AuthContext.tsx), and [protected routes](../../../frontend/src/components/ProtectedRoutes.tsx).

## If you change this

- **Hits:** API permissions, token handling, route protection, account UI and [API contract](../../api.md).
- **Does not automatically hit:** Steam credentials, which belong to [configuration](configuration.md).

## Surfaces and see

Users authenticate through the frontend and API; backend permissions determine access. See [architecture](../../architecture.md).
