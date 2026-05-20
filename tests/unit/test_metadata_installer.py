import unittest
from unittest.mock import patch

from sima_cli.install.metadata_installer import _is_platform_compatible, _resolve_resource_url


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

    def test_resolve_resource_url_encodes_artifact_filename_characters(self):
        self.assertEqual(
            _resolve_resource_url(
                "https://artifacts.example.com/internals/main/abc/metadata.json",
                "neat-runtime_2.0.0+main.abc_arm64.deb",
            ),
            "https://artifacts.example.com/internals/main/abc/neat-runtime_2.0.0%2Bmain.abc_arm64.deb",
        )

    def test_resolve_resource_url_preserves_relative_path_separators(self):
        self.assertEqual(
            _resolve_resource_url(
                "https://artifacts.example.com/core/main/abc/metadata.json",
                "nested/package+debug/file name.deb",
            ),
            "https://artifacts.example.com/core/main/abc/nested/package%2Bdebug/file%20name.deb",
        )

    def test_resolve_resource_url_leaves_absolute_url_unchanged(self):
        url = "https://downloads.example.com/pkg+debug.deb"
        self.assertEqual(
            _resolve_resource_url("https://artifacts.example.com/pkg/metadata.json", url),
            url,
        )


if __name__ == "__main__":
    unittest.main()
