"""Small cross-platform exclusive file lock for configuration updates."""

from __future__ import annotations

import errno
import os
import tempfile
import time
from contextlib import contextmanager

_IS_WINDOWS = os.name == "nt"


def _ensure_posix_lock_file(lock_path: str):
    """Create a readable stable sidecar only when it does not already exist."""
    try:
        os.stat(lock_path)
        return
    except FileNotFoundError:
        pass

    directory = os.path.dirname(lock_path) or os.curdir
    prefix = f".{os.path.basename(lock_path)}."
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=prefix,
        suffix=".tmp",
        dir=directory,
    )
    try:
        # fchmod is not affected by the process umask. Publish the stable name
        # only after its access mode is ready for authorized reader/writers.
        os.fchmod(descriptor, 0o644)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary_path, lock_path)
        except FileExistsError:
            # Another process provisioned the stable sidecar first. Keep it as-is.
            pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


@contextmanager
def _posix_file_lock(lock_path: str):
    _ensure_posix_lock_file(lock_path)
    descriptor = os.open(lock_path, os.O_RDONLY)
    try:
        lock_file = os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise

    with lock_file:
        import fcntl

        acquired = False
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            acquired = True
            yield
        finally:
            if acquired:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(file_path: str):
    """Lock the stable ``<file_path>.lock`` sidecar until the context exits.

    POSIX readers and writers need read access to this non-secret file. Existing
    sidecars are never chmodded or replaced; new ones are linked into place with
    mode 0644 before the stable name becomes visible.
    """
    lock_path = f"{os.fsdecode(os.fspath(file_path))}.lock"
    if not _IS_WINDOWS:
        with _posix_file_lock(lock_path):
            yield
        return

    with open(lock_path, "a+b") as lock_file:
        acquired = False
        import msvcrt

        # msvcrt locks a byte range. Ensure byte zero exists before locking it.
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

        while not acquired:
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                time.sleep(0.05)

        try:
            yield
        finally:
            if acquired:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
