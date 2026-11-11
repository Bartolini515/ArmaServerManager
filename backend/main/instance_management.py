"""Application services for safe instance-file and single-object mutations."""

from __future__ import annotations

import os
import logging
import re
import stat
import uuid
import tempfile
from django.core.files import File
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import DatabaseError, models, transaction

from .instance_files import (
    CleanupPending,
    ManagedFileError,
    ManagedFileJournal,
    ManagedFileRoot,
    UnsafeManagedPathError,
)
from .instance_lifecycle import (
    is_instance_lifecycle_busy,
    lifecycle_transaction,
    require_lifecycle_autocommit,
)
from .models import Instances, Missions, Ports, Profile, PreparedPreset
from .modpreset.preset_extraction import preset_parser
from .modpreset.start_files import generate_server_config, generate_sh_file


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_INSTANCE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_logger = logging.getLogger(__name__)


class InstanceManagementError(RuntimeError):
    """Base error with a static diagnostic safe for API mapping."""

    code = "instance_operation_failed"
    message = "Instance file operation failed."
    status_code = 500

    def __init__(self):
        super().__init__(self.message)


class InstanceNotFoundError(InstanceManagementError):
    code = "instance_not_found"
    message = "Instance was not found."
    status_code = 404


class ProfileNotFoundError(InstanceManagementError):
    code = "profile_not_found"
    message = "Profile was not found."
    status_code = 404


class InstanceBusyError(InstanceManagementError):
    code = "instance_busy"
    message = "The instance has an active lifecycle operation."
    status_code = 400


class ProfileBusyError(InstanceManagementError):
    code = "profile_instances_busy"
    message = "The profile has an instance with an active lifecycle operation."
    status_code = 400


class InstanceLimitExceeded(InstanceManagementError):
    code = "instance_limit_exceeded"
    message = "The instance limit has been reached."
    status_code = 403


class NoAvailablePortError(InstanceManagementError):
    code = "no_available_port"
    message = "No instance port is available."
    status_code = 403


class MissingPresetError(InstanceManagementError):
    code = "preset_required"
    message = "An instance preset is required."
    status_code = 400


class MissingLogFileError(InstanceManagementError):
    code = "log_file_missing"
    message = "The instance has no log file."
    status_code = 404


class ManagedFileMissingError(ManagedFileError):
    code = "managed_file_missing"
    message = "The managed file does not exist."


class InstanceOperationError(InstanceManagementError):
    """An internal operation failed without a safe lower-level classification."""


@dataclass(frozen=True)
class MutationResult:
    instance: Instances | None = None
    cleanup_pending: CleanupPending | None = None
    delete_result: tuple[int, dict[str, int]] = (0, {})


def validate_instance_preset(
    instance: Instances,
    config_snapshot: Mapping[str, Any],
) -> str:
    """Return a verified local path to the instance's stored preset."""
    del config_snapshot
    media_root = _media_root()
    relative_name = _preset_relative_name(instance)
    target = _filefield_managed_path("preset", media_root, relative_name)
    if not _validate_existing_file(media_root, relative_name, allow_missing=False):
        raise ManagedFileMissingError()
    return str(target)


def validate_instance_start_file(
    instance: Instances,
    config_snapshot: Mapping[str, Any],
) -> str:
    """Return the expected generated start script after verifying its stored path."""
    arma_root = _arma_root(config_snapshot)
    _assert_real_directory(arma_root)
    _validate_instance_name(instance.name)
    expected_name = f"start{instance.name}.sh"
    relative_name = _absolute_managed_relative_name(
        instance.start_file_path,
        arma_root,
        expected_name,
    )
    if not _validate_existing_file(arma_root, relative_name, allow_missing=False):
        raise ManagedFileMissingError()
    _validate_executable_root(arma_root)
    return str(_path_from_relative_name(arma_root, relative_name))


def validate_instance_log_file(
    instance: Instances,
    *,
    require_exists: bool = False,
) -> str | None:
    """Validate an optional log location without requiring any private stage root."""
    if not instance.log_file or not instance.log_file.name:
        return None
    media_root = _media_root()
    relative_name = _log_relative_name(instance)
    target = _filefield_managed_path(
        "log_file",
        media_root,
        relative_name,
        instance=instance,
    )
    _validate_existing_file(
        media_root,
        relative_name,
        allow_missing=not require_exists,
    )
    return str(target)


def preflight_managed_root_layout(config_snapshot: Mapping[str, Any]) -> None:
    """Reject private-root overlap before callers open eager operation loggers.

    This read-only check enumerates configured and served roots but deliberately
    does not require the administrator-provisioned private directories to exist.
    The mutation boundary repeats the checks and validates those directories.
    """
    media_root = _media_root()
    arma_root = _arma_root(config_snapshot)
    public_roots = _public_roots(media_root, arma_root, config_snapshot)
    _assert_private_root_exclusions(_private_sibling(media_root), public_roots)
    _assert_private_root_exclusions(_private_sibling(arma_root), public_roots)


def prepare_instance_log_directory(instance: Instances) -> None:
    """Create only the validated per-user log parent needed for first-start output."""
    media_root = _media_root()
    expected_name = _generated_log_relative_name(instance)
    _filefield_managed_path(
        "log_file",
        media_root,
        expected_name,
        instance=instance,
    )
    _validate_relative_name(expected_name)
    _ensure_managed_parent(media_root, expected_name)


