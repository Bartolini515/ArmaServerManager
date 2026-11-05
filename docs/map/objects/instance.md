---
type: object
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Instance and port

An instance is the `Instances` model; it associates an owner, uploaded preset and reserved `Ports` record with server state.

## Why this shape

Instance management must coordinate persisted state, host files and a port; these have distinct failure and cleanup paths.

## Shape and connections

- [Model and cleanup](../../../backend/main/models.py): `Instances` owns preset, log and start-file references and joins `Profile` and `Ports`.
- [Instance API](../../../backend/main/views.py) and [tasks](../../../backend/main/tasks.py) change state; [instance UI](../../../frontend/src/components/instances/) presents it.

## If you change this

- **Hits:** serializers, lifecycle tasks, instance UI, model tests and [data model](../../data-model.md).
- **Does not automatically hit:** the independent [mission model](mission.md).

## Surfaces and see

The API, Celery worker and UI read or change instance state. See [instance lifecycle](../../domain/instance-lifecycle.md) and [management process](../processes/instance-management.md).
