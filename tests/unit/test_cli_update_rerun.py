import unittest

from sima_cli.cli import _should_rerun_after_update


class TestCliUpdateRerun(unittest.TestCase):
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
