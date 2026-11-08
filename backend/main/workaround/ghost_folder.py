"""Per-operation Steam download workspaces and safe mod publication."""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import shutil
import stat
import sys
import tempfile
from typing import Any, Callable
import uuid

from ..utils.operation_logging import LogCallback


logger = logging.getLogger(__name__)


class GhostFolder:
    def __init__(
        self,
        name: str,
        path: str,
        log_callback: LogCallback | None = None,
        *,
        cancel_check: Callable[[], bool] | None = None,
        before_shared_write: Callable[[], None] | None = None,
    ) -> None:
        """Create a unique download workspace below the configured root."""
        self.name = name
        self.base_path = os.path.realpath(os.path.abspath(path or "."))
        self.log_callback = log_callback
        self.cancel_check = cancel_check
        self.before_shared_write = before_shared_write
        self.ghost_folder_path = ""
        self._owned_directories: dict[str, tuple[str, int, int, str, int, int]] = {}
        self.retained = False

        self._ensure_active()
        os.makedirs(self.base_path, exist_ok=True)
        self.ghost_folder_path = self._create_owned_directory(
            self.base_path,
            prefix="steam-download-",
        )
        try:
            self._notify("Created per-operation Steam download workspace.")
        except BaseException:
            self.cleanup()
            raise

    def _create_owned_directory(self, root: str, *, prefix: str) -> str:
        root_path = os.path.realpath(os.path.abspath(root))
        self._ensure_active()
        os.makedirs(root_path, exist_ok=True)
        parent_metadata = os.stat(root_path, follow_symlinks=False)
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise OSError("Steam download parent is not a directory")
        parent_identity = _directory_path_identity(root_path)
        if parent_identity is None:
            raise OSError("Could not bind the Steam download parent directory")
        self._ensure_active()
        directory = tempfile.mkdtemp(prefix=prefix, dir=root_path)
        resolved = os.path.realpath(directory)
        if not _is_within(root_path, resolved):
            self._report_cleanup_failure()
            raise OSError("Created Steam download directory escaped its configured root")

        try:
            metadata = os.lstat(directory)
        except OSError:
            self._report_cleanup_failure()
            raise
        if not stat.S_ISDIR(metadata.st_mode):
            self._report_cleanup_failure()
            raise OSError("Created Steam download path is not a directory")
        directory_identity = _directory_path_identity(directory)
        if directory_identity is None:
            self._report_cleanup_failure()
            raise OSError("Could not bind the created Steam download directory")

        owned_path = os.path.abspath(directory)
        self._owned_directories[owned_path] = (
            root_path,
            directory_identity[0],
            directory_identity[1],
            root_path,
            parent_identity[0],
            parent_identity[1],
        )
        return owned_path

    def move_files(self, destination_path: str, internal_path: str | None = None) -> None:
        """Publish each downloaded item through a private destination sibling."""
        source = (
            self.ghost_folder_path
            if internal_path is None
            else os.path.join(self.ghost_folder_path, internal_path)
        )
        source_root = os.path.realpath(self.ghost_folder_path)
        source_directory = os.path.realpath(source)
        if not _is_within(source_root, source_directory):
            raise OSError("Steam download source escaped its owned workspace")

        destination_root = os.path.realpath(os.path.abspath(destination_path or "."))
        self._ensure_active()
        os.makedirs(destination_root, exist_ok=True)
        if not os.path.isdir(source_directory):
            raise FileNotFoundError("Steam download content directory is unavailable")

        for entry in os.scandir(source_directory):
            if entry.is_symlink():
                raise OSError("Steam download contained an unexpected symbolic link")
            source_item = os.path.abspath(entry.path)
            destination_item = os.path.join(destination_root, entry.name)
            if not _is_within(source_root, os.path.realpath(source_item)):
                raise OSError("Steam download item escaped its owned workspace")
            if os.path.lexists(destination_item):
                self._notify(f"Preserved existing mod {entry.name}.")
                continue

            stage_path = self._create_owned_directory(
                destination_root,
                prefix=f".{entry.name}.stage-",
            )
            try:
                payload_path = os.path.join(stage_path, "payload")
                if entry.is_dir(follow_symlinks=False):
                    self._ensure_active()
                    _reject_nested_symlinks(source_item)
                    self._ensure_active()
                    shutil.copytree(source_item, payload_path, symlinks=True)
                    _reject_nested_symlinks(payload_path)
                elif entry.is_file(follow_symlinks=False):
                    self._ensure_active()
                    shutil.copy2(source_item, payload_path)
                else:
                    raise OSError("Steam download item is not a regular file or directory")

                try:
                    self._ensure_active()
                    _atomic_publish(payload_path, destination_item)
                except OSError:
                    # Another operation may have completed this mod while this
                    # operation was copying it. Preserve that completed path.
                    if os.path.lexists(destination_item):
                        self._notify(f"Preserved existing mod {entry.name}.")
                        continue
                    raise
                self._notify(f"Published mod {entry.name} atomically.")
            finally:
                cleaned = self._remove_owned_directory(stage_path)
                if not cleaned:
                    self._report_cleanup_failure()
                    # Keep the stage in the ownership registry. The outer
                    # owner gets one safe cleanup retry and reports any
                    # residual through DownloadResult metadata.

    def cleanup(self) -> bool:
        """Remove only directories created by this operation; never mask errors."""
        if self.retained:
            self._report_cleanup_failure()
            return False

        complete = True
        owned_paths = list(self._owned_directories)
        owned_paths.sort(key=lambda candidate: candidate == self.ghost_folder_path)
        for owned_path in owned_paths:
            if not self._remove_owned_directory(owned_path):
                complete = False

        if complete:
            self._notify_cleanup("Removed per-operation Steam download workspace.")
        else:
            self._report_cleanup_failure()
        return complete

    def retain(self) -> None:
        """Keep this workspace because an owned process may still write to it."""
        self.retained = True

    def _remove_owned_directory(self, path: str) -> bool:
        try:
            ownership = self._owned_directories.get(os.path.abspath(path))
            if ownership is None:
                return False
            root_path, expected_device, expected_inode, parent_path, parent_device, parent_inode = ownership
            if not _is_within(root_path, os.path.dirname(os.path.abspath(path))):
                return False
        except FileNotFoundError:
            self._owned_directories.pop(os.path.abspath(path), None)
            return True
        except Exception:
            return False

        parent_handle = None
        directory_handle = None
        try:
            parent_handle = _open_owned_directory(
                parent_path,
                parent_device,
                parent_inode,
                delete_access=False,
            )
            if parent_handle is None:
                return False
            if sys.platform == "win32":
                directory_handle = _open_owned_directory(
                    path,
                    expected_device,
                    expected_inode,
                    delete_access=True,
                )
                if directory_handle is None:
                    return False
                if not _remove_windows_tree(path, directory_handle):
                    return False
                if not _delete_windows_handle(directory_handle):
                    return False
            else:
                ownership = (
                    root_path,
                    expected_device,
                    expected_inode,
                    parent_path,
                    parent_device,
                    parent_inode,
                )
                if not _remove_posix_owned_directory(path, ownership):
                    return False
        except FileNotFoundError:
            self._owned_directories.pop(os.path.abspath(path), None)
            return True
        except Exception:
            return False
        finally:
            _close_directory_handle(directory_handle)
            _close_directory_handle(parent_handle)

        self._owned_directories.pop(os.path.abspath(path), None)
        return True

    def _notify(self, message: str) -> None:
        if self.log_callback:
            self._ensure_active()
            self.log_callback(message)

    def _notify_cleanup(self, message: str) -> None:
        if self.log_callback and not self._cancelled():
            try:
                self._ensure_active()
                self.log_callback(message)
            except Exception:
                if not self._cancelled():
                    _log_cleanup_warning("Steam download workspace cleanup log callback failed.")

    def _report_cleanup_failure(self) -> None:
        message = "Could not safely remove an owned Steam download directory; cleanup is incomplete."
        if not self._cancelled():
            _log_cleanup_warning(message)
        if self.log_callback and not self._cancelled():
            try:
                self._ensure_active()
                self.log_callback(message)
            except Exception:
                if not self._cancelled():
                    _log_cleanup_warning("Steam download workspace cleanup log callback failed.")

    def _cancelled(self) -> bool:
        if self.cancel_check is None:
            return False
        try:
            return bool(self.cancel_check())
        except Exception:
            return True

    def _ensure_active(self) -> None:
        if self._cancelled():
            from ..download_leases import LeaseLostError

            raise LeaseLostError() from None
        if self.before_shared_write is not None:
            self.before_shared_write()


