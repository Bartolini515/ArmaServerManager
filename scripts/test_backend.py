"""Run Django tests with disposable settings, configuration, and filesystem paths."""

from __future__ import annotations

import builtins
import json
import os
import platform
import socket
import subprocess
import io
import threading
from pathlib import Path
import sys
import tempfile


def _blocked_external_call(*args, **kwargs):
    raise RuntimeError(
        "Isolated backend tests blocked external network or host process access"
    )


_blocked_external_call._asm_isolation_guard = True


def _create_temporary_configuration(test_root: Path) -> Path:
    paths = {
        "steamcmd": test_root / "steamcmd",
        "arma3": test_root / "arma3",
        "mods_directory": test_root / "mods",
        "logs_directory": test_root / "logs",
        "download_directory": test_root / "downloads",
    }
    for directory in (*paths.values(), test_root / "media", test_root / "static"):
        directory.mkdir(parents=True, exist_ok=True)

    config_path = test_root / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "paths": {key: str(value) for key, value in paths.items()},
                "steam_auth": {"username": "", "password": "", "shared_secret": ""},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return config_path


def _build_path_guard(repository_root: Path):
    """Build a fail-closed path guard that resolves aliases before allowing access."""
    backend_root = repository_root / "backend"
    forbidden_files = {
        repository_root / ".env",
        backend_root / ".env",
        backend_root / "data" / "config.json",
        backend_root / "db.sqlite3",
    }
    forbidden_directories = {
        backend_root / "data",
        backend_root / "media",
        backend_root / "logs",
    }
    forbidden_file_names_lexical = {
        os.path.normcase(os.path.abspath(path)) for path in forbidden_files
    }
    forbidden_directory_names_lexical = {
        os.path.normcase(os.path.abspath(path)) for path in forbidden_directories
    }

    resolving_path = threading.local()

    def _is_protected(path_name: str) -> bool:
        file_names = forbidden_file_names_lexical | forbidden_file_names_canonical
        directory_names = (
            forbidden_directory_names_lexical | forbidden_directory_names_canonical
        )
        return path_name in file_names or any(
            path_name == directory or path_name.startswith(directory + os.sep)
            for directory in directory_names
        )

    def _canonical_name(path: Path) -> str:
        try:
            resolved_path = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                "Isolated backend tests could not safely resolve a filesystem path"
            ) from exc
        return os.path.normcase(os.path.abspath(os.fspath(resolved_path)))

    # Build canonical protected sets before installing wrappers. The lexical
    # names remain authoritative too, so access through either side of a
    # symlinked protected path is rejected without opening its contents.
    forbidden_file_names_canonical = {
        _canonical_name(path) for path in forbidden_files
    }
    forbidden_directory_names_canonical = {
        _canonical_name(path) for path in forbidden_directories
    }

    def _path_name(value) -> str | None:
        if isinstance(value, (str, bytes, os.PathLike)):
            lexical_name = os.path.normcase(
                os.path.abspath(os.fsdecode(os.fspath(value)))
            )
            if _is_protected(lexical_name) or getattr(resolving_path, "active", False):
                return lexical_name

            resolving_path.active = True
            try:
                return _canonical_name(Path(lexical_name))
            except (OSError, RuntimeError) as exc:
                if isinstance(exc, RuntimeError) and str(exc).startswith(
                    "Isolated backend tests blocked repository runtime data access"
                ):
                    raise
                raise RuntimeError(
                    "Isolated backend tests could not safely resolve a filesystem path"
                ) from exc
            finally:
                resolving_path.active = False
        return None

    def _guard_path(value) -> None:
        path_name = _path_name(value)
        if path_name is not None and _is_protected(path_name):
            raise RuntimeError(
                "Isolated backend tests blocked repository runtime data access"
            )

    return _guard_path


def _install_isolation_guards(repository_root: Path):
    """Reject access to repository runtime state and external host operations."""
    guard_path = _build_path_guard(repository_root)

    def _guarded(original):
        def call(path, *args, **kwargs):
            guard_path(path)
            return original(path, *args, **kwargs)

        return call

    def _guarded_two_paths(original):
        def call(source, destination, *args, **kwargs):
            guard_path(source)
            guard_path(destination)
            return original(source, destination, *args, **kwargs)

        return call

    previous_values = []

    def _replace(owner, name, value) -> None:
        previous_values.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    for _owner, _name in (
        (builtins, "open"),
        (io, "open"),
        (os, "open"),
        (os, "stat"),
        (os, "lstat"),
        (os, "scandir"),
        (os, "listdir"),
        (os, "access"),
        (os, "unlink"),
        (os, "remove"),
        (os, "mkdir"),
        (os, "makedirs"),
        (os, "rmdir"),
        (os.path, "exists"),
        (os.path, "lexists"),
    ):
        _replace(_owner, _name, _guarded(getattr(_owner, _name)))

    for _name in ("rename", "replace", "link", "symlink"):
        if hasattr(os, _name):
            _replace(os, _name, _guarded_two_paths(getattr(os, _name)))

    def _test_platform_system() -> str:
        if sys.platform.startswith("win"):
            return "Windows"
        if sys.platform == "darwin":
            return "Darwin"
        if sys.platform.startswith("linux"):
            return "Linux"
        return sys.platform

    # platform.system() may spawn a local OS version command on Windows. A
    # sys.platform mapping gives the same value without opening a process.
    _replace(platform, "system", _test_platform_system)

    original_popen = subprocess.Popen

    class _BlockedPopen(original_popen):
        def __init__(self, *args, **kwargs):
            _blocked_external_call(*args, **kwargs)

    _replace(subprocess, "Popen", _BlockedPopen)
    for _name in ("run", "call", "check_call", "check_output"):
        _replace(subprocess, _name, _blocked_external_call)
    for _name in ("system", "popen", "kill"):
        if hasattr(os, _name):
            _replace(os, _name, _blocked_external_call)
    _replace(socket, "create_connection", _blocked_external_call)
    for _name in (
        "connect",
        "connect_ex",
        "send",
        "sendall",
        "sendto",
        "sendmsg",
        "sendfile",
    ):
        if hasattr(socket.socket, _name):
            _replace(socket.socket, _name, _blocked_external_call)

    # These are the only psutil operations used by the task layer to identify
    # or control host server processes. System metric reads remain available.
    import psutil

    for _name in ("Process", "pid_exists", "net_connections"):
        _replace(psutil, _name, _blocked_external_call)

    def restore() -> None:
        for owner, name, value in reversed(previous_values):
            setattr(owner, name, value)

    return restore


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    backend_root = repository_root / "backend"
    with tempfile.TemporaryDirectory(prefix="arma-server-manager-tests-") as temporary_root:
        test_root = Path(temporary_root).resolve()
        config_path = _create_temporary_configuration(test_root)

        os.environ["DJANGO_SETTINGS_MODULE"] = "testsupport.settings"
        os.environ["ASM_TEST_ROOT"] = str(test_root)
        os.environ["ASM_CONFIG_PATH"] = str(config_path)
        _install_isolation_guards(repository_root)
        os.environ["ASM_TEST_ISOLATION_GUARDS"] = "installed"
        sys.path.insert(0, str(repository_root))
        sys.path.insert(0, str(backend_root))

        from django.core.management import execute_from_command_line

        execute_from_command_line([sys.argv[0], "test", *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    main()
