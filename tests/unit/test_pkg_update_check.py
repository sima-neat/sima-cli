import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from sima_cli.utils.pkg_update_check import (
    AUTO_ACCEPT_UPDATE_ENV,
    FORCE_UPDATE_CHECK_RESULT_ENV,
    PUBLIC_PYPI_SIMPLE_URL,
    _compare_versions,
    check_for_update,
    update_package,
)


class _PyPIResponse:
    def __init__(self, version):
        self._payload = f'{{"info": {{"version": "{version}"}}}}'.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


class TestPkgUpdateCheck(unittest.TestCase):
    def test_compare_versions_handles_newer_older_and_equal_numeric_releases(self):
        self.assertEqual(_compare_versions("2.1.4", "2.1.5"), -1)
        self.assertEqual(_compare_versions("2.1.5", "2.1.4"), 1)
        self.assertEqual(_compare_versions("2.1.5", "2.1.5"), 0)
        self.assertEqual(_compare_versions("2.1", "2.1.0"), 0)

    def test_update_package_uses_public_pypi_in_isolated_mode(self):
        with patch("sima_cli.utils.pkg_update_check.subprocess.run") as run, \
             patch("sima_cli.utils.pkg_update_check.cleanup_pip_leftovers"), \
             patch("sima_cli.utils.pkg_update_check.click.secho"):
            result = update_package("sima-cli")

        run.assert_called_once()
        cmd = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual(cmd, [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--isolated",
            "--upgrade",
            "--index-url",
            PUBLIC_PYPI_SIMPLE_URL,
            "sima-cli",
        ])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertTrue(result)

    def test_update_package_reports_failure(self):
        with patch(
            "sima_cli.utils.pkg_update_check.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["pip"]),
        ), \
             patch("sima_cli.utils.pkg_update_check.cleanup_pip_leftovers") as cleanup, \
             patch("sima_cli.utils.pkg_update_check.click.secho") as secho:
            result = update_package("sima-cli")

        cleanup.assert_not_called()
        self.assertIn("Failed to update sima-cli", secho.call_args.args[0])
        self.assertFalse(result)

    def test_check_for_update_skips_when_update_check_is_disabled(self):
        with patch.dict(os.environ, {"SIMA_CLI_CHECK_FOR_UPDATE": "0"}), \
             patch("sima_cli.utils.pkg_update_check.importlib.metadata.version") as version:
            result = check_for_update("sima-cli")

        version.assert_not_called()
        self.assertFalse(result)

    def test_check_for_update_uses_reworded_message_when_published_version_is_newer(self):
        with patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.4"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.5")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm", return_value=False), \
             patch("sima_cli.utils.pkg_update_check.click.secho") as secho:
            result = check_for_update("sima-cli")

        self.assertIn(
            "Current sima-cli is not the latest published version: 2.1.4 → 2.1.5",
            secho.call_args_list[0].args[0],
        )
        self.assertNotIn("Update available", secho.call_args_list[0].args[0])
        self.assertFalse(result)

    def test_check_for_update_does_not_prompt_when_current_is_latest(self):
        with patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.5"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.5")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm") as confirm, \
             patch("sima_cli.utils.pkg_update_check.update_package") as update:
            result = check_for_update("sima-cli")

        confirm.assert_not_called()
        update.assert_not_called()
        self.assertFalse(result)

    def test_check_for_update_can_force_prompt_when_current_is_latest(self):
        with patch.dict(os.environ, {FORCE_UPDATE_CHECK_RESULT_ENV: "1"}), \
             patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.5"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.5")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm", return_value=True) as confirm, \
             patch("sima_cli.utils.pkg_update_check.update_package", return_value=True) as update:
            result = check_for_update("sima-cli")

        confirm.assert_called_once()
        update.assert_called_once_with("sima-cli")
        self.assertTrue(result)

    def test_check_for_update_can_force_auto_accepted_update_when_current_is_latest(self):
        with patch.dict(os.environ, {
            AUTO_ACCEPT_UPDATE_ENV: "1",
            FORCE_UPDATE_CHECK_RESULT_ENV: "1",
        }), \
             patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.5"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.5")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm") as confirm, \
             patch("sima_cli.utils.pkg_update_check.update_package", return_value=True) as update:
            result = check_for_update("sima-cli")

        confirm.assert_not_called()
        update.assert_called_once_with("sima-cli")
        self.assertTrue(result)

    def test_check_for_update_auto_accepts_update_when_env_is_enabled(self):
        with patch.dict(os.environ, {AUTO_ACCEPT_UPDATE_ENV: "1"}), \
             patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.4"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.5")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm") as confirm, \
             patch("sima_cli.utils.pkg_update_check.update_package", return_value=True) as update:
            result = check_for_update("sima-cli")

        confirm.assert_not_called()
        update.assert_called_once_with("sima-cli")
        self.assertTrue(result)

    def test_check_for_update_does_not_auto_update_on_windows(self):
        with patch.dict(os.environ, {AUTO_ACCEPT_UPDATE_ENV: "1"}), \
             patch("sima_cli.utils.pkg_update_check.sys.platform", "win32"), \
             patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.4"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.5")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm") as confirm, \
             patch("sima_cli.utils.pkg_update_check.update_package", return_value=False) as update:
            result = check_for_update("sima-cli")

        confirm.assert_not_called()
        update.assert_called_once_with("sima-cli")
        self.assertFalse(result)

    def test_check_for_update_skips_automatic_downgrade_when_current_is_newer(self):
        with patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.5"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.4")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm") as confirm, \
             patch("sima_cli.utils.pkg_update_check.update_package") as update, \
             patch("sima_cli.utils.pkg_update_check.click.secho") as secho:
            result = check_for_update("sima-cli")

        confirm.assert_not_called()
        update.assert_not_called()
        self.assertIn(
            "Current sima-cli (2.1.5) is newer than the latest published version (2.1.4); skipping automatic update.",
            secho.call_args_list[0].args[0],
        )
        self.assertIn(
            "If you want to force downgrade, run `sima-cli selfupdate`.",
            secho.call_args_list[1].args[0],
        )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