@contextmanager
def staged_first_instance_log(
    instance: Instances,
    config_snapshot: Mapping[str, Any],
    content_file,
):
    """Stage a new start log inside the caller's lifecycle/database transaction."""
    media_root = _media_root()
    relative_name = _generated_log_relative_name(instance)
    target_path = _filefield_managed_path(
        "log_file",
        media_root,
        relative_name,
        instance=instance,
    )
    if _validate_existing_file(media_root, relative_name, allow_missing=True):
        raise UnsafeManagedPathError()
    if _is_referenced_elsewhere(
        target_path,
        excluded_profile_field=None,
        excluded_instance_field=None,
    ):
        raise UnsafeManagedPathError()

    roots = _managed_roots(
        config_snapshot,
        media_prefixes=(f"user_{instance.user_id}/",),
        arma_names=(),
    )
    journal = ManagedFileJournal(roots)
    with journal:
        _ensure_managed_parent(media_root, relative_name)

        def write_log(stage_path: Path) -> None:
            with open(stage_path, "wb") as staged_file:
                for chunk in content_file.chunks():
                    staged_file.write(chunk)

        journal.stage("media", relative_name, write_log)
        yield journal, relative_name, str(target_path)


def open_validated_file(path: str | os.PathLike[str]) -> BinaryIO:
    """Open a previously validated regular file without following its final symlink."""
    file_path = _absolute_path(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(file_path, flags)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise UnsafeManagedPathError()
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        return stream
    except FileNotFoundError:
        raise ManagedFileMissingError() from None
    except ManagedFileError:
        raise
    except OSError:
        raise ManagedFileError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def create_instance(
    *,
    owner: Profile,
    validated_data: Mapping[str, Any],
    config_snapshot: Mapping[str, Any],
    log_callback: Callable[[str], object] | None = None,
) -> MutationResult:
    """Create an instance and publish its preset and generated files together."""
    preset_upload = validated_data.get("preset")
    name = validated_data.get("name")
    if preset_upload is None:
        raise MissingPresetError()
    _validate_instance_name(name)
    _validate_upload_leaf(getattr(preset_upload, "name", None))
    try:
        require_lifecycle_autocommit()
        with ExitStack() as stack:
            with lifecycle_transaction():
                locked_owner = Profile.objects.select_for_update().filter(pk=owner.pk).first()
                if locked_owner is None:
                    raise ProfileNotFoundError()
                if not locked_owner.is_staff and locked_owner.instances.count() >= 5:
                    raise InstanceLimitExceeded()
                if Instances.objects.filter(name=name).exists():
                    raise InstanceOperationError()

                port = Ports.objects.select_for_update().filter(is_available=True).order_by(
                    "port_number"
                ).first()
                if port is None:
                    raise NoAvailablePortError()

                media_root = _media_root()
                arma_root = _arma_root(config_snapshot)
                _assert_real_directory(arma_root)
                preset_name = _available_preset_name(locked_owner, preset_upload, media_root)
                start_name = f"start{name}.sh"
                config_name = f"server_{locked_owner.username}.cfg"
                start_target = _path_from_relative_name(arma_root, start_name)
                config_target = _path_from_relative_name(arma_root, config_name)

                if _path_exists_unsafe(start_target):
                    raise UnsafeManagedPathError()
                if _is_referenced_elsewhere(
                    start_target,
                    excluded_profile_field=None,
                    excluded_instance_field=None,
                ):
                    raise UnsafeManagedPathError()
                if locked_owner.server_config:
                    existing_config_name = _profile_config_relative_name(
                        locked_owner,
                        arma_root,
                    )
                    if existing_config_name != config_name or not _validate_existing_file(
                        arma_root,
                        existing_config_name,
                        allow_missing=False,
                    ):
                        raise UnsafeManagedPathError()
                    has_new_config = False
                else:
                    if _path_exists_unsafe(config_target) or _is_referenced_elsewhere(
                        config_target,
                        excluded_profile_field=None,
                        excluded_instance_field=None,
                    ):
                        raise UnsafeManagedPathError()
                    has_new_config = True

                media_prefix = f"user_{locked_owner.pk}/"
                arma_names = [start_name]
                if has_new_config:
                    arma_names.append(config_name)
                _validate_model_path_length(
                    str(start_target),
                    Instances._meta.get_field("start_file_path").max_length,
                )
                if has_new_config:
                    _validate_model_path_length(
                        str(config_target),
                        Profile._meta.get_field("server_config").max_length,
                    )
                roots = _managed_roots(
                    config_snapshot,
                    media_prefixes=(media_prefix,),
                    arma_names=tuple(arma_names),
                )
                journal = stack.enter_context(ManagedFileJournal(roots))
                _ensure_managed_parent(media_root, preset_name)

                def write_upload(stage_path: Path) -> None:
                    with open(stage_path, "wb") as staged_file:
                        for chunk in preset_upload.chunks():
                            staged_file.write(chunk)

                preset_stage = journal.stage("media", preset_name, write_upload)
                mod_paths = preset_parser(
                    str(preset_stage),
                    log_callback=log_callback,
                )

                if has_new_config:
                    config_stage = journal.stage(
                        "arma",
                        config_name,
                        lambda stage_path: generate_server_config(
                            locked_owner.username,
                            "test",
                            str(arma_root),
                            log_callback=log_callback,
                            output_path=str(stage_path),
                        ),
                    )
                    new_server_config = str(config_target)
                else:
                    config_stage = None
                    new_server_config = locked_owner.server_config

                script_stage = journal.stage(
                    "arma",
                    start_name,
                    lambda stage_path: generate_sh_file(
                        name,
                        port.port_number,
                        locked_owner.username,
                        mod_paths,
                        str(_snapshot_path(config_snapshot, "mods_directory")),
                        str(arma_root),
                        log_callback=log_callback,
                        output_path=str(stage_path),
                    ),
                )
                journal.publish()

                instance = Instances(
                    name=name,
                    user=locked_owner,
                    preset=preset_name,
                    port=port,
                    start_file_path=str(start_target),
                )
                instance.save()
                if has_new_config:
                    locked_owner.server_config = new_server_config
                    locked_owner.save(update_fields=["server_config"])
                port.is_available = False
                port.save(update_fields=["is_available"])
                transaction.on_commit(journal.mark_committed)

            cleanup_pending = journal.finalize()
        _warn_cleanup_pending(cleanup_pending)
        return MutationResult(instance=instance, cleanup_pending=cleanup_pending)
    except (ManagedFileError, InstanceManagementError):
        raise
    except (DatabaseError, OSError, ValueError):
        raise InstanceOperationError() from None
    except Exception:
        raise InstanceOperationError() from None


def replace_admin_instance_preset(
    *,
    instance_id: int,
    validated_data: Mapping[str, Any],
    config_snapshot: Mapping[str, Any],
    log_callback: Callable[[str], object] | None = None,
) -> MutationResult:
    """Replace a stopped administrative preset and generated script atomically."""
    preset_upload = validated_data.get("preset")
    if preset_upload is None:
        raise MissingPresetError()
    _validate_upload_leaf(getattr(preset_upload, "name", None))
    try:
        require_lifecycle_autocommit()
        with ExitStack() as stack:
            with lifecycle_transaction():
                instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
                if instance is None:
                    raise InstanceNotFoundError()
                owner = Profile.objects.select_for_update().filter(pk=instance.user_id).first()
                if owner is None:
                    raise UnsafeManagedPathError()
                _lock_instance_port(instance)
                if not instance.is_admin_instance:
                    raise InstanceManagementError()
                if is_instance_lifecycle_busy(instance):
                    raise InstanceBusyError()

                media_root = _media_root()
                arma_root = _arma_root(config_snapshot)
                _assert_real_directory(arma_root)
                old_preset_name = _preset_relative_name(instance)
                old_preset_exists = _validate_existing_file(
                    media_root,
                    old_preset_name,
                    allow_missing=True,
                )
                old_script_name = f"start{instance.name}.sh"
                if instance.start_file_path:
                    stored_script_name = _absolute_managed_relative_name(
                        instance.start_file_path,
                        arma_root,
                        old_script_name,
                    )
                    old_script_is_owned = True
                else:
                    stored_script_name = old_script_name
                    old_script_is_owned = False
                old_script_exists = _validate_existing_file(
                    arma_root,
                    stored_script_name,
                    allow_missing=True,
                )
                if old_script_exists and not old_script_is_owned:
                    raise UnsafeManagedPathError()
                old_preset_path = _path_from_relative_name(media_root, old_preset_name)
                old_script_path = _path_from_relative_name(arma_root, stored_script_name)
                _assert_not_shared_reference(
                    old_preset_path,
                    excluded_instance_field=(instance.pk, "preset"),
                )
                _assert_not_shared_reference(
                    old_script_path,
                    excluded_instance_field=(instance.pk, "start_file_path"),
                )

                new_preset_name = _available_preset_name(owner, preset_upload, media_root)
                new_preset_path = _path_from_relative_name(media_root, new_preset_name)
                if _is_referenced_elsewhere(
                    new_preset_path,
                    excluded_profile_field=None,
                    excluded_instance_field=None,
                ):
                    raise UnsafeManagedPathError()
                media_prefix = f"user_{owner.pk}/"
                roots = _managed_roots(
                    config_snapshot,
                    media_prefixes=(media_prefix,),
                    arma_names=(old_script_name,),
                )
                _validate_model_path_length(
                    str(old_script_path),
                    Instances._meta.get_field("start_file_path").max_length,
                )
                journal = stack.enter_context(ManagedFileJournal(roots))
                _ensure_managed_parent(media_root, new_preset_name)

                def write_upload(stage_path: Path) -> None:
                    with open(stage_path, "wb") as staged_file:
                        for chunk in preset_upload.chunks():
                            staged_file.write(chunk)

                preset_stage = journal.stage("media", new_preset_name, write_upload)
                mod_paths = preset_parser(
                    str(preset_stage),
                    log_callback=log_callback,
                )
                script_stage = journal.stage(
                    "arma",
                    old_script_name,
                    lambda stage_path: generate_sh_file(
                        instance.name,
                        instance.port.port_number,
                        owner.username,
                        mod_paths,
                        str(_snapshot_path(config_snapshot, "mods_directory")),
                        str(arma_root),
                        log_callback=log_callback,
                        is_admin_instance=True,
                        output_path=str(stage_path),
                    ),
                    replace=old_script_exists,
                )
                if old_preset_exists:
                    journal.remove("media", old_preset_name)
                journal.publish()

                instance.preset = new_preset_name
                instance.start_file_path = str(old_script_path)
                instance.is_ready = False
                instance.save(update_fields=["preset", "start_file_path", "is_ready"])
                transaction.on_commit(journal.mark_committed)

            cleanup_pending = journal.finalize()
        _warn_cleanup_pending(cleanup_pending)
        return MutationResult(instance=instance, cleanup_pending=cleanup_pending)
    except (ManagedFileError, InstanceManagementError):
        raise
    except (DatabaseError, OSError, ValueError):
        raise InstanceOperationError() from None
    except Exception:
        raise InstanceOperationError() from None


def delete_instance(
    *,
    instance_id: int,
    config_snapshot: Mapping[str, Any],
    using: str | None = None,
    keep_parents: bool = False,
) -> MutationResult:
    """Delete one stopped instance, its proven-owned files, and release its port."""
    try:
        require_lifecycle_autocommit()
        with ExitStack() as stack:
            with lifecycle_transaction():
                instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
                if instance is None:
                    raise InstanceNotFoundError()
                owner = Profile.objects.select_for_update().filter(pk=instance.user_id).first()
                if owner is None:
                    raise UnsafeManagedPathError()
                port = _lock_instance_port(instance)
                if is_instance_lifecycle_busy(instance):
                    raise InstanceBusyError()

                media_root = _media_root()
                arma_root = _arma_root(config_snapshot)
                file_plan = _validate_instance_files_for_deletion(
                    instance,
                    media_root,
                    arma_root,
                )
                media_prefixes = (f"user_{owner.pk}/",) if any(
                    root_key == "media" for root_key, _name in file_plan
                ) else ()
                arma_names = tuple(
                    name for root_key, name in file_plan if root_key == "arma"
                )
                journal = None
                if file_plan:
                    roots = _managed_roots(
                        config_snapshot,
                        media_prefixes=media_prefixes,
                        arma_names=arma_names,
                    )
                    journal = stack.enter_context(ManagedFileJournal(roots))
                    for root_key, relative_name in file_plan:
                        journal.remove(root_key, relative_name)
                    journal.publish()

                port.is_available = True
                port.save(update_fields=["is_available"])
                delete_result = models.Model.delete(
                    instance,
                    using=using,
                    keep_parents=keep_parents,
                )
                if journal is not None:
                    transaction.on_commit(journal.mark_committed)

            cleanup_pending = journal.finalize() if journal is not None else None
        _warn_cleanup_pending(cleanup_pending)
        return MutationResult(cleanup_pending=cleanup_pending, delete_result=delete_result)
    except (ManagedFileError, InstanceManagementError):
        raise
    except (DatabaseError, OSError, ValueError):
        raise InstanceOperationError() from None
    except Exception:
        raise InstanceOperationError() from None


def delete_profile(
    *,
    profile_id: int,
    config_snapshot: Mapping[str, Any],
    using: str | None = None,
    keep_parents: bool = False,
) -> MutationResult:
    """Delete a profile only after every owned instance passes stopped-state and path checks."""
    try:
        require_lifecycle_autocommit()
        with ExitStack() as stack:
            with lifecycle_transaction():
                owner = Profile.objects.select_for_update().filter(pk=profile_id).first()
                if owner is None:
                    raise ProfileNotFoundError()
                instances = list(
                    Instances.objects.select_for_update()
                    .filter(user_id=owner.pk)
                    .order_by("pk")
                )
                if any(is_instance_lifecycle_busy(instance) for instance in instances):
                    raise ProfileBusyError()

                ports = list(
                    Ports.objects.select_for_update()
                    .filter(pk__in=[instance.port_id for instance in instances])
                    .order_by("pk")
                )
                ports_by_id = {port.pk: port for port in ports}
                if any(instance.port_id not in ports_by_id for instance in instances):
                    raise UnsafeManagedPathError()

                media_root = _media_root()
                arma_root = _arma_root(config_snapshot)
                file_plan: list[tuple[str, str]] = []
                for instance in instances:
                    file_plan.extend(
                        _validate_instance_files_for_deletion(
                            instance,
                            media_root,
                            arma_root,
                        )
                    )

                config_to_remove: str | None = None
                if owner.server_config:
                    config_name = _profile_config_relative_name(owner, arma_root)
                    config_exists = _validate_existing_file(
                        arma_root,
                        config_name,
                        allow_missing=True,
                    )
                    config_path = _path_from_relative_name(arma_root, config_name)
                    if config_exists and not _is_referenced_elsewhere(
                        config_path,
                        excluded_profile_field=(owner.pk, "server_config"),
                        excluded_instance_field=None,
                    ):
                        config_to_remove = config_name

                media_prefixes = (f"user_{owner.pk}/",) if any(
                    root_key == "media" for root_key, _name in file_plan
                ) else ()
                arma_names = [name for root_key, name in file_plan if root_key == "arma"]
                if config_to_remove is not None:
                    arma_names.append(config_to_remove)
                    file_plan.append(("arma", config_to_remove))
                journal = None
                if file_plan:
                    roots = _managed_roots(
                        config_snapshot,
                        media_prefixes=media_prefixes,
                        arma_names=tuple(dict.fromkeys(arma_names)),
                    )
                    journal = stack.enter_context(ManagedFileJournal(roots))
                    for root_key, relative_name in file_plan:
                        journal.remove(root_key, relative_name)
                    journal.publish()

                for port in ports:
                    port.is_available = True
                    port.save(update_fields=["is_available"])
                delete_result = models.Model.delete(
                    owner,
                    using=using,
                    keep_parents=keep_parents,
                )
                if journal is not None:
                    transaction.on_commit(journal.mark_committed)

            cleanup_pending = journal.finalize() if journal is not None else None
        _warn_cleanup_pending(cleanup_pending)
        return MutationResult(cleanup_pending=cleanup_pending, delete_result=delete_result)
    except (ManagedFileError, InstanceManagementError):
        raise
    except (DatabaseError, OSError, ValueError):
        raise InstanceOperationError() from None
    except Exception:
        raise InstanceOperationError() from None


def delete_instance_logs(
    *,
    instance_id: int,
    config_snapshot: Mapping[str, Any],
) -> MutationResult:
    """Remove a stopped instance log and clear its metadata in the same journal boundary."""
    try:
        require_lifecycle_autocommit()
        with ExitStack() as stack:
            with lifecycle_transaction():
                instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
                if instance is None:
                    raise InstanceNotFoundError()
                owner = Profile.objects.select_for_update().filter(pk=instance.user_id).first()
                if owner is None:
                    raise UnsafeManagedPathError()
                _lock_instance_port(instance)
                if is_instance_lifecycle_busy(instance):
                    raise InstanceBusyError()
                if not instance.log_file or not instance.log_file.name:
                    raise MissingLogFileError()

                media_root = _media_root()
                arma_root = _arma_root(config_snapshot)
                relative_name = _log_relative_name(instance)
                _filefield_managed_path(
                    "log_file",
                    media_root,
                    relative_name,
                    instance=instance,
                )
                exists = _validate_existing_file(
                    media_root,
                    relative_name,
                    allow_missing=True,
                )
                target_path = _path_from_relative_name(media_root, relative_name)
                _assert_not_shared_reference(
                    target_path,
                    excluded_instance_field=(instance.pk, "log_file"),
                )
                journal = None
                if exists:
                    roots = _managed_roots(
                        config_snapshot,
                        media_prefixes=(f"user_{owner.pk}/",),
                        arma_names=(),
                    )
                    journal = stack.enter_context(ManagedFileJournal(roots))
                    journal.remove("media", relative_name)
                    journal.publish()

                instance.log_file = None
                instance.save(update_fields=["log_file"])
                if journal is not None:
                    transaction.on_commit(journal.mark_committed)

            cleanup_pending = journal.finalize() if journal is not None else None
        _warn_cleanup_pending(cleanup_pending)
        return MutationResult(instance=instance, cleanup_pending=cleanup_pending)
    except (ManagedFileError, InstanceManagementError):
        raise
    except (DatabaseError, OSError, ValueError):
        raise InstanceOperationError() from None
    except Exception:
        raise InstanceOperationError() from None


def _prepared_file_path(record: PreparedPreset, *, allow_missing: bool):
    name = _validate_relative_name(record.preset_file.name)
    if len(name.split('/')) != 2 or not name.startswith('prepared_presets/'):
        raise UnsafeManagedPathError()
    root = _media_root()
    storage = record.preset_file.storage
    if not isinstance(storage, FileSystemStorage) or _absolute_path(storage.location) != root:
        raise UnsafeManagedPathError()
    exists = _validate_existing_file(root, name, allow_missing=allow_missing)
    return root, name, exists


def create_prepared_preset(*, name, upload, config_snapshot):
    filename = _validate_upload_leaf(upload.name)
    require_lifecycle_autocommit()
    with ExitStack() as stack:
        with lifecycle_transaction():
            root = _media_root()
            roots = _managed_roots(config_snapshot, media_prefixes=('prepared_presets/',), arma_names=())
            _ensure_managed_parent(root, 'prepared_presets/' + filename)
            field = PreparedPreset._meta.get_field('preset_file')
            candidate = field.storage.get_available_name('prepared_presets/' + filename, max_length=field.max_length).replace('\\', '/')
            candidate = _validate_relative_name(candidate)
            if len(candidate.split('/')) != 2 or not candidate.startswith('prepared_presets/'):
                raise UnsafeManagedPathError()
            if PreparedPreset.objects.filter(preset_file=candidate).exists():
                raise ManagedFileError()
            journal = stack.enter_context(ManagedFileJournal(roots))
            def write(stage):
                with open(stage, 'wb') as destination:
                    for chunk in upload.chunks():
                        destination.write(chunk)
            journal.stage('media', candidate, write)
            journal.publish()
            record = PreparedPreset.objects.create(name=name, name_key=name.casefold(), preset_file=candidate)
            transaction.on_commit(journal.mark_committed)
        pending = journal.finalize()
        return record, pending


def delete_prepared_preset(*, preset_id, config_snapshot):
    require_lifecycle_autocommit()
    journal = None
    with ExitStack() as stack:
        with lifecycle_transaction():
            record = PreparedPreset.objects.select_for_update().filter(pk=preset_id).first()
            if record is None:
                raise InstanceNotFoundError()
            _, name, exists = _prepared_file_path(record, allow_missing=True)
            if exists:
                roots = _managed_roots(config_snapshot, media_prefixes=('prepared_presets/',), arma_names=())
                journal = stack.enter_context(ManagedFileJournal(roots))
                journal.remove('media', name)
                journal.publish()
            record.delete()
            if journal is not None:
                transaction.on_commit(journal.mark_committed)
        return journal.finalize() if journal is not None else None


def copy_prepared_preset(preset_id):
    """Capture an independent temporary upload while catalog deletion is excluded."""
    require_lifecycle_autocommit()
    with lifecycle_transaction():
        record = PreparedPreset.objects.select_for_update().filter(pk=preset_id).first()
        if record is None:
            raise InstanceNotFoundError()
        root, name, _ = _prepared_file_path(record, allow_missing=False)
        temporary = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b')
        try:
            with open_validated_file(root / name) as source:
                while chunk := source.read(65536):
                    temporary.write(chunk)
            temporary.seek(0)
            return File(temporary, name=name.rsplit('/', 1)[-1])
        except BaseException:
            temporary.close()
            raise


def delete_mission(*, mission_id: int, config_snapshot: Mapping[str, Any]):
    """Remove one mission under the lifecycle mutex and managed-file journal."""
    require_lifecycle_autocommit()
    journal = None
    with ExitStack() as stack:
        with lifecycle_transaction():
            if Instances.objects.filter(is_running=True).exists():
                raise InstanceBusyError()
            mission = Missions.objects.select_for_update().filter(pk=mission_id).first()
            if mission is None:
                raise InstanceNotFoundError()
            name = _validate_relative_name(mission.mission_file.name)
            if len(name.split('/')) != 2 or not name.startswith('missions/'):
                raise UnsafeManagedPathError()
            media_root = _media_root()
            storage = mission.mission_file.storage
            if not isinstance(storage, FileSystemStorage) or _absolute_path(storage.location) != media_root:
                raise UnsafeManagedPathError()
            if Missions.objects.exclude(pk=mission.pk).filter(mission_file=name).exists():
                raise UnsafeManagedPathError()
            exists = _validate_existing_file(media_root, name, allow_missing=True)
            if exists:
                roots = _managed_roots(config_snapshot, media_prefixes=('missions/',), arma_names=())
                journal = stack.enter_context(ManagedFileJournal(roots))
                journal.remove('media', name)
                journal.publish()
            Missions.objects.filter(pk=mission.pk).delete()
            if journal is not None:
                transaction.on_commit(journal.mark_committed)
        return journal.finalize() if journal is not None else None


def _managed_roots(
    config_snapshot: Mapping[str, Any],
    *,
    media_prefixes: tuple[str, ...],
    arma_names: tuple[str, ...],
) -> dict[str, ManagedFileRoot]:
    media_root = _media_root()
    arma_root = _arma_root(config_snapshot)
    public_roots = _public_roots(media_root, arma_root, config_snapshot)
    roots: dict[str, ManagedFileRoot] = {}

    if media_prefixes:
        private_root = _private_sibling(media_root)
        _assert_private_root(private_root, public_roots)
        roots["media"] = ManagedFileRoot(
            media_root,
            private_root,
            allowed_prefixes=media_prefixes,
        )
    if arma_names:
        private_root = _private_sibling(arma_root)
        _assert_private_root(private_root, public_roots)
        roots["arma"] = ManagedFileRoot(
            arma_root,
            private_root,
            allowed_names=arma_names,
        )
    if not roots:
        raise UnsafeManagedPathError()
    return roots


def _public_roots(
    media_root: Path,
    arma_root: Path,
    config_snapshot: Mapping[str, Any],
) -> tuple[Path, ...]:
    roots = [media_root, arma_root]
    media_setting = getattr(settings, "MEDIA_ROOT", None)
    if media_setting:
        roots.append(_absolute_path(media_setting))
    try:
        configured_paths = config_snapshot["paths"]
    except (KeyError, TypeError):
        raise UnsafeManagedPathError() from None
    if not isinstance(configured_paths, Mapping):
        raise UnsafeManagedPathError()
    for key in ("steamcmd", "arma3", "mods_directory", "download_directory"):
        value = configured_paths.get(key)
        if not value:
            continue
        roots.append(_snapshot_path(config_snapshot, key))
    log_directory = configured_paths.get("logs_directory", "/logs")
    roots.append(_absolute_path(log_directory))

    for model, field_name in (
        (Instances, "preset"),
        (Instances, "log_file"),
        (Missions, "mission_file"),
        (PreparedPreset, "preset_file"),
    ):
        try:
            storage = model._meta.get_field(field_name).storage
        except Exception:
            raise UnsafeManagedPathError() from None
        if not isinstance(storage, FileSystemStorage):
            raise UnsafeManagedPathError()
        try:
            roots.append(_absolute_path(storage.location))
        except Exception:
            raise UnsafeManagedPathError() from None

    static_values = [getattr(settings, "STATIC_ROOT", None)]
    try:
        static_values.extend(getattr(settings, "STATICFILES_DIRS", ()))
    except TypeError:
        raise UnsafeManagedPathError() from None
    for value in static_values:
        if not value:
            continue
        if isinstance(value, tuple) and len(value) == 2:
            value = value[1]
        roots.append(_absolute_path(value))
    return tuple(roots)


def _private_sibling(trusted_root: Path) -> Path:
    return trusted_root.parent / ".asm-private"


def _assert_private_root(private_root: Path, public_roots: tuple[Path, ...]) -> None:
    _assert_real_directory(private_root)
    _assert_private_root_exclusions(private_root, public_roots)


def _assert_private_root_exclusions(
    private_root: Path,
    public_roots: tuple[Path, ...],
) -> None:
    for public_root in public_roots:
        if _paths_overlap(private_root, public_root):
            raise UnsafeManagedPathError()


def _paths_overlap(first: Path, second: Path) -> bool:
    first_key = _path_key(first)
    second_key = _path_key(second)
    try:
        common = os.path.commonpath((first_key, second_key))
    except ValueError:
        return False
    return common == first_key or common == second_key


def _media_root() -> Path:
    storage = Instances._meta.get_field("preset").storage
    if not isinstance(storage, FileSystemStorage):
        raise UnsafeManagedPathError()
    try:
        root = _absolute_path(storage.location)
    except (AttributeError, TypeError, ValueError):
        raise UnsafeManagedPathError() from None
    _assert_real_directory(root)
    return root


def _filefield_managed_path(
    field_name: str,
    media_root: Path,
    relative_name: str,
    *,
    instance: Instances | None = None,
) -> Path:
    """Require local field storage to resolve exactly inside the trusted media root."""
    try:
        storage = Instances._meta.get_field(field_name).storage
    except Exception:
        raise UnsafeManagedPathError() from None
    expected_path = _path_from_relative_name(media_root, relative_name)
    storages = [storage]
    if instance is not None:
        try:
            file_storage = getattr(instance, field_name).storage
        except Exception:
            raise UnsafeManagedPathError() from None
        storages.append(file_storage)
    for selected_storage in storages:
        if not isinstance(selected_storage, FileSystemStorage):
            raise UnsafeManagedPathError()
        try:
            resolved_path = _absolute_path(selected_storage.path(relative_name))
        except Exception:
            raise UnsafeManagedPathError() from None
        if _path_key(resolved_path) != _path_key(expected_path):
            raise UnsafeManagedPathError()
    return expected_path


def _warn_cleanup_pending(cleanup_pending: CleanupPending | None) -> None:
    if cleanup_pending is None:
        return
    try:
        _logger.warning(
            "Managed-file cleanup is pending (%s; %d private files).",
            cleanup_pending.reason,
            cleanup_pending.pending_count,
        )
    except Exception:
        pass


def _arma_root(config_snapshot: Mapping[str, Any]) -> Path:
    return _snapshot_path(config_snapshot, "arma3")


def _snapshot_path(config_snapshot: Mapping[str, Any], key: str) -> Path:
    try:
        raw_value = config_snapshot["paths"][key]
    except (KeyError, TypeError):
        raise UnsafeManagedPathError() from None
    if not isinstance(raw_value, (str, os.PathLike)) or not os.fspath(raw_value):
        raise UnsafeManagedPathError()
    return _absolute_path(raw_value)


def _assert_real_directory(path: Path) -> None:
    path = _absolute_path(path)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        info = _lstat(current)
        if info is None or _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeManagedPathError()


def _validate_executable_root(arma_root: Path) -> None:
    if not _validate_existing_file(arma_root, "arma3server_x64", allow_missing=False):
        raise ManagedFileMissingError()


def _validate_instance_name(name: Any) -> None:
    if (
        not isinstance(name, str)
        or len(name) < 3
        or not _SAFE_INSTANCE_NAME.fullmatch(name)
        or name in (".", "..")
    ):
        raise UnsafeManagedPathError()


def _validate_model_path_length(value: str, max_length: int) -> None:
    if not isinstance(value, str) or len(value) > max_length:
        raise UnsafeManagedPathError()


def _validate_upload_leaf(filename: Any) -> str:
    if (
        not isinstance(filename, str)
        or len(filename) < 3
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or not _SAFE_COMPONENT.fullmatch(filename)
        or filename in (".", "..")
    ):
        raise UnsafeManagedPathError()
    return filename


def _validate_relative_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or ":" in value
    ):
        raise UnsafeManagedPathError()
    parts = value.split("/")
    if any(
        not part
        or part in (".", "..")
        or not _SAFE_COMPONENT.fullmatch(part)
        for part in parts
    ):
        raise UnsafeManagedPathError()
    return value


