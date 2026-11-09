"""Transactional journal for explicitly managed regular files.

The caller supplies administrator-managed roots, private same-filesystem
staging directories, and the relative names each operation may touch. The
journal does not provide database/filesystem power-loss atomicity.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_LINK_ABSENT = "absent"
_LINK_EXPECTED = "expected"
_LINK_FOREIGN = "foreign"
_LINK_UNAVAILABLE = "unavailable"


class ManagedFileError(RuntimeError):
    """Base error with a static, non-sensitive diagnostic."""

    code = "managed_file_operation_failed"
    message = "Managed file operation failed."

    def __init__(self):
        super().__init__(self.message)


class UnsafeManagedPathError(ManagedFileError):
    code = "unsafe_managed_path"
    message = "Managed file path is unsafe or outside its trusted root."


class ManagedFileStateError(ManagedFileError):
    code = "invalid_journal_state"
    message = "Managed file journal is in an invalid state."


class ManagedFileRollbackError(ManagedFileError):
    code = "rollback_uncertain"
    message = "Managed file rollback is uncertain; verified private repair evidence was retained where available."

    def __init__(
        self,
        repair_evidence: Sequence[Path],
        *,
        descriptor_uncertain: bool = False,
        unresolved_paths: Sequence[Path] = (),
        foreign_paths: Sequence[Path] = (),
    ):
        self.repair_evidence = tuple(Path(path) for path in repair_evidence)
        self.descriptor_uncertain = descriptor_uncertain
        self.unresolved_paths = tuple(Path(path) for path in unresolved_paths)
        self.foreign_paths = tuple(Path(path) for path in foreign_paths)
        super().__init__()


class ManagedFileSetupError(ManagedFileError):
    code = "stage_setup_uncertain"
    message = "Managed stage setup failed; descriptor cleanup is uncertain."

    def __init__(
        self,
        repair_evidence: Sequence[Path] = (),
        *,
        descriptor_uncertain: bool = False,
    ):
        self.repair_evidence = tuple(Path(path) for path in repair_evidence)
        self.descriptor_uncertain = descriptor_uncertain
        super().__init__()


@dataclass(frozen=True)
class CleanupPending:
    """Static summary for private files that could not be removed after commit."""

    pending: bool = True
    reason: str = "private_cleanup_failed"
    pending_count: int = 0


@dataclass(frozen=True)
class ManagedFileRoot:
    """Trusted public root and private stage directory for one filesystem."""

    trusted_root: Path
    private_root: Path
    allowed_prefixes: tuple[str, ...] = ()
    allowed_names: tuple[str, ...] = ()

    def __post_init__(self):
        trusted_root = _absolute_path(self.trusted_root)
        private_root = _absolute_path(self.private_root)
        _validate_directory(trusted_root)
        _validate_directory(private_root)
        if _same_or_descendant(private_root, trusted_root):
            raise UnsafeManagedPathError()
        if _device_id(trusted_root) != _device_id(private_root):
            raise UnsafeManagedPathError()

        allowed_names = tuple(_validate_relative_name(name) for name in self.allowed_names)
        allowed_prefixes = tuple(
            _validate_prefix(prefix) for prefix in self.allowed_prefixes
        )
        if not allowed_names and not allowed_prefixes:
            raise UnsafeManagedPathError()

        object.__setattr__(self, "trusted_root", trusted_root)
        object.__setattr__(self, "private_root", private_root)
        object.__setattr__(self, "allowed_names", allowed_names)
        object.__setattr__(self, "allowed_prefixes", allowed_prefixes)

    def allows(self, relative_name: str) -> bool:
        if relative_name in self.allowed_names:
            return True
        return any(relative_name.startswith(prefix) for prefix in self.allowed_prefixes)


@dataclass
class _Operation:
    root_key: str
    relative_name: str
    kind: str
    replace: bool
    target_path: Path
    target_parent: Path
    original_identity: tuple[int, int] | None
    stage_path: Path | None = None
    stage_identity: tuple[int, int] | None = None
    stage_fd: int | None = None
    stage_close_uncertain: bool = False
    backup_path: Path | None = None
    backup_created: bool = False
    backup_link_attempted: bool = False
    original_removed: bool = False
    new_published: bool = False
    new_link_attempted: bool = False
    restore_link_attempted: bool = False
    restore_link_created: bool = False


class ManagedFileJournal:
    """Stage, publish, and compensate changes to registered regular files."""

    def __init__(self, roots: Mapping[str, ManagedFileRoot]):
        if not roots or any(not isinstance(key, str) or not key for key in roots):
            raise UnsafeManagedPathError()
        if any(not isinstance(root, ManagedFileRoot) for root in roots.values()):
            raise UnsafeManagedPathError()

        for root in roots.values():
            for public_root in (candidate.trusted_root for candidate in roots.values()):
                if _same_or_descendant(root.private_root, public_root):
                    raise UnsafeManagedPathError()

        self._roots = dict(roots)
        self._operations: list[_Operation] = []
        self._targets: set[str] = set()
        self._state = "open"
        self._committed = False
        self._finalized = False
        self.cleanup_pending: CleanupPending | None = None

    def __enter__(self) -> "ManagedFileJournal":
        if self._state != "open":
            raise ManagedFileStateError()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if not self._committed and self._state not in ("rolled_back", "rollback_uncertain"):
            self.rollback()
        elif self._committed and not self._finalized:
            self.finalize()
        return False

    def stage(
        self,
        root_key: str,
        relative_name: str,
        writer: Callable[[Path], object],
        *,
        replace: bool = False,
    ) -> Path:
        """Register a private regular stage before invoking its first writer."""
        self._require_open()
        if not callable(writer):
            raise ManagedFileStateError()

        root, name, target, parent, original_identity = self._prepare_target(
            root_key, relative_name, allow_existing=replace
        )
        key = _normalized_path(target)
        if key in self._targets:
            raise ManagedFileStateError()
        self._targets.add(key)

        operation = _Operation(
            root_key=root_key,
            relative_name=name,
            kind="stage",
            replace=replace,
            target_path=target,
            target_parent=parent,
            original_identity=original_identity,
        )
        self._operations.append(operation)
        try:
            self._create_stage(root, operation, _stage_suffix(name))
            assert operation.stage_path is not None
            writer(operation.stage_path)
            _verify_private_file(
                root.private_root,
                operation.stage_path,
                operation.stage_identity,
            )
        except BaseException:
            self._state = "failed"
            try:
                self.rollback()
            except ManagedFileRollbackError as rollback_error:
                rollback_error.descriptor_uncertain = any(
                    candidate.stage_close_uncertain for candidate in self._operations
                )
                raise
            raise
        assert operation.stage_path is not None
        return operation.stage_path

    def remove(self, root_key: str, relative_name: str) -> None:
        """Register deletion of a caller-proven owned regular file."""
        self._require_open()
        root, name, target, parent, original_identity = self._prepare_target(
            root_key, relative_name, allow_existing=True
        )
        key = _normalized_path(target)
        if key in self._targets:
            raise ManagedFileStateError()
        self._targets.add(key)
        self._operations.append(
            _Operation(
                root_key=root_key,
                relative_name=name,
                kind="remove",
                replace=True,
                target_path=target,
                target_parent=parent,
                original_identity=original_identity,
            )
        )

    def publish(self) -> None:
        """Publish all registered operations, retaining backups until commit."""
        self._require_open()
        self._validate_operations_before_publish()
        self._state = "publishing"

        try:
            for operation in self._operations:
                root = self._root(operation.root_key)
                if operation.original_identity is not None:
                    operation.backup_path = self._new_backup_path(root)
                    _link_owned_file(
                        operation.target_path,
                        operation.backup_path,
                        operation.original_identity,
                        on_attempt=lambda: setattr(operation, "backup_link_attempted", True),
                        on_created=lambda: setattr(operation, "backup_created", True),
                    )
                    _unlink_target(
                        operation.target_path,
                        operation.target_parent,
                        operation.original_identity,
                    )
                    operation.original_removed = True

                if operation.kind == "stage":
                    assert operation.stage_path is not None
                    assert operation.stage_identity is not None
                    _link_owned_file(
                        operation.stage_path,
                        operation.target_path,
                        operation.stage_identity,
                        on_attempt=lambda: setattr(operation, "new_link_attempted", True),
                        on_created=lambda: setattr(operation, "new_published", True),
                    )
            self._state = "published"
        except BaseException as error:
            try:
                self.rollback()
            except ManagedFileRollbackError:
                raise
            if isinstance(error, ManagedFileError):
                raise
            raise ManagedFileError() from None

    def mark_committed(self) -> None:
        """Mark that the caller's metadata transaction has irreversibly committed."""
        if self._state not in ("open", "published") or self._committed:
            raise ManagedFileStateError()
        if self._state == "open" and self._operations:
            raise ManagedFileStateError()
        self._committed = True
        self._state = "committed"

    def finalize(self) -> CleanupPending | None:
        """Remove private stages/backups after commit; never roll back committed files."""
        if not self._committed or self._finalized:
            raise ManagedFileStateError()

        pending_count = 0
        for operation in self._operations:
            root = self._root(operation.root_key)
            if operation.stage_path is not None:
                try:
                    _unlink_private_file(
                        root.private_root,
                        operation.stage_path,
                        operation.stage_identity,
                    )
                except ManagedFileError:
                    pending_count += 1
            if operation.backup_path is not None and operation.backup_created:
                try:
                    _unlink_private_file(
                        root.private_root,
                        operation.backup_path,
                        operation.original_identity,
                    )
                except ManagedFileError:
                    pending_count += 1

        self._finalized = True
        if pending_count:
            self.cleanup_pending = CleanupPending(pending_count=pending_count)
            self._state = "finalized_pending"
        else:
            self._state = "finalized"
        return self.cleanup_pending

    def rollback(self) -> None:
        """Restore pre-commit state and remove only registered owned files."""
        if self._committed or self._state in ("rolled_back", "finalized", "finalized_pending"):
            raise ManagedFileStateError()

        failures = False
        evidence: list[Path] = []
        unresolved_paths: list[Path] = []
        foreign_paths: list[Path] = []
        for operation in reversed(self._operations):
            root = self._root(operation.root_key)
            operation_failed = False
            operation_unresolved: list[Path] = []
            operation_foreign: list[Path] = []
            if operation.stage_fd is not None:
                descriptor_to_close = operation.stage_fd
                operation.stage_fd = None
                try:
                    os.close(descriptor_to_close)
                except BaseException:
                    operation.stage_close_uncertain = True
                    failures = True
                    operation_failed = True

            if operation.kind == "stage" and operation.new_link_attempted:
                destination_state, _ = _inspect_public_link_destination(
                    root.trusted_root,
                    operation.target_path,
                    operation.stage_identity,
                )
                if destination_state == _LINK_EXPECTED:
                    operation.new_published = True
                    try:
                        _unlink_target_if_identity(
                            operation.target_path,
                            operation.target_parent,
                            operation.stage_identity,
                            allow_missing=True,
                        )
                        operation.new_published = False
                    except ManagedFileError:
                        operation_failed = True
                        operation_unresolved.append(operation.target_path)
                elif destination_state == _LINK_ABSENT:
                    operation.new_published = False
                elif destination_state == _LINK_FOREIGN:
                    operation.new_published = False
                    if operation.original_removed:
                        operation_failed = True
                        operation_foreign.append(operation.target_path)
                else:
                    operation_failed = True
                    operation_unresolved.append(operation.target_path)

            if operation.backup_path is not None and (
                operation.backup_link_attempted or operation.backup_created
            ):
                backup_state, _ = _inspect_link_destination(
                    operation.backup_path,
                    operation.original_identity,
                )
                if backup_state == _LINK_EXPECTED:
                    operation.backup_created = True
                    try:
                        _restore_backup(
                            root.private_root,
                            operation.backup_path,
                            operation.target_path,
                            operation.target_parent,
                            operation.original_identity,
                            on_attempt=lambda: setattr(operation, "restore_link_attempted", True),
                            on_created=lambda: setattr(operation, "restore_link_created", True),
                        )
                        operation.backup_created = False
                        operation.original_removed = False
                    except ManagedFileError:
                        operation_failed = True
                        operation_unresolved.extend(
                            (operation.target_path, operation.backup_path)
                        )
                elif backup_state in (_LINK_ABSENT, _LINK_FOREIGN):
                    target_state, _ = _inspect_public_link_destination(
                        root.trusted_root,
                        operation.target_path,
                        operation.original_identity,
                    )
                    if target_state == _LINK_EXPECTED:
                        operation.backup_created = False
                        if backup_state == _LINK_FOREIGN:
                            operation_foreign.append(operation.backup_path)
                    else:
                        operation_failed = True
                        operation_unresolved.append(operation.target_path)
                        if backup_state == _LINK_FOREIGN:
                            operation_foreign.append(operation.backup_path)
                        if target_state == _LINK_FOREIGN:
                            operation_foreign.append(operation.target_path)
                else:
                    operation_failed = True
                    operation_unresolved.append(operation.backup_path)
                    if operation.original_removed:
                        operation_unresolved.append(operation.target_path)

            if operation.stage_path is not None and not operation_failed:
                try:
                    if operation.stage_identity is None:
                        _unlink_unidentified_stage(root.private_root, operation.stage_path)
                    else:
                        _unlink_private_file(
                            root.private_root,
                            operation.stage_path,
                            operation.stage_identity,
                        )
                except ManagedFileError:
                    operation_failed = True
                    operation_unresolved.append(operation.stage_path)

            if operation_failed:
                failures = True
                evidence.extend(_private_evidence(root.private_root, operation))
                unresolved_paths.extend(operation_unresolved)
                foreign_paths.extend(operation_foreign)

        if failures:
            self._state = "rollback_uncertain"
            unique_evidence = tuple(dict.fromkeys(evidence))
            raise ManagedFileRollbackError(
                unique_evidence,
                descriptor_uncertain=any(
                    operation.stage_close_uncertain for operation in self._operations
                ),
                unresolved_paths=tuple(dict.fromkeys(unresolved_paths)),
                foreign_paths=tuple(dict.fromkeys(foreign_paths)),
            ) from None
        self._state = "rolled_back"

    def _require_open(self) -> None:
        if self._state != "open" or self._committed:
            raise ManagedFileStateError()

    def _root(self, key: str) -> ManagedFileRoot:
        try:
            return self._roots[key]
        except (KeyError, TypeError):
            raise UnsafeManagedPathError() from None

    def _prepare_target(
        self,
        root_key: str,
        relative_name: str,
        *,
        allow_existing: bool,
    ) -> tuple[ManagedFileRoot, str, Path, Path, tuple[int, int] | None]:
        root = self._root(root_key)
        name = _validate_relative_name(relative_name)
        if not root.allows(name):
            raise UnsafeManagedPathError()

        _validate_directory(root.trusted_root)
        _validate_directory(root.private_root)
        target = root.trusted_root.joinpath(*name.split("/"))
        parent = target.parent
        _validate_target_parent(root.trusted_root, parent)
        if _device_id(parent) != _device_id(root.private_root):
            raise UnsafeManagedPathError()

        identity = _optional_regular_file_identity(target, parent)
        if identity is not None and not allow_existing:
            raise UnsafeManagedPathError()
        return root, name, target, parent, identity

    def _create_stage(
        self,
        root: ManagedFileRoot,
        operation: _Operation,
        suffix: str,
    ) -> None:
        _validate_directory(root.trusted_root)
        _validate_directory(root.private_root)
        if _device_id(root.trusted_root) != _device_id(root.private_root):
            raise UnsafeManagedPathError()
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix="asm-stage-",
                suffix=suffix,
                dir=root.private_root,
            )
        except OSError:
            raise ManagedFileError() from None

        operation.stage_fd = descriptor
        operation.stage_path = Path(raw_path)
        primary_failure: BaseException | None = None
        try:
            file_info = os.fstat(descriptor)
            if not stat.S_ISREG(file_info.st_mode):
                raise UnsafeManagedPathError()
            operation.stage_identity = _identity(file_info)
        except BaseException as error:
            primary_failure = error

        descriptor_to_close = operation.stage_fd
        operation.stage_fd = None
        if descriptor_to_close is not None:
            try:
                os.close(descriptor_to_close)
            except BaseException as error:
                operation.stage_close_uncertain = True
                if primary_failure is None:
                    primary_failure = error

        try:
            path_identity = _regular_file_identity(operation.stage_path)
            if operation.stage_identity is None:
                operation.stage_identity = path_identity
            elif path_identity != operation.stage_identity:
                raise UnsafeManagedPathError()
        except BaseException as error:
            if primary_failure is None:
                primary_failure = error

        if primary_failure is not None:
            if operation.stage_close_uncertain:
                raise ManagedFileSetupError(descriptor_uncertain=True) from None
            if isinstance(primary_failure, ManagedFileError):
                raise primary_failure
            raise ManagedFileError() from None

    def _new_backup_path(self, root: ManagedFileRoot) -> Path:
        _validate_directory(root.private_root)
        for _ in range(8):
            candidate = root.private_root / f"asm-backup-{uuid.uuid4().hex}.tmp"
            if _lstat(candidate) is None:
                return candidate
        raise ManagedFileError()

    def _validate_operations_before_publish(self) -> None:
        for operation in self._operations:
            root = self._root(operation.root_key)
            _validate_directory(root.trusted_root)
            _validate_directory(root.private_root)
            _validate_target_parent(root.trusted_root, operation.target_parent)
            if _device_id(operation.target_parent) != _device_id(root.private_root):
                raise UnsafeManagedPathError()
            current = _optional_regular_file_identity(operation.target_path, operation.target_parent)
            if current != operation.original_identity:
                raise ManagedFileError()
            if operation.kind == "stage":
                if operation.stage_path is None or operation.stage_identity is None:
                    raise ManagedFileStateError()
                _verify_private_file(root.private_root, operation.stage_path, operation.stage_identity)


