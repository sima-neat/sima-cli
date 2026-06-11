import unittest
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.app_zoo.commands import appzoo


class TestAppZooCommands(unittest.TestCase):
    def test_appzoo_list_shows_deprecation_notice(self):
        with patch("sima_cli.app_zoo.commands.console.print") as console_print, \
             patch("sima_cli.app_zoo.commands.should_show_post_neat_ga_deprecation_notice", return_value=True), \
             patch("sima_cli.app_zoo.commands.list_apps") as list_apps:
            result = CliRunner().invoke(appzoo, ["list"], obj={"internal": True})

        self.assertEqual(result.exit_code, 0)
        console_print.assert_called_once()
        panel = console_print.call_args.args[0]
        self.assertEqual(panel.title, "App Zoo Deprecation Notice")
        self.assertEqual(panel.border_style, "yellow")
        self.assertIn("legacy Palette SDKs", panel.renderable)
        self.assertIn("https://developer.sima.ai/examples", panel.renderable)
        list_apps.assert_called_once_with(True, None)

    def test_appzoo_list_suppresses_deprecation_notice_before_neat_ga(self):
        with patch("sima_cli.app_zoo.commands.console.print") as console_print, \
             patch("sima_cli.app_zoo.commands.should_show_post_neat_ga_deprecation_notice", return_value=False), \
             patch("sima_cli.app_zoo.commands.list_apps") as list_apps:
            result = CliRunner().invoke(appzoo, ["list"], obj={"internal": True})

        self.assertEqual(result.exit_code, 0)
        console_print.assert_not_called()
        list_apps.assert_called_once_with(True, None)


if __name__ == "__main__":
    unittest.main()
