import unittest

from sima_cli.cli import (
    _allows_external_prerelease_fallback,
    _should_rerun_after_update,
    _update_auto_confirm_requested,
)


class TestCliUpdateRerun(unittest.TestCase):
    def test_update_auto_confirm_is_recognized_before_or_after_command(self):
        self.assertTrue(_update_auto_confirm_requested(["sima-cli", "update", "-y"]))
        self.assertTrue(_update_auto_confirm_requested(["sima-cli", "-y", "update"]))
        self.assertFalse(_update_auto_confirm_requested(["sima-cli", "update"]))
        self.assertFalse(_update_auto_confirm_requested(["sima-cli", "sdk", "start", "-y"]))

    def test_external_prerelease_fallback_requires_update_and_force(self):
        self.assertTrue(
            _allows_external_prerelease_fallback(["sima-cli", "-i", "update", "-f"])
        )
        self.assertTrue(
            _allows_external_prerelease_fallback(["sima-cli", "-i", "update", "--force"])
        )
        self.assertFalse(
            _allows_external_prerelease_fallback(["sima-cli", "-i", "update"])
        )
        self.assertFalse(
            _allows_external_prerelease_fallback(["sima-cli", "-i", "install", "-f"])
        )

    def test_reruns_regular_commands(self):
        self.assertTrue(_should_rerun_after_update(["sima-cli", "sdk", "list"]))
        self.assertTrue(_should_rerun_after_update(["sima-cli", "--internal", "sdk", "list"]))

    def test_does_not_rerun_help_or_update_commands(self):
        self.assertFalse(_should_rerun_after_update(["sima-cli"]))
        self.assertFalse(_should_rerun_after_update(["sima-cli", "--help"]))
        self.assertFalse(_should_rerun_after_update(["sima-cli", "sdk", "--help"]))
        self.assertFalse(_should_rerun_after_update(["sima-cli", "selfupdate"]))
        self.assertFalse(_should_rerun_after_update(["sima-cli", "version"]))


if __name__ == "__main__":
    unittest.main()
