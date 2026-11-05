# Feature and function map

This is a navigation map of current capabilities and principal functions, verified against the working tree on 2026-11-03 (base revision `150a315`). It is not an exhaustive symbol index or a guarantee of authorization, correctness, or operational safety. For exact routes and access rules use the [API contract](../api.md), then verify the implementation and tests. See [known risks](../known-risks.md) for limitations.

## Application capabilities

| Capability | Main implementation | Related context |
| --- | --- | --- |
| Log in, log out, restore session and guard routes | `LoginViewset.create`, `AccountViewset.get_user` in [views](../../backend/main/views.py); Knox logout in [URL configuration](../../backend/servermanager/urls.py); [login](../../frontend/src/components/auth/Login.tsx), [API client](../../frontend/src/components/AxiosInstance.tsx), [protected routes](../../frontend/src/components/ProtectedRoutes.tsx) | [Authentication](objects/authentication.md) |
| View account and change password | `AccountViewset.list`, `change_password` in [views](../../backend/main/views.py); [account](../../frontend/src/components/account/Account.tsx), [password form](../../frontend/src/components/auth/ChangePassword.tsx) | [API contract](../api.md) |
| View system resource information | `ServicesViewset.getSystemInfo` in [views](../../backend/main/views.py); [resource monitor](../../frontend/src/components/dashboard/SystemResourcesMonitor.tsx) | [Architecture](../architecture.md) |
| List, create and delete instances | `InstancesViewset.list`, `create`, `destroy` in [views](../../backend/main/views.py); [instances screen](../../frontend/src/components/instances/Instances.tsx), [create dialog](../../frontend/src/components/instances/modals/CreateInstanceModal.tsx) | [Instance](objects/instance.md), [lifecycle](processes/instance-management.md) |
| Start and stop servers, track task progress | `InstancesViewset.start`, `stop`, `task_status` in [views](../../backend/main/views.py); `start_server_task`, `stop_server_task` in [tasks](../../backend/main/tasks.py); [instances screen](../../frontend/src/components/instances/Instances.tsx) | [Lifecycle](../domain/instance-lifecycle.md) |
| Download preset mods and change an administrative preset | `InstancesViewset.download_mods`, `change_preset` in [views](../../backend/main/views.py); `download_mods_task` in [tasks](../../backend/main/tasks.py); [preset dialog](../../frontend/src/components/instances/modals/ChangePresetModal.tsx) | [Preset and mods](objects/preset-mods.md), [download](processes/mod-download.md) |
| View, download and delete instance logs | `InstancesViewset.logs`, `download_logs`, `delete_logs` in [views](../../backend/main/views.py); [log dialog](../../frontend/src/components/instances/modals/ServerLogsModal.tsx) | [API contract](../api.md), [risks](../known-risks.md) |
| Upload and replace a mission file | `MissionsViewset.create` in [views](../../backend/main/views.py); `MissionSerializer.validate_mission_file`, `create` in [serializers](../../backend/main/serializers.py); [mission dialog](../../frontend/src/components/missions/CreateMissionModal.tsx) | [Mission upload](processes/mission-upload.md), [file behavior](../domain/files-and-missions.md) |
| Manage profiles and server configuration | `ModeratorPanelViewset` profile and configuration actions in [views](../../backend/main/views.py); [user management](../../frontend/src/components/moderatorPanel/ModeratorPanelDataManagement.tsx), [configuration screen](../../frontend/src/components/moderatorPanel/ModeratorPanelConfiguration.tsx) | [Configuration](objects/configuration.md), [API contract](../api.md) |
| Change appearance | [Settings](../../frontend/src/components/settings/Settings.tsx), `CustomThemeProvider` and `useCustomTheme` in [theme context](../../frontend/src/contexts/ThemeContext.tsx) | [Frontend composition](../architecture.md) |

## Backend functions and supporting components

These are implementation entry points, grouped by job; inclusion does not imply each helper is directly exposed through the UI.