def _open_owned_directory(
    path: str,
    expected_device: int,
    expected_inode: int,
    *,
    dir_fd: int | None = None,
    delete_access: bool = True,
) -> Any | None:
    """Open a no-follow directory handle and verify its recorded identity."""
    if os.name == "nt":
        if dir_fd is not None:
            return None
        handle = _open_windows_handle(path, delete_access=delete_access)
        if handle is None:
            return None
        identity = _windows_handle_identity(handle)
        if identity is None or identity != (expected_device, expected_inode):
            _close_windows_handle(handle)
            return None
        return handle

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, dir_fd=dir_fd)
        metadata = os.fstat(descriptor)
    except Exception:
        return None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_dev != expected_device
        or metadata.st_ino != expected_inode
    ):
        os.close(descriptor)
        return None
    return descriptor


def _remove_posix_owned_directory(
    path: str,
    ownership: tuple[str, int, int, str, int, int],
    *,
    operations: Any | None = None,
) -> bool:
    """Claim a POSIX directory in a private quarantine before deleting it.

    The parent chain and same-filesystem quarantine are opened and identity
    checked. The source name is moved with atomic no-replace rename, then the
    moved object's identity is checked before any recursive traversal. A raced
    replacement can be moved into quarantine, but is never traversed/deleted.

    Trust boundary: this relies on verified owner/mode/ACL ancestry and a
    private 0700 quarantine to exclude other effective users. It does not
    protect against malicious same-UID or root code, privileged mount changes,
    or an untrusted principal already holding a writable directory handle.
    If those preconditions cannot be established, cleanup remains pending.
    """
    root_path, expected_device, expected_inode, parent_path, parent_device, parent_inode = ownership
    if not _is_within(root_path, os.path.dirname(os.path.abspath(path))):
        return False
    ops = operations or _PosixCleanupOperations()
    parent_handles = ops.open_parent(parent_path, parent_device, parent_inode)
    if not parent_handles:
        return False

    parent_fd = parent_handles[-1]
    quarantine_name = None
    quarantine_fd = None
    quarantine_identity = None
    claim_name = f".claimed-{uuid.uuid4().hex}"
    claimed = False
    claim_fd = None
    try:
        quarantine = ops.create_quarantine(parent_fd, expected_device)
        if quarantine is None:
            return False
        quarantine_name, quarantine_fd, quarantine_identity = quarantine
        if ops.identity_at(parent_fd, quarantine_name) != quarantine_identity:
            return False

        source_name = os.path.basename(os.path.abspath(path))
        try:
            ops.rename_noreplace(parent_fd, source_name, quarantine_fd, claim_name)
        except OSError:
            return False
        claimed = True

        claim_fd = ops.open_directory(
            quarantine_fd,
            claim_name,
            expected_device,
            expected_inode,
        )
        if claim_fd is None:
            # The claimed object did not match our creation identity. Leave it
            # in the private quarantine without opening its contents.
            return False
        if ops.identity_fd(claim_fd) != (expected_device, expected_inode):
            return False
        if not ops.remove_tree(claim_fd):
            return False

        # Recheck both names after recursive work. POSIX has no unlink-by-fd
        # primitive with expected-inode semantics, so the private 0700 parent
        # and validated ancestry are the final-name trust boundary.
        if ops.identity_at(quarantine_fd, claim_name) != (expected_device, expected_inode):
            return False
        if ops.identity_at(parent_fd, quarantine_name) != quarantine_identity:
            return False
        ops.rmdir(quarantine_fd, claim_name)
        if ops.identity_at(parent_fd, quarantine_name) != quarantine_identity:
            return False
        ops.rmdir(parent_fd, quarantine_name)
        return True
    except Exception:
        return False
    finally:
        if quarantine_name is not None and quarantine_fd is not None and not claimed:
            try:
                if ops.identity_at(parent_fd, quarantine_name) == quarantine_identity:
                    ops.rmdir(parent_fd, quarantine_name)
            except Exception:
                pass
        if claim_fd is not None:
            ops.close(claim_fd)
        if quarantine_fd is not None:
            ops.close(quarantine_fd)
        ops.close_parent(parent_handles)


