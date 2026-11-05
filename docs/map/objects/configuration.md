---
type: object
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Configuration

Runtime configuration supplies host paths and Steam authentication settings to backend workflows.

## Why this shape

Configuration is shared across API and background tasks and may contain sensitive values.

## Shape and connections

- [Config implementation](../../../backend/main/utils/config.py), [serializer](../../../backend/main/serializers.py), [moderator API](../../../backend/main/views.py), and [moderator UI](../../../frontend/src/components/moderatorPanel/).

## If you change this

- **Hits:** configuration serialization, moderator forms, affected tasks and [configuration documentation](../../configuration.md).
- **Does not automatically hit:** unrelated theme settings.

## Surfaces and see

Administrators edit configuration; backend code reads it. Use invented fixtures only; never inspect real configuration or credentials. See [known risks](../../known-risks.md).
