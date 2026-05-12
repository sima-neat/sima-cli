import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from sima_cli.utils.pkg_update_check import (
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
            update_package("sima-cli")

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

    def test_update_package_reports_failure(self):
        with patch(
            "sima_cli.utils.pkg_update_check.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["pip"]),
        ), \
             patch("sima_cli.utils.pkg_update_check.cleanup_pip_leftovers") as cleanup, \
             patch("sima_cli.utils.pkg_update_check.click.secho") as secho:
            update_package("sima-cli")

        cleanup.assert_not_called()
        self.assertIn("Failed to update sima-cli", secho.call_args.args[0])

    def test_check_for_update_uses_reworded_message_when_published_version_is_newer(self):
        with patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.4"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.5")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm", return_value=False), \
             patch("sima_cli.utils.pkg_update_check.click.secho") as secho:
            check_for_update("sima-cli")

        self.assertIn(
            "Current sima-cli is not the latest published version: 2.1.4 → 2.1.5",
            secho.call_args_list[0].args[0],
        )
        self.assertNotIn("Update available", secho.call_args_list[0].args[0])

    def test_check_for_update_skips_automatic_downgrade_when_current_is_newer(self):
        with patch("sima_cli.utils.pkg_update_check.importlib.metadata.version", return_value="2.1.5"), \
             patch("sima_cli.utils.pkg_update_check.has_internet", return_value=True), \
             patch("sima_cli.utils.pkg_update_check.urllib.request.urlopen", return_value=_PyPIResponse("2.1.4")), \
             patch("sima_cli.utils.pkg_update_check.click.confirm") as confirm, \
             patch("sima_cli.utils.pkg_update_check.update_package") as update, \
             patch("sima_cli.utils.pkg_update_check.click.secho") as secho:
            check_for_update("sima-cli")

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


if __name__ == "__main__":
    unittest.main()