class _PosixCleanupOperations:
    """Small syscall boundary that keeps POSIX cleanup testable with fakes."""

    def open_parent(
        self,
        path: str,
        expected_device: int,
        expected_inode: int,
    ) -> list[int] | None:
        return _open_posix_parent_chain(path, expected_device, expected_inode)

    def create_quarantine(self, parent_fd: int, expected_device: int):
        return _create_posix_quarantine(parent_fd, expected_device)

    def identity_at(self, directory_fd: int, name: str):
        return _posix_identity_at(directory_fd, name)

    def identity_fd(self, descriptor: int):
        metadata = os.fstat(descriptor)
        return metadata.st_dev, metadata.st_ino

    def rename_noreplace(self, source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
        _renameat2_noreplace(source_fd, source_name, target_fd, target_name)

    def open_directory(
        self,
        parent_fd: int,
        name: str,
        expected_device: int,
        expected_inode: int,
    ) -> int | None:
        return _open_posix_directory(parent_fd, name, expected_device, expected_inode)

    def remove_tree(self, descriptor: int) -> bool:
        return _remove_posix_tree(descriptor)

    def rmdir(self, directory_fd: int, name: str) -> None:
        os.rmdir(name, dir_fd=directory_fd)

    def close(self, descriptor: int) -> None:
        os.close(descriptor)

    def close_parent(self, descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_posix_parent_chain(
    path: str,
    expected_device: int,
    expected_inode: int,
) -> list[int] | None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current_fd = os.open(os.path.sep, flags)
        descriptors.append(current_fd)
        root_metadata = os.fstat(current_fd)
        if not _trusted_posix_ancestor(current_fd, root_metadata):
            _close_posix_descriptors(descriptors)
            return None
        for component in (part for part in os.path.abspath(path).split(os.path.sep) if part):
            current_fd = os.open(component, flags, dir_fd=current_fd)
            descriptors.append(current_fd)
            metadata = os.fstat(current_fd)
            if not _trusted_posix_ancestor(current_fd, metadata):
                _close_posix_descriptors(descriptors)
                return None
        final_metadata = os.fstat(descriptors[-1])
        if (final_metadata.st_dev, final_metadata.st_ino) != (expected_device, expected_inode):
            _close_posix_descriptors(descriptors)
            return None
        return descriptors
    except Exception:
        _close_posix_descriptors(descriptors)
        return None


def _trusted_posix_ancestor(directory_fd: int, metadata: os.stat_result) -> bool:
    effective_uid = os.geteuid()
    if metadata.st_uid not in (0, effective_uid):
        return False
    writable_by_others = bool(metadata.st_mode & 0o022)
    sticky_protected = bool(metadata.st_mode & stat.S_ISVTX) and metadata.st_uid in (0, effective_uid)
    if writable_by_others and not sticky_protected:
        return False
    return _posix_acl_is_verifiable_and_safe(directory_fd)


def _posix_acl_is_verifiable_and_safe(directory_fd: int) -> bool:
    list_xattrs = getattr(os, "listxattr", None)
    if list_xattrs is None:
        return False
    try:
        attributes = list_xattrs(directory_fd)
    except (OSError, TypeError, NotImplementedError):
        return False
    return not any("acl" in str(attribute).lower() for attribute in attributes)


def _create_posix_quarantine(parent_fd: int, expected_device: int):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(8):
        name = f".steam-cleanup-{uuid.uuid4().hex}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
            metadata = os.fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            if not _private_posix_quarantine_is_safe(descriptor, metadata, expected_device):
                os.close(descriptor)
                return None
            return name, descriptor, identity
        except Exception:
            return None
    return None


def _private_posix_quarantine_is_safe(
    directory_fd: int,
    metadata: os.stat_result,
    expected_device: int,
) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_dev == expected_device
        and metadata.st_uid == os.geteuid()
        and not (metadata.st_mode & 0o077)
        and _posix_acl_is_verifiable_and_safe(directory_fd)
    )


def _posix_identity_at(directory_fd: int, name: str) -> tuple[int, int] | None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return None
    return metadata.st_dev, metadata.st_ino


def _renameat2_noreplace(
    source_fd: int,
    source_name: str,
    target_fd: int,
    target_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOTSUP, "atomic no-replace directory claim is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory claim is unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(
        source_fd,
        os.fsencode(source_name),
        target_fd,
        os.fsencode(target_name),
        1,  # RENAME_NOREPLACE
    ) == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number), source_name)