def _preset_relative_name(instance: Instances) -> str:
    try:
        relative_name = _validate_relative_name(instance.preset.name)
    except (AttributeError, TypeError):
        raise UnsafeManagedPathError() from None
    prefix = f"user_{instance.user_id}/presets/"
    if not relative_name.startswith(prefix) or "/" in relative_name[len(prefix) :]:
        raise UnsafeManagedPathError()
    return relative_name


def _log_relative_name(instance: Instances) -> str:
    try:
        relative_name = _validate_relative_name(instance.log_file.name)
    except (AttributeError, TypeError):
        raise UnsafeManagedPathError() from None
    prefix = f"user_{instance.user_id}/logs/instance_{instance.pk}_"
    if not relative_name.startswith(prefix) or "/" in relative_name[len(prefix) :]:
        raise UnsafeManagedPathError()
    return relative_name


def _generated_log_relative_name(instance: Instances) -> str:
    try:
        relative_name = f"user_{instance.user_id}/logs/instance_{instance.pk}_log_file.txt"
    except (AttributeError, TypeError):
        raise UnsafeManagedPathError() from None
    return _validate_relative_name(relative_name)


def _profile_config_relative_name(owner: Profile, arma_root: Path) -> str:
    expected_name = f"server_{owner.username}.cfg"
    _validate_relative_name(expected_name)
    if not owner.server_config:
        raise UnsafeManagedPathError()
    relative_name = _absolute_managed_relative_name(
        owner.server_config,
        arma_root,
        expected_name,
    )
    return relative_name


