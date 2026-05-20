import unittest
from unittest.mock import patch

from sima_cli.install.metadata_installer import _is_platform_compatible


class MetadataInstallerCompatibilityTests(unittest.TestCase):
    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "mac"))
    def test_empty_platforms_are_compatible_with_any_environment(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        self.assertTrue(_is_platform_compatible({"platforms": []}))


if __name__ == "__main__":
    unittest.main()
