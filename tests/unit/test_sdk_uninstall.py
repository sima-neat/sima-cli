import unittest
from unittest.mock import patch

from sima_cli.sdk.uninstall import _select_containers_for_removal


class TestSdkUninstall(unittest.TestCase):
    def test_remove_selector_uses_checkbox_for_single_container(self):
        checkbox_result = unittest.mock.Mock()
        checkbox_result.execute.return_value = ["neat-latest"]

        with patch("sima_cli.sdk.uninstall.inquirer.checkbox", return_value=checkbox_result) as checkbox:
            selected = _select_containers_for_removal([
                {"Names": "neat-latest", "Image": "ghcr.io/sima-neat/sdk:latest"}
            ])

        checkbox.assert_called_once()
        self.assertEqual(selected, ["neat-latest"])

    def test_remove_selector_yes_to_all_returns_all_names(self):
        selected = _select_containers_for_removal(
            [
                {"Names": "neat-latest"},
                {"Names": "yocto-latest"},
            ],
            yes_to_all=True,
        )

        self.assertEqual(selected, ["neat-latest", "yocto-latest"])


if __name__ == "__main__":
    unittest.main()
