---
type: process
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Instance management

## Input → movement → output

An authenticated request creates or controls an instance; the API coordinates persistence, files and asynchronous lifecycle tasks; the UI presents instance and task state.

## Steps

1. [Instance API](../../../backend/main/views.py) validates creation, reserves a port and generates files; lifecycle endpoints enqueue work.
2. [Tasks](../../../backend/main/tasks.py) start, stop and reconcile server state; [instance UI](../../../frontend/src/components/instances/) invokes actions and polls.

## If you change this

- **Hits:** [instance and port](../objects/instance.md), task status, host integration and [lifecycle documentation](../../domain/instance-lifecycle.md).
- **Does not automatically hit:** mission upload.

## Surfaces and see

API, Celery, host integration and UI participate. Consult [architecture](../../architecture.md) and relevant code before changing operational behavior.
