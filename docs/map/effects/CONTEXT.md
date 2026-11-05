# Change impact — routing contract

## Inputs

Working: the proposed change. Reference: [map contract](../CONTEXT.md).

## Process

Select the relevant row, open its cards, then verify the current source and tests. These are first-order starting points, not a complete dependency graph.

| Change | Open |
| --- | --- |
| Instance, ports, lifecycle or task polling | [Instance](../objects/instance.md), [management](../processes/instance-management.md) |
| Preset, Workshop or download | [Preset and mods](../objects/preset-mods.md), [download](../processes/mod-download.md) |
| Mission or upload | [Mission](../objects/mission.md), [upload](../processes/mission-upload.md) |
| Configuration or Steam settings | [Configuration](../objects/configuration.md), [download](../processes/mod-download.md) |
| Login, permissions or protected routes | [Authentication](../objects/authentication.md) |

## Outputs

A task-specific list of source, tests and documentation to inspect.

## Check

Verify each selected connection in source; consult the owner for consumers outside the repository before moving paths.