def _open_posix_directory(
    parent_fd: int,
    name: str,
    expected_device: int,
    expected_inode: int,
) -> int | None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
    except Exception:
        return None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (expected_device, expected_inode)
    ):
        os.close(descriptor)
        return None
    return descriptor


def _close_posix_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _directory_path_identity(path: str) -> tuple[int, int] | None:
    if os.name == "nt":
        handle = _open_windows_handle(path, delete_access=False)
        if handle is None:
            return None
        try:
            return _windows_handle_identity(handle)
        finally:
            _close_windows_handle(handle)
    try:
        metadata = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(metadata.st_mode):
        return None
    return metadata.st_dev, metadata.st_ino


def _remove_posix_tree(directory_fd: int) -> bool:
    """Remove entries through an opened directory descriptor, never its path."""
    try:
        parent_device = os.fstat(directory_fd).st_dev
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                try:
                    metadata = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(metadata.st_mode):
                        if metadata.st_dev != parent_device:
                            return False
                        child_fd = _open_owned_directory(
                            entry.name,
                            metadata.st_dev,
                            metadata.st_ino,
                            dir_fd=directory_fd,
                        )
                        if child_fd is None:
                            return False
                        try:
                            if not _remove_posix_tree(child_fd):
                                return False
                        finally:
                            os.close(child_fd)
                        current = os.stat(
                            entry.name,
                            dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                        if (
                            current.st_dev != metadata.st_dev
                            or current.st_ino != metadata.st_ino
                            or not stat.S_ISDIR(current.st_mode)
                        ):
                            return False
                        os.rmdir(entry.name, dir_fd=directory_fd)
                    else:
                        # unlinkat relative to the pinned parent removes a link
                        # itself and never follows a symlink target.
                        os.unlink(entry.name, dir_fd=directory_fd)
                except FileNotFoundError:
                    continue
        return True
    except Exception:
        return False


def _remove_windows_tree(path: str, directory_handle: Any) -> bool:
    """Delete a directory tree through verified, non-reparse file handles."""
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                child_path = os.path.join(path, entry.name)
                child_handle = _open_windows_handle(child_path, delete_access=True)
                if child_handle is None:
                    return False
                try:
                    info = _windows_handle_info(child_handle)
                    if info is None:
                        return False
                    attributes = info[0]
                    is_reparse_point = bool(attributes & 0x400)
                    is_directory = bool(attributes & 0x10)
                    if is_directory and not is_reparse_point:
                        if not _remove_windows_tree(child_path, child_handle):
                            return False
                    if not _delete_windows_handle(child_handle):
                        return False
                finally:
                    _close_windows_handle(child_handle)
        return True
    except Exception:
        return False


def _open_windows_handle(path: str, *, delete_access: bool) -> Any | None:
    """Open a Windows object without following its final reparse point."""
    import ctypes.wintypes as wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    access = 0x80  # FILE_READ_ATTRIBUTES
    if delete_access:
        access |= 0x10000  # DELETE
    handle = create_file(
        os.fspath(path),
        access,
        0x1 | 0x2,  # Share read/write; deny rename/delete while open.
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        return None
    return handle


def _windows_handle_info(handle: Any) -> tuple[int, int, int] | None:
    import ctypes.wintypes as wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTimeLow", wintypes.DWORD),
            ("ftCreationTimeHigh", wintypes.DWORD),
            ("ftLastAccessTimeLow", wintypes.DWORD),
            ("ftLastAccessTimeHigh", wintypes.DWORD),
            ("ftLastWriteTimeLow", wintypes.DWORD),
            ("ftLastWriteTimeHigh", wintypes.DWORD),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation))
    get_info.restype = wintypes.BOOL
    info = _ByHandleFileInformation()
    if not get_info(handle, ctypes.byref(info)):
        return None
    inode = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    return int(info.dwFileAttributes), int(info.dwVolumeSerialNumber), inode