def _absolute_managed_relative_name(
    value: Any,
    trusted_root: Path,
    expected_name: str,
) -> str:
    if not isinstance(value, (str, os.PathLike)) or not os.fspath(value):
        raise UnsafeManagedPathError()
    candidate = _absolute_path(value)
    expected = _path_from_relative_name(trusted_root, expected_name)
    candidate_key = os.path.normcase(os.path.abspath(os.fspath(candidate)))
    expected_key = os.path.normcase(os.path.abspath(os.fspath(expected)))
    if candidate_key != expected_key:
        raise UnsafeManagedPathError()
    return _validate_relative_name(expected_name)


def _path_from_relative_name(root: Path, relative_name: str) -> Path:
    relative_name = _validate_relative_name(relative_name)
    return root.joinpath(*relative_name.split("/"))


def _validate_existing_file(
    trusted_root: Path,
    relative_name: str,
    *,
    allow_missing: bool,
) -> bool:
    relative_name = _validate_relative_name(relative_name)
    _assert_real_directory(trusted_root)
    current = trusted_root
    parts = relative_name.split("/")
    for component in parts[:-1]:
        current = current / component
        info = _lstat(current)
        if info is None:
            if allow_missing:
                return False
            raise ManagedFileMissingError()
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeManagedPathError()

    target = current / parts[-1]
    info = _lstat(target)
    if info is None:
        if allow_missing:
            return False
        raise ManagedFileMissingError()
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise UnsafeManagedPathError()
    return True


