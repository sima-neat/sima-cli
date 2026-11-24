import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.cli import main


class TestCliUpdate(unittest.TestCase):
    def _invoke_update(self, args, *, internal_reachable=True):
        runner = CliRunner()
        with patch("sima_cli.cli.check_for_update", return_value=False), \
             patch("sima_cli.cli.get_environment_type", return_value=("board", "elxr")), \
             patch("sima_cli.cli.internal_resource_exists", return_value=True), \
             patch("sima_cli.cli.check_artifactory_reachability", return_value=internal_reachable), \
             patch("sima_cli.cli.is_devkit_running_elxr", return_value=True), \
             patch("sima_cli.cli.perform_update") as perform_update:
            result = runner.invoke(main, args, obj={})
        self.assertEqual(result.exit_code, 0, result.output)
        return perform_update

    def test_update_yes_uses_official_release_noninteractively(self):
        perform_update = self._invoke_update(["update", "-y"])

        self.assertFalse(perform_update.call_args.args[2])
        self.assertTrue(perform_update.call_args.kwargs["auto_confirm"])
        self.assertFalse(perform_update.call_args.kwargs["force_external_fallback"])

    def test_internal_update_yes_uses_internal_mirror_noninteractively(self):
        perform_update = self._invoke_update(["-i", "update", "-y"])

        self.assertTrue(perform_update.call_args.args[2])
        self.assertTrue(perform_update.call_args.kwargs["auto_confirm"])
        self.assertFalse(perform_update.call_args.kwargs["force_external_fallback"])

    def test_global_and_update_yes_with_force_selects_public_prerelease(self):
        perform_update = self._invoke_update(["-y", "update", "-f", "-y"])

        self.assertFalse(perform_update.call_args.args[2])
        self.assertTrue(perform_update.call_args.kwargs["auto_confirm"])
        self.assertTrue(perform_update.call_args.kwargs["force_external_fallback"])

    def test_update_yes_auto_accepts_cli_self_update_check(self):
        auto_accept_values = []

        def capture_auto_accept(_package):
            auto_accept_values.append(os.environ.get("SIMA_CLI_AUTO_ACCEPT_UPDATE"))
            return False

        with patch("sima_cli.cli.check_for_update", side_effect=capture_auto_accept), \
             patch("sima_cli.cli.sys.argv", ["sima-cli", "update", "-y"]), \
             patch("sima_cli.cli.get_environment_type", return_value=("board", "elxr")), \
             patch("sima_cli.cli.is_devkit_running_elxr", return_value=True), \
             patch("sima_cli.cli.perform_update"):
            result = CliRunner().invoke(main, ["update", "-y"], obj={})

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(auto_accept_values, ["1"])

    def test_dryrun_is_rejected_outside_elxr(self):
        runner = CliRunner()

        with patch("sima_cli.cli.check_for_update", return_value=False), \
             patch("sima_cli.cli.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.cli.is_devkit_running_elxr", return_value=False), \
             patch("sima_cli.cli.perform_update") as perform_update:
            result = runner.invoke(main, ["update", "--dryrun"], obj={})

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--dryrun is only supported", result.output)
        perform_update.assert_not_called()

    def test_internal_linux_host_update_installs_daily_package_without_device_ip(self):
        runner = CliRunner()
        version = "3.0.0_daily_develop_B1774"

        with patch("sima_cli.cli.check_for_update", return_value=False), \
             patch("sima_cli.cli.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.cli.check_artifactory_reachability", return_value=False), \
             patch("sima_cli.cli.install_host_package") as install_host_package, \
             patch("sima_cli.cli.handle_update") as handle_update, \
             patch("sima_cli.cli.perform_update") as perform_update:
            result = runner.invoke(main, ["-i", "update", "-v", version, "-y"], obj={})

        self.assertEqual(result.exit_code, 0, result.output)
        install_host_package.assert_called_once_with(version, auto_confirm=True)
        handle_update.assert_not_called()
        perform_update.assert_not_called()

    def test_internal_linux_host_update_opens_version_picker_when_version_is_omitted(self):
        runner = CliRunner()

        with patch("sima_cli.cli.check_for_update", return_value=False), \
             patch("sima_cli.cli.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.cli.check_artifactory_reachability", return_value=False), \
             patch("sima_cli.cli.install_host_package") as install_host_package:
            result = runner.invoke(main, ["-i", "update"], obj={})

        self.assertEqual(result.exit_code, 0, result.output)
        install_host_package.assert_called_once_with(None, auto_confirm=False)

    def test_internal_linux_host_update_yes_selects_newest_without_picker(self):
        runner = CliRunner()

        with patch("sima_cli.cli.check_for_update", return_value=False), \
             patch("sima_cli.cli.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.cli.check_artifactory_reachability", return_value=False), \
             patch("sima_cli.cli.install_host_package") as install_host_package:
            result = runner.invoke(main, ["-i", "update", "-y"], obj={})

        self.assertEqual(result.exit_code, 0, result.output)
        install_host_package.assert_called_once_with(None, auto_confirm=True)

    def test_internal_host_update_rejects_non_linux_host(self):
        runner = CliRunner()

        with patch("sima_cli.cli.check_for_update", return_value=False), \
             patch("sima_cli.cli.get_environment_type", return_value=("host", "mac")), \
             patch("sima_cli.cli.check_artifactory_reachability", return_value=False), \
             patch("sima_cli.cli.install_host_package") as install_host_package:
            result = runner.invoke(
                main,
                ["-i", "update", "-v", "3.0.0_daily_develop_B1774"],
                obj={},
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("only supported on Linux hosts", result.output)
        install_host_package.assert_not_called()

    def test_linux_host_update_requires_internal_mode(self):
        runner = CliRunner()

        with patch("sima_cli.cli.check_for_update", return_value=False), \
             patch("sima_cli.cli.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.cli.install_host_package") as install_host_package:
            result = runner.invoke(
                main,
                ["update", "-v", "3.0.0_daily_develop_B1774"],
                obj={},
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("require -i/--internal", result.output)
        install_host_package.assert_not_called()

    def _invoke_install(self, args):
        with patch("sima_cli.cli.check_for_update", return_value=False) as check_for_update, \
             patch("sima_cli.cli.get_environment_type", return_value=("host", "windows")), \
             patch("sima_cli.cli.install_from_metadata") as install_from_metadata:
            result = CliRunner().invoke(main, args, obj={})
        self.assertEqual(result.exit_code, 0, result.output)
        install_from_metadata.assert_called_once()
        return check_for_update

    def test_install_from_local_metadata_skips_update_check(self):
        # An offline bundle is installed from its metadata file without network access.
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            for args in (
                ["install", "-m", str(metadata)],
                ["-y", "install", "--native-only", "--mirror", str(metadata)],
                ["install", f"--mirror={metadata}"],
                ["install", f"-m{metadata}"],
                ["install", "-fm", str(metadata)],
                ["install", f"-fm{metadata}"],
            ):
                with self.subTest(args=args):
                    self._invoke_install(args).assert_not_called()

    def test_install_from_metadata_url_keeps_update_check(self):
        for args in (
            ["install", "-m", "https://example.invalid/metadata.json"],
            ["install", "-m", "missing-metadata.json"],
            ["install", "-fm", "https://example.invalid/metadata.json"],
            ["install", "-mhttps://example.invalid/metadata.json"],
        ):
            with self.subTest(args=args):
                self._invoke_install(args).assert_called_once_with("sima-cli")


if __name__ == "__main__":
    unittest.main()