def _windows_handle_identity(handle: Any) -> tuple[int, int] | None:
    info = _windows_handle_info(handle)
    if info is None or not (info[0] & 0x10) or (info[0] & 0x400):
        return None
    return info[1], info[2]


def _delete_windows_handle(handle: Any) -> bool:
    import ctypes.wintypes as wintypes

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    set_info.restype = wintypes.BOOL
    info = _FileDispositionInfo(True)
    return bool(set_info(handle, 4, ctypes.byref(info), ctypes.sizeof(info)))


def _close_windows_handle(handle: Any) -> None:
    if handle is None:
        return
    import ctypes.wintypes as wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def _close_directory_handle(handle: Any) -> None:
    if handle is None:
        return
    if os.name == "nt":
        _close_windows_handle(handle)
    else:
        try:
            os.close(handle)
        except OSError:
            pass


def _is_within(root: str, path: str) -> bool:
    try:
        common = os.path.commonpath((os.path.realpath(root), os.path.realpath(path)))
    except (OSError, ValueError):
        return False
    return os.path.normcase(common) == os.path.normcase(os.path.realpath(root))


def _reject_nested_symlinks(directory: str) -> None:
    for current_root, directory_names, file_names in os.walk(directory, followlinks=False):
        names = (*directory_names, *file_names)
        for name in names:
            if os.path.islink(os.path.join(current_root, name)):
                raise OSError("Steam download contained an unexpected symbolic link")


def _atomic_publish(source: str, destination: str) -> None:
    """Atomically publish without replacing any destination that won a race."""
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable", destination)
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            1,  # RENAME_NOREPLACE
        )
        if result == 0:
            return
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)

    if sys.platform == "win32":
        # MoveFile/rename on Windows fails when the target already exists.
        os.rename(source, destination)
        return

    # POSIX rename can replace an existing empty directory; a precheck followed
    # by rename is racy, so unsupported platforms must refuse publication.
    raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable", destination)


def _log_cleanup_warning(message: str) -> None:
    try:
        logger.warning(message)
    except Exception:
        pass
