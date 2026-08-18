# Files and missions

The application treats uploaded files as inputs to host-side workflows. The storage layout is defined by Django `upload_to` functions and model deletion overrides.

## Presets

An instance preset is an uploaded HTML file stored below a user-specific preset directory. `preset_parser` extracts links and Workshop IDs. The serializer accepts safe ASCII filenames containing letters, digits, underscores, dots, and hyphens; names shorter than three characters are rejected.

The preset is not the mod content itself. Missing Workshop content is downloaded asynchronously and moved into the configured mods directory. A generated start script references the selected mod paths.

## Generated files

The backend can create:

- a per-user Arma server configuration file containing the generated server identity/configuration;
- an instance start shell script containing the port, user, Arma path, and mod arguments;
- an instance log file under the configured media/log path;
- temporary ghost-folder content during SteamCMD downloads.

These files may contain host paths, usernames, mod IDs, or operational output. They are not suitable for fixtures or public documentation.

## Missions

Mission uploads are `.pbo` files stored under `missions/`. `MissionSerializer` validates the extension and safe filename. Before creating a new mission, it builds the stored path from the basename and deletes an existing record with the same path; this is replacement-by-filename, not version history.

The mission endpoint is authenticated but does not associate a mission with an uploading user in the current model. The exact behavior is documented in [the API contract](../api.md) and [known risks](../known-risks.md).

## Deletion and cleanup

`Profile.delete`, `Instances.delete`, and `Missions.delete` attempt to remove their associated files before deleting the database row. Cleanup is conditional on the path existing and is not a distributed transaction. A database row may therefore outlive an unavailable file, or a file deletion may fail independently of database state.