def _ensure_managed_parent(trusted_root: Path, relative_name: str) -> None:
    relative_name = _validate_relative_name(relative_name)
    _assert_real_directory(trusted_root)
    current = trusted_root
    for component in relative_name.split("/")[:-1]:
        current = current / component
        info = _lstat(current)
        if info is None:
            try:
                current.mkdir()
            except FileExistsError:
                info = _lstat(current)
            else:
                info = _lstat(current)
        if info is None or _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeManagedPathError()


def _path_exists_unsafe(path: Path) -> bool:
    info = _lstat(path)
    if info is None:
        return False
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise UnsafeManagedPathError()
    return True


def _absolute_path(value: Any) -> Path:
    try:
        raw_path = Path(os.fspath(value))
    except (TypeError, ValueError, OSError):
        raise UnsafeManagedPathError() from None
    if not raw_path.is_absolute() or any(part == ".." for part in raw_path.parts):
        raise UnsafeManagedPathError()
    return Path(os.path.abspath(os.fspath(raw_path)))


def _path_key(path: str | os.PathLike[str]) -> str:
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))
    except (TypeError, ValueError, OSError):
        raise UnsafeManagedPathError() from None


def _lstat(path: Path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise ManagedFileError() from None


def _is_link_or_reparse(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _available_preset_name(owner: Profile, upload, media_root: Path) -> str:
    filename = _validate_upload_leaf(getattr(upload, "name", None))
    prefix = f"user_{owner.pk}/presets/"
    initial_name = prefix + filename
    field = Instances._meta.get_field("preset")
    storage = field.storage
    if not isinstance(storage, FileSystemStorage):
        raise UnsafeManagedPathError()

    for _attempt in range(16):
        try:
            candidate = storage.get_available_name(
                initial_name,
                max_length=field.max_length,
            )
        except Exception:
            raise ManagedFileError() from None
        if not isinstance(candidate, str):
            raise UnsafeManagedPathError()
        candidate = candidate.replace("\\", "/")
        candidate = _validate_relative_name(candidate)
        if not candidate.startswith(prefix) or "/" in candidate[len(prefix) :]:
            raise UnsafeManagedPathError()
        target = _path_from_relative_name(media_root, candidate)
        if not _validate_existing_file(media_root, candidate, allow_missing=True):
            if not _is_referenced_elsewhere(
                target,
                excluded_profile_field=None,
                excluded_instance_field=None,
            ):
                return candidate
        stem, suffix = os.path.splitext(filename)
        initial_name = f"{prefix}{stem}_{uuid.uuid4().hex[:8]}{suffix}"
        if len(initial_name) > field.max_length:
            raise ManagedFileError()
    raise ManagedFileError()


def _validate_instance_files_for_deletion(
    instance: Instances,
    media_root: Path,
    arma_root: Path,
) -> list[tuple[str, str]]:
    _assert_real_directory(arma_root)
    plan: list[tuple[str, str]] = []
    if instance.preset and instance.preset.name:
        preset_name = _preset_relative_name(instance)
        preset_exists = _validate_existing_file(
            media_root,
            preset_name,
            allow_missing=True,
        )
        preset_path = _path_from_relative_name(media_root, preset_name)
        _assert_not_shared_reference(
            preset_path,
            excluded_instance_field=(instance.pk, "preset"),
        )
        if preset_exists:
            plan.append(("media", preset_name))

    if instance.log_file and instance.log_file.name:
        log_name = _log_relative_name(instance)
        log_path = _filefield_managed_path(
            "log_file",
            media_root,
            log_name,
            instance=instance,
        )
        log_exists = _validate_existing_file(
            media_root,
            log_name,
            allow_missing=True,
        )
        _assert_not_shared_reference(
            log_path,
            excluded_instance_field=(instance.pk, "log_file"),
        )
        if log_exists:
            plan.append(("media", log_name))

    if instance.start_file_path:
        _validate_instance_name(instance.name)
        start_name = f"start{instance.name}.sh"
        relative_name = _absolute_managed_relative_name(
            instance.start_file_path,
            arma_root,
            start_name,
        )
        start_exists = _validate_existing_file(
            arma_root,
            relative_name,
            allow_missing=True,
        )
        start_path = _path_from_relative_name(arma_root, relative_name)
        _assert_not_shared_reference(
            start_path,
            excluded_instance_field=(instance.pk, "start_file_path"),
        )
        if start_exists:
            plan.append(("arma", relative_name))
    return plan


def _lock_instance_port(instance: Instances) -> Ports:
    port = Ports.objects.select_for_update().filter(pk=instance.port_id).first()
    if port is None:
        raise UnsafeManagedPathError()
    return port


def _assert_not_shared_reference(
    path: Path,
    *,
    excluded_instance_field: tuple[int, str] | None = None,
) -> None:
    if _is_referenced_elsewhere(
        path,
        excluded_profile_field=None,
        excluded_instance_field=excluded_instance_field,
    ):
        raise UnsafeManagedPathError()


def _is_referenced_elsewhere(
    path: Path,
    *,
    excluded_profile_field: tuple[int, str] | None,
    excluded_instance_field: tuple[int, str] | None,
) -> bool:
    target_key = _path_key(path)
    media_root = _media_root()
    for instance_id, preset_name, log_name, start_path in Instances.objects.values_list(
        "pk",
        "preset",
        "log_file",
        "start_file_path",
    ).iterator():
        for field_name, stored_value, media_field in (
            ("preset", preset_name, True),
            ("log_file", log_name, True),
            ("start_file_path", start_path, False),
        ):
            if (
                excluded_instance_field is not None
                and (instance_id, field_name) == excluded_instance_field
            ):
                continue
            if not stored_value:
                continue
            try:
                candidate = (
                    Path(os.path.abspath(os.path.join(media_root, stored_value)))
                    if media_field
                    else Path(os.path.abspath(stored_value))
                )
                if _path_key(candidate) == target_key:
                    return True
            except (TypeError, ValueError, OSError, UnsafeManagedPathError):
                continue

    for profile_id, server_config in Profile.objects.values_list(
        "pk",
        "server_config",
    ).iterator():
        if excluded_profile_field == (profile_id, "server_config") or not server_config:
            continue
        try:
            candidate = Path(os.path.abspath(server_config))
            if _path_key(candidate) == target_key:
                return True
        except (TypeError, ValueError, OSError, UnsafeManagedPathError):
            continue
    return False
