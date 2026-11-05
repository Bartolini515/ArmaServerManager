---
type: process
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Mission upload

## Input → movement → output

A user submits a mission file; the API validates and saves it through the serializer; a mission record and stored file result.

## Steps

1. [Mission UI](../../../frontend/src/components/missions/) sends the upload to [MissionsViewset](../../../backend/main/views.py).
2. [MissionSerializer](../../../backend/main/serializers.py) validates and handles replacement; [Missions](../../../backend/main/models.py) owns file cleanup.

## If you change this

- **Hits:** [mission and files](../objects/mission.md), serializer, endpoint, UI and [files and missions](../../domain/files-and-missions.md).
- **Does not automatically hit:** mod-download tasks.

## Surfaces and see

Frontend, API and Django storage participate. See [API contract](../../api.md).
