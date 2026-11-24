import hashlib
import io
import json
import os
import stat
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, patch

import click

from sima_cli.install import metadata_installer
from sima_cli.install.registry import PackageRegistry
from sima_cli.install.metadata_installer import (
    InstallationPreflightError,
    _combine_multipart_files,
    _download_and_validate_metadata,
    _extract_archives_in_folder,
    _is_http_forbidden_error,
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
    def test_incompatible_local_metadata_returns_empty_result_tuple(self):
        with TemporaryDirectory() as tmpdir:
            metadata_path = Path(tmpdir) / "metadata.json"
            metadata_path.write_text("{}", encoding="utf-8")

            with patch("sima_cli.install.metadata_installer.validate_metadata"), \
                 patch(
                     "sima_cli.install.metadata_installer._is_platform_compatible",
                     return_value=False,
                 ):
                result = _download_and_validate_metadata(str(metadata_path))

        self.assertEqual(result, (None, None))

    def test_http_forbidden_detection_handles_wrapped_download_error(self):
        try:
            try:
                raise RuntimeError("403 Client Error: Forbidden")
            except RuntimeError as exc:
                raise RuntimeError("Download failed") from exc
        except RuntimeError as exc:
            self.assertTrue(_is_http_forbidden_error(exc))

    def test_http_forbidden_detection_rejects_other_http_errors(self):
        self.assertFalse(_is_http_forbidden_error(RuntimeError("404 Client Error: Not Found")))

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

    def test_get_palette_sdk_version_preserves_legacy_2_1_2_release(self):
        with TemporaryDirectory() as tmpdir:
            release_file = Path(tmpdir) / "sdk-release"
            release_file.write_text(
                "SDK Version = 2.1.2.3_Palette_SDK_neat_release-2.1_3b4be39\n"
                "eLXr Version = 2.1.2_release_neat_release-2.1_3b4be39\n",
                encoding="utf-8",
            )

            self.assertEqual(_get_palette_sdk_version(release_file), "2.1.2.3")

    def test_get_palette_sdk_version_prefers_platform_base_for_pre_release_sdk(self):
        with TemporaryDirectory() as tmpdir:
            release_file = Path(tmpdir) / "sdk-release"
            release_file.write_text(
                "SDK Profile = platform-cross\n"
                "Platform Version = 2.1.3~pre4617\n"
                "Platform Base = 2.1.3\n"
                "Platform Channel = pre-release\n"
                "SDK Version = "
                "2.1.3~pre4617_Palette_SDK_neat_develop_4b9f4a1\n",
                encoding="utf-8",
            )

            self.assertEqual(_get_palette_sdk_version(release_file), "2.1.3")

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
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "windows"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("windows", "11", "amd64"))
    def test_windows_11_x64_host_matches_native_package(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [{
                "type": "host",
                "os": ["windows"],
                "versions": {"windows": [">=11"]},
                "arch": ["amd64"],
            }]
        }

        self.assertTrue(_is_platform_compatible(metadata))

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "windows"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("windows", "10", "amd64"))
    def test_windows_10_host_is_rejected_for_native_package(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [{
                "type": "host",
                "os": ["windows"],
                "versions": {"windows": [">=11"]},
                "arch": ["amd64"],
            }]
        }

        with self.assertRaises(SystemExit):
            _is_platform_compatible(metadata)

    @patch("sima_cli.install.metadata_installer.get_sima_build_version", return_value=("", ""))
    @patch("sima_cli.install.metadata_installer.get_exact_devkit_type", return_value="")
    @patch("sima_cli.install.metadata_installer.get_environment_type", return_value=("host", "windows"))
    @patch("sima_cli.install.metadata_installer._detected_host_platform", return_value=("windows", "11", "arm64"))
    def test_windows_11_arm64_host_is_rejected_for_x64_package(
        self,
        _mock_host,
        _mock_env,
        _mock_devkit,
        _mock_version,
    ):
        metadata = {
            "platforms": [{
                "type": "host",
                "os": ["windows"],
                "versions": {"windows": [">=11"]},
                "arch": ["amd64"],
            }]
        }

        with self.assertRaises(SystemExit):
            _is_platform_compatible(metadata)

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
        with TemporaryDirectory() as tmpdir:
            self.assertEqual(
                _metadata_resource_path(
                    tmpdir,
                    "device-agent/neat-runtime_2.0.0%2Bmain.abc_arm64.deb",
                    "https://artifacts.example.com/device-agent/neat-runtime_2.0.0%252Bmain.abc_arm64.deb",
                ),
                Path(tmpdir) / "device-agent" / "neat-runtime_2.0.0%2Bmain.abc_arm64.deb",
            )

    def test_metadata_resource_path_uses_basename_for_absolute_url(self):
        with TemporaryDirectory() as tmpdir:
            self.assertEqual(
                _metadata_resource_path(
                    tmpdir,
                    "https://downloads.example.com/releases/install.sh",
                    "https://downloads.example.com/releases/install.sh",
                ),
                Path(tmpdir) / "install.sh",
            )

    def test_metadata_resource_path_rejects_unsafe_paths(self):
        with TemporaryDirectory() as tmpdir:
            resources = (
                "../install.sh",
                "nested/../../install.sh",
                "/tmp/install.sh",
                r"C:\tmp\install.sh",
            )
            for resource in resources:
                with self.subTest(resource=resource), self.assertRaises(click.ClickException):
                    _metadata_resource_path(
                        tmpdir,
                        resource,
                        "https://artifacts.example.com/install.sh",
                    )

    def test_normalize_downloaded_metadata_resource_renames_to_expected_path(self):
        with TemporaryDirectory() as tmpdir:
            downloaded = os.path.join(tmpdir, "neat-runtime_2.0.0%252Bmain.abc_arm64.deb")
            expected = os.path.join(tmpdir, "device-agent", "neat-runtime_2.0.0%2Bmain.abc_arm64.deb")
            with open(downloaded, "w", encoding="utf-8") as f:
                f.write("deb")

            local_path = _normalize_downloaded_metadata_resource(downloaded, Path(expected))

            self.assertEqual(local_path, expected)
            self.assertTrue(os.path.exists(expected))
            self.assertFalse(os.path.exists(downloaded))

    @patch("sima_cli.install.metadata_installer.download_file_from_url")
    def test_download_metadata_file_resource_preserves_nested_resource_path(self, mock_download):
        with TemporaryDirectory() as tmpdir:
            downloaded = Path(tmpdir) / "install_kerrigan_device_agent.sh"
            downloaded.write_text("#!/bin/sh\n", encoding="utf-8")
            mock_download.return_value = str(downloaded)
            expected = Path(tmpdir) / "device-agent" / downloaded.name

            local_path = _download_metadata_file_resource(
                "device-agent/install_kerrigan_device_agent.sh",
                ["https://artifacts.example.com/device-agent/install_kerrigan_device_agent.sh"],
                tmpdir,
                expected,
                False,
            )

            self.assertEqual(local_path, str(expected))
            self.assertEqual(mock_download.call_args.kwargs["dest_folder"], str(expected.parent))
            self.assertEqual(expected.read_text(encoding="utf-8"), "#!/bin/sh\n")
            self.assertFalse(downloaded.exists())

    @patch("sima_cli.install.metadata_installer.download_file_from_url")
    def test_download_metadata_file_resource_avoids_same_basename_collision(self, mock_download):
        with TemporaryDirectory() as tmpdir:
            install_root = Path(tmpdir)

            def download(url, dest_folder, internal):
                destination = Path(dest_folder) / Path(url).name
                destination.parent.mkdir(parents=True, exist_ok=True)
                content = "nested" if "/device/" in url else "root"
                destination.write_text(content, encoding="utf-8")
                return str(destination)

            mock_download.side_effect = download
            root_path = install_root / "install.sh"
            nested_path = install_root / "device" / "install.sh"

            _download_metadata_file_resource(
                "install.sh",
                ["https://artifacts.example.com/install.sh"],
                tmpdir,
                root_path,
                False,
            )
            _download_metadata_file_resource(
                "device/install.sh",
                ["https://artifacts.example.com/device/install.sh"],
                tmpdir,
                nested_path,
                False,
            )

            self.assertEqual(root_path.read_text(encoding="utf-8"), "root")
            self.assertEqual(nested_path.read_text(encoding="utf-8"), "nested")

    @patch("sima_cli.install.metadata_installer._extract_tar_streaming")
    def test_combine_multipart_files_processes_nested_resources(self, mock_extract):
        with TemporaryDirectory() as tmpdir:
            install_root = Path(tmpdir)
            nested = install_root / "payloads"
            nested.mkdir()
            first = nested / "bundle-split-aa"
            second = nested / "bundle-split-ab"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            _combine_multipart_files(tmpdir, local_paths=[str(first), str(second)])

            nested = nested.resolve()
            combined = nested / "bundle.tar"
            self.assertEqual(combined.read_bytes(), b"firstsecond")
            mock_extract.assert_called_once_with(combined, nested / "bundle")

    @patch("sima_cli.install.metadata_installer._extract_zip_streaming")
    @patch("sima_cli.install.metadata_installer._extract_tar_streaming")
    def test_extract_archives_processes_nested_resources(self, mock_tar, mock_zip):
        with TemporaryDirectory() as tmpdir:
            install_root = Path(tmpdir)
            nested = install_root / "payloads"
            nested.mkdir()
            tar_path = nested / "bundle.tar.gz"
            zip_path = nested / "assets.zip"
            tar_path.touch()
            zip_path.touch()

            _extract_archives_in_folder(tmpdir, [str(tar_path), str(zip_path)])

            nested = nested.resolve()
            tar_path = tar_path.resolve()
            zip_path = zip_path.resolve()
            mock_tar.assert_called_once_with(tar_path, nested / "bundle")
            mock_zip.assert_called_once_with(zip_path, nested / "assets")

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
            script_path = os.path.join(tmpdir, "device-agent", "install_neat_framework.sh")
            os.makedirs(os.path.dirname(script_path))
            with open(script_path, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
            os.chmod(script_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

            _mark_install_script_executable(
                {"installation": {"script": "./device-agent/install_neat_framework.sh"}},
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

    def test_unwritable_current_directory_gives_powershell_advice_on_windows(self):
        with TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch(
                    "sima_cli.install.metadata_installer.tempfile.NamedTemporaryFile",
                    side_effect=PermissionError("denied"),
                ), patch("sima_cli.install.metadata_installer._is_windows", return_value=True):
                    with self.assertRaises(InstallationPreflightError) as raised:
                        _ensure_install_dir_writable(".", command_name="sima-cli neat install")
            finally:
                os.chdir(original_cwd)

        message = raised.exception.message
        self.assertIn(
            "for example in PowerShell:\n"
            "  mkdir -Force $HOME\\neat-pcie; cd $HOME\\neat-pcie\n"
            "  sima-cli neat install ...",
            message,
        )
        self.assertNotIn("mkdir -p", message)
        self.assertNotIn("&&", message)

    def test_windows_directory_is_not_used_as_the_work_directory(self):
        for cwd in ("C:\\Windows\\System32", "c:\\windows", "C:\\WINDOWS\\Temp\\x"):
            with self.subTest(cwd=cwd), patch.dict(os.environ, {"SystemRoot": "C:\\Windows"}), patch(
                "sima_cli.install.metadata_installer._is_windows", return_value=True
            ), patch(
                "sima_cli.install.metadata_installer.Path.cwd", return_value=cwd
            ), patch(
                "sima_cli.install.metadata_installer.tempfile.NamedTemporaryFile"
            ) as write_test:
                with self.assertRaises(InstallationPreflightError) as raised:
                    _ensure_install_dir_writable(".", command_name="sima-cli neat install")

            message = raised.exception.message
            self.assertIn(f"Current directory '{cwd}' is inside the Windows directory", message)
            self.assertIn("mkdir -Force $HOME\\neat-pcie; cd $HOME\\neat-pcie", message)
            self.assertIn("--install-dir <writable-directory>", message)
            write_test.assert_not_called()

    def test_directories_beside_the_windows_directory_are_usable(self):
        with TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch.dict(os.environ, {"SystemRoot": "C:\\Windows"}), patch(
                    "sima_cli.install.metadata_installer._is_windows", return_value=True
                ):
                    _ensure_install_dir_writable(".")
            finally:
                os.chdir(original_cwd)
        with patch.dict(os.environ, {"SystemRoot": "C:\\Windows"}):
            from sima_cli.install.metadata_installer import _inside_windows_directory

            self.assertFalse(_inside_windows_directory("C:\\WindowsApps\\work"))
            self.assertFalse(_inside_windows_directory("C:\\Users\\sima\\Windows"))
            self.assertTrue(_inside_windows_directory("C:\\Windows\\System32"))

    def test_windows_11_is_detected_from_the_build_number(self):
        from types import SimpleNamespace
        from sima_cli.install.metadata_installer import _detected_host_platform

        # (build, product type, platform.release() of the interpreter running sima-cli)
        cases = [
            (19045, 1, "10", "10"),      # Windows 10 22H2
            (22631, 1, "10", "11"),      # Windows 11 23H2 seen by an older Python
            (26100, 1, "10", "11"),      # Windows 11 24H2
            (26200, 1, "11", "11"),      # Windows 11 25H2
            (26100, 3, "2025Server", "2025Server"),  # Windows Server 2025 is not Windows 11
        ]
        for build, product_type, release, expected in cases:
            version = SimpleNamespace(major=10, minor=0, build=build, product_type=product_type)
            with self.subTest(build=build, product_type=product_type), patch(
                "sima_cli.install.metadata_installer.platform.system", return_value="Windows"
            ), patch(
                "sima_cli.install.metadata_installer.platform.release", return_value=release
            ), patch(
                "sima_cli.install.metadata_installer.sys.getwindowsversion",
                return_value=version, create=True,
            ), patch(
                "sima_cli.install.metadata_installer.current_host_arch", return_value="amd64"
            ):
                self.assertEqual(_detected_host_platform(), ("windows", expected, "amd64"))

    def test_installation_preflight_error_renders_yellow_panel(self):
        error = InstallationPreflightError("Current directory '/' is not writable.")

        with patch("sima_cli.install.metadata_installer.console.print") as print_mock:
            error.show()

        panel = print_mock.call_args.args[0]
        self.assertEqual(panel.title, "Installation Failed")
        self.assertEqual(panel.border_style, "yellow")
        self.assertIn("Current directory '/' is not writable.", str(panel.renderable))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LocalWindowsMetadataTests(unittest.TestCase):
    """A local windows-msi metadata file installs the files in its own folder."""

    NAME = "gh:sima-neat/core/pciehost/windows/amd64"

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name) / "pcie offline" / "neat-offline"
        files = {
            "sima-pcie-host-0.6.1-win64.msi": b"core msi",
            "pyneatpcie-0.6.1-cp314-cp314-win_amd64.whl": b"wheel",
            "wheelhouse/numpy-2.5.4-cp314-cp314-win_amd64.whl": b"numpy",
            "card/neat-core-offline-installer.tar.gz": b"card bundle",
        }
        for resource, content in files.items():
            path = self.bundle.joinpath(*resource.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.metadata = {
            "name": self.NAME,
            "version": "0.6.1",
            "release": "v0.6.1",
            "platforms": [{"type": "host", "os": ["windows"], "arch": ["amd64"]}],
            "resources": list(files),
            "resources-checksum": {
                resource: _sha256(self.bundle.joinpath(*resource.split("/"))) for resource in files
            },
            "installation": {
                "type": "windows-msi",
                "msi": "sima-pcie-host-0.6.1-win64.msi",
                "python": {
                    "wheel": "pyneatpcie-0.6.1-cp314-cp314-win_amd64.whl",
                    "distribution": "pyneatpcie",
                    "version": "0.6.1",
                    "import": "pyneatpcie",
                    "wheelhouse": [{
                        "wheel": "wheelhouse/numpy-2.5.4-cp314-cp314-win_amd64.whl",
                        "distribution": "numpy",
                        "version": "2.5.4",
                    }],
                },
            },
        }
        self.registry = PackageRegistry(Path(self.temp.name) / "registry.json")

    def write_metadata(self, metadata=None):
        path = self.bundle / "metadata.json"
        path.write_text(json.dumps(metadata or self.metadata), encoding="utf-8")
        return path

    def install(
        self, metadata_url, install_windows_package=None, fails=False, windows=True, **options
    ):
        """Run install_from_metadata without network; return the mocks and the output."""
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(
                patch("sima_cli.install.windows_package._is_windows", return_value=windows)
            )
            mocks = {
                name: stack.enter_context(patch.object(metadata_installer, name, **kwargs))
                for name, kwargs in {
                    "registry": {"new": self.registry},
                    "download_file_from_url": {"side_effect": AssertionError("network")},
                    "_download_assets": {"side_effect": AssertionError("network")},
                    "_is_platform_compatible": {"return_value": True},
                    "_check_whether_disk_is_big_enough": {"return_value": True},
                    "preflight_windows_package": {},
                    "install_windows_package": install_windows_package or {
                        "return_value": {"native": "installed"}
                    },
                    "_extract_archives_in_folder": {},
                    "_combine_multipart_files": {},
                    "_print_post_install_message": {},
                }.items()
            }
            if fails:
                with self.assertRaises(SystemExit) as raised:
                    metadata_installer.install_from_metadata(
                        str(metadata_url), False, ".", **options
                    )
                self.assertEqual(raised.exception.code, 1)
            else:
                metadata_installer.install_from_metadata(str(metadata_url), False, ".", **options)
        return mocks, output.getvalue()

    def receipts(self):
        return [
            (entry["version"], entry["state"])
            for entry in PackageRegistry(self.registry.registry_path)._data
        ]

    def test_local_windows_metadata_installs_from_its_folder(self):
        # Same receipt and installer call as an online install: rerun, upgrade, downgrade
        # and remove then take the online paths. An older receipt is replaced.
        old_metadata = {**self.metadata, "version": "0.6.0"}
        self.registry.create_entry(self.NAME, "0.6.0", old_metadata, ".")
        self.registry.update_state(self.NAME, "0.6.0", "installed")
        metadata_path = self.write_metadata()

        mocks, output = self.install(metadata_path, native_only=True)

        mocks["install_windows_package"].assert_called_once_with(
            metadata=self.metadata,
            install_dir=os.path.abspath(self.bundle),
            python_executable=None,
            native_only=True,
            record_receipt=ANY,
            check_prerequisites=False,
            retry_command=None,
        )
        mocks["preflight_windows_package"].assert_called_once_with(self.metadata, None, True)
        mocks["_extract_archives_in_folder"].assert_not_called()
        mocks["_combine_multipart_files"].assert_not_called()
        self.assertIn(
            f"Installing from the packages in: {os.path.abspath(self.bundle)}", output
        )
        self.assertEqual(self.receipts(), [("0.6.1", "installed")])
        self.assertIn(f"Replaced the registry receipt of {self.NAME} 0.6.0 with 0.6.1.", output)
        self.assertEqual(
            PackageRegistry(self.registry.registry_path)._data[0]["metadata"], self.metadata
        )
        self.assertFalse((self.bundle / "card" / "neat-core-offline-installer").exists())

    def test_local_windows_metadata_reports_missing_and_changed_files(self):
        numpy = "wheelhouse/numpy-2.5.4-cp314-cp314-win_amd64.whl"
        metadata_path = self.write_metadata()
        (self.bundle / "sima-pcie-host-0.6.1-win64.msi").unlink()
        self.bundle.joinpath(*numpy.split("/")).write_bytes(b"x")

        mocks, output = self.install(metadata_path, fails=True)

        mocks["install_windows_package"].assert_not_called()
        self.assertIn(
            "Offline bundle files are missing or changed; nothing was installed:\n"
            "  sima-pcie-host-0.6.1-win64.msi: missing\n"
            f"  {numpy}: SHA-256 differs (expected "
            f"{self.metadata['resources-checksum'][numpy]}, "
            f"found {hashlib.sha256(b'x').hexdigest()})\n"
            "Copy the bundle again from the computer that downloaded it, "
            "then rerun the same command.",
            output,
        )
        self.assertEqual(self.receipts(), [("0.6.1", "failed")])

    def test_local_windows_metadata_rejects_url_resource(self):
        url = "https://example.invalid/neat-core-offline-installer.tar.gz"
        self.metadata["resources"][3] = url
        self.metadata["resources-checksum"][url] = self.metadata["resources-checksum"].pop(
            "card/neat-core-offline-installer.tar.gz"
        )
        metadata_path = self.write_metadata()

        mocks, output = self.install(metadata_path, fails=True)

        mocks["install_windows_package"].assert_not_called()
        self.assertIn(
            f"Resource '{url}' is not a file in the folder of the local metadata file", output
        )
        self.assertEqual(self.receipts(), [("0.6.1", "failed")])

    def test_local_windows_metadata_on_another_host_fails_before_receipt(self):
        metadata_path = self.write_metadata()
        with patch.object(metadata_installer, "_verify_local_resources") as verify:
            mocks, output = self.install(metadata_path, fails=True, windows=False)

        self.assertIn("The windows-msi installation type can only run on Windows.", output)
        verify.assert_not_called()
        mocks["preflight_windows_package"].assert_not_called()
        mocks["install_windows_package"].assert_not_called()
        self.assertEqual(self.receipts(), [])

    def test_local_windows_metadata_download_only_still_downloads(self):
        # --download-only mirrors the resources from any host, without a receipt.
        metadata_path = self.write_metadata()
        with patch.object(
            metadata_installer, "_download_assets", return_value=[]
        ) as download, patch.object(
            metadata_installer, "_verify_local_resources"
        ) as verify, patch.object(
            metadata_installer, "registry", self.registry
        ), patch.object(
            metadata_installer, "_check_whether_disk_is_big_enough", return_value=True
        ), patch(
            "sima_cli.install.windows_package._is_windows", return_value=False
        ), redirect_stdout(io.StringIO()) as output:
            metadata_installer.install_from_metadata(
                str(metadata_path), False, ".", download_only=True
            )

        download.assert_called_once_with(self.metadata, str(metadata_path), ".", False, tag=None)
        verify.assert_not_called()
        self.assertNotIn("Installing from the packages in", output.getvalue())
        self.assertEqual(self.receipts(), [])

    def test_local_script_metadata_still_downloads(self):
        metadata = {
            "name": "demo", "version": "1.0", "release": "", "platforms": [],
            "resources": ["payload.tar.gz"], "installation": {"script": "echo installed"},
        }
        metadata_path = self.write_metadata(metadata)
        with patch.object(
            metadata_installer, "_download_assets", return_value=[]
        ) as download, patch.object(
            metadata_installer, "registry", self.registry
        ), patch.object(
            metadata_installer, "_check_whether_disk_is_big_enough", return_value=True
        ), redirect_stdout(io.StringIO()) as output:
            metadata_installer.install_from_metadata(str(metadata_path), False, ".")

        download.assert_called_once_with(metadata, str(metadata_path), ".", False, tag=None)
        self.assertNotIn("Installing from the packages in", output.getvalue())


if __name__ == "__main__":
    unittest.main()
