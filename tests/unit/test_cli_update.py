import unittest
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.cli import main


class TestCliUpdate(unittest.TestCase):
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
