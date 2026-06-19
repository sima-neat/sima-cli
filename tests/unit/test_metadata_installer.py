import unittest
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sima_cli.install.metadata_installer import (
    InstallationPreflightError,
    _download_metadata_file_resource,
    _ensure_install_dir_writable,
    _filter_download_compatible_resources,
    _get_palette_sdk_version,
    _is_platform_compatible,
    _mark_install_script_executable,
    _metadata_resource_path,
    _normalize_downloaded_metadata_resource,
    _resolve_resource_url,
    _resolve_resource_url_candidates,
)


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

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("2.1.1", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("board", "modalix"))
    def test_board_without_version_is_compatible_with_matching_board(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "board",
                    "compatible_with": ["modalix"],
                }
            ]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("2.1.1", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("board", "modalix"))
    def test_board_legacy_exact_version_string_is_compatible(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "board",
                    "compatible_with": ["modalix"],
                    "version": "2.1.1",
                }
            ]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("2.1.1", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("board", "modalix"))
    def test_board_exact_version_spec_is_compatible(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "board",
                    "compatible_with": ["modalix"],
                    "version": "==2.1.1",
                }
            ]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("2.1.1", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("board", "modalix"))
    def test_board_range_version_spec_is_compatible(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "board",
                    "compatible_with": ["modalix"],
                    "version": ">=2.1.0,<=2.1.2",
                }
            ]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("2.2.0", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("board", "modalix"))
    def test_board_range_version_spec_rejects_incompatible_version(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "board",
                    "compatible_with": ["modalix"],
                    "version": ">=2.1.0,<=2.1.2",
                }
            ]
        }

        with self.assertRaises(SystemExit):
            _is_platform_compatible(metadata)

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("2.1.1", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="modalix-ea")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("board", "modalix"))
    def test_board_matches_exact_devkit_type_alias(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "board",
                    "compatible_with": ["modalix-ea"],
                    "version": ">=2.1.0",
                }
            ]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("sdk", "palette"))
    def test_palette_platform_matches_palette_sdk_container(
        self,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        self.assertTrue(_is_platform_compatible({"platforms": [{"type": "palette"}]}))

    def test_get_palette_sdk_version_reads_sdk_release_prefix(self):
        with TemporaryDirectory() as tmpdir:
            release_file = Path(tmpdir) / "sdk-release"
            release_file.write_text(
                "SDK Version = 2.0.0_Palette_SDK_neat_main_7547251\n"
                "eLXr Version = 2.0.0_release_neat_main_7547251\n",
                encoding="utf-8",
            )

            self.assertEqual(_get_palette_sdk_version(release_file), "2.0.0")

    @patch("sima_cli.install.metadata_installer._get_palette_sdk_version", return_value="2.0.0")
    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("sdk", "palette"))
    def test_palette_platform_version_matches_palette_sdk_container(
        self,
        _mock_env,
        _mock_devkit,
        _mock_build_version,
        _mock_palette_version,
    ):
        self.assertTrue(_is_platform_compatible({"platforms": [{"type": "palette", "version": "2.0.0"}]}))

    @patch("sima_cli.install.metadata_installer._get_palette_sdk_version", return_value="2.1.0")
    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("sdk", "palette"))
    def test_palette_platform_version_rejects_incompatible_sdk_container(
        self,
        _mock_env,
        _mock_devkit,
        _mock_build_version,
        _mock_palette_version,
    ):
        with self.assertRaises(SystemExit):
            _is_platform_compatible({"platforms": [{"type": "palette", "version": "2.0.0"}]})

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "mac"))
    @patch("sima_cli.install.metadata_installer.platform.mac_ver", return_value=("14.5", ("", "", ""), ""))
    @patch("sima_cli.install.metadata_installer.platform.system", return_value="Darwin")
    def test_host_platform_matches_macos(
        self,
        _mock_system,
        _mock_mac_ver,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        self.assertTrue(_is_platform_compatible({"platforms": [{"type": "host", "os": ["mac"]}]}))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "linux"))
    @patch("sima_cli.install.metadata_installer.subprocess.check_output", return_value="Ubuntu 22.04.5 LTS")
    @patch("sima_cli.install.metadata_installer.platform.system", return_value="Linux")
    def test_host_linux_platform_matches_ubuntu(
        self,
        _mock_system,
        _mock_check_output,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        self.assertTrue(_is_platform_compatible({"platforms": [{"type": "host", "os": ["linux"]}]}))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "linux"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("ubuntu", "24.04", "amd64"))
    def test_host_version_and_arch_match(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "host",
                    "os": ["ubuntu"],
                    "versions": {"ubuntu": ["==24.04"]},
                    "arch": ["amd64"],
                }
            ]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "linux"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("ubuntu", "24.04", "amd64"))
    def test_host_bare_version_preserves_prefix_match(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "host",
                    "os": ["ubuntu"],
                    "versions": {"ubuntu": ["24"]},
                }
            ]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "linux"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("ubuntu", "22.04", "amd64"))
    def test_host_version_rejects_incompatible_ubuntu(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "host",
                    "os": ["ubuntu"],
                    "versions": {"ubuntu": ["==24.04"]},
                }
            ]
        }

        with self.assertRaises(SystemExit):
            _is_platform_compatible(metadata)

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "linux"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("ubuntu", "24.04", "arm64"))
    def test_host_arch_rejects_incompatible_arch(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "host",
                    "os": ["ubuntu"],
                    "versions": {"ubuntu": ["==24.04"]},
                    "arch": ["amd64"],
                }
            ]
        }

        with self.assertRaises(SystemExit):
            _is_platform_compatible(metadata)

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "linux"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("ubuntu", "22.04", "amd64"))
    def test_force_bypasses_host_platform_rejection(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [
                {
                    "type": "host",
                    "os": ["ubuntu"],
                    "versions": {"ubuntu": ["==24.04"]},
                    "arch": ["amd64"],
                }
            ]
        }

        self.assertFalse(_is_platform_compatible(metadata, force=True))

    @patch("sima_cli.install.metadata_installer.platform.system", return_value="Darwin")
    @patch("sima_cli.install.metadata_installer.platform.machine", return_value="arm64")
    def test_download_compatible_resources_keeps_macos_arm_wheels_and_non_wheels(
        self,
        _mock_machine,
        _mock_system,
    ):
        resources = [
            "install.sh",
            "pkg-1.0.0-py3-none-any.whl",
            "pkg-1.0.0-py3-none-macosx_11_0_arm64.whl",
            "pkg-1.0.0-py3-none-macosx_10_9_x86_64.whl",
            "pkg-1.0.0-py3-none-manylinux2014_aarch64.whl",
            "pkg-1.0.0-py3-none-win_amd64.whl",
        ]

        self.assertEqual(
            _filter_download_compatible_resources(resources),
            [
                "install.sh",
                "pkg-1.0.0-py3-none-any.whl",
                "pkg-1.0.0-py3-none-macosx_11_0_arm64.whl",
            ],
        )

    @patch("sima_cli.install.metadata_installer.platform.system", return_value="Linux")
    @patch("sima_cli.install.metadata_installer.platform.machine", return_value="x86_64")
    def test_download_compatible_resources_keeps_linux_x86_wheels(
        self,
        _mock_machine,
        _mock_system,
    ):
        resources = [
            "pkg-1.0.0-py3-none-any.whl",
            "pkg-1.0.0-cp311-cp311-manylinux2014_x86_64.whl",
            "pkg-1.0.0-cp311-cp311-linux_aarch64.whl",
            "pkg-1.0.0-py3-none-macosx_11_0_arm64.whl",
        ]

        self.assertEqual(
            _filter_download_compatible_resources(resources),
            [
                "pkg-1.0.0-py3-none-any.whl",
                "pkg-1.0.0-cp311-cp311-manylinux2014_x86_64.whl",
            ],
        )

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

    def test_resolve_resource_url_candidates_include_percent_preserving_fallback(self):
        self.assertEqual(
            _resolve_resource_url_candidates(
                "https://docs.example.com/pkg/metadata.json",
                "sima_lmm-2.0.0.dev0%2Bmaster.6-py3-none-any.whl",
            ),
            [
                "https://docs.example.com/pkg/sima_lmm-2.0.0.dev0%252Bmaster.6-py3-none-any.whl",
                "https://docs.example.com/pkg/sima_lmm-2.0.0.dev0%2Bmaster.6-py3-none-any.whl",
            ],
        )

    def test_resolve_resource_url_candidates_omit_duplicate_fallback(self):
        self.assertEqual(
            _resolve_resource_url_candidates(
                "https://artifacts.example.com/pkg/metadata.json",
                "pkg+debug.deb",
            ),
            ["https://artifacts.example.com/pkg/pkg%2Bdebug.deb"],
        )

    def test_metadata_resource_path_uses_relative_resource_name(self):
        self.assertEqual(
            os.path.basename(
                _metadata_resource_path(
                    "/tmp/pkg",
                    "neat-runtime_2.0.0%2Bmain.abc_arm64.deb",
                    "https://artifacts.example.com/neat-runtime_2.0.0%252Bmain.abc_arm64.deb",
                )
            ),
            "neat-runtime_2.0.0%2Bmain.abc_arm64.deb",
        )

    def test_normalize_downloaded_metadata_resource_renames_to_expected_path(self):
        with TemporaryDirectory() as tmpdir:
            downloaded = os.path.join(tmpdir, "neat-runtime_2.0.0%252Bmain.abc_arm64.deb")
            expected = os.path.join(tmpdir, "neat-runtime_2.0.0%2Bmain.abc_arm64.deb")
            with open(downloaded, "w", encoding="utf-8") as f:
                f.write("deb")

            local_path = _normalize_downloaded_metadata_resource(downloaded, Path(expected))

            self.assertEqual(local_path, expected)
            self.assertTrue(os.path.exists(expected))
            self.assertFalse(os.path.exists(downloaded))

    @patch("sima_cli.install.metadata_installer.download_file_from_url")
    def test_download_metadata_file_resource_retries_percent_preserving_url(self, mock_download):
        with TemporaryDirectory() as tmpdir:
            downloaded = os.path.join(tmpdir, "sima_lmm-2.0.0.dev0%2Bmaster.6-py3-none-any.whl")
            with open(downloaded, "w", encoding="utf-8") as f:
                f.write("wheel")

            mock_download.side_effect = [RuntimeError("403 forbidden"), downloaded]
            expected = Path(tmpdir) / "sima_lmm-2.0.0.dev0%2Bmaster.6-py3-none-any.whl"

            local_path = _download_metadata_file_resource(
                "sima_lmm-2.0.0.dev0%2Bmaster.6-py3-none-any.whl",
                [
                    "https://docs.example.com/pkg/sima_lmm-2.0.0.dev0%252Bmaster.6-py3-none-any.whl",
                    "https://docs.example.com/pkg/sima_lmm-2.0.0.dev0%2Bmaster.6-py3-none-any.whl",
                ],
                tmpdir,
                expected,
                False,
            )

            self.assertEqual(local_path, str(expected))
            self.assertEqual(mock_download.call_count, 2)

    def test_mark_install_script_executable_sets_execute_bits(self):
        with TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "install_neat_framework.sh")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
            os.chmod(script_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

            _mark_install_script_executable(
                {"installation": {"script": "./install_neat_framework.sh"}},
                tmpdir,
            )

            mode = os.stat(script_path).st_mode
            self.assertTrue(mode & stat.S_IXUSR)
            self.assertTrue(mode & stat.S_IXGRP)
            self.assertTrue(mode & stat.S_IXOTH)

    def test_mark_install_script_executable_ignores_paths_outside_install_dir(self):
        with TemporaryDirectory() as tmpdir, TemporaryDirectory() as outside:
            script_path = os.path.join(outside, "install.sh")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
            os.chmod(script_path, stat.S_IRUSR | stat.S_IWUSR)

            _mark_install_script_executable(
                {"installation": {"script": script_path}},
                tmpdir,
            )

            self.assertFalse(os.stat(script_path).st_mode & stat.S_IXUSR)

    def test_ensure_install_dir_writable_reports_current_directory(self):
        with TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch(
                    "sima_cli.install.metadata_installer.tempfile.NamedTemporaryFile",
                    side_effect=PermissionError("denied"),
                ):
                    with self.assertRaisesRegex(
                        InstallationPreflightError,
                        "(?s)Current directory .* is not writable.*downloads package assets",
                    ):
                        _ensure_install_dir_writable(".")
            finally:
                os.chdir(original_cwd)

    def test_ensure_install_dir_writable_allows_writable_current_directory(self):
        with TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                _ensure_install_dir_writable(".")
            finally:
                os.chdir(original_cwd)

    def test_installation_preflight_error_renders_yellow_panel(self):
        error = InstallationPreflightError("Current directory '/' is not writable.")

        with patch("sima_cli.install.metadata_installer.console.print") as print_mock:
            error.show()

        panel = print_mock.call_args.args[0]
        self.assertEqual(panel.title, "Installation Failed")
        self.assertEqual(panel.border_style, "yellow")
        self.assertIn("Current directory '/' is not writable.", str(panel.renderable))


if __name__ == "__main__":
    unittest.main()