| Job | Functions or classes | Source |
| --- | --- | --- |
| Store profiles, instances, ports and missions; remove associated files | `Profile`, `Instances`, `Ports`, `Missions`, model `delete` methods, `user_presets_directory_path`, `user_logs_directory_path` | [models.py](../../backend/main/models.py) |
| Validate and serialize API data | `ProfileSerializer`, `LoginSerializer`, `Password_changeSerializer`, `Profiles_moderatorPanelSerializer`, `ConfigSerializer`, `InstanceSerializer`, `MissionSerializer` | [serializers.py](../../backend/main/serializers.py) |
| Download mods; start, stop, reconcile and time out instances | `download_mods_task`, `start_server_task`, `stop_server_task`, `check_all_servers_status_task`, `instance_timeout_task` | [tasks.py](../../backend/main/tasks.py) |
| Parse preset references and identify installed content | `extract_links`, `extract_workshop_ids`, `preset_parser`; `mod_path`, `check_installed` | [preset extraction](../../backend/main/modpreset/preset_extraction.py), [mod paths](../../backend/main/modpreset/modpathing.py) |
| Generate or locate server startup files | `generate_server_config`, `generate_sh_file`, `check_sh_file_exists` | [start files](../../backend/main/modpreset/start_files.py) |
| Run and observe a server process | `start_server`, `reader_thread`, `stream_process_output` | [server handling](../../backend/main/serverhandling/start_server.py), [process output](../../backend/main/utils/process_output.py) |
| Steam authentication and download helpers | `load_credentials`, `generate_steam_guard_code`, `assign_new_steamguard`, `download_mods`, `steamcmd_download`, `download_fallback`, `test_connection` | [Steam authentication](../../backend/main/steamcmd/steam_auth.py), [downloads](../../backend/main/steamcmd/mods_download.py) |
| Host file workarounds | `GhostFolder`, `delete_steamcmd_appcache`, `lowercase_addons_directory` | [ghost folder](../../backend/main/workaround/ghost_folder.py), [cache deletion](../../backend/main/workaround/cache_deletion.py), [addon normalization](../../backend/main/workaround/modsrenamer.py) |
| Read or update runtime configuration; write operation logs | `Config.get`, `Config.update`, `operation_loggers` | [configuration](../../backend/main/utils/config.py), [operation logging](../../backend/main/utils/operation_logging.py) |
| Populate ports through a management command | `Command.add_arguments`, `Command.handle` | [populate_ports.py](../../backend/main/management/commands/populate_ports.py) |

## Frontend foundations

| Job | Functions or components | Source |
| --- | --- | --- |
| Routes, API requests and authentication | `App`, `AxiosInstance`, `ProtectedRoutes`, `AuthProvider`, `useAuth` | [App](../../frontend/src/App.tsx), [API client](../../frontend/src/components/AxiosInstance.tsx), [protected routes](../../frontend/src/components/ProtectedRoutes.tsx), [auth context](../../frontend/src/contexts/AuthContext.tsx) |
| Notifications and themes | `AlertProvider`, `useAlert`, `CustomThemeProvider`, `useCustomTheme` | [alert context](../../frontend/src/contexts/AlertContext.tsx), [theme context](../../frontend/src/contexts/ThemeContext.tsx) |
| Polling, countdowns and error formatting | `useInterval`, `useCountdown`, `timeUtils`, `getApiErrorMessage`, `getFieldErrorMessage`, `humanFileSize` | [interval](../../frontend/src/hooks/use-interval.ts), [countdown](../../frontend/src/hooks/use-countdown.ts), [API errors](../../frontend/src/util/api-error.ts), [utilities](../../frontend/src/util/util.ts) |
| Reusable controls, dialogs and feedback | Form controls, `AlertDialog`, `MyAlert`, progress indicator | [shared UI](../../frontend/src/UI/) |

## Maintenance

When a capability, main function or its location changes, update the relevant row and verify links and behavior against source. Add entries for meaningful behavior, rather than every private callback or generated symbol. Existing [code map](../code-map.md), [API contract](../api.md), domain documents and source retain their respective responsibilities.
