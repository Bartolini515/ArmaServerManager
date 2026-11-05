---
type: object
universe: live
status: verified
verified: 2026-11-03
revision: 150a315 plus working tree
---

# Preset and mods

A preset is an instance-owned upload containing Workshop references; it is distinct from installed mod content.

## Why this shape

Parsing the input and fetching missing content are separate operations, and download work runs asynchronously.

## Shape and connections

- [Instance model](../../../backend/main/models.py), [preset parser](../../../backend/main/modpreset/preset_extraction.py), [installed-mod checks](../../../backend/main/modpreset/modpathing.py), and [SteamCMD integration](../../../backend/main/steamcmd/mods_download.py).
- The [download task](../../../backend/main/tasks.py) uses the preset and marks the instance ready after success.

## If you change this

- **Hits:** instance creation, download task, generated start files, task polling, and [files and missions](../../domain/files-and-missions.md).
- **Does not automatically hit:** mission upload validation.

## Surfaces and see

The API and worker process presets; SteamCMD obtains missing mods. See [download process](../processes/mod-download.md).
