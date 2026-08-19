import os
import unittest
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


if __name__ == "__main__":
    unittest.main()
