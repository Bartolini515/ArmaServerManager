import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main.modpreset.start_files import generate_server_config, generate_sh_file


class StartFileStagingTests(unittest.TestCase):
    def test_explicit_outputs_leave_default_files_unchanged_and_keep_content(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            arma_directory = root / "arma"
            comparison_directory = root / "comparison"
            mods_directory = root / "mods"
            arma_directory.mkdir()
            comparison_directory.mkdir()
            mods_directory.mkdir()

            username = "fixture_user"
            server_name = "fixture-server"
            final_config = arma_directory / f"server_{username}.cfg"
            final_script = arma_directory / f"start{server_name}.sh"
            final_config.write_bytes(b"caller-owned final config")
            final_script.write_bytes(b"caller-owned final script")

            expected_config = generate_server_config(
                username,
                "invented-password",
                str(comparison_directory),
            )
            staged_config = arma_directory / "server_fixture_user.stage"
            config_result = generate_server_config(
                username,
                "invented-password",
                str(arma_directory),
                output_path=str(staged_config),
            )

            mod_paths = [
                os.path.join(str(mods_directory), "first_mod"),
                os.path.join(str(mods_directory), "second_mod"),
            ]
            expected_script = generate_sh_file(
                server_name,
                2302,
                username,
                mod_paths,
                str(mods_directory),
                str(comparison_directory),
            )
            staged_script = arma_directory / "startfixture-server.stage"
            script_result = generate_sh_file(
                server_name,
                2302,
                username,
                mod_paths,
                str(mods_directory),
                str(arma_directory),
                output_path=str(staged_script),
            )

            self.assertEqual(config_result, str(staged_config))
            self.assertEqual(script_result, str(staged_script))
            self.assertEqual(staged_config.read_bytes(), Path(expected_config).read_bytes())
            self.assertEqual(staged_script.read_bytes(), Path(expected_script).read_bytes())
            self.assertEqual(final_config.read_bytes(), b"caller-owned final config")
            self.assertEqual(final_script.read_bytes(), b"caller-owned final script")

            script_content = staged_script.read_text()
            self.assertIn('-config="server_fixture_user.cfg"', script_content)
            expected_mods = (
                f"-mod='{os.path.basename(str(mods_directory))}{os.sep}first_mod'"
                f"\\;'"
                f"{os.path.basename(str(mods_directory))}{os.sep}second_mod'"
            )
            self.assertIn(expected_mods, script_content)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(staged_script).st_mode), 0o755)

            admin_stage = arma_directory / "startadmin-instance.stage"
            generate_sh_file(
                "admin-instance",
                2310,
                username,
                [],
                str(mods_directory),
                str(arma_directory),
                is_admin_instance=True,
                output_path=str(admin_stage),
            )
            self.assertIn(
                '-config="server_admin-instance.cfg"',
                admin_stage.read_text(),
            )

    def test_default_outputs_keep_legacy_paths_callbacks_and_script_mode(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            arma_directory = Path(temporary_directory)
            username = "legacy_user"
            server_name = "legacy-server"
            config_messages = []
            script_messages = []

            config_path = generate_server_config(
                username,
                "invented-password",
                str(arma_directory),
                config_messages.append,
            )
            script_path = generate_sh_file(
                server_name,
                2402,
                username,
                [],
                "invented-mods",
                str(arma_directory),
                script_messages.append,
                False,
            )

            self.assertEqual(config_path, f"{arma_directory}/server_{username}.cfg")
            self.assertEqual(script_path, f"{arma_directory}/start{server_name}.sh")
            self.assertEqual(
                config_messages,
                [f"Generated server config for {username} at {config_path}"],
            )
            self.assertEqual(
                script_messages,
                [f"Generated script: {script_path} for {server_name} on port 2402"],
            )
            config_content = Path(config_path).read_text()
            self.assertIn('passwordAdmin = "invented-password";', config_content)
            self.assertIn(
                f"Witaj na serwerze FOG użytkownika {username}!",
                config_content,
            )
            self.assertIn('-config="server_legacy_user.cfg"', Path(script_path).read_text())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(script_path).st_mode), 0o755)

    def test_output_failures_propagate_without_generator_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            arma_directory = root / "arma"
            arma_directory.mkdir()
            config_stage = arma_directory / "candidate.cfg"
            script_stage = arma_directory / "candidate.sh"
            final_config = arma_directory / "server_fixture_user.cfg"
            final_script = arma_directory / "startfixture-server.sh"
            config_stage.write_bytes(b"caller-owned staged config")
            script_stage.write_bytes(b"caller-owned staged script")
            final_config.write_bytes(b"caller-owned final config")
            final_script.write_bytes(b"caller-owned final script")

            for generator, arguments, output_path in (
                (
                    generate_server_config,
                    ("fixture_user", "invented-password", str(arma_directory)),
                    config_stage,
                ),
                (
                    generate_sh_file,
                    ("fixture-server", 2302, "fixture_user", [], "mods", str(arma_directory)),
                    script_stage,
                ),
            ):
                with self.subTest(generator=generator.__name__, failure="write"):
                    with patch("builtins.open", side_effect=OSError("simulated write failure")):
                        with self.assertRaisesRegex(OSError, "simulated write failure"):
                            generator(*arguments, output_path=str(output_path))

            self.assertEqual(config_stage.read_bytes(), b"caller-owned staged config")
            self.assertEqual(script_stage.read_bytes(), b"caller-owned staged script")
            self.assertEqual(final_config.read_bytes(), b"caller-owned final config")
            self.assertEqual(final_script.read_bytes(), b"caller-owned final script")

            with self.assertRaises(OSError):
                generate_server_config(
                    "fixture_user",
                    "invented-password",
                    str(arma_directory),
                    output_path=str(root / "missing" / "candidate.cfg"),
                )

            chmod_stage = arma_directory / "chmod-failure.sh"
            with patch(
                "main.modpreset.start_files.os.chmod",
                side_effect=OSError("simulated chmod failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated chmod failure"):
                    generate_sh_file(
                        "fixture-server",
                        2302,
                        "fixture_user",
                        [],
                        "mods",
                        str(arma_directory),
                        output_path=str(chmod_stage),
                    )
            self.assertTrue(chmod_stage.is_file())
            self.assertEqual(final_script.read_bytes(), b"caller-owned final script")

            for generator, arguments, output_path in (
                (
                    generate_server_config,
                    ("fixture_user", "invented-password", str(arma_directory)),
                    arma_directory / "callback-failure.cfg",
                ),
                (
                    generate_sh_file,
                    ("fixture-server", 2302, "fixture_user", [], "mods", str(arma_directory)),
                    arma_directory / "callback-failure.sh",
                ),
            ):
                with self.subTest(generator=generator.__name__, failure="callback"):
                    def fail_callback(_message):
                        raise RuntimeError("simulated callback failure")

                    with self.assertRaisesRegex(RuntimeError, "simulated callback failure"):
                        generator(
                            *arguments,
                            log_callback=fail_callback,
                            output_path=str(output_path),
                        )
                    self.assertTrue(output_path.is_file())


if __name__ == "__main__":
    unittest.main()
