"""Regression tests for the standalone isolated Django test environment."""

from __future__ import annotations

import builtins
import importlib
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase
from scripts.test_backend import _build_path_guard, _install_isolation_guards


class TestIsolationTests(SimpleTestCase):
    def test_settings_use_disposable_local_services_and_paths(self):
        test_root = Path(os.environ["ASM_TEST_ROOT"]).resolve()

        self.assertEqual(settings.SETTINGS_MODULE, "testsupport.settings")
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(settings.DATABASES["default"]["NAME"], ":memory:")
        self.assertEqual(
            settings.CACHES["default"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )
        self.assertEqual(settings.CELERY_BROKER_URL, "memory://")
        self.assertEqual(settings.CELERY_RESULT_BACKEND, "cache+memory://")
        self.assertFalse(settings.CELERY_TASK_ALWAYS_EAGER)
        self.assertEqual(os.environ["ASM_TEST_ISOLATION_GUARDS"], "installed")
        for path in (settings.MEDIA_ROOT, settings.STATIC_ROOT, settings.LOG_ROOT):
            self.assertTrue(Path(path).resolve().is_relative_to(test_root))

    def test_config_loader_uses_temporary_override_before_loading_global_config(self):
        temporary_config = Path(os.environ["ASM_CONFIG_PATH"]).resolve()
        test_root = Path(os.environ["ASM_TEST_ROOT"]).resolve()
        backend_root = Path(__file__).resolve().parents[2]
        forbidden_config = os.path.normcase(
            os.path.abspath(backend_root / "data" / "config.json")
        )

        def normalized_path(value) -> str | None:
            if isinstance(value, (str, bytes, os.PathLike)):
                return os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(value))))
            return None

        original_exists = os.path.exists
        original_open = builtins.open

        def guarded_exists(path):
            if normalized_path(path) == forbidden_config:
                raise AssertionError("The isolated runner inspected the repository config path")
            return original_exists(path)

        def guarded_open(file, *args, **kwargs):
            if normalized_path(file) == forbidden_config:
                raise AssertionError("The isolated runner opened the repository config path")
            return original_open(file, *args, **kwargs)

        self.assertTrue(temporary_config.is_relative_to(test_root))

        module_name = "main.utils.config"
        current_module = sys.modules.get(module_name)
        with (
            patch.dict(os.environ, {"ASM_CONFIG_PATH": str(temporary_config)}),
            patch("os.path.exists", side_effect=guarded_exists),
            patch("builtins.open", side_effect=guarded_open),
        ):
            try:
                module = (
                    importlib.reload(current_module)
                    if current_module is not None
                    else importlib.import_module(module_name)
                )
            except AssertionError as exc:
                self.fail(str(exc))
        self.assertEqual(Path(module.config.file_path).resolve(), temporary_config)
        self.assertEqual(
            Path(module.config.get("paths.logs_directory")).resolve(),
            test_root / "logs",
        )

    def test_network_guard_blocks_alternate_socket_apis_before_os_calls(self):
        socket_apis = ["connect_ex", "sendto"]
        if hasattr(socket.socket, "sendmsg"):
            socket_apis.append("sendmsg")

        unguarded_apis = []
        for api_name in socket_apis:
            api = getattr(socket.socket, api_name)
            guard = getattr(api, "__func__", api)
            if not getattr(guard, "_asm_isolation_guard", False):
                unguarded_apis.append(api_name)
                continue

            socket_type = socket.SOCK_DGRAM if api_name == "sendto" else socket.SOCK_STREAM
            candidate = socket.socket(socket.AF_INET, socket_type)
            try:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "blocked external network or host process access",
                ):
                    if api_name == "connect_ex":
                        candidate.connect_ex(("127.0.0.1", 9))
                    elif api_name == "sendto":
                        candidate.sendto(b"test", ("127.0.0.1", 9))
                    else:
                        candidate.sendmsg([b"test"])
            finally:
                candidate.close()

        self.assertEqual(
            unguarded_apis,
            [],
            f"Socket APIs have no process-installed isolation guard: {unguarded_apis}",
        )

    def test_filesystem_guard_checks_both_rename_paths(self):
        test_root = Path(os.environ["ASM_TEST_ROOT"]).resolve()
        fake_repository = test_root / "rename-guard-fixture"
        protected_directory = fake_repository / "backend" / "data"
        protected_directory.mkdir(parents=True, exist_ok=True)
        cases = []
        for operation_name in ("rename", "replace"):
            for protected_side in ("source", "destination"):
                source = test_root / f"{operation_name}-{protected_side}-source.txt"
                if protected_side == "source":
                    source = protected_directory / source.name
                    destination = test_root / f"{operation_name}-safe-destination.txt"
                else:
                    destination = protected_directory / f"{operation_name}-destination.txt"
                source.write_text("invented disposable data", encoding="utf-8")
                cases.append((operation_name, protected_side, source, destination))

        restore_guards = _install_isolation_guards(fake_repository)

        try:
            for operation_name, protected_side, source, destination in cases:
                move = getattr(os, operation_name)
                with self.subTest(operation=operation_name, side=protected_side):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "blocked repository runtime data access",
                    ):
                        move(source, destination)
        finally:
            restore_guards()

    def test_filesystem_guard_resolves_symlink_destination_aliases(self):
        test_root = Path(os.environ["ASM_TEST_ROOT"]).resolve()
        fake_repository = test_root / "symlink-guard-fixture"
        protected_directory = fake_repository / "backend" / "data"
        protected_directory.mkdir(parents=True, exist_ok=True)
        source = test_root / "symlink-safe-source.txt"
        source.write_text("invented disposable data", encoding="utf-8")
        alias_destination = test_root / "invented-symlink-alias" / "config.json"
        alias_destination.parent.mkdir(parents=True, exist_ok=True)
        alias_name = os.path.normcase(os.path.abspath(alias_destination))
        protected_target = protected_directory / "config.json"
        original_resolve = Path.resolve

        def resolve_invented_alias(path, strict=False):
            if os.path.normcase(os.path.abspath(os.fspath(path))) == alias_name:
                return protected_target
            return original_resolve(path, strict=strict)

        restore_guards = _install_isolation_guards(fake_repository)
        try:
            with patch.object(Path, "resolve", resolve_invented_alias):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "blocked repository runtime data access",
                ):
                    os.replace(source, alias_destination)
        finally:
            restore_guards()

    def test_path_guard_blocks_canonical_targets_of_protected_symlinks(self):
        test_root = Path(os.environ["ASM_TEST_ROOT"]).resolve()
        fake_repository = test_root / "canonical-path-guard-fixture"
        backend_root = fake_repository / "backend"
        lexical_data_directory = backend_root / "data"
        lexical_config_path = lexical_data_directory / "config.json"
        lexical_env_path = fake_repository / ".env"
        real_data_directory = test_root / "invented-real-runtime-data"
        real_config_path = real_data_directory / "config.json"
        real_env_path = test_root / "invented-real-env-target"
        alternate_config_alias = test_root / "alternate-data-alias" / "config.json"
        alternate_env_alias = test_root / "alternate-env-alias"

        def normalized(path):
            return os.path.normcase(os.path.abspath(os.fspath(path)))

        resolved_aliases = {
            normalized(lexical_data_directory): real_data_directory,
            normalized(lexical_config_path): real_config_path,
            normalized(lexical_env_path): real_env_path,
            normalized(alternate_config_alias): real_config_path,
            normalized(alternate_env_alias): real_env_path,
        }

        def resolve_invented_symlinks(path, strict=False):
            path_name = normalized(path)
            return resolved_aliases.get(path_name, Path(path_name))

        with patch.object(Path, "resolve", resolve_invented_symlinks):
            guard_path = _build_path_guard(fake_repository)
            for candidate in (
                lexical_config_path,
                real_config_path,
                alternate_config_alias,
                lexical_env_path,
                real_env_path,
                alternate_env_alias,
            ):
                with self.subTest(candidate=candidate):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "blocked repository runtime data access",
                    ):
                        guard_path(candidate)