def _absolute_path(path_value: os.PathLike | str) -> Path:
    try:
        path = Path(path_value)
    except (TypeError, ValueError):
        raise UnsafeManagedPathError() from None
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise UnsafeManagedPathError()
    return Path(os.path.abspath(os.fspath(path)))


def _validate_relative_name(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise UnsafeManagedPathError()
    if value.startswith("/") or ":" in value:
        raise UnsafeManagedPathError()
    parts = value.split("/")
    if any(not part or part in (".", "..") or not _SAFE_COMPONENT.fullmatch(part) for part in parts):
        raise UnsafeManagedPathError()
    return value


def _stage_suffix(relative_name: str) -> str:
    leaf = relative_name.rsplit("/", 1)[-1]
    if leaf.endswith(".html"):
        return ".html"
    suffix = Path(leaf).suffix
    if suffix and suffix != ".":
        return suffix
    return ".tmp"


def _validate_prefix(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("/"):
        raise UnsafeManagedPathError()
    normalized = _validate_relative_name(value[:-1])
    return f"{normalized}/"


def _validate_directory(path: Path) -> os.stat_result:
    path = _absolute_path(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        info = _lstat(current)
        if info is None or _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeManagedPathError()
    result = _lstat(path)
    if result is None or _is_link_or_reparse(result) or not stat.S_ISDIR(result.st_mode):
        raise UnsafeManagedPathError()
    return result


def _validate_target_parent(trusted_root: Path, parent: Path) -> None:
    root_info = _validate_directory(trusted_root)
    del root_info
    try:
        relative_parent = parent.relative_to(trusted_root)
    except ValueError:
        raise UnsafeManagedPathError() from None
    current = trusted_root
    for part in relative_parent.parts:
        if part in ("", ".", ".."):
            raise UnsafeManagedPathError()
        current = current / part
        info = _lstat(current)
        if info is None or _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeManagedPathError()


def _optional_regular_file_identity(path: Path, parent: Path) -> tuple[int, int] | None:
    _validate_directory(parent)
    info = _lstat(path)
    if info is None:
        return None
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise UnsafeManagedPathError()
    return _identity(info)


def _regular_file_identity(path: Path) -> tuple[int, int]:
    info = _lstat(path)
    if info is None or _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise UnsafeManagedPathError()
    return _identity(info)


def _identity(info: os.stat_result) -> tuple[int, int]:
    device = int(info.st_dev)
    inode = int(info.st_ino)
    if inode == 0:
        raise UnsafeManagedPathError()
    return device, inode


def _device_id(path: Path) -> int:
    info = _validate_directory(path)
    return int(info.st_dev)


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise ManagedFileError() from None


def _same_or_descendant(path: Path, root: Path) -> bool:
    candidate = os.path.normcase(os.path.abspath(os.fspath(path))).rstrip("\\/")
    base = os.path.normcase(os.path.abspath(os.fspath(root))).rstrip("\\/")
    if candidate == base:
        return True
    return candidate.startswith(base + os.sep)


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _verify_private_file(
    private_root: Path,
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> None:
    _validate_directory(private_root)
    if path.parent != private_root or not path.name.startswith("asm-stage-"):
        raise UnsafeManagedPathError()
    if expected_identity is None or _regular_file_identity(path) != expected_identity:
        raise UnsafeManagedPathError()
    if _device_id(private_root) != expected_identity[0]:
        raise UnsafeManagedPathError()


def _link_owned_file(
    source: Path,
    destination: Path,
    expected_identity: tuple[int, int] | None,
    *,
    on_attempt: Callable[[], object] | None = None,
    on_created: Callable[[], object] | None = None,
) -> None:
    _validate_directory(source.parent)
    _validate_directory(destination.parent)
    if expected_identity is None or _regular_file_identity(source) != expected_identity:
        raise UnsafeManagedPathError()
    destination_state, _destination_identity = _inspect_link_destination(
        destination,
        expected_identity,
    )
    if destination_state != _LINK_ABSENT:
        raise ManagedFileError()
    if on_attempt is not None:
        on_attempt()
    try:
        os.link(source, destination)
    except OSError:
        destination_state, _destination_identity = _inspect_link_destination(
            destination,
            expected_identity,
        )
        if destination_state == _LINK_EXPECTED and on_created is not None:
            on_created()
        raise ManagedFileError() from None
    if on_created is not None:
        on_created()
    destination_state, _destination_identity = _inspect_link_destination(
        destination,
        expected_identity,
    )
    if destination_state != _LINK_EXPECTED:
        raise ManagedFileError()


def _inspect_link_destination(
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> tuple[str, tuple[int, int] | None]:
    try:
        _validate_directory(path.parent)
    except ManagedFileError:
        return _LINK_UNAVAILABLE, None
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return _LINK_ABSENT, None
    except OSError:
        return _LINK_UNAVAILABLE, None
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        return _LINK_UNAVAILABLE, None
    try:
        identity = _identity(info)
    except ManagedFileError:
        return _LINK_UNAVAILABLE, None
    if expected_identity is not None and identity == expected_identity:
        return _LINK_EXPECTED, identity
    return _LINK_FOREIGN, identity


def _inspect_public_link_destination(
    trusted_root: Path,
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> tuple[str, tuple[int, int] | None]:
    try:
        _validate_target_parent(trusted_root, path.parent)
    except ManagedFileError:
        return _LINK_UNAVAILABLE, None
    return _inspect_link_destination(path, expected_identity)


def _unlink_target(path: Path, parent: Path, expected_identity: tuple[int, int]) -> None:
    current = _optional_regular_file_identity(path, parent)
    if current != expected_identity:
        raise ManagedFileError()
    try:
        os.unlink(path)
    except OSError:
        raise ManagedFileError() from None


def _unlink_target_if_identity(
    path: Path,
    parent: Path,
    expected_identity: tuple[int, int] | None,
    *,
    allow_missing: bool,
) -> None:
    if expected_identity is None:
        raise ManagedFileStateError()
    current = _optional_regular_file_identity(path, parent)
    if current is None and allow_missing:
        return
    if current != expected_identity:
        raise ManagedFileError()
    try:
        os.unlink(path)
    except OSError:
        raise ManagedFileError() from None


def _unlink_private_file(
    private_root: Path,
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> None:
    _validate_directory(private_root)
    if path.parent != private_root or expected_identity is None:
        raise UnsafeManagedPathError()
    info = _lstat(path)
    if info is None:
        return
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise UnsafeManagedPathError()
    if _identity(info) != expected_identity:
        raise ManagedFileError()
    try:
        os.unlink(path)
    except OSError:
        raise ManagedFileError() from None


def _unlink_unidentified_stage(private_root: Path, path: Path) -> None:
    _validate_directory(private_root)
    if path.parent != private_root or not path.name.startswith("asm-stage-"):
        raise UnsafeManagedPathError()
    info = _lstat(path)
    if info is None:
        return
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise UnsafeManagedPathError()
    if int(info.st_dev) != _device_id(private_root):
        raise UnsafeManagedPathError()
    try:
        os.unlink(path)
    except OSError:
        raise ManagedFileError() from None


def _restore_backup(
    private_root: Path,
    backup_path: Path,
    target_path: Path,
    target_parent: Path,
    expected_identity: tuple[int, int] | None,
    *,
    on_attempt: Callable[[], object] | None = None,
    on_created: Callable[[], object] | None = None,
) -> None:
    if expected_identity is None or backup_path.parent != private_root:
        raise UnsafeManagedPathError()
    _validate_directory(private_root)
    backup_identity = _regular_file_identity(backup_path)
    if backup_identity != expected_identity:
        raise ManagedFileError()
    current_target = _optional_regular_file_identity(target_path, target_parent)
    if current_target is None:
        _link_owned_file(
            backup_path,
            target_path,
            expected_identity,
            on_attempt=on_attempt,
            on_created=on_created,
        )
    elif current_target != expected_identity:
        raise ManagedFileError()
    _unlink_private_file(private_root, backup_path, expected_identity)


def _private_evidence(private_root: Path, operation: _Operation) -> tuple[Path, ...]:
    evidence = []
    for path, expected_identity, prefix in (
        (operation.stage_path, operation.stage_identity, "asm-stage-"),
        (operation.backup_path, operation.original_identity, "asm-backup-"),
    ):
        if path is None:
            continue
        try:
            _verify_private_evidence(
                private_root,
                path,
                expected_identity,
                prefix=prefix,
            )
        except ManagedFileError:
            continue
        evidence.append(path)
    return tuple(evidence)


def _verify_private_evidence(
    private_root: Path,
    path: Path,
    expected_identity: tuple[int, int] | None,
    *,
    prefix: str,
) -> None:
    root_info = _validate_directory(private_root)
    if path.parent != private_root or not path.name.startswith(prefix):
        raise UnsafeManagedPathError()
    info = _lstat(path)
    if info is None or _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise UnsafeManagedPathError()
    if int(info.st_dev) != int(root_info.st_dev):
        raise UnsafeManagedPathError()
    if expected_identity is not None and _identity(info) != expected_identity:
        raise UnsafeManagedPathError()
