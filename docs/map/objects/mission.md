---
type: object
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Mission and files

The `Missions` model stores an uploaded mission file independently of an instance.

## Why this shape

Mission uploads have their own validation, replacement and file-cleanup behavior.

## Shape and connections

- [Model](../../../backend/main/models.py), [serializer](../../../backend/main/serializers.py), [API](../../../backend/main/views.py), and [mission UI](../../../frontend/src/components/missions/).
- [Files and missions](../../domain/files-and-missions.md) describes storage and replacement; check the serializer for current details.

## If you change this

- **Hits:** serializer, mission endpoint, frontend upload, file cleanup and domain documentation.
- **Does not automatically hit:** instance port allocation.

## Surfaces and see

Authenticated users upload through the UI and API; Django stores and removes files. See [mission upload](../processes/mission-upload.md).
