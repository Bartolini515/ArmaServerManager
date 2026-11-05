---
type: process
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Mod download

## Input → movement → output

An instance preset enters a task; the worker parses its Workshop IDs, checks installed content and invokes SteamCMD for missing mods; a successful task marks the instance ready.

## Steps

1. [Instance API](../../../backend/main/views.py) schedules the task and returns its identifier.
2. [Download task](../../../backend/main/tasks.py) parses the preset, checks mods, reports progress and updates readiness; [SteamCMD integration](../../../backend/main/steamcmd/mods_download.py) handles downloads.

## If you change this

- **Hits:** [preset and mods](../objects/preset-mods.md), instance readiness, polling, logging and [lifecycle documentation](../../domain/instance-lifecycle.md).
- **Does not automatically hit:** mission file replacement.

## Surfaces and see

API, Celery, cache, SteamCMD and UI are involved. Consult [known risks](../../known-risks.md) before changing host operations.
