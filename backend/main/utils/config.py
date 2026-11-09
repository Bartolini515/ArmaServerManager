"""JSON-backed application configuration with atomic cross-process updates."""

from __future__ import annotations

import copy
import errno
import json
import os
import stat
import tempfile

from .config_lock import exclusive_file_lock

_IS_POSIX = os.name == "posix"


class Config:
    def __init__(self, file_path: str):
        """Initialize a configuration reader for one JSON file.

        Relative paths remain relative to this module, matching the original
        configuration path behavior.
        """
        if not os.path.isabs(file_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.file_path = os.path.abspath(os.path.join(script_dir, file_path))
        else:
            self.file_path = os.path.abspath(file_path)

        self._config = {}
        self._create_blank_config()

    @staticmethod
    def _default_config() -> dict:
        """Return a new blank configuration shape with no shared mutable data."""
        return {
            "paths": {
                "steamcmd": "",
                "arma3": "",
                "mods_directory": "",
                "logs_directory": "",
                "download_directory": "",
            },
            "steam_auth": {
                "username": "",
                "password": "",
                "shared_secret": "",
            },
        }

    @property
    def config(self) -> dict:
        """Return a fresh, current snapshot for legacy ``config.config`` users."""
        return self.snapshot()

    def _read_file(self) -> dict:
        with open(self.file_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _read_or_create_locked(self) -> dict:
        """Read an existing file or atomically bootstrap it while locked."""
        try:
            return self._read_file()
        except FileNotFoundError:
            default_config = self._default_config()
            self._atomic_write(default_config)
            return default_config

    def _create_blank_config(self):
        """Load existing JSON or bootstrap a missing file safely."""
        self._config = self.snapshot()

    def _load(self):
        """Refresh the in-memory view from the latest complete disk snapshot."""
        self._config = self.snapshot()

    def _atomic_write(self, value: dict):
        """Publish JSON through a flushed temporary sibling and atomic replace."""
        directory = os.path.dirname(self.file_path)
        prefix = f".{os.path.basename(self.file_path)}."
        temporary_path = None
        try:
            existing_stat = os.stat(self.file_path)
        except FileNotFoundError:
            existing_stat = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=prefix,
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(value, temporary_file, indent=2, ensure_ascii=False)
                if existing_stat is not None:
                    self._preserve_existing_access(temporary_path, existing_stat)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, self.file_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _preserve_existing_access(temporary_path: str, existing_stat):
        """Preserve an existing POSIX owner, group, and mode before replace."""
        if _IS_POSIX:
            # Replacing with a new inode must retain the access contract of an
            # existing config. Let failures abort before os.replace so the old
            # file remains available to all of its current readers.
            mode = stat.S_IMODE(existing_stat.st_mode)
            try:
                os.chown(temporary_path, existing_stat.st_uid, existing_stat.st_gid)
            except OSError as ownership_error:
                writer_uid = Config._root_owner_fallback_uid(
                    existing_stat,
                    mode,
                    ownership_error,
                )
                if writer_uid is None:
                    raise PermissionError(
                        "Cannot preserve existing config owner/group; update aborted"
                    ) from ownership_error
                try:
                    os.chown(temporary_path, writer_uid, existing_stat.st_gid)
                except OSError as group_error:
                    raise PermissionError(
                        "Cannot preserve existing config group ownership; update aborted"
                    ) from group_error

            try:
                os.chmod(temporary_path, mode)
            except OSError as mode_error:
                raise PermissionError(
                    "Cannot preserve existing config mode; update aborted"
                ) from mode_error

    @staticmethod
    def _root_owner_fallback_uid(existing_stat, mode: int, ownership_error):
        """Allow only the reviewed root-owned 0660 shared-group fallback."""
        if (
            existing_stat.st_uid != 0
            or mode != 0o660
            or getattr(ownership_error, "errno", None) != errno.EPERM
        ):
            return None

        writer_uid = os.geteuid()
        if writer_uid == 0:
            return None

        try:
            writer_groups = set(os.getgroups())
            writer_groups.add(os.getegid())
        except OSError:
            return None
        if existing_stat.st_gid not in writer_groups:
            return None
        return writer_uid

    def _deep_update(self, source: dict, overrides: dict):
        """Recursively merge overrides while copying caller-owned values."""
        for key, value in overrides.items():
            existing_value = source.get(key)
            if isinstance(value, dict) and isinstance(existing_value, dict):
                self._deep_update(existing_value, value)
            else:
                source[key] = copy.deepcopy(value)

    def _snapshot_unlocked(self) -> dict:
        """Read one snapshot while the caller holds the sidecar lock."""
        try:
            current_config = self._read_file()
        except FileNotFoundError:
            current_config = self._read_or_create_locked()

        self._config = copy.deepcopy(current_config)
        return copy.deepcopy(current_config)

    def snapshot(self) -> dict:
        """Return one coherent safe snapshot of the current JSON file."""
        if _IS_POSIX:
            try:
                current_config = self._read_file()
            except FileNotFoundError:
                # Recheck under the writer lock so bootstrap cannot race a
                # concurrent update that is publishing the first file.
                with exclusive_file_lock(self.file_path):
                    return self._snapshot_unlocked()

            self._config = copy.deepcopy(current_config)
            return copy.deepcopy(current_config)

        # Windows readers hold the sidecar lock because open file handles can
        # prevent another process from replacing the target.
        with exclusive_file_lock(self.file_path):
            return self._snapshot_unlocked()

    def get(self, key_path: str, default=None):
        """Retrieve a dot-path value from the latest safe disk snapshot."""
        keys = key_path.split(".")
        value = self.snapshot()
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def update(self, new_config: dict):
        """Deep-merge updates under a sidecar lock and atomically publish JSON.

        Omitted keys retain their current values, including credentials. An
        explicitly supplied empty string replaces a stored value and clears it.
        """
        overrides = copy.deepcopy(new_config)
        with exclusive_file_lock(self.file_path):
            try:
                current_config = self._read_file()
            except FileNotFoundError:
                # Publish the merged result directly so readers cannot observe
                # an intermediate blank bootstrap during this update.
                current_config = self._default_config()

            merged_config = copy.deepcopy(current_config)
            self._deep_update(merged_config, overrides)
            self._atomic_write(merged_config)
            self._config = merged_config


# Keep the accepted Task 1 environment override ahead of the default path.
config = Config(os.environ.get("ASM_CONFIG_PATH", "../../data/config.json"))
