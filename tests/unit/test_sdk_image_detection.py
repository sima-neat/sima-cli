import json
import os
import socket
import subprocess
import unittest
from click.testing import CliRunner
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, Mock, patch

from sima_cli.sdk.linux_shared_network import (
    _disable_nm_shared_devkit_ipv6,
    _configure_nm_shared_devkit_forwarding,
    _configure_nm_shared_devkit_ipv6_internet,
    _configure_nm_shared_devkit_internet,
    _parse_route_iface_and_source,
)
from sima_cli.sdk.install import (
    _configure_nfs_export,
    _detect_existing_linux_nfs_export,
    _detect_host_ip,
    _detect_local_ip_candidates,
    _detect_routed_host_ip,
    _parse_export_line,
    _refresh_mpk_config_json,
    _setup_devkit_share,
    _setup_sdk_extensions,
    LINUX_NEAT_EXPORTS_PATH,
    ParsedNfsExport,
    setup_and_start,
)
from sima_cli.sdk.commands import launch_sdk_tool, sdk
from sima_cli.sdk.cmdexec import SdkContainerUnavailable, exec_container_cmd
from sima_cli.sdk.neat import (
    _code_ui_token_cache_path,
    _ensure_certificates,
    _generate_self_signed_cert,
    _install_mkcert,
    _is_port_available,
    NeatRunConfig,
    allocate_neat_ports,
    is_docker_port_collision_error,
    prepare_neat_container_run,
    reserved_ports_from_neat_port_map,
)
from sima_cli.sdk.utils import (
    _append_unique_line,
    _bash_profile_sources_bashrc_script,
    _configure_group_file,
    container_user_mapping_unavailable,
    _copy_sima_cli_auth_cache_to_container,
    _devcontainer_metadata_label,
    _extract_sdk_base_version,
    _ensure_passwd_user,
    _ensure_shadow_user,
    is_docker_user_mapping_error,
    _sudoers_drop_in_script,
    _prepare_log_host_dir,
    container_matches_sdk_keyword,
    ensure_model_sdk_extension_installed,
    ensure_codex_vscode_extension_installed,
    extract_short_name,
    get_local_sima_images,
    get_workspace,
    install_neat_playbooks,
    is_neat_elxr_image,
    is_neat_sdk_image,
    is_port_in_use,
    is_snap_docker_cli,
    prompt_multi_select,
    sanitize_container_hostname,
    sanitize_container_name,
    start_docker_container,
)


SAMPLE_DOCKER_IMAGES = """elxr:latest
sdk:latest
783709528641.dkr.ecr.us-west-2.amazonaws.com/vdp-cli/elxr:2.0.0_Palette_SDK_master_B240
783709528641.dkr.ecr.us-west-2.amazonaws.com/vdp-cli/modelsdk:2.0.0_Palette_SDK_master_B240
ghcr.io/sima-neat/elxr:latest
ghcr.io/sima-neat/elxr-sdk:latest
ghcr.io/sima-neat/sdk-feature-devkit-sync:latest
"""


class TestSdkImageDetection(unittest.TestCase):
    def test_windows_port_check_handles_non_utf8_netstat_output(self):
        netstat_output = (
            "활성 연결\n"
            "  프로토콜  로컬 주소              외부 주소              상태           PID\n"
            "  TCP    0.0.0.0:49152          0.0.0.0:0              LISTENING       1234\n"
        ).encode("cp949")
        result = subprocess.CompletedProcess(
            ["netstat", "-ano", "-p", "TCP"],
            0,
            stdout=netstat_output,
            stderr=b"",
        )

        with patch("sima_cli.sdk.utils.check_os", return_value="windows"), \
             patch("sima_cli.sdk.utils.locale.getpreferredencoding", return_value="cp949"), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=result):
            self.assertTrue(is_port_in_use(49152))

    @patch("sima_cli.sdk.utils.subprocess.check_output", return_value=SAMPLE_DOCKER_IMAGES)
    def test_get_local_sima_images_includes_ghcr_elxr(self, _mock_check_output):
        images = get_local_sima_images()
        self.assertIn("ghcr.io/sima-neat/elxr:latest", images)
        self.assertIn("ghcr.io/sima-neat/elxr-sdk:latest", images)
        self.assertIn("ghcr.io/sima-neat/sdk-feature-devkit-sync:latest", images)
        self.assertIn("elxr:latest", images)
        self.assertIn("sdk:latest", images)
        self.assertIn(
            "783709528641.dkr.ecr.us-west-2.amazonaws.com/vdp-cli/elxr:2.0.0_Palette_SDK_master_B240",
            images,
        )

    def test_extract_short_name_for_ghcr_elxr(self):
        self.assertEqual(extract_short_name("ghcr.io/sima-neat/elxr:latest"), "neat")

    def test_extract_short_name_for_ghcr_elxr_sdk_alias(self):
        self.assertEqual(extract_short_name("ghcr.io/sima-neat/elxr-sdk:latest"), "neat")

    def test_extract_short_name_for_neat_sdk(self):
        self.assertEqual(extract_short_name("ghcr.io/sima-neat/sdk:latest"), "neat")
        self.assertEqual(extract_short_name("ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), "neat")
        self.assertEqual(extract_short_name("ghcr:sima-neat/sdk-feature-devkit-sync:latest"), "neat")
        self.assertEqual(extract_short_name("sdk:latest"), "neat")
        self.assertEqual(extract_short_name("neat-sdk:noble-toolchain-probe"), "neat")
        self.assertEqual(extract_short_name("elxr:latest"), "elxr")

    def test_container_hostname_is_shortened_for_sha_tagged_images(self):
        image = "ghcr.io/sima-neat/sdk-feature-devkit-sync:76b8a6bad7e0c3e0b98c356c1879b98d32a90782"
        container_name = sanitize_container_name(image)
        hostname = sanitize_container_hostname(container_name)

        self.assertGreater(len(container_name), 63)
        self.assertLessEqual(len(hostname), 63)
        self.assertRegex(hostname, r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

    def test_is_neat_elxr_image(self):
        self.assertTrue(is_neat_sdk_image("ghcr.io/sima-neat/sdk:latest"))
        self.assertTrue(is_neat_sdk_image("ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"))
        self.assertTrue(is_neat_sdk_image("sdk:latest"))
        self.assertTrue(is_neat_sdk_image("neat-sdk:noble-toolchain-probe"))
        self.assertTrue(is_neat_sdk_image("ghcr.io/sima-neat/elxr:latest"))
        self.assertTrue(is_neat_sdk_image("ghcr.io/sima-neat/elxr-sdk:latest"))
        self.assertTrue(is_neat_elxr_image("ghcr.io/sima-neat/sdk:latest"))
        self.assertFalse(is_neat_sdk_image("elxr:latest"))
        self.assertFalse(is_neat_elxr_image("artifacts.eng.sima.ai/elxr:2.1.0"))
        self.assertFalse(is_neat_elxr_image("ghcr.io/sima-neat/modelsdk:latest"))
        self.assertFalse(is_neat_sdk_image("ghcr.io/other/sdk-feature-devkit-sync:latest"))

    def test_devcontainer_metadata_label_sets_attach_user_and_workspace(self):
        self.assertEqual(
            _devcontainer_metadata_label("devuser"),
            '[{"remoteUser":"devuser","workspaceFolder":"/workspace"}]',
        )

    def test_container_matches_neat_keyword_for_current_and_legacy_images(self):
        current = {
            "Names": "ghcr.io-sima-neat-sdk-feature-devkit-sync-latest",
            "Image": "ghcr.io/sima-neat/sdk-feature-devkit-sync:latest",
        }
        legacy = {
            "Names": "ghcr.io-sima-neat-elxr-sdk-latest",
            "Image": "ghcr.io/sima-neat/elxr-sdk:latest",
        }
        elxr = {
            "Names": "elxr-latest",
            "Image": "elxr:latest",
        }
        local = {
            "Names": "sdk-latest",
            "Image": "sdk:latest",
        }
        local_probe = {
            "Names": "neat-sdk-noble-toolchain-probe",
            "Image": "neat-sdk:noble-toolchain-probe",
        }

        self.assertTrue(container_matches_sdk_keyword(current, "neat"))
        self.assertTrue(container_matches_sdk_keyword(legacy, "neat"))
        self.assertTrue(container_matches_sdk_keyword(local, "neat"))
        self.assertTrue(container_matches_sdk_keyword(local_probe, "neat"))
        self.assertFalse(container_matches_sdk_keyword(current, "elxr"))
        self.assertFalse(container_matches_sdk_keyword(legacy, "elxr"))
        self.assertFalse(container_matches_sdk_keyword(local, "elxr"))
        self.assertFalse(container_matches_sdk_keyword(local_probe, "elxr"))
        self.assertTrue(container_matches_sdk_keyword(elxr, "elxr"))
        self.assertFalse(container_matches_sdk_keyword(elxr, "neat"))

    def test_setup_sdk_extensions_creates_home_folder_on_x86(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.install.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.install.Path.home", return_value=Path(tmpdir)), \
                 patch("sima_cli.sdk.install.shutil.disk_usage", return_value=Mock(free=30 * 1024 ** 3)), \
                 patch("builtins.input", return_value=""):
                extensions_dir = _setup_sdk_extensions(["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"])

            self.assertEqual(extensions_dir, str((Path(tmpdir) / "sima-sdk-extensions").resolve()))
            self.assertTrue(Path(extensions_dir).is_dir())

    def test_setup_sdk_extensions_allows_custom_folder_on_x86(self):
        with TemporaryDirectory() as tmpdir:
            custom_dir = Path(tmpdir) / "large-disk" / "extensions"
            with patch("sima_cli.sdk.install.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.install.Path.home", return_value=Path(tmpdir)), \
                 patch("sima_cli.sdk.install.shutil.disk_usage", return_value=Mock(free=8 * 1024 ** 3)), \
                 patch("builtins.input", return_value=str(custom_dir)):
                extensions_dir = _setup_sdk_extensions(["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"])

            self.assertEqual(extensions_dir, str(custom_dir.resolve()))
            self.assertTrue(custom_dir.is_dir())

    def test_setup_sdk_extensions_noninteractive_uses_default_without_prompt(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.install.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.install.Path.home", return_value=Path(tmpdir)), \
                 patch("sima_cli.sdk.install.shutil.disk_usage", return_value=Mock(free=30 * 1024 ** 3)), \
                 patch("builtins.input", side_effect=AssertionError("should not prompt")):
                extensions_dir = _setup_sdk_extensions(
                    ["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"],
                    noninteractive=True,
                )

            self.assertEqual(extensions_dir, str((Path(tmpdir) / "sima-sdk-extensions").resolve()))
            self.assertTrue(Path(extensions_dir).is_dir())

    def test_setup_sdk_extensions_allows_arm64_when_version_unknown(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.install.platform.machine", return_value="aarch64"), \
                 patch("sima_cli.sdk.install.Path.home", return_value=Path(tmpdir)), \
                 patch("sima_cli.sdk.install.shutil.disk_usage", return_value=Mock(free=30 * 1024 ** 3)), \
                 patch("builtins.input", side_effect=AssertionError("should not prompt")):
                extensions_dir = _setup_sdk_extensions(
                    ["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"],
                    noninteractive=True,
                )

            self.assertEqual(extensions_dir, str((Path(tmpdir) / "sima-sdk-extensions").resolve()))
            self.assertTrue(Path(extensions_dir).is_dir())

    def test_setup_sdk_extensions_skips_arm64_before_2_1_1(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.install.platform.machine", return_value="aarch64"), \
                 patch("sima_cli.sdk.install.Path.home", return_value=Path(tmpdir)), \
                 patch("sima_cli.sdk.install.shutil.disk_usage", side_effect=AssertionError("should not check disk")), \
                 patch("builtins.input", side_effect=AssertionError("should not prompt")):
                extensions_dir = _setup_sdk_extensions(
                    ["ghcr.io/sima-neat/sdk:2.0.0"],
                    noninteractive=True,
                )

            self.assertEqual(extensions_dir, "")
            self.assertFalse((Path(tmpdir) / "sima-sdk-extensions").exists())

    def test_setup_sdk_extensions_allows_arm64_for_2_1_1(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.install.platform.machine", return_value="arm64"), \
                 patch("sima_cli.sdk.install.Path.home", return_value=Path(tmpdir)), \
                 patch("sima_cli.sdk.install.shutil.disk_usage", return_value=Mock(free=30 * 1024 ** 3)), \
                 patch("builtins.input", side_effect=AssertionError("should not prompt")):
                extensions_dir = _setup_sdk_extensions(
                    ["ghcr.io/sima-neat/sdk:2.1.1"],
                    noninteractive=True,
                )

            self.assertEqual(extensions_dir, str((Path(tmpdir) / "sima-sdk-extensions").resolve()))
            self.assertTrue(Path(extensions_dir).is_dir())

    def test_get_workspace_noninteractive_uses_default_and_creates_it(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            workspace = home / "workspace"

            def fake_expanduser(path):
                return str(home) if path == "~" else str(home / path[2:]) if path.startswith("~/") else path

            with patch("sima_cli.sdk.utils.get_running_containers", return_value=[]), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", side_effect=fake_expanduser), \
                 patch("builtins.input", side_effect=AssertionError("should not prompt")):
                selected = get_workspace(noninteractive=True)

            self.assertEqual(selected, str(workspace.resolve()))
            self.assertTrue(workspace.is_dir())
            self.assertEqual((home / ".simaai" / ".mount").read_text(), str(workspace.resolve()))

    def test_get_workspace_override_creates_and_persists_it(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            workspace = Path(tmpdir) / "aws-workspace"
            home.mkdir()

            def fake_expanduser(path):
                return str(home) if path == "~" else str(home / path[2:]) if path.startswith("~/") else path

            with patch("sima_cli.sdk.utils.get_running_containers", side_effect=AssertionError("should not inspect containers")), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", side_effect=fake_expanduser), \
                 patch("builtins.input", side_effect=AssertionError("should not prompt")):
                selected = get_workspace(workspace_override=str(workspace))

            self.assertEqual(selected, str(workspace.resolve()))
            self.assertTrue(workspace.is_dir())
            self.assertEqual((home / ".simaai" / ".mount").read_text(), str(workspace.resolve()))

    def test_get_workspace_recovers_when_running_containers_have_no_mount_file(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            workspace = home / "remote-workspace"

            def fake_expanduser(path):
                return str(home) if path == "~" else str(home / path[2:]) if path.startswith("~/") else path

            with patch("sima_cli.sdk.utils.get_running_containers", return_value=["sdk-latest"]), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", side_effect=fake_expanduser), \
                 patch("builtins.input", side_effect=[str(workspace), "y"]):
                selected = get_workspace()

            self.assertEqual(selected, str(workspace.resolve()))
            self.assertTrue(workspace.is_dir())
            self.assertEqual((home / ".simaai" / ".mount").read_text(), str(workspace.resolve()))

    def test_get_workspace_noninteractive_recovers_when_running_containers_have_no_mount_file(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            workspace = home / "workspace"

            def fake_expanduser(path):
                return str(home) if path == "~" else str(home / path[2:]) if path.startswith("~/") else path

            with patch("sima_cli.sdk.utils.get_running_containers", return_value=["sdk-latest"]), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", side_effect=fake_expanduser), \
                 patch("builtins.input", side_effect=AssertionError("should not prompt")):
                selected = get_workspace(noninteractive=True)

            self.assertEqual(selected, str(workspace.resolve()))
            self.assertTrue(workspace.is_dir())
            self.assertEqual((home / ".simaai" / ".mount").read_text(), str(workspace.resolve()))

    def test_sdk_setup_workspace_option_is_forwarded(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.setup_and_start") as setup_start:
            result = runner.invoke(sdk, ["setup", "--workspace", "/tmp/aws-workspace", "-y", "-n"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(setup_start.call_args.kwargs["workspace"], "/tmp/aws-workspace")
        self.assertTrue(setup_start.call_args.kwargs["yes_to_all"])
        self.assertTrue(setup_start.call_args.kwargs["noninteractive"])

    def test_sdk_setup_minimal_option_is_forwarded(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.setup_and_start") as setup_start:
            result = runner.invoke(sdk, ["setup", "--minimal", "-y", "-n"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(setup_start.call_args.kwargs["minimal"])

    def test_sdk_setup_persistent_network_profile_option_is_forwarded(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.setup_and_start") as setup_start:
            result = runner.invoke(sdk, ["setup", "--devkit", "10.42.0.78", "--persistent-network-profile", "-y", "-n"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(setup_start.call_args.kwargs["persistent_network_profile"])

    def test_sdk_network_repair_persist_option_is_forwarded(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.repair_linux_devkit_network") as repair, \
             patch("sima_cli.sdk.commands.print_network_doctor_report"):
            repair.return_value.has_errors = False
            result = runner.invoke(sdk, ["network", "repair", "--devkit", "10.42.0.78", "--persist"])

        self.assertEqual(result.exit_code, 0, result.output)
        repair.assert_called_once_with(container="", devkit_ip="10.42.0.78", persist=True)

    def test_sdk_network_repair_requires_devkit(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"):
            result = runner.invoke(sdk, ["network", "repair"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Missing option '--devkit'", result.output)

    def test_sdk_network_repair_rejects_ipv6_devkit(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.repair_linux_devkit_network") as repair:
            result = runner.invoke(sdk, ["network", "repair", "--devkit", "fe80::1"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("supports IPv4 DevKit addresses only", result.output)
        repair.assert_not_called()

    def test_sdk_network_rollback_defaults_to_dry_run(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.rollback_linux_shared_devkit_network", return_value=[]) as rollback:
            result = runner.invoke(sdk, ["network", "rollback", "--devkit", "10.42.0.78"])

        self.assertEqual(result.exit_code, 0, result.output)
        rollback.assert_called_once_with(
            "10.42.0.78",
            dry_run=True,
            remove_persistent_profile=False,
        )

    def test_sdk_network_rollback_apply_removes_persistent_profile_when_requested(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.Path.exists") as path_exists, \
             patch("sima_cli.sdk.commands.rollback_linux_shared_devkit_network", return_value=[]) as rollback:
            result = runner.invoke(
                sdk,
                [
                    "network",
                    "rollback",
                    "--devkit",
                    "10.42.0.78",
                    "--apply",
                    "--remove-persistent-profile",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        path_exists.assert_not_called()
        rollback.assert_called_once_with(
            "10.42.0.78",
            dry_run=False,
            remove_persistent_profile=True,
        )

    def test_sdk_network_rollback_apply_prompts_for_persistent_profile(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.Path.exists", return_value=True), \
             patch("sima_cli.sdk.commands.rollback_linux_shared_devkit_network", return_value=[]) as rollback:
            result = runner.invoke(
                sdk,
                ["network", "rollback", "--devkit", "10.42.0.78", "--apply"],
                input="y\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("persistent SDK network repair profile", result.output)
        rollback.assert_called_once_with(
            "10.42.0.78",
            dry_run=False,
            remove_persistent_profile=True,
        )

    def test_sdk_network_rollback_apply_keeps_persistent_profile_by_default(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.Path.exists", return_value=True), \
             patch("sima_cli.sdk.commands.rollback_linux_shared_devkit_network", return_value=[]) as rollback:
            result = runner.invoke(
                sdk,
                ["network", "rollback", "--devkit", "10.42.0.78", "--apply"],
                input="\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        rollback.assert_called_once_with(
            "10.42.0.78",
            dry_run=False,
            remove_persistent_profile=False,
        )

    def test_setup_devkit_share_marks_noninteractive_bootstrap(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.install._detect_routed_host_ip", return_value=("10.0.0.76", "en0", [("en0", "10.0.0.76")])), \
                 patch("sima_cli.sdk.install._print_devkit_nfs_banner"), \
                 patch("sima_cli.sdk.install._configure_nfs_export"), \
                 patch("sima_cli.sdk.install._detect_existing_linux_nfs_export", return_value=None), \
                 patch("sima_cli.sdk.install._configure_devkit_shared_network_for_setup"):
                env = _setup_devkit_share(
                    "10.0.0.20",
                    tmpdir,
                    ["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"],
                    noninteractive=True,
                )

            self.assertEqual(env["devkit_ip"], "10.0.0.20")
            self.assertFalse(env["bootstrap_interactive"])
            self.assertTrue(env["noninteractive"])

    def test_parse_export_line_reads_clients_and_options(self):
        exports = _parse_export_line(
            "/scratch/srv/nfs/share 192.168.0.0/20(rw,sync,no_subtree_check,crossmnt) *(ro)"
        )

        self.assertEqual(len(exports), 2)
        self.assertEqual(exports[0].path, Path("/scratch/srv/nfs/share"))
        self.assertEqual(exports[0].client, "192.168.0.0/20")
        self.assertIn("crossmnt", exports[0].options)
        self.assertEqual(exports[1].client, "*")

    def test_detect_existing_linux_nfs_export_uses_pseudo_root_path(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "scratch" / "srv" / "nfs"
            share = root / "share"
            workspace = share / "workspace"
            workspace.mkdir(parents=True)
            exports = [
                ParsedNfsExport(root, "192.168.0.0/20", ("rw", "sync", "crossmnt", "fsid=0")),
                ParsedNfsExport(share, "192.168.0.0/20", ("rw", "sync", "crossmnt")),
            ]

            with patch("sima_cli.sdk.install.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.install._read_linux_exports", return_value=exports):
                detected = _detect_existing_linux_nfs_export(workspace, "192.168.4.20", "192.168.1.10")

        self.assertIsNotNone(detected)
        self.assertEqual(detected.server, "192.168.1.10")
        self.assertEqual(detected.local_export_path, str(share.resolve()))
        self.assertEqual(detected.export_path, "/share/workspace")

    def test_setup_devkit_share_reuses_existing_linux_export(self):
        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "share" / "workspace"
            workspace.mkdir(parents=True)
            exports = [
                ParsedNfsExport(Path(tmpdir), "192.168.0.0/20", ("rw", "sync", "crossmnt", "fsid=0")),
                ParsedNfsExport(Path(tmpdir) / "share", "192.168.0.0/20", ("rw", "sync", "crossmnt")),
            ]

            with patch("sima_cli.sdk.install._detect_routed_host_ip", return_value=("192.168.1.10", "eno1", [("eno1", "192.168.1.10")])), \
                 patch("sima_cli.sdk.install.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.install._read_linux_exports", return_value=exports), \
                 patch("sima_cli.sdk.install._print_devkit_nfs_banner") as banner, \
                 patch("sima_cli.sdk.install._configure_nfs_export") as configure_export, \
                 patch("sima_cli.sdk.install._configure_devkit_shared_network_for_setup") as configure_network:
                env = _setup_devkit_share(
                    "192.168.4.20",
                    str(workspace),
                    ["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"],
                    noninteractive=True,
                )

        banner.assert_not_called()
        configure_export.assert_not_called()
        configure_network.assert_called_once_with("192.168.4.20", noninteractive=True, persistent_network_profile=False)
        self.assertEqual(env["host_ip"], "192.168.1.10")
        self.assertEqual(env["workspace"], "/share/workspace")
        self.assertFalse(env["bootstrap_interactive"])

    def test_setup_devkit_share_fails_when_existing_export_blocks_devkit_ip(self):
        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "share" / "workspace"
            workspace.mkdir(parents=True)
            exports = [
                ParsedNfsExport(Path(tmpdir), "192.168.0.0/20", ("rw", "sync", "crossmnt", "fsid=0")),
                ParsedNfsExport(Path(tmpdir) / "share", "192.168.0.0/20", ("rw", "sync", "crossmnt")),
            ]

            with patch("sima_cli.sdk.install._detect_routed_host_ip", return_value=("192.168.1.10", "eno1", [("eno1", "192.168.1.10")])), \
                 patch("sima_cli.sdk.install.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.install._read_linux_exports", return_value=exports), \
                 patch("sima_cli.sdk.install._configure_nfs_export") as configure_export, \
                 patch("sima_cli.sdk.install._configure_devkit_shared_network_for_setup") as configure_network:
                with self.assertRaisesRegex(RuntimeError, "not allowed by the export client"):
                    _setup_devkit_share(
                        "192.168.135.40",
                        str(workspace),
                        ["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"],
                    )

        configure_export.assert_not_called()
        configure_network.assert_not_called()

    def test_setup_devkit_share_updates_stale_managed_export_for_new_devkit_ip(self):
        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "share" / "workspace"
            workspace.mkdir(parents=True)
            exports = [
                ParsedNfsExport(
                    Path(tmpdir) / "share",
                    "192.168.2.101",
                    ("rw", "sync", "no_subtree_check", "no_root_squash", "insecure"),
                    source=LINUX_NEAT_EXPORTS_PATH,
                ),
            ]

            with patch("sima_cli.sdk.install._detect_routed_host_ip", return_value=("192.168.2.10", "eno1", [("eno1", "192.168.2.10")])), \
                 patch("sima_cli.sdk.install.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.install._read_linux_exports", return_value=exports), \
                 patch("sima_cli.sdk.install._print_devkit_nfs_banner") as banner, \
                 patch("sima_cli.sdk.install._configure_nfs_export") as configure_export, \
                 patch("sima_cli.sdk.install._configure_devkit_shared_network_for_setup") as configure_network:
                env = _setup_devkit_share(
                    "192.168.2.100",
                    str(workspace),
                    ["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"],
                    noninteractive=True,
                )

        banner.assert_called_once_with(str(workspace), "192.168.2.100", "linux")
        configure_export.assert_called_once_with(workspace, "192.168.2.100", "linux", "192.168.2.10")
        configure_network.assert_called_once_with("192.168.2.100", noninteractive=True, persistent_network_profile=False)
        self.assertEqual(env["host_ip"], "192.168.2.10")
        self.assertEqual(env["workspace"], str(workspace))
        self.assertFalse(env["bootstrap_interactive"])

    def test_setup_devkit_share_fails_when_mixed_managed_and_unmanaged_exports_block_devkit_ip(self):
        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "share" / "workspace"
            workspace.mkdir(parents=True)
            exports = [
                ParsedNfsExport(
                    Path(tmpdir),
                    "192.168.2.101",
                    ("rw", "sync", "crossmnt", "fsid=0"),
                    source=Path("/etc/exports"),
                ),
                ParsedNfsExport(
                    Path(tmpdir) / "share",
                    "192.168.2.101",
                    ("rw", "sync", "no_subtree_check", "no_root_squash", "insecure"),
                    source=LINUX_NEAT_EXPORTS_PATH,
                ),
            ]

            with patch("sima_cli.sdk.install._detect_routed_host_ip", return_value=("192.168.2.10", "eno1", [("eno1", "192.168.2.10")])), \
                 patch("sima_cli.sdk.install.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.install._read_linux_exports", return_value=exports), \
                 patch("sima_cli.sdk.install._configure_nfs_export") as configure_export, \
                 patch("sima_cli.sdk.install._configure_devkit_shared_network_for_setup") as configure_network:
                with self.assertRaisesRegex(RuntimeError, "existing unmanaged NFS export"):
                    _setup_devkit_share(
                        "192.168.2.100",
                        str(workspace),
                        ["ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"],
                    )

        configure_export.assert_not_called()
        configure_network.assert_not_called()

    def test_detect_host_ip_uses_routable_non_vpn_candidate(self):
        candidates = [("en0", "192.168.1.10"), ("feth0", "10.10.1.2")]
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=candidates), \
             patch("sima_cli.sdk.install._routed_ipv4_for_target", return_value="10.10.1.2"):
            host_ip, iface, all_candidates = _detect_host_ip("10.10.1.20")

        self.assertEqual(host_ip, "10.10.1.2")
        self.assertEqual(iface, "feth0")
        self.assertEqual(all_candidates, candidates)

    def test_detect_host_ip_allows_routable_link_local_candidate(self):
        candidates = [("en7", "169.254.10.20")]
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=candidates), \
             patch("sima_cli.sdk.install._routed_ipv4_for_target", return_value="169.254.10.20"):
            host_ip, iface, all_candidates = _detect_host_ip("169.254.10.30")

        self.assertEqual(host_ip, "169.254.10.20")
        self.assertEqual(iface, "en7")
        self.assertEqual(all_candidates, candidates)

    def test_detect_host_ip_falls_back_when_routed_ip_is_not_supported(self):
        candidates = [("en0", "192.168.1.10")]
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=candidates), \
             patch("sima_cli.sdk.install._routed_ipv4_for_target", return_value="100.64.1.2"), \
             patch("builtins.print") as mock_print:
            host_ip, iface, all_candidates = _detect_host_ip("10.10.1.20")

        self.assertEqual(host_ip, "192.168.1.10")
        self.assertEqual(iface, "en0")
        self.assertEqual(all_candidates, candidates)
        self.assertTrue(any("Ignoring it for DevKit sync" in call.args[0] for call in mock_print.call_args_list))

    def test_detect_host_ip_fails_when_routed_ip_is_not_supported_and_no_candidates_exist(self):
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=[]), \
             patch("sima_cli.sdk.install._routed_ipv4_for_target", return_value="100.64.1.2"):
            with self.assertRaisesRegex(RuntimeError, "no supported non-VPN host interface"):
                _detect_host_ip("10.10.1.20")

    def test_detect_routed_host_ip_matches_candidate_interface(self):
        candidates = [("en0", "192.168.1.10"), ("eth1", "10.10.1.2")]
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=candidates), \
             patch("sima_cli.sdk.install._routed_ipv4_for_target", return_value="10.10.1.2"):
            host_ip, iface, all_candidates = _detect_routed_host_ip("10.10.1.20")

        self.assertEqual(host_ip, "10.10.1.2")
        self.assertEqual(iface, "eth1")
        self.assertEqual(all_candidates, candidates)

    def test_detect_routed_host_ip_trusts_routed_ip_not_in_candidates(self):
        # Unlike _detect_host_ip, the routed IP is kept even when it is not a
        # known candidate; the interface is reported as "routed".
        candidates = [("en0", "192.168.1.10")]
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=candidates), \
             patch("sima_cli.sdk.install._routed_ipv4_for_target", return_value="100.64.1.2"):
            host_ip, iface, all_candidates = _detect_routed_host_ip("10.10.1.20")

        self.assertEqual(host_ip, "100.64.1.2")
        self.assertEqual(iface, "routed")
        self.assertEqual(all_candidates, candidates)

    def test_detect_routed_host_ip_falls_back_to_first_candidate_without_route(self):
        candidates = [("en0", "192.168.1.10"), ("eth1", "10.10.1.2")]
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=candidates), \
             patch("sima_cli.sdk.install._routed_ipv4_for_target", return_value=""):
            host_ip, iface, all_candidates = _detect_routed_host_ip("10.10.1.20")

        self.assertEqual(host_ip, "192.168.1.10")
        self.assertEqual(iface, "en0")
        self.assertEqual(all_candidates, candidates)

    def test_detect_routed_host_ip_uses_socket_fallback_when_nothing_found(self):
        fake_sock = Mock()
        fake_sock.getsockname.return_value = ("172.16.0.5", 12345)
        with patch("sima_cli.sdk.install._detect_local_ip_candidates", return_value=[]), \
             patch("sima_cli.sdk.install.socket.socket", return_value=fake_sock):
            host_ip, iface, all_candidates = _detect_routed_host_ip(None)

        self.assertEqual(host_ip, "172.16.0.5")
        self.assertEqual(iface, "auto")
        self.assertEqual(all_candidates, [])
        fake_sock.connect.assert_called_once_with(("8.8.8.8", 80))
        fake_sock.close.assert_called_once()

    def test_detect_local_ip_candidates_keeps_physical_link_local(self):
        with patch("sima_cli.sdk.install.sys.platform", "darwin"), \
             patch("sima_cli.sdk.install.get_local_ip_candidates", return_value=[("en0", "192.168.1.10")]), \
             patch("sima_cli.sdk.install._detect_physical_ipv4s_macos", return_value=[("en7", "169.254.10.20")]):
            candidates = _detect_local_ip_candidates()

        self.assertIn(("en0", "192.168.1.10"), candidates)
        self.assertIn(("en7", "169.254.10.20"), candidates)

    def test_linux_nfs_export_cleans_duplicate_workspace_entries(self):
        with TemporaryDirectory() as tmpdir:
            with patch(
                "sima_cli.sdk.install._find_executable",
                side_effect=lambda name: {
                    "exportfs": "/usr/sbin/exportfs",
                    "systemctl": "/usr/bin/systemctl",
                }.get(name),
            ), patch("sima_cli.sdk.install.subprocess.run") as run:
                _configure_nfs_export(Path(tmpdir), "10.0.0.244", "linux", "10.0.0.1")

        run.assert_called_once()
        cmd = run.call_args[0][0]
        script = cmd[-1]
        self.assertIn("clean_exports_file /etc/exports", script)
        self.assertIn("for f in /etc/exports.d/*", script)
        self.assertIn("/etc/exports.d/neat-sdk.exports", script)
        self.assertIn("10.0.0.244(rw,sync,no_subtree_check,no_root_squash,insecure)", script)

    def test_macos_nfs_export_allows_failed_restart_when_nfsd_is_running(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.install.subprocess.run") as run:
                _configure_nfs_export(Path(tmpdir), "192.168.1.20", "darwin", "192.168.1.10")

        run.assert_called_once()
        cmd = run.call_args[0][0]
        script = cmd[-1]
        self.assertIn("nfsd checkexports", script)
        self.assertIn("if ! nfsd restart; then", script)
        self.assertIn("nfsd status | grep -q 'nfsd is running'", script)
        self.assertIn("192.168.1.20", script)

    def test_parse_route_iface_and_source(self):
        iface, src = _parse_route_iface_and_source(
            "10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1 uid 1000"
        )

        self.assertEqual(iface, "enx6c1ff720d573")
        self.assertEqual(src, "10.42.0.1")

    def test_configure_nm_shared_forwarding_inserts_sdk_bridge_allow_rule(self):
        docker_inspect = """[
          {
            "Id": "ca007b7ec8f512345678",
            "Options": {},
            "IPAM": {"Config": [{"Subnet": "172.23.0.0/16"}]}
          }
        ]"""
        nft_chain = """
table ip nm-shared-enx6c1ff720d573 {
  chain filter_forward {
    type filter hook forward priority filter; policy accept;
    ct state { established, related } accept
    oifname "enx6c1ff720d573" reject
  }
}
"""
        nft_nat_chain = """
table ip nm-shared-enx6c1ff720d573 {
  chain nat_postrouting {
    type nat hook postrouting priority srcnat; policy accept;
  }
}
"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:5] == ["/usr/bin/ip", "-o", "-4", "addr", "show"]:
                return Mock(returncode=0, stdout="2: enx6c1ff720d573 inet 10.42.0.1/24 brd 10.42.0.255 scope global enx6c1ff720d573\n", stderr="")
            if cmd[:4] == ["/usr/bin/docker", "network", "inspect", "simasdkbridge"]:
                return Mock(returncode=0, stdout=docker_inspect, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout=nft_chain, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "nat_postrouting"]:
                return Mock(returncode=0, stdout=nft_nat_chain, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "insert", "rule", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "insert", "rule", "ip", "nm-shared-enx6c1ff720d573", "nat_postrouting"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/iptables", "-t", "nat"]:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "docker": "/usr/bin/docker",
                     "nft": "/usr/sbin/nft",
                     "iptables": "/usr/sbin/iptables",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _configure_nm_shared_devkit_forwarding("10.42.0.175")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/nft",
                "insert",
                "rule",
                "ip",
                "nm-shared-enx6c1ff720d573",
                "filter_forward",
                "iifname",
                "br-ca007b7ec8f5",
                "oifname",
                "enx6c1ff720d573",
                "ip",
                "saddr",
                "172.23.0.0/16",
                "ip",
                "daddr",
                "10.42.0.0/24",
                "accept",
            ],
            commands,
        )
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/nft",
                "insert",
                "rule",
                "ip",
                "nm-shared-enx6c1ff720d573",
                "nat_postrouting",
                "ip",
                "saddr",
                "172.23.0.0/16",
                "oifname",
                "enx6c1ff720d573",
                "masquerade",
            ],
            commands,
        )
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/nft",
                "insert",
                "rule",
                "ip",
                "nm-shared-enx6c1ff720d573",
                "filter_forward",
                "iifname",
                "enx6c1ff720d573",
                "oifname",
                "br-ca007b7ec8f5",
                "ip",
                "daddr",
                "172.23.0.0/16",
                "ct",
                "state",
                "related,established",
                "accept",
            ],
            commands,
        )

    def test_configure_nm_shared_forwarding_is_idempotent(self):
        docker_inspect = """[
          {
            "Id": "ca007b7ec8f512345678",
            "Options": {},
            "IPAM": {"Config": [{"Subnet": "172.23.0.0/16"}]}
          }
        ]"""
        nft_chain = """
table ip nm-shared-enx6c1ff720d573 {
  chain filter_forward {
    iifname "br-ca007b7ec8f5" oifname "enx6c1ff720d573" ip saddr 172.23.0.0/16 ip daddr 10.42.0.0/24 accept
    iifname "enx6c1ff720d573" oifname "br-ca007b7ec8f5" ip daddr 172.23.0.0/16 ct state established,related accept
    oifname "enx6c1ff720d573" reject
  }
}
"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:5] == ["/usr/bin/ip", "-o", "-4", "addr", "show"]:
                return Mock(returncode=0, stdout="2: enx6c1ff720d573 inet 10.42.0.1/24 brd 10.42.0.255 scope global enx6c1ff720d573\n", stderr="")
            if cmd[:4] == ["/usr/bin/docker", "network", "inspect", "simasdkbridge"]:
                return Mock(returncode=0, stdout=docker_inspect, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout=nft_chain, stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/iptables", "-t", "nat"]:
                return Mock(returncode=0, stdout="-A POSTROUTING -s 172.23.0.0/16 ! -o br-ca007b7ec8f5 -j MASQUERADE\n", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "docker": "/usr/bin/docker",
                     "nft": "/usr/sbin/nft",
                     "iptables": "/usr/sbin/iptables",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _configure_nm_shared_devkit_forwarding("10.42.0.175")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(cmd[:3] == ["sudo", "/usr/sbin/nft", "insert"] for cmd in commands))

    def test_configure_nm_shared_forwarding_uses_iptables_nat_fallback(self):
        docker_inspect = """[
          {
            "Id": "ca007b7ec8f512345678",
            "Options": {},
            "IPAM": {"Config": [{"Subnet": "172.23.0.0/16"}]}
          }
        ]"""
        nft_chain = """
table ip nm-shared-enx6c1ff720d573 {
  chain filter_forward {
    oifname "enx6c1ff720d573" reject
  }
}
"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:5] == ["/usr/bin/ip", "-o", "-4", "addr", "show"]:
                return Mock(returncode=0, stdout="2: enx6c1ff720d573 inet 10.42.0.1/24 brd 10.42.0.255 scope global enx6c1ff720d573\n", stderr="")
            if cmd[:4] == ["/usr/bin/docker", "network", "inspect", "simasdkbridge"]:
                return Mock(returncode=0, stdout=docker_inspect, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout=nft_chain, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "nat_postrouting"]:
                return Mock(returncode=1, stdout="", stderr="no such chain")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "insert", "rule", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd == ["sudo", "/usr/sbin/iptables", "-t", "nat", "-S", "POSTROUTING"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:6] == ["sudo", "/usr/sbin/iptables", "-t", "nat", "-C", "POSTROUTING"]:
                return Mock(returncode=1, stdout="", stderr="missing")
            if cmd[:6] == ["sudo", "/usr/sbin/iptables", "-t", "nat", "-I", "POSTROUTING"]:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "docker": "/usr/bin/docker",
                     "nft": "/usr/sbin/nft",
                     "iptables": "/usr/sbin/iptables",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _configure_nm_shared_devkit_forwarding("10.42.0.175")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/iptables",
                "-t",
                "nat",
                "-I",
                "POSTROUTING",
                "1",
                "-s",
                "172.23.0.0/16",
                "-o",
                "enx6c1ff720d573",
                "-j",
                "MASQUERADE",
            ],
            commands,
        )

    def test_configure_nm_shared_forwarding_skips_wsl(self):
        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.linux_shared_network._is_wsl", return_value=True), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run") as run:
            applied = _configure_nm_shared_devkit_forwarding("10.42.0.175")

        self.assertFalse(applied)
        run.assert_not_called()

    def test_configure_nm_shared_devkit_internet_adds_forwarding_and_nat(self):
        nft_chain = """
table ip nm-shared-enx6c1ff720d573 {
  chain filter_forward {
    oifname "enx6c1ff720d573" reject
  }
}
"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"] and cmd[-1] == "10.42.0.175":
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"] and cmd[-1] == "8.8.8.8":
                return Mock(returncode=0, stdout="8.8.8.8 via 192.168.86.1 dev enp6s0 src 192.168.86.42\n", stderr="")
            if cmd[:5] == ["/usr/bin/ip", "-o", "-4", "addr", "show"]:
                return Mock(returncode=0, stdout="2: enx6c1ff720d573 inet 10.42.0.1/24 brd 10.42.0.255 scope global enx6c1ff720d573\n", stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout=nft_chain, stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/sysctl", "-w", "net.ipv4.ip_forward=1"]:
                return Mock(returncode=0, stdout="net.ipv4.ip_forward = 1\n", stderr="")
            if cmd[:3] == ["sudo", "/usr/sbin/iptables", "-C"]:
                return Mock(returncode=1, stdout="", stderr="missing")
            if cmd[:4] == ["sudo", "/usr/sbin/iptables", "-t", "nat"] and "-C" in cmd:
                return Mock(returncode=1, stdout="", stderr="missing")
            if cmd[:3] == ["sudo", "/usr/sbin/iptables", "-I"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/iptables", "-t", "nat"] and "-I" in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "nft": "/usr/sbin/nft",
                     "iptables": "/usr/sbin/iptables",
                     "sysctl": "/usr/sbin/sysctl",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _configure_nm_shared_devkit_internet("10.42.0.175")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/iptables",
                "-I",
                "FORWARD",
                "1",
                "-i",
                "enx6c1ff720d573",
                "-o",
                "enp6s0",
                "-s",
                "10.42.0.0/24",
                "-j",
                "ACCEPT",
            ],
            commands,
        )
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/iptables",
                "-I",
                "FORWARD",
                "2",
                "-i",
                "enp6s0",
                "-o",
                "enx6c1ff720d573",
                "-d",
                "10.42.0.0/24",
                "-m",
                "conntrack",
                "--ctstate",
                "RELATED,ESTABLISHED",
                "-j",
                "ACCEPT",
            ],
            commands,
        )
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/iptables",
                "-t",
                "nat",
                "-I",
                "POSTROUTING",
                "1",
                "-s",
                "10.42.0.0/24",
                "-o",
                "enp6s0",
                "-j",
                "MASQUERADE",
            ],
            commands,
        )

    def test_configure_nm_shared_devkit_internet_is_idempotent(self):
        nft_chain = """
table ip nm-shared-enx6c1ff720d573 {
  chain filter_forward {
    oifname "enx6c1ff720d573" reject
  }
}
"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"] and cmd[-1] == "10.42.0.175":
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"] and cmd[-1] == "8.8.8.8":
                return Mock(returncode=0, stdout="8.8.8.8 via 192.168.86.1 dev enp6s0 src 192.168.86.42\n", stderr="")
            if cmd[:5] == ["/usr/bin/ip", "-o", "-4", "addr", "show"]:
                return Mock(returncode=0, stdout="2: enx6c1ff720d573 inet 10.42.0.1/24 brd 10.42.0.255 scope global enx6c1ff720d573\n", stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout=nft_chain, stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/sysctl", "-w", "net.ipv4.ip_forward=1"]:
                return Mock(returncode=0, stdout="net.ipv4.ip_forward = 1\n", stderr="")
            if cmd[:3] == ["sudo", "/usr/sbin/iptables", "-C"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/iptables", "-t", "nat"] and "-C" in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "nft": "/usr/sbin/nft",
                     "iptables": "/usr/sbin/iptables",
                     "sysctl": "/usr/sbin/sysctl",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _configure_nm_shared_devkit_internet("10.42.0.175")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(cmd[:3] == ["sudo", "/usr/sbin/iptables", "-I"] for cmd in commands))
        self.assertFalse(any(cmd[:5] == ["sudo", "/usr/sbin/iptables", "-t", "nat", "-I"] for cmd in commands))

    def test_configure_nm_shared_devkit_internet_skips_wsl(self):
        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.linux_shared_network._is_wsl", return_value=True), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run") as run:
            applied = _configure_nm_shared_devkit_internet("10.42.0.175")

        self.assertFalse(applied)
        run.assert_not_called()

    def test_configure_nm_shared_devkit_ipv6_internet_adds_forwarding_and_nat(self):
        nft_chain = """
table ip nm-shared-enx6c1ff720d573 {
  chain filter_forward {
    oifname "enx6c1ff720d573" reject
  }
}
"""
        nft6_chain = """
table ip6 nm-shared-enx6c1ff720d573 {
  chain filter_forward {
    oifname "enx6c1ff720d573" reject
  }
}
"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:6] == ["/usr/bin/ip", "-o", "-6", "route", "show", "default"]:
                return Mock(returncode=0, stdout="default via fe80::1 dev enp6s0 proto ra metric 100 pref medium\n", stderr="")
            if cmd[:7] == ["/usr/bin/ip", "-o", "-6", "addr", "show", "dev", "enx6c1ff720d573"]:
                return Mock(returncode=0, stdout="2: enx6c1ff720d573 inet6 fd42:42::1/64 scope global\n", stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout=nft_chain, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip6", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout=nft6_chain, stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "insert", "rule", "ip6", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/sysctl", "-w", "net.ipv6.conf.all.forwarding=1"]:
                return Mock(returncode=0, stdout="net.ipv6.conf.all.forwarding = 1\n", stderr="")
            if cmd[:3] == ["sudo", "/usr/sbin/ip6tables", "-C"]:
                return Mock(returncode=1, stdout="", stderr="missing")
            if cmd[:4] == ["sudo", "/usr/sbin/ip6tables", "-t", "nat"] and "-C" in cmd:
                return Mock(returncode=1, stdout="", stderr="missing")
            if cmd[:3] == ["sudo", "/usr/sbin/ip6tables", "-I"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:4] == ["sudo", "/usr/sbin/ip6tables", "-t", "nat"] and "-I" in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "nft": "/usr/sbin/nft",
                     "ip6tables": "/usr/sbin/ip6tables",
                     "sysctl": "/usr/sbin/sysctl",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _configure_nm_shared_devkit_ipv6_internet("10.42.0.175")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "sudo",
                "/usr/sbin/ip6tables",
                "-t",
                "nat",
                "-I",
                "POSTROUTING",
                "1",
                "-s",
                "fd42:42::/64",
                "-o",
                "enp6s0",
                "-j",
                "MASQUERADE",
            ],
            commands,
        )
        self.assertTrue(any(cmd[:7] == ["sudo", "/usr/sbin/nft", "insert", "rule", "ip6", "nm-shared-enx6c1ff720d573", "filter_forward"] for cmd in commands))

    def test_configure_nm_shared_devkit_ipv6_internet_skips_without_host_ipv6(self):
        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:6] == ["/usr/bin/ip", "-o", "-6", "route", "show", "default"]:
                return Mock(returncode=1, stdout="", stderr="network unreachable")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout='oifname "enx6c1ff720d573" reject\n', stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "nft": "/usr/sbin/nft",
                     "ip6tables": "/usr/sbin/ip6tables",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _configure_nm_shared_devkit_ipv6_internet("10.42.0.175")

        self.assertFalse(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(cmd[:2] == ["sudo", "/usr/sbin/ip6tables"] for cmd in commands))

    def test_disable_nm_shared_devkit_ipv6_modifies_host_connection(self):
        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["/usr/bin/ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.175 dev enx6c1ff720d573 src 10.42.0.1\n", stderr="")
            if cmd[:7] == ["sudo", "/usr/sbin/nft", "list", "chain", "ip", "nm-shared-enx6c1ff720d573", "filter_forward"]:
                return Mock(returncode=0, stdout='oifname "enx6c1ff720d573" reject\n', stderr="")
            if cmd[:5] == ["/usr/bin/nmcli", "-g", "GENERAL.CONNECTION", "device", "show"]:
                return Mock(returncode=0, stdout="Wired shared connection\n", stderr="")
            if cmd[:5] == ["sudo", "/usr/bin/nmcli", "connection", "modify", "Wired shared connection"]:
                return Mock(returncode=0, stdout="", stderr="")
            if cmd[:5] == ["sudo", "/usr/bin/nmcli", "device", "reapply", "enx6c1ff720d573"]:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch(
                 "sima_cli.sdk.linux_shared_network._find_executable",
                 side_effect=lambda name: {
                     "ip": "/usr/bin/ip",
                     "nft": "/usr/sbin/nft",
                     "nmcli": "/usr/bin/nmcli",
                 }.get(name),
             ), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = _disable_nm_shared_devkit_ipv6("10.42.0.175")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "sudo",
                "/usr/bin/nmcli",
                "connection",
                "modify",
                "Wired shared connection",
                "ipv6.method",
                "ignore",
            ],
            commands,
        )

    def test_launch_sdk_tool_uses_attach_shell_path_when_no_command(self):
        with patch("sima_cli.sdk.commands.exec_container_cmd") as exec_container_cmd:
            launch_sdk_tool("neat", (), ctx=None)

        exec_container_cmd.assert_called_once_with(None, "neat", None)

    def test_launch_sdk_tool_preserves_explicit_command(self):
        with patch("sima_cli.sdk.commands.exec_container_cmd") as exec_container_cmd:
            launch_sdk_tool("neat", ("echo", "hello"), ctx=None)

        exec_container_cmd.assert_called_once_with(None, "neat", "echo hello")

    def test_launch_sdk_tool_warns_for_legacy_palette_sdks(self):
        with patch("sima_cli.sdk.commands.console.print") as console_print, \
             patch("sima_cli.sdk.commands.should_show_post_neat_ga_deprecation_notice", return_value=True), \
             patch("sima_cli.sdk.commands.exec_container_cmd"):
            for tool in ("elxr", "model", "yocto", "mpk"):
                with self.subTest(tool=tool):
                    console_print.reset_mock()

                    launch_sdk_tool(tool, (), ctx=None)

                    console_print.assert_called_once()
                    panel = console_print.call_args.args[0]
                    self.assertEqual(panel.title, "Legacy Palette SDK")
                    self.assertEqual(panel.border_style, "yellow")
                    self.assertIn("Palette Neat", panel.renderable)
                    self.assertIn("https://community.sima.ai", panel.renderable)

    def test_launch_sdk_tool_suppresses_legacy_palette_warning_before_neat_ga(self):
        with patch("sima_cli.sdk.commands.console.print") as console_print, \
             patch("sima_cli.sdk.commands.should_show_post_neat_ga_deprecation_notice", return_value=False), \
             patch("sima_cli.sdk.commands.exec_container_cmd") as exec_container_cmd:
            launch_sdk_tool("mpk", (), ctx=None)

        console_print.assert_not_called()
        exec_container_cmd.assert_called_once_with(None, "mpk", None)

    def test_launch_sdk_tool_does_not_warn_for_neat_sdk(self):
        with patch("sima_cli.sdk.commands.console.print") as console_print, \
             patch("sima_cli.sdk.commands.should_show_post_neat_ga_deprecation_notice", return_value=True), \
             patch("sima_cli.sdk.commands.exec_container_cmd"):
            launch_sdk_tool("neat", (), ctx=None)

        console_print.assert_not_called()

    def test_neat_starts_stopped_container_when_unavailable(self):
        stopped_container = {
            "Names": "ghcr.io-sima-neat-sdk-v2.1.2",
            "Image": "ghcr.io/sima/neat-sdk:v2.1.2",
            "State": "exited",
            "Status": "Exited (0) 1 minute ago",
        }
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch(
                 "sima_cli.sdk.commands.exec_container_cmd",
                 side_effect=[SdkContainerUnavailable("missing"), None],
             ) as exec_cmd, \
             patch("sima_cli.sdk.commands.get_all_containers", return_value=[stopped_container]), \
             patch("sima_cli.sdk.commands.ensure_existing_neat_container_startable") as ensure_startable, \
             patch("sima_cli.sdk.commands.subprocess.run") as run:
            result = CliRunner().invoke(sdk, ["neat"])

        self.assertEqual(result.exit_code, 0, result.output)
        ensure_startable.assert_called_once_with("ghcr.io-sima-neat-sdk-v2.1.2")
        self.assertIn("Starting existing Neat SDK container", result.output)
        run.assert_called_once_with(
            ["docker", "start", "ghcr.io-sima-neat-sdk-v2.1.2"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(exec_cmd.call_count, 2)
        exec_cmd.assert_any_call(ANY, "neat", None, raise_on_missing=True)
        exec_cmd.assert_any_call(ANY, "neat", None)

    def test_neat_starts_only_version_matching_stopped_containers(self):
        containers = [
            {
                "Names": "ghcr.io-sima-neat-sdk-v2.1.2",
                "Image": "ghcr.io/sima/neat-sdk:v2.1.2",
                "State": "exited",
                "Status": "Exited (0) 1 minute ago",
            },
            {
                "Names": "ghcr.io-sima-neat-sdk-v2.1.3",
                "Image": "ghcr.io/sima/neat-sdk:v2.1.3",
                "State": "exited",
                "Status": "Exited (0) 1 minute ago",
            },
        ]
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch(
                 "sima_cli.sdk.commands.exec_container_cmd",
                 side_effect=[SdkContainerUnavailable("missing"), None],
             ), \
             patch("sima_cli.sdk.commands.get_all_containers", return_value=containers), \
             patch("sima_cli.sdk.commands.ensure_existing_neat_container_startable"), \
             patch("sima_cli.sdk.commands.subprocess.run") as run:
            result = CliRunner().invoke(sdk, ["-v", "v2.1.2", "neat"])

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            ["docker", "start", "ghcr.io-sima-neat-sdk-v2.1.2"],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_neat_reports_setup_when_no_stopped_container_exists(self):
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch(
                 "sima_cli.sdk.commands.exec_container_cmd",
                 side_effect=SdkContainerUnavailable("missing"),
             ), \
             patch("sima_cli.sdk.commands.get_all_containers", return_value=[]), \
             patch("sima_cli.sdk.commands.subprocess.run") as run:
            result = CliRunner().invoke(sdk, ["neat"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No stopped Neat SDK containers found", result.output)
        self.assertIn("sima-cli sdk setup", result.output)
        run.assert_not_called()

    def test_neat_preserves_passthrough_yes_flag(self):
        with patch("sima_cli.sdk.commands.check_and_start_docker"), \
             patch("sima_cli.sdk.commands.launch_sdk_tool") as launch:
            result = CliRunner().invoke(sdk, ["neat", "python", "script.py", "-y"])

        self.assertEqual(result.exit_code, 0, result.output)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], "neat")
        self.assertEqual(launch.call_args.args[1], ("python", "script.py", "-y"))
        self.assertTrue(launch.call_args.kwargs["recover_unavailable"])

    def test_setup_help_includes_model_compiler_flag_and_legacy_alias(self):
        with patch("sima_cli.sdk.commands.check_and_start_docker"):
            result = CliRunner().invoke(sdk, ["setup", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--no-model-compiler", result.output)
        self.assertIn("--no-model-sdk", result.output)

    def test_setup_accepts_no_model_compiler_aliases(self):
        for flag in ("--no-model-compiler", "--no-model-sdk"):
            with self.subTest(flag=flag), \
                 patch("sima_cli.sdk.commands.check_and_start_docker"), \
                 patch("sima_cli.sdk.commands.setup_and_start") as setup_start:
                result = CliRunner().invoke(sdk, ["setup", flag, "-y", "-n"])

                self.assertEqual(result.exit_code, 0)
                self.assertTrue(setup_start.call_args.kwargs["no_model_sdk"])

    def test_neat_port_allocator_uses_defaults_when_available(self):
        with patch("sima_cli.sdk.neat._is_port_available", return_value=True):
            port_map, port_args = allocate_neat_ports()

        self.assertEqual(port_map["mainUI"]["host"], 9900)
        self.assertEqual(port_map["codeUI"]["host"], 9999)
        self.assertEqual(port_map["videoUI"]["host"], 8081)
        self.assertEqual(port_map["webSSH"]["host"], 8022)
        self.assertEqual(port_map["rtsp"]["tcp"]["host"], 8554)
        self.assertNotIn("udp", port_map["rtsp"])
        self.assertEqual(port_map["videoUDP"]["hostStart"], 9000)
        self.assertEqual(port_map["videoUDP"]["hostEnd"], 9079)
        self.assertEqual(port_map["metadataUDP"]["hostStart"], 9100)
        self.assertEqual(port_map["metadataUDP"]["hostEnd"], 9179)
        self.assertEqual(port_map["webRTC"]["hostStart"], 40000)
        self.assertEqual(port_map["webRTC"]["hostEnd"], 40199)
        self.assertIn("8022:8022/tcp", port_args)
        self.assertIn("9999:9999/tcp", port_args)
        self.assertIn("9000-9079:9000-9079/udp", port_args)
        self.assertIn("9100-9179:9100-9179/udp", port_args)
        self.assertIn("40000-40199:40000-40199/udp", port_args)

    def test_neat_port_allocator_no_insight_skips_insight_ports(self):
        with patch("sima_cli.sdk.neat._is_port_available", return_value=True):
            port_map, port_args = allocate_neat_ports(no_insight=True)

        self.assertNotIn("mainUI", port_map)
        self.assertNotIn("codeUI", port_map)
        self.assertNotIn("videoUI", port_map)
        self.assertNotIn("videoUDP", port_map)
        self.assertNotIn("metadataUDP", port_map)
        self.assertNotIn("webRTC", port_map)
        self.assertNotIn("rtsp", port_map)
        self.assertNotIn("webSSH", port_map)
        self.assertNotIn("cert", port_map)
        self.assertEqual(port_args, [])
        for mapping in port_args:
            self.assertNotIn("8022", mapping)
            self.assertNotIn("8554", mapping)
            self.assertNotIn("9900", mapping)
            self.assertNotIn("9999", mapping)
            self.assertNotIn("8081", mapping)
            self.assertNotIn("9000-9079", mapping)
            self.assertNotIn("9100-9179", mapping)
            self.assertNotIn("40000", mapping)

    def test_neat_port_allocator_moves_udp_range_as_contiguous_block(self):
        def is_available(port, protocol):
            if protocol == "udp" and port == 9006:
                return False
            return True

        with patch("sima_cli.sdk.neat._is_port_available", side_effect=is_available), \
             patch("sima_cli.sdk.neat.random.shuffle", side_effect=lambda values: None):
            port_map, port_args = allocate_neat_ports()

        self.assertEqual(port_map["videoUDP"]["hostStart"], 18000)
        self.assertEqual(port_map["videoUDP"]["hostEnd"], 18079)
        self.assertIn("18000-18079:9000-9079/udp", port_args)
        self.assertEqual(port_map["webRTC"]["hostStart"], 40000)
        self.assertIn("40000-40199:40000-40199/udp", port_args)

    def test_neat_port_allocator_respects_reserved_ports_from_failed_docker_run(self):
        reserved_ports = {
            ("udp", port) for port in range(9000, 9080)
        }

        with patch("sima_cli.sdk.neat._is_port_available", return_value=True), \
             patch("sima_cli.sdk.neat.random.shuffle", side_effect=lambda values: None):
            port_map, port_args = allocate_neat_ports(reserved_ports=reserved_ports)

        self.assertEqual(port_map["videoUDP"]["hostStart"], 18000)
        self.assertEqual(port_map["videoUDP"]["hostEnd"], 18079)
        self.assertIn("18000-18079:9000-9079/udp", port_args)

    def test_neat_port_allocator_moves_webrtc_range_past_busy_udp_port(self):
        def is_available(port, protocol):
            return not (protocol == "udp" and port == 40042)

        with patch("sima_cli.sdk.neat._is_port_available", side_effect=is_available):
            port_map, port_args = allocate_neat_ports()

        self.assertEqual(port_map["webRTC"]["hostStart"], 40043)
        self.assertEqual(port_map["webRTC"]["hostEnd"], 40242)
        self.assertEqual(port_map["webRTC"]["containerStart"], 40043)
        self.assertEqual(port_map["webRTC"]["containerEnd"], 40242)
        self.assertIn("40043-40242:40043-40242/udp", port_args)

    @unittest.skipUnless(socket.has_ipv6, "IPv6 is unavailable")
    def test_udp_port_unavailable_when_ipv6_wildcard_listener_exists(self):
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as listener:
            if hasattr(socket, "IPV6_V6ONLY"):
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.bind(("::", 0))
            port = listener.getsockname()[1]

            self.assertFalse(_is_port_available(port, "udp"))

    def test_prepare_neat_container_run_writes_port_map_and_paths(self):
        with TemporaryDirectory() as tmpdir:
            cert_file = Path(tmpdir) / ".sdk-latest" / "sdk-cert" / "neat-sdk.pem"
            key_file = Path(tmpdir) / ".sdk-latest" / "sdk-cert" / "neat-sdk-key.pem"
            with patch("sima_cli.sdk.neat._is_port_available", return_value=True), \
                 patch("sima_cli.sdk.neat._ensure_certificates", return_value=(cert_file, key_file)), \
                 patch("sima_cli.sdk.neat._detect_webrtc_host_ip", return_value="10.0.0.76"), \
                 patch.dict(os.environ, {"SIMA_CLI_HOME": str(Path(tmpdir) / ".sima-cli")}):
                config = prepare_neat_container_run(tmpdir, "sdk-latest", yes_to_all=True, noninteractive=True)
                token_cache_path = _code_ui_token_cache_path()

            port_map_path = Path(config.port_map_host_path)
            self.assertTrue(port_map_path.exists())
            self.assertTrue(token_cache_path.exists())
            self.assertEqual(token_cache_path.stat().st_mode & 0o777, 0o600)
            self.assertIn("insight-config", config.config_host_dir)
            self.assertIn("sdk-cert", config.cert_host_dir)
            self.assertEqual(config.port_map["schema"], "sima.neat.port-map.v1")
            self.assertEqual(config.port_map["cert"]["certFile"], "/sdk-cert/neat-sdk.pem")
            self.assertEqual(config.webrtc_host_ip, "10.0.0.76")
            self.assertTrue(config.code_ui_token)
            self.assertNotIn("token", config.port_map["codeUI"])
            persisted_port_map = json.loads(port_map_path.read_text(encoding="utf-8"))
            self.assertNotIn("token", persisted_port_map["codeUI"])
            token_cache = json.loads(token_cache_path.read_text(encoding="utf-8"))
            cached_code_ui = token_cache["containers"]["sdk-latest"]["codeUI"]
            self.assertEqual(cached_code_ui["host"], config.port_map["codeUI"]["host"])
            self.assertEqual(cached_code_ui["token"], config.code_ui_token)
            self.assertEqual(
                cached_code_ui["url"],
                f"http://localhost:{config.port_map['codeUI']['host']}/?tkn={config.code_ui_token}&folder=/workspace",
            )

    def test_prepare_neat_container_run_accepts_no_insight(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.neat._is_port_available", return_value=True), \
                 patch("sima_cli.sdk.neat._ensure_certificates") as certs, \
                 patch("sima_cli.sdk.neat._detect_webrtc_host_ip") as webrtc:
                config = prepare_neat_container_run(
                    tmpdir,
                    "sdk-latest",
                    yes_to_all=True,
                    noninteractive=True,
                    no_insight=True,
                )

            self.assertNotIn("mainUI", config.port_map)
            self.assertNotIn("codeUI", config.port_map)
            self.assertNotIn("videoUI", config.port_map)
            self.assertNotIn("rtsp", config.port_map)
            self.assertNotIn("webSSH", config.port_map)
            self.assertEqual(config.port_args, [])
            self.assertEqual(config.config_host_dir, "")
            self.assertEqual(config.cert_host_dir, "")
            self.assertEqual(config.port_map_host_path, "")
            certs.assert_not_called()
            webrtc.assert_not_called()

    def test_prepare_neat_container_run_minimal_skips_insight_certificates(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.neat._ensure_certificates") as certs, \
                 patch("sima_cli.sdk.neat._detect_webrtc_host_ip") as webrtc:
                config = prepare_neat_container_run(
                    tmpdir,
                    "sdk-latest",
                    yes_to_all=True,
                    noninteractive=True,
                    minimal=True,
                )

            self.assertEqual(config.port_args, [])
            self.assertEqual(config.config_host_dir, "")
            self.assertEqual(config.cert_host_dir, "")
            self.assertEqual(config.port_map_host_path, "")
            self.assertEqual(config.webrtc_host_ip, "")
            certs.assert_not_called()
            webrtc.assert_not_called()

    def test_mkcert_missing_noninteractive_accepts_default_install(self):
        with patch("sima_cli.sdk.neat.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.neat.shutil.which", side_effect=["/opt/homebrew/bin/brew", "/opt/homebrew/bin/mkcert"]), \
             patch("sima_cli.sdk.neat._run_install_command") as install, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            mkcert = _install_mkcert(yes_to_all=False, noninteractive=True)

        self.assertEqual(mkcert, "/opt/homebrew/bin/mkcert")
        install.assert_called_once_with(["brew", "install", "mkcert"])

    def test_mkcert_linux_ubuntu_2004_enables_universe_before_install(self):
        def which(name):
            return {
                "add-apt-repository": "/usr/bin/add-apt-repository",
                "mkcert": "/usr/bin/mkcert",
            }.get(name)

        with patch("sima_cli.sdk.neat.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.neat._is_wsl", return_value=False), \
             patch("sima_cli.sdk.neat._os_release_ids", return_value={"ubuntu", "debian"}), \
             patch("sima_cli.sdk.neat._os_release_value", return_value="20.04"), \
             patch("sima_cli.sdk.neat.shutil.which", side_effect=which), \
             patch("sima_cli.sdk.neat._run_install_command") as install, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            mkcert = _install_mkcert(yes_to_all=False, noninteractive=True)

        self.assertEqual(mkcert, "/usr/bin/mkcert")
        self.assertEqual(
            [call.args[0] for call in install.call_args_list],
            [
                ["sudo", "add-apt-repository", "-y", "universe"],
                ["sudo", "apt-get", "update"],
                ["sudo", "apt-get", "install", "-y", "mkcert", "libnss3-tools"],
            ],
        )

    def test_mkcert_linux_ubuntu_2004_installs_add_apt_repository_when_missing(self):
        def which(name):
            return "/usr/bin/mkcert" if name == "mkcert" else None

        with patch("sima_cli.sdk.neat.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.neat._is_wsl", return_value=False), \
             patch("sima_cli.sdk.neat._os_release_ids", return_value={"ubuntu", "debian"}), \
             patch("sima_cli.sdk.neat._os_release_value", return_value="20.04"), \
             patch("sima_cli.sdk.neat.shutil.which", side_effect=which), \
             patch("sima_cli.sdk.neat._run_install_command") as install, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            mkcert = _install_mkcert(yes_to_all=False, noninteractive=True)

        self.assertEqual(mkcert, "/usr/bin/mkcert")
        self.assertEqual(
            [call.args[0] for call in install.call_args_list],
            [
                ["sudo", "apt-get", "update"],
                ["sudo", "apt-get", "install", "-y", "software-properties-common"],
                ["sudo", "add-apt-repository", "-y", "universe"],
                ["sudo", "apt-get", "update"],
                ["sudo", "apt-get", "install", "-y", "mkcert", "libnss3-tools"],
            ],
        )

    def test_mkcert_linux_newer_ubuntu_does_not_enable_universe(self):
        with patch("sima_cli.sdk.neat.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.neat._is_wsl", return_value=False), \
             patch("sima_cli.sdk.neat._os_release_ids", return_value={"ubuntu", "debian"}), \
             patch("sima_cli.sdk.neat._os_release_value", return_value="22.04"), \
             patch("sima_cli.sdk.neat.shutil.which", return_value="/usr/bin/mkcert"), \
             patch("sima_cli.sdk.neat._run_install_command") as install, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            mkcert = _install_mkcert(yes_to_all=False, noninteractive=True)

        self.assertEqual(mkcert, "/usr/bin/mkcert")
        self.assertEqual(
            [call.args[0] for call in install.call_args_list],
            [
                ["sudo", "apt-get", "update"],
                ["sudo", "apt-get", "install", "-y", "mkcert", "libnss3-tools"],
            ],
        )

    def test_mkcert_linux_debian_does_not_enable_ubuntu_universe(self):
        with patch("sima_cli.sdk.neat.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.neat._is_wsl", return_value=False), \
             patch("sima_cli.sdk.neat._os_release_ids", return_value={"debian"}), \
             patch("sima_cli.sdk.neat._os_release_value", return_value="12"), \
             patch("sima_cli.sdk.neat.shutil.which", return_value="/usr/bin/mkcert"), \
             patch("sima_cli.sdk.neat._run_install_command") as install, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            mkcert = _install_mkcert(yes_to_all=False, noninteractive=True)

        self.assertEqual(mkcert, "/usr/bin/mkcert")
        self.assertEqual(
            [call.args[0] for call in install.call_args_list],
            [
                ["sudo", "apt-get", "update"],
                ["sudo", "apt-get", "install", "-y", "mkcert", "libnss3-tools"],
            ],
        )

    def test_ensure_certificates_falls_back_when_mkcert_install_fails(self):
        with TemporaryDirectory() as tmpdir:
            cert_dir = Path(tmpdir)
            with patch("sima_cli.sdk.neat._ensure_mkcert", return_value="/usr/bin/mkcert"), \
                 patch("sima_cli.sdk.neat._collect_cert_hosts", return_value=["localhost", "127.0.0.1"]), \
                 patch(
                     "sima_cli.sdk.neat.subprocess.run",
                     side_effect=[
                         subprocess.CalledProcessError(1, ["/usr/bin/mkcert", "-install"]),
                         Mock(returncode=0),
                     ],
                 ) as run, \
                 patch("sima_cli.sdk.neat.shutil.which", return_value="/usr/bin/openssl"):
                cert_file, key_file = _ensure_certificates(
                    cert_dir,
                    devkit_env=None,
                    yes_to_all=True,
                    noninteractive=False,
                )

        self.assertEqual(cert_file, cert_dir / "neat-sdk.pem")
        self.assertEqual(key_file, cert_dir / "neat-sdk-key.pem")
        self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/mkcert", "-install"])
        openssl_cmd = run.call_args_list[1].args[0]
        self.assertEqual(openssl_cmd[:6], ["/usr/bin/openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048"])
        self.assertIn("subjectAltName=DNS:localhost,IP:127.0.0.1", openssl_cmd)

    def test_generate_self_signed_cert_requires_openssl(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.neat.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "openssl is not available"):
                    _generate_self_signed_cert(
                        Path(tmpdir) / "neat-sdk.pem",
                        Path(tmpdir) / "neat-sdk-key.pem",
                        ["localhost"],
                    )

    def test_docker_port_collision_detection(self):
        self.assertTrue(is_docker_port_collision_error("Bind for 0.0.0.0:9900 failed: port is already allocated"))
        self.assertTrue(is_docker_port_collision_error("listen udp 0.0.0.0:9000: bind: address already in use"))
        self.assertFalse(is_docker_port_collision_error("image not found"))

    def test_reserved_ports_from_neat_port_map_extracts_tcp_and_udp_mappings(self):
        port_map = {
            "schema": "sima.neat.port-map.v1",
            "mainUI": {"protocol": "tcp", "host": 9900, "container": 9900},
            "codeUI": {"protocol": "tcp", "host": 9999, "container": 9999},
            "videoUDP": {
                "protocol": "udp",
                "containerStart": 9000,
                "containerEnd": 9079,
                "hostStart": 18000,
                "hostEnd": 18079,
            },
            "rtsp": {"tcp": {"host": 8554, "container": 8554}},
        }

        reserved = reserved_ports_from_neat_port_map(port_map)

        self.assertIn(("tcp", 9900), reserved)
        self.assertIn(("tcp", 9999), reserved)
        self.assertIn(("tcp", 8554), reserved)
        self.assertIn(("udp", 18000), reserved)
        self.assertIn(("udp", 18079), reserved)
        self.assertNotIn(("udp", 17999), reserved)
        self.assertNotIn(("udp", 18080), reserved)

    def test_start_neat_container_mounts_workspace_directly(self):
        with TemporaryDirectory() as tmpdir:
            neat_config = NeatRunConfig(
                port_map={
                    "schema": "sima.neat.port-map.v1",
                    "mainUI": {"protocol": "tcp", "host": 9900, "container": 9900},
                    "codeUI": {"protocol": "tcp", "host": 9999, "container": 9999},
                    "videoUI": {"protocol": "tcp", "host": 8081, "container": 8081},
                    "webSSH": {"protocol": "tcp", "host": 8022, "container": 8022},
                    "rtsp": {"tcp": {"host": 8554, "container": 8554}},
                    "videoUDP": {"protocol": "udp", "containerStart": 9000, "containerEnd": 9079, "hostStart": 9000, "hostEnd": 9079},
                    "metadataUDP": {"protocol": "udp", "containerStart": 9100, "containerEnd": 9179, "hostStart": 9100, "hostEnd": 9179},
                    "webRTC": {"protocol": "udp", "containerStart": 40000, "containerEnd": 40199, "hostStart": 40000, "hostEnd": 40199},
                    "cert": {"mount": "/sdk-cert", "certFile": "/sdk-cert/neat-sdk.pem", "keyFile": "/sdk-cert/neat-sdk-key.pem"},
                },
                port_args=[
                    "9900:9900/tcp",
                    "9999:9999/tcp",
                    "8081:8081/tcp",
                    "8022:8022/tcp",
                    "8554:8554/tcp",
                    "9000-9079:9000-9079/udp",
                    "9100-9179:9100-9179/udp",
                    "40000-40199:40000-40199/udp",
                ],
                config_host_dir=f"{tmpdir}/.ghcr.io-sima-neat-sdk-feature-devkit-sync-latest/insight-config",
                cert_host_dir=f"{tmpdir}/.ghcr.io-sima-neat-sdk-feature-devkit-sync-latest/sdk-cert",
                port_map_host_path=f"{tmpdir}/.ghcr.io-sima-neat-sdk-feature-devkit-sync-latest/insight-config/neat-port-map.json",
                cert_file_host_path=f"{tmpdir}/.ghcr.io-sima-neat-sdk-feature-devkit-sync-latest/sdk-cert/neat-sdk.pem",
                key_file_host_path=f"{tmpdir}/.ghcr.io-sima-neat-sdk-feature-devkit-sync-latest/sdk-cert/neat-sdk-key.pem",
                webrtc_host_ip="10.0.0.76",
                code_ui_token="code-token",
            )
            docker_result = Mock(returncode=0, stdout="container-id\n", stderr="")
            with patch("sima_cli.sdk.utils.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.utils.os.makedirs"), \
                 patch("sima_cli.sdk.utils.configure_container"), \
                 patch("sima_cli.sdk.utils.detect_current_user", return_value=("devuser", 1000, 1000)), \
                 patch("sima_cli.sdk.neat.prepare_neat_container_run", return_value=neat_config), \
                 patch("sima_cli.sdk.neat.print_neat_setup_summary"), \
                 patch("sima_cli.sdk.utils.subprocess.run", return_value=docker_result) as run:
                start_docker_container(
                    uid=1000,
                    gid=1000,
                    port=0,
                    workspace=tmpdir,
                    image="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest",
                )

        docker_cmd = run.call_args[0][0]
        self.assertNotIn("--user=1000:1000", docker_cmd)
        self.assertIn(f"{tmpdir}:/home/docker/sima-cli/", docker_cmd)
        self.assertIn(f"{tmpdir}:/workspace", docker_cmd)
        self.assertIn("--label", docker_cmd)
        self.assertIn(
            'devcontainer.metadata=[{"remoteUser":"devuser","workspaceFolder":"/workspace"}]',
            docker_cmd,
        )
        self.assertIn("OPENVSCODE_SERVER_USER=devuser", docker_cmd)
        self.assertIn("OPENVSCODE_SERVER_EXTENSIONS_DIR=/home/devuser/.openvscode-server/extensions", docker_cmd)
        self.assertIn("OPENVSCODE_WORKSPACE=/workspace", docker_cmd)
        self.assertIn("OPENVSCODE_SERVER_TOKEN=code-token", docker_cmd)
        self.assertIn(f"{tmpdir}/.ghcr.io-sima-neat-sdk-feature-devkit-sync-latest/logs/supervisor:/var/log/supervisor", docker_cmd)
        for mapping in (
            "9900:9900/tcp",
            "9999:9999/tcp",
            "8081:8081/tcp",
            "8022:8022/tcp",
            "8554:8554/tcp",
            "9000-9079:9000-9079/udp",
            "9100-9179:9100-9179/udp",
            "40000-40199:40000-40199/udp",
        ):
            self.assertIn(mapping, docker_cmd)
        self.assertIn("-e", docker_cmd)
        self.assertIn("MTX_RTSPTRANSPORTS=tcp", docker_cmd)
        self.assertIn("CONTAINER_HOST_IP=10.0.0.76", docker_cmd)
        self.assertIn(f"{neat_config.config_host_dir}:/home/docker/.insight-config", docker_cmd)
        self.assertIn(f"{neat_config.cert_host_dir}:/sdk-cert", docker_cmd)

    def test_start_neat_container_passes_no_insight_to_prepare(self):
        with TemporaryDirectory() as tmpdir:
            neat_config = NeatRunConfig(
                port_map={
                    "schema": "sima.neat.port-map.v1",
                    "cert": {"mount": "/sdk-cert", "certFile": "/sdk-cert/neat-sdk.pem", "keyFile": "/sdk-cert/neat-sdk-key.pem"},
                },
                port_args=[],
                config_host_dir=f"{tmpdir}/.sdk-latest/insight-config",
                cert_host_dir=f"{tmpdir}/.sdk-latest/sdk-cert",
                port_map_host_path=f"{tmpdir}/.sdk-latest/insight-config/neat-port-map.json",
                cert_file_host_path=f"{tmpdir}/.sdk-latest/sdk-cert/neat-sdk.pem",
                key_file_host_path=f"{tmpdir}/.sdk-latest/sdk-cert/neat-sdk-key.pem",
                webrtc_host_ip="",
            )
            docker_result = Mock(returncode=0, stdout="container-id\n", stderr="")
            with patch("sima_cli.sdk.utils.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.utils.os.makedirs"), \
                 patch("sima_cli.sdk.utils.configure_container"), \
                 patch("sima_cli.sdk.utils.detect_current_user", return_value=("devuser", 1000, 1000)), \
                 patch("sima_cli.sdk.neat.prepare_neat_container_run", return_value=neat_config) as prepare, \
                 patch("sima_cli.sdk.neat.print_neat_setup_summary"), \
                 patch("sima_cli.sdk.utils.subprocess.run", return_value=docker_result) as run:
                start_docker_container(
                    uid=1000,
                    gid=1000,
                    port=0,
                    workspace=tmpdir,
                    image="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest",
                    no_insight=True,
                )

        self.assertTrue(prepare.call_args.kwargs["no_insight"])
        docker_cmd = run.call_args[0][0]
        self.assertNotIn("8022:8022/tcp", docker_cmd)
        self.assertNotIn("8554:8554/tcp", docker_cmd)
        self.assertNotIn("9900:9900/tcp", docker_cmd)
        self.assertNotIn("9999:9999/tcp", docker_cmd)
        self.assertNotIn("8081:8081/tcp", docker_cmd)
        self.assertNotIn("9000-9079:9000-9079/udp", docker_cmd)
        self.assertNotIn("9100-9179:9100-9179/udp", docker_cmd)
        self.assertNotIn("40000-40199:40000-40199/udp", docker_cmd)

    def test_start_neat_container_minimal_passes_no_insight_to_prepare_and_configure(self):
        with TemporaryDirectory() as tmpdir:
            neat_config = NeatRunConfig(
                port_map={"schema": "sima.neat.port-map.v1"},
                port_args=[],
                config_host_dir="",
                cert_host_dir="",
                port_map_host_path="",
                cert_file_host_path="",
                key_file_host_path="",
                webrtc_host_ip="",
            )
            docker_result = Mock(returncode=0, stdout="container-id\n", stderr="")
            with patch("sima_cli.sdk.utils.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.utils.configure_container") as configure, \
                 patch("sima_cli.sdk.utils.detect_current_user", return_value=("devuser", 1000, 1000)), \
                 patch("sima_cli.sdk.neat.prepare_neat_container_run", return_value=neat_config) as prepare, \
                 patch("sima_cli.sdk.neat.print_neat_setup_summary"), \
                 patch("sima_cli.sdk.utils.subprocess.run", return_value=docker_result) as run:
                start_docker_container(
                    uid=1000,
                    gid=1000,
                    port=0,
                    workspace=tmpdir,
                    image="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest",
                    minimal=True,
                )

        self.assertTrue(prepare.call_args.kwargs["no_insight"])
        self.assertTrue(configure.call_args.kwargs["minimal"])
        docker_cmd = run.call_args[0][0]
        self.assertNotIn("/home/docker/.insight-config", docker_cmd)
        self.assertNotIn("/sdk-cert", docker_cmd)

    def test_start_neat_container_minimal_does_not_create_insight_certificates(self):
        with TemporaryDirectory() as tmpdir:
            docker_result = Mock(returncode=0, stdout="container-id\n", stderr="")
            with patch("sima_cli.sdk.utils.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.utils.configure_container"), \
                 patch("sima_cli.sdk.utils.detect_current_user", return_value=("devuser", 1000, 1000)), \
                 patch("sima_cli.sdk.neat._ensure_certificates") as certs, \
                 patch("sima_cli.sdk.neat._detect_webrtc_host_ip") as webrtc, \
                 patch("sima_cli.sdk.utils.subprocess.run", return_value=docker_result) as run:
                start_docker_container(
                    uid=1000,
                    gid=1000,
                    port=0,
                    workspace=tmpdir,
                    image="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest",
                    minimal=True,
                )

        certs.assert_not_called()
        webrtc.assert_not_called()
        docker_cmd = run.call_args[0][0]
        self.assertNotIn("/home/docker/.insight-config", docker_cmd)
        self.assertNotIn("/sdk-cert", docker_cmd)
        self.assertNotIn("CONTAINER_HOST_IP=", docker_cmd)

    def test_setup_no_model_sdk_skips_extension_directory_and_passes_flag(self):
        image = "ghcr.io/sima-neat/sdk:latest"
        with patch("sima_cli.sdk.install.ensure_simasdkbridge_network"), \
             patch("sima_cli.sdk.install.syscheck"), \
             patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
             patch("sima_cli.sdk.install.get_container_status", return_value={}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value=None), \
             patch("sima_cli.sdk.install._setup_sdk_extensions") as setup_extensions, \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=None), \
             patch("sima_cli.sdk.install.start_docker_container") as start_container:
            setup_and_start(no_model_sdk=True, yes_to_all=True, noninteractive=True)

        setup_extensions.assert_not_called()
        self.assertEqual(start_container.call_args.kwargs["sdk_extensions_dir"], "")
        self.assertTrue(start_container.call_args.kwargs["no_model_sdk"])

    def test_setup_passes_yes_to_all_to_colima_devkit_network_warning(self):
        image = "ghcr.io/sima-neat/sdk:latest"
        with patch("sima_cli.sdk.install.ensure_simasdkbridge_network"), \
             patch("sima_cli.sdk.install.syscheck"), \
             patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
             patch("sima_cli.sdk.install.warn_if_colima_devkit_network_may_need_bridged") as warn_colima, \
             patch("sima_cli.sdk.install.get_container_status", return_value={}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value=None), \
             patch("sima_cli.sdk.install._setup_sdk_extensions") as setup_extensions, \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=None), \
             patch("sima_cli.sdk.install.start_docker_container"):
            setup_and_start(
                no_model_sdk=True,
                yes_to_all=True,
                noninteractive=False,
                devkit_ip="10.0.0.244",
            )

        setup_extensions.assert_not_called()
        warn_colima.assert_called_once_with(
            "10.0.0.244",
            noninteractive=False,
            yes_to_all=True,
        )

    def test_setup_minimal_skips_extension_directory_and_passes_flags(self):
        image = "ghcr.io/sima-neat/sdk:latest"
        with patch("sima_cli.sdk.install.ensure_simasdkbridge_network"), \
             patch("sima_cli.sdk.install.syscheck"), \
             patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
             patch("sima_cli.sdk.install.get_container_status", return_value={}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value=None), \
             patch("sima_cli.sdk.install._setup_sdk_extensions") as setup_extensions, \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=None), \
             patch("sima_cli.sdk.install.start_docker_container") as start_container:
            setup_and_start(minimal=True, yes_to_all=True, noninteractive=True)

        setup_extensions.assert_not_called()
        self.assertEqual(start_container.call_args.kwargs["sdk_extensions_dir"], "")
        self.assertTrue(start_container.call_args.kwargs["no_model_sdk"])
        self.assertTrue(start_container.call_args.kwargs["minimal"])
        self.assertTrue(start_container.call_args.kwargs["no_insight"])

    def test_setup_warns_for_snap_docker_with_neat_sdk(self):
        image = "ghcr.io/sima-neat/sdk:latest"
        with patch("sima_cli.sdk.install.ensure_simasdkbridge_network"), \
             patch("sima_cli.sdk.install.syscheck"), \
             patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.is_snap_docker_cli", return_value=True), \
             patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
             patch("sima_cli.sdk.install.get_container_status", return_value={}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value=None), \
             patch("sima_cli.sdk.install._setup_sdk_extensions") as setup_extensions, \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=None), \
             patch("sima_cli.sdk.install.start_docker_container") as start_container, \
             patch("sima_cli.sdk.install.Console.print") as console_print:
            setup_and_start(no_model_sdk=True, yes_to_all=True, noninteractive=True)

        setup_extensions.assert_not_called()
        start_container.assert_called_once()
        snap_panels = [
            call.args[0]
            for call in console_print.call_args_list
            if "Snap Docker" in str(getattr(call.args[0], "title", ""))
        ]
        self.assertEqual(1, len(snap_panels))
        self.assertIn("Snap Docker detected", str(snap_panels[0].renderable))

    def test_snap_docker_detection_checks_resolved_snap_shim(self):
        with patch("sima_cli.sdk.utils.shutil.which", return_value="/usr/bin/docker"), \
             patch("sima_cli.sdk.utils.os.path.realpath", return_value="/snap/bin/docker"):
            self.assertTrue(is_snap_docker_cli())

    def test_refresh_mpk_config_skips_neat_sdk_selection(self):
        with patch("sima_cli.sdk.install.get_all_containers", side_effect=AssertionError("should not inspect containers")):
            _refresh_mpk_config_json(["ghcr.io/sima-neat/sdk:latest"])

    def test_setup_no_insight_refuses_existing_neat_container(self):
        image = "ghcr.io/sima-neat/sdk:latest"
        with patch("sima_cli.sdk.install.ensure_simasdkbridge_network"), \
             patch("sima_cli.sdk.install.syscheck"), \
             patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
             patch("sima_cli.sdk.install.get_container_status", return_value={}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value=None), \
             patch("sima_cli.sdk.install._setup_sdk_extensions", return_value=None), \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value="ghcr.io-sima-neat-sdk-latest"):
            with self.assertRaisesRegex(RuntimeError, "Cannot apply --no-insight"):
                setup_and_start(no_insight=True, yes_to_all=False, noninteractive=False)

    def test_setup_repairs_existing_container_user_mapping_before_attach(self):
        image = "ghcr.io/sima-neat/sdk:latest"
        container = "ghcr.io-sima-neat-sdk-latest"
        with patch("sima_cli.sdk.install.ensure_simasdkbridge_network"), \
             patch("sima_cli.sdk.install.syscheck"), \
             patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
             patch("sima_cli.sdk.install.get_container_status", return_value={container: "running"}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value=None), \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=container), \
             patch("sima_cli.sdk.install.is_container_running", return_value=True), \
             patch("sima_cli.sdk.install.check_os", return_value="linux"), \
             patch("sima_cli.sdk.install.detect_current_user", return_value=("devuser", 1000, 1000)), \
             patch("sima_cli.sdk.install.configure_container_user") as configure_user, \
             patch("sima_cli.sdk.install._refresh_mpk_config_json"), \
             patch("sima_cli.sdk.install.subprocess.run", return_value=Mock(returncode=0)) as run:
            setup_and_start(no_model_sdk=True, yes_to_all=True, noninteractive=True)

        configure_user.assert_called_once_with(container, "devuser", 1000, 1000)
        self.assertEqual(
            run.call_args.args[0],
            ["docker", "exec", "-it", "-u", "devuser", container, "bash", "-l"],
        )

    def test_start_neat_container_uses_valid_short_hostname_for_long_image_tag(self):
        with TemporaryDirectory() as tmpdir:
            image = "ghcr.io/sima-neat/sdk-feature-devkit-sync:76b8a6bad7e0c3e0b98c356c1879b98d32a90782"
            container_name = sanitize_container_name(image)
            hostname = sanitize_container_hostname(container_name)
            neat_config = NeatRunConfig(
                port_map={
                    "schema": "sima.neat.port-map.v1",
                    "mainUI": {"protocol": "tcp", "host": 9900, "container": 9900},
                    "codeUI": {"protocol": "tcp", "host": 9999, "container": 9999},
                    "videoUI": {"protocol": "tcp", "host": 8081, "container": 8081},
                    "webSSH": {"protocol": "tcp", "host": 8022, "container": 8022},
                    "rtsp": {"tcp": {"host": 8554, "container": 8554}},
                    "videoUDP": {"protocol": "udp", "containerStart": 9000, "containerEnd": 9079, "hostStart": 9000, "hostEnd": 9079},
                    "metadataUDP": {"protocol": "udp", "containerStart": 9100, "containerEnd": 9179, "hostStart": 9100, "hostEnd": 9179},
                    "webRTC": {"protocol": "udp", "containerStart": 40000, "containerEnd": 40199, "hostStart": 40000, "hostEnd": 40199},
                    "cert": {"mount": "/sdk-cert", "certFile": "/sdk-cert/neat-sdk.pem", "keyFile": "/sdk-cert/neat-sdk-key.pem"},
                },
                port_args=[],
                config_host_dir=f"{tmpdir}/.{container_name}/insight-config",
                cert_host_dir=f"{tmpdir}/.{container_name}/sdk-cert",
                port_map_host_path=f"{tmpdir}/.{container_name}/insight-config/neat-port-map.json",
                cert_file_host_path=f"{tmpdir}/.{container_name}/sdk-cert/neat-sdk.pem",
                key_file_host_path=f"{tmpdir}/.{container_name}/sdk-cert/neat-sdk-key.pem",
                webrtc_host_ip="10.0.0.76",
            )
            docker_result = Mock(returncode=0, stdout="container-id\n", stderr="")
            with patch("sima_cli.sdk.utils.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.utils.os.makedirs"), \
                 patch("sima_cli.sdk.utils.configure_container"), \
                 patch("sima_cli.sdk.utils.detect_current_user", return_value=("runner", 1001, 1001)), \
                 patch("sima_cli.sdk.neat.prepare_neat_container_run", return_value=neat_config), \
                 patch("sima_cli.sdk.neat.print_neat_setup_summary"), \
                 patch("sima_cli.sdk.utils.subprocess.run", return_value=docker_result) as run:
                start_docker_container(
                    uid=1001,
                    gid=1001,
                    port=0,
                    workspace=tmpdir,
                    image=image,
                )

        docker_cmd = run.call_args[0][0]
        hostname_index = docker_cmd.index("--hostname") + 1
        self.assertEqual(docker_cmd[hostname_index], hostname)
        self.assertLessEqual(len(docker_cmd[hostname_index]), 63)
        self.assertIn("--name", docker_cmd)
        self.assertIn(container_name, docker_cmd)

    def test_start_neat_container_retries_when_docker_reports_port_collision(self):
        with TemporaryDirectory() as tmpdir:
            first_config = NeatRunConfig({}, ["9900:9900/tcp"], f"{tmpdir}/config1", f"{tmpdir}/cert1", "", "", "")
            second_config = NeatRunConfig({}, ["19900:9900/tcp"], f"{tmpdir}/config2", f"{tmpdir}/cert2", "", "", "")
            failed = Mock(returncode=125, stdout="", stderr="port is already allocated")
            inspect_created = Mock(returncode=0, stdout="created\n", stderr="")
            removed = Mock(returncode=0, stdout="", stderr="")
            succeeded = Mock(returncode=0, stdout="container-id\n", stderr="")
            with patch("sima_cli.sdk.utils.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.utils.os.makedirs"), \
                 patch("sima_cli.sdk.utils.configure_container"), \
                 patch("sima_cli.sdk.neat.prepare_neat_container_run", side_effect=[first_config, second_config]) as prepare, \
                 patch("sima_cli.sdk.neat.print_neat_setup_summary"), \
                 patch("sima_cli.sdk.utils.subprocess.run", side_effect=[failed, inspect_created, removed, succeeded]) as run:
                start_docker_container(
                    uid=1000,
                    gid=1000,
                    port=0,
                    workspace=tmpdir,
                    image="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest",
                )

        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(run.call_args_list[1][0][0][:3], ["docker", "inspect", "-f"])
        self.assertEqual(run.call_args_list[2][0][0], ["docker", "rm", "-f", "ghcr.io-sima-neat-sdk-feature-devkit-sync-latest"])
        self.assertIn("19900:9900/tcp", run.call_args[0][0])

    def test_start_neat_container_excludes_failed_port_map_on_retry(self):
        with TemporaryDirectory() as tmpdir:
            first_config = NeatRunConfig(
                {
                    "schema": "sima.neat.port-map.v1",
                    "videoUDP": {
                        "protocol": "udp",
                        "containerStart": 9000,
                        "containerEnd": 9079,
                        "hostStart": 9000,
                        "hostEnd": 9079,
                    },
                },
                ["9000-9079:9000-9079/udp"],
                f"{tmpdir}/config1",
                f"{tmpdir}/cert1",
                "",
                "",
                "",
            )
            second_config = NeatRunConfig(
                {
                    "schema": "sima.neat.port-map.v1",
                    "videoUDP": {
                        "protocol": "udp",
                        "containerStart": 9000,
                        "containerEnd": 9079,
                        "hostStart": 18000,
                        "hostEnd": 18079,
                    },
                },
                ["18000-18079:9000-9079/udp"],
                f"{tmpdir}/config2",
                f"{tmpdir}/cert2",
                "",
                "",
                "",
            )
            prepare_reserved_args = []

            def prepare_side_effect(**kwargs):
                prepare_reserved_args.append(set(kwargs["reserved_ports"]))
                return first_config if len(prepare_reserved_args) == 1 else second_config

            failed = Mock(
                returncode=125,
                stdout="",
                stderr="Bind for 0.0.0.0:9000 failed: port is already allocated",
            )
            inspect_created = Mock(returncode=0, stdout="created\n", stderr="")
            removed = Mock(returncode=0, stdout="", stderr="")
            succeeded = Mock(returncode=0, stdout="container-id\n", stderr="")
            with patch("sima_cli.sdk.utils.platform.system", return_value="Linux"), \
                 patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
                 patch("sima_cli.sdk.utils.os.makedirs"), \
                 patch("sima_cli.sdk.utils.configure_container"), \
                 patch("sima_cli.sdk.neat.prepare_neat_container_run", side_effect=prepare_side_effect), \
                 patch("sima_cli.sdk.neat.print_neat_setup_summary"), \
                 patch("sima_cli.sdk.utils.subprocess.run", side_effect=[failed, inspect_created, removed, succeeded]) as run:
                start_docker_container(
                    uid=1000,
                    gid=1000,
                    port=0,
                    workspace=tmpdir,
                    image="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest",
                )

        self.assertEqual(prepare_reserved_args[0], set())
        self.assertIn(("udp", 9000), prepare_reserved_args[1])
        self.assertIn(("udp", 9079), prepare_reserved_args[1])
        self.assertIn("18000-18079:9000-9079/udp", run.call_args[0][0])

    def test_non_neat_container_does_not_prepare_neat_config(self):
        with TemporaryDirectory() as tmpdir:
            with patch("sima_cli.sdk.neat.prepare_neat_container_run") as prepare, \
                 patch("sima_cli.sdk.utils.configure_container"), \
                 patch("sima_cli.sdk.utils.run_command") as run_command:
                start_docker_container(
                    uid=1000,
                    gid=1000,
                    port=0,
                    workspace=tmpdir,
                    image="elxr:latest",
                )

        prepare.assert_not_called()
        docker_cmd = run_command.call_args_list[0][0][0]
        self.assertIn("--user=1000:1000", docker_cmd)
        self.assertNotIn("/home/docker/.insight-config", docker_cmd)
        self.assertNotIn("/sdk-cert", docker_cmd)

    def test_extract_sdk_base_version(self):
        sdk_release = "\n".join([
            "SDK Version = 2.0.0_Palette_SDK_neat_main_780365a",
            "eLXr Version = 2.0.0_release_neat_main_780365a",
        ])

        self.assertEqual(_extract_sdk_base_version(sdk_release), "2.0.0")

    def test_copy_sima_cli_auth_cache_skips_non_neat_image(self):
        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="artifacts.eng.sima.ai/elxr:2.1.0"), \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            _copy_sima_cli_auth_cache_to_container("container", "devuser", 1000, 1000)

        run_command.assert_not_called()

    def test_copy_sima_cli_auth_cache_copies_existing_files_for_neat(self):
        with TemporaryDirectory() as tmpdir:
            auth_dir = Path(tmpdir) / ".sima-cli"
            auth_dir.mkdir()
            (auth_dir / ".tokens.json").write_text("{}", encoding="utf-8")
            (auth_dir / ".sima-cli-cookies.txt").write_text("# cookies", encoding="utf-8")

            with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", return_value=str(auth_dir)), \
                 patch("sima_cli.sdk.utils.run_command") as run_command:
                _copy_sima_cli_auth_cache_to_container("container", "devuser", 1000, 1000)

        calls = run_command.call_args_list
        self.assertEqual(calls[:2], [
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "mkdir", "-p", "/home/devuser/.sima-cli",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "chown", "1000:1000", "/home/devuser/.sima-cli",
            ], fatal=False),
        ])
        self.assertEqual(len(calls), 8)
        self.assertEqual(calls[2], unittest.mock.call([
            "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.tokens.json",
        ], fatal=False))
        self.assertEqual(calls[3].args[0][0:2], ["docker", "cp"])
        self.assertTrue(Path(calls[3].args[0][2]).is_absolute())
        self.assertTrue(Path(calls[3].args[0][2]).name == ".tokens.json")
        self.assertEqual(calls[3].args[0][3], "container:/home/devuser/.sima-cli/.tokens.json")
        self.assertEqual(calls[4], unittest.mock.call([
            "docker", "exec", "-u", "root", "container", "chown", "1000:1000", "/home/devuser/.sima-cli/.tokens.json",
        ], fatal=False))
        self.assertEqual(calls[5], unittest.mock.call([
            "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.sima-cli-cookies.txt",
        ], fatal=False))
        self.assertEqual(calls[6].args[0][0:2], ["docker", "cp"])
        self.assertTrue(Path(calls[6].args[0][2]).is_absolute())
        self.assertTrue(Path(calls[6].args[0][2]).name == ".sima-cli-cookies.txt")
        self.assertEqual(calls[6].args[0][3], "container:/home/devuser/.sima-cli/.sima-cli-cookies.txt")
        self.assertEqual(calls[7], unittest.mock.call([
            "docker", "exec", "-u", "root", "container", "chown", "1000:1000", "/home/devuser/.sima-cli/.sima-cli-cookies.txt",
        ], fatal=False))

    def test_copy_sima_cli_auth_cache_cleans_partial_target_when_copy_fails(self):
        with TemporaryDirectory() as tmpdir:
            auth_dir = Path(tmpdir) / ".sima-cli"
            auth_dir.mkdir()
            (auth_dir / ".tokens.json").write_text("{}", encoding="utf-8")

            with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", return_value=str(auth_dir)), \
                 patch("sima_cli.sdk.utils.run_command", side_effect=[True, True, True, False, True]) as run_command:
                _copy_sima_cli_auth_cache_to_container("container", "devuser", 1000, 1000)

        self.assertEqual(run_command.call_args_list, [
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "mkdir", "-p", "/home/devuser/.sima-cli",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "chown", "1000:1000", "/home/devuser/.sima-cli",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "cp", unittest.mock.ANY, "container:/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
        ])

    def test_copy_sima_cli_auth_cache_continues_when_one_file_fails(self):
        with TemporaryDirectory() as tmpdir:
            auth_dir = Path(tmpdir) / ".sima-cli"
            auth_dir.mkdir()
            (auth_dir / ".tokens.json").write_text("{}", encoding="utf-8")
            (auth_dir / ".sima-cli-cookies.txt").write_text("# cookies", encoding="utf-8")

            with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", return_value=str(auth_dir)), \
                 patch("sima_cli.sdk.utils.run_command", side_effect=[True, True, True, False, True, True, True, True]) as run_command:
                _copy_sima_cli_auth_cache_to_container("container", "devuser", 1000, 1000)

        self.assertEqual(run_command.call_args_list, [
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "mkdir", "-p", "/home/devuser/.sima-cli",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "chown", "1000:1000", "/home/devuser/.sima-cli",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "cp", unittest.mock.ANY, "container:/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.sima-cli-cookies.txt",
            ], fatal=False),
            unittest.mock.call([
                "docker", "cp", unittest.mock.ANY, "container:/home/devuser/.sima-cli/.sima-cli-cookies.txt",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "chown", "1000:1000", "/home/devuser/.sima-cli/.sima-cli-cookies.txt",
            ], fatal=False),
        ])

    def test_copy_sima_cli_auth_cache_leaves_directory_writable_when_all_files_fail(self):
        with TemporaryDirectory() as tmpdir:
            auth_dir = Path(tmpdir) / ".sima-cli"
            auth_dir.mkdir()
            (auth_dir / ".tokens.json").write_text("{}", encoding="utf-8")
            (auth_dir / ".sima-cli-cookies.txt").write_text("# cookies", encoding="utf-8")

            with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
                 patch("sima_cli.sdk.utils.os.path.expanduser", return_value=str(auth_dir)), \
                 patch("sima_cli.sdk.utils.run_command", side_effect=[True, True, True, False, True, True, False, True]) as run_command:
                _copy_sima_cli_auth_cache_to_container("container", "devuser", 1000, 1000)

        self.assertEqual(run_command.call_args_list, [
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "mkdir", "-p", "/home/devuser/.sima-cli",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "chown", "1000:1000", "/home/devuser/.sima-cli",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "cp", unittest.mock.ANY, "container:/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.tokens.json",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.sima-cli-cookies.txt",
            ], fatal=False),
            unittest.mock.call([
                "docker", "cp", unittest.mock.ANY, "container:/home/devuser/.sima-cli/.sima-cli-cookies.txt",
            ], fatal=False),
            unittest.mock.call([
                "docker", "exec", "-u", "root", "container", "rm", "-f", "/home/devuser/.sima-cli/.sima-cli-cookies.txt",
            ], fatal=False),
        ])

    def test_model_sdk_extension_skips_non_neat_elxr_image(self):
        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="artifacts.eng.sima.ai/elxr:2.1.0"), \
             patch("sima_cli.sdk.utils.yes_no_prompt") as prompt, \
             patch("sima_cli.sdk.utils.subprocess.run") as run:
            ensure_model_sdk_extension_installed("container", "docker")

        prompt.assert_not_called()
        run.assert_not_called()

    def test_model_sdk_extension_skips_arm64_before_2_1_1(self):
        sdk_release = "SDK Version = 2.0.0_Palette_SDK_neat_main_780365a\n"
        read_result = unittest.mock.Mock(returncode=0, stdout=sdk_release)

        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
             patch("sima_cli.sdk.utils.platform.machine", return_value="arm64"), \
             patch("sima_cli.sdk.utils.yes_no_prompt") as prompt, \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=read_result) as run, \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            ensure_model_sdk_extension_installed("container", "docker")

        prompt.assert_not_called()
        run.assert_called_once()
        run_command.assert_not_called()

    def test_model_sdk_extension_skips_when_user_declines(self):
        sdk_release = "SDK Version = 2.0.0_Palette_SDK_neat_main_780365a\n"
        read_result = unittest.mock.Mock(returncode=0, stdout=sdk_release)

        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
             patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
             patch("sima_cli.sdk.utils.yes_no_prompt", return_value=False), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=read_result) as run, \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            ensure_model_sdk_extension_installed("container", "docker")

        run.assert_called_once()
        run_command.assert_not_called()

    def test_model_sdk_extension_installs_for_neat_elxr_image(self):
        sdk_release = "SDK Version = 2.0.0_Palette_SDK_neat_main_780365a\n"
        read_result = unittest.mock.Mock(returncode=0, stdout=sdk_release)

        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
             patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
             patch("sima_cli.sdk.utils.yes_no_prompt", return_value=True), \
             patch("sima_cli.sdk.utils.sys.stdin.isatty", return_value=True), \
             patch("sima_cli.sdk.utils.sys.stdout.isatty", return_value=True), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=read_result) as run, \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            ensure_model_sdk_extension_installed("container", "docker")

        run.assert_called_once_with(
            ["docker", "exec", "container", "cat", "/etc/sdk-release"],
            text=True,
            capture_output=True,
            check=False,
        )
        install_script = run_command.call_args_list[1].args[0][-1]
        self.assertIn("export HOME=/home/docker", install_script)
        self.assertIn("export SIMA_CLI_AUTO_ACCEPT_UPDATE=1", install_script)
        self.assertIn("export PATH=\"$HOME/.sima-cli/.venv/bin:$HOME/.local/bin:$PATH\"", install_script)
        self.assertIn("SIMA_CLI_BIN=\"$HOME/.sima-cli/.venv/bin/sima-cli\"", install_script)
        self.assertIn("/etc/sudoers.d/sima-cli-user", install_script)
        self.assertIn("'docker ALL=(ALL:ALL) NOPASSWD:ALL'", install_script)
        self.assertIn("su -s /bin/bash docker -c 'sudo -n true'", install_script)
        self.assertIn("trap cleanup_model_sdk_install EXIT", install_script)
        self.assertLess(install_script.index("/etc/sudoers.d/sima-cli-user"), install_script.index("su -s /bin/bash docker -c"))
        self.assertLess(install_script.index("sudo -n true"), install_script.index("\"$SIMA_CLI_BIN\" install -v 2.0.0 tools/model-compiler/amd64"))
        self.assertLess(install_script.index("trap cleanup_model_sdk_install EXIT"), install_script.index("su -s /bin/bash docker -c"))
        self.assertIn("chown -R docker:docker \"$HOME/extension-installation\" \"$HOME/.sima-cli\" 2>/dev/null || true", install_script)
        self.assertIn("su -s /bin/bash docker -c", install_script)
        self.assertIn("\"$SIMA_CLI_BIN\" install -v 2.0.0 tools/model-compiler/amd64", install_script)
        self.assertNotIn("libllvm", install_script)
        self.assertEqual(run_command.call_args_list, [
            unittest.mock.call([
                "docker",
                "exec",
                "-it",
                "-u",
                "docker",
                "container",
                "bash",
                "-lc",
                "export SIMA_CLI_AUTO_ACCEPT_UPDATE=1; sima-cli login",
            ]),
            unittest.mock.call([
                "docker",
                "exec",
                "-u",
                "root",
                "container",
                "bash",
                "-lc",
                unittest.mock.ANY,
            ]),
        ])

    def test_model_sdk_extension_installs_aarch64_for_2_1_1(self):
        sdk_release = "SDK Version = 2.1.1_Palette_SDK_neat_main_780365a\n"
        read_result = unittest.mock.Mock(returncode=0, stdout=sdk_release)

        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
             patch("sima_cli.sdk.utils.platform.machine", return_value="arm64"), \
             patch("sima_cli.sdk.utils.yes_no_prompt", return_value=True), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=read_result), \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            ensure_model_sdk_extension_installed("container", "docker")

        self.assertEqual(
            run_command.call_args_list[-1],
            unittest.mock.call([
                "docker",
                "exec",
                "-u",
                "root",
                "container",
                "bash",
                "-lc",
                unittest.mock.ANY,
            ]),
        )
        self.assertIn(
            "\"$SIMA_CLI_BIN\" install -v 2.1.1 tools/model-compiler/arm64",
            run_command.call_args_list[-1].args[0][-1],
        )
        self.assertIn(
            "su -s /bin/bash docker -c",
            run_command.call_args_list[-1].args[0][-1],
        )

    def test_model_sdk_extension_auto_installs_without_prompt(self):
        sdk_release = "SDK Version = 2.0.0_Palette_SDK_neat_main_780365a\n"
        read_result = unittest.mock.Mock(returncode=0, stdout=sdk_release)

        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
             patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
             patch("sima_cli.sdk.utils.yes_no_prompt") as prompt, \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=read_result), \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            ensure_model_sdk_extension_installed("container", "docker", auto_install=True)

        prompt.assert_not_called()
        self.assertEqual(run_command.call_count, 2)

    def test_configure_container_skips_model_sdk_extension_when_requested(self):
        with patch("sima_cli.sdk.utils.check_os", return_value="windows"), \
             patch("sima_cli.sdk.utils.run_command"), \
             patch("sima_cli.sdk.utils._copy_sima_cli_auth_cache_to_container"), \
             patch("sima_cli.sdk.utils.ensure_sima_cli_installed"), \
             patch("sima_cli.sdk.utils.ensure_model_sdk_extension_installed") as model_sdk, \
             patch("sima_cli.sdk.utils._sync_codex_skills"), \
             patch("sima_cli.sdk.utils.install_neat_playbooks"):
            from sima_cli.sdk.utils import configure_container

            configure_container("container", no_model_sdk=True)

        model_sdk.assert_not_called()

    def test_configure_container_minimal_skips_sima_cli_model_sdk_and_playbooks(self):
        with patch("sima_cli.sdk.utils.check_os", return_value="windows"), \
             patch("sima_cli.sdk.utils.run_command"), \
             patch("sima_cli.sdk.utils._copy_sima_cli_auth_cache_to_container"), \
             patch("sima_cli.sdk.utils.ensure_sima_cli_installed") as sima_cli_install, \
             patch("sima_cli.sdk.utils.ensure_model_sdk_extension_installed") as model_sdk, \
             patch("sima_cli.sdk.utils._sync_codex_skills"), \
             patch("sima_cli.sdk.utils.install_neat_playbooks") as playbooks:
            from sima_cli.sdk.utils import configure_container

            configure_container("container", minimal=True)

        sima_cli_install.assert_not_called()
        model_sdk.assert_not_called()
        playbooks.assert_not_called()

    def test_configure_container_uses_absolute_temp_files_for_user_mapping(self):
        copied_files = {}
        copied_file_parents = {}
        updated_group = ""

        def fake_run_command(cmd, *args, **kwargs):
            nonlocal updated_group
            if cmd[:2] == ["docker", "cp"]:
                src, dest = cmd[2], cmd[3]
                if src == "container:/etc/passwd":
                    Path(dest).write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
                    copied_files["passwd"] = dest
                    copied_file_parents["passwd"] = Path(dest).parent.stat().st_mode & 0o777
                elif src == "container:/etc/shadow":
                    Path(dest).write_text("root:*:::::::\n", encoding="utf-8")
                    copied_files["shadow"] = dest
                    copied_file_parents["shadow"] = Path(dest).parent.stat().st_mode & 0o777
                elif src == "container:/etc/group":
                    Path(dest).write_text("root:x:0:\nsudo:x:27:\ndocker:x:900:\n", encoding="utf-8")
                    copied_files["group"] = dest
                    copied_file_parents["group"] = Path(dest).parent.stat().st_mode & 0o777
                elif dest == "container:/etc/group":
                    updated_group = Path(src).read_text(encoding="utf-8")
                else:
                    self.assertTrue(Path(src).is_absolute())
            return True

        with TemporaryDirectory() as home, \
             patch.dict(os.environ, {"HOME": home}), \
             patch("sima_cli.sdk.utils.check_os", return_value="macos"), \
             patch("sima_cli.sdk.utils.detect_current_user", return_value=("jimfan", 501, 20)), \
             patch("sima_cli.sdk.utils.run_command", side_effect=fake_run_command), \
             patch("sima_cli.sdk.utils._copy_sima_cli_auth_cache_to_container"), \
             patch("sima_cli.sdk.utils.ensure_sima_cli_installed"), \
             patch("sima_cli.sdk.utils.ensure_model_sdk_extension_installed"), \
             patch("sima_cli.sdk.utils._sync_codex_skills"), \
             patch("sima_cli.sdk.utils.install_neat_playbooks"):
            from sima_cli.sdk.utils import configure_container

            configure_container("container", no_model_sdk=True)

        self.assertEqual(set(copied_files), {"passwd", "shadow", "group"})
        self.assertEqual(set(copied_file_parents), {"passwd", "shadow", "group"})
        for path in copied_files.values():
            self.assertTrue(Path(path).is_absolute())
            self.assertEqual(os.path.commonpath([home, path]), home)
            self.assertTrue(Path(path).parent.name.startswith("sima-cli-sdk-"))
            self.assertFalse(Path(path).parent.name.startswith("."))
        for mode in copied_file_parents.values():
            self.assertEqual(mode, 0o755)
        self.assertIn("sudo:x:27:jimfan\n", updated_group)
        self.assertIn("docker:x:900:jimfan\n", updated_group)

    def test_install_neat_playbooks_skips_non_neat_image(self):
        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="artifacts.eng.sima.ai/elxr:2.1.0"), \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            install_neat_playbooks("container", "docker")

        run_command.assert_not_called()

    def test_install_neat_playbooks_runs_inside_neat_container(self):
        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk-feature-devkit-sync:latest"), \
             patch("sima_cli.sdk.utils.run_command") as run_command:
            install_neat_playbooks("container", "docker")

        run_command.assert_called_once_with([
            "docker",
            "exec",
            "-u",
            "docker",
            "-e",
            "SIMA_CLI_AUTO_ACCEPT_UPDATE=1",
            "-e",
            "GITHUB_TOKEN",
            "container",
            "bash",
            "-lc",
            "cd /home/docker && sima-cli install gh:sima-neat/playbooks",
        ])

    def test_codex_vscode_extension_skips_when_openvscode_missing(self):
        missing_server = Mock(returncode=1)
        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.2"), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=missing_server) as run, \
             patch("sima_cli.sdk.utils.yes_no_prompt") as prompt:
            ensure_codex_vscode_extension_installed("container", "docker", auto_install=True)

        self.assertEqual(run.call_count, 1)
        prompt.assert_not_called()

    def test_codex_vscode_extension_skips_when_user_declines(self):
        server_available = Mock(returncode=0)
        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.2"), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=server_available) as run, \
             patch("sima_cli.sdk.utils.yes_no_prompt", return_value=False) as prompt:
            ensure_codex_vscode_extension_installed("container", "docker")

        self.assertEqual(run.call_count, 1)
        prompt.assert_called_once()

    def test_codex_vscode_extension_auto_installs_without_prompt(self):
        server_available = Mock(returncode=0)
        install_result = Mock(returncode=0, stdout="installed\n", stderr="")
        with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.2"), \
             patch("sima_cli.sdk.utils.subprocess.run", side_effect=[server_available, install_result]) as run, \
             patch("sima_cli.sdk.utils.yes_no_prompt") as prompt:
            ensure_codex_vscode_extension_installed(
                "container",
                "docker",
                auto_install=True,
                uid=1000,
                gid=1000,
            )

        prompt.assert_not_called()
        self.assertEqual(run.call_count, 2)
        install_cmd = run.call_args_list[-1].args[0]
        self.assertEqual(install_cmd[:5], ["docker", "exec", "-u", "root", "container"])
        self.assertIn("--install-extension openai.chatgpt", install_cmd[-1])
        self.assertIn("find /opt/openvscode-server/extensions -maxdepth 1 -type d -name 'openai.chatgpt-*'", install_cmd[-1])
        self.assertIn("chown -R 1000:1000 /home/docker/.openvscode-server/extensions", install_cmd[-1])
        self.assertIn("extensions.supportNodeGlobalNavigator", install_cmd[-1])
        self.assertIn("supervisorctl restart openvscode-server", install_cmd[-1])

    def test_configure_container_skips_codex_extension_for_minimal(self):
        with patch("sima_cli.sdk.utils.check_os", return_value="windows"), \
             patch("sima_cli.sdk.utils.run_command"), \
             patch("sima_cli.sdk.utils._copy_sima_cli_auth_cache_to_container"), \
             patch("sima_cli.sdk.utils.ensure_sima_cli_installed"), \
             patch("sima_cli.sdk.utils.ensure_model_sdk_extension_installed"), \
             patch("sima_cli.sdk.utils._sync_codex_skills"), \
             patch("sima_cli.sdk.utils.install_neat_playbooks"), \
             patch("sima_cli.sdk.utils.ensure_codex_vscode_extension_installed") as codex_extension:
            from sima_cli.sdk.utils import configure_container

            configure_container("container", minimal=True)

        codex_extension.assert_not_called()

    def test_sudoers_drop_in_uses_sudoers_d_without_replacing_base_file(self):
        script = _sudoers_drop_in_script("ji.fan")

        self.assertIn("/etc/sudoers.d/sima-cli-user", script)
        self.assertIn("'ji.fan ALL=(ALL:ALL) NOPASSWD:ALL'", script)
        self.assertIn("#includedir /etc/sudoers.d", script)
        self.assertIn(">> /etc/sudoers", script)
        self.assertNotIn("echo 'ji.fan ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers", script)
        self.assertNotIn("chown", script)

    def test_append_unique_line_preserves_existing_last_line_without_newline(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "passwd"
            path.write_text("root:x:0:0:root:/root:/bin/bash", encoding="utf-8")

            _append_unique_line(str(path), "ji.fan:x:841037974:841037974::/home/ji.fan:/bin/bash")
            _append_unique_line(str(path), "ji.fan:x:841037974:841037974::/home/ji.fan:/bin/bash")

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "root:x:0:0:root:/root:/bin/bash\n"
                "ji.fan:x:841037974:841037974::/home/ji.fan:/bin/bash\n",
            )

    def test_ensure_passwd_user_prioritizes_host_user_when_uid_collides_with_ubuntu(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "passwd"
            path.write_text(
                "root:x:0:0:root:/root:/bin/bash\n"
                "ubuntu:x:1000:1000:Ubuntu:/home/ubuntu:/bin/bash\n",
                encoding="utf-8",
            )

            _ensure_passwd_user(str(path), "jim", 1000, 1000)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "root:x:0:0:root:/root:/bin/bash\n"
                "jim:x:1000:1000::/home/jim:/bin/bash\n"
                "ubuntu:x:1000:1000:Ubuntu:/home/ubuntu:/bin/bash\n",
            )

    def test_ensure_shadow_user_replaces_existing_host_user_entry(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shadow"
            path.write_text(
                "root:*:::::::\n"
                "jim:old:::::::\n"
                "ubuntu:*:::::::\n",
                encoding="utf-8",
            )

            _ensure_shadow_user(str(path), "jim")

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "root:*:::::::\n"
                "ubuntu:*:::::::\n"
                "jim:$6$hash$placeholder:::::::\n",
            )

    def test_configure_group_file_adds_primary_group_docker_and_sudo_membership(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "group"
            path.write_text(
                "root:x:0:\n"
                "sudo:x:27:\n"
                "ubuntu:x:1000:\n"
                "docker:x:999:existing",
                encoding="utf-8",
            )

            _configure_group_file(str(path), "jim", 1000)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "root:x:0:\n"
                "sudo:x:27:jim\n"
                "jim:x:1000:\n"
                "ubuntu:x:1000:\n"
                "docker:x:999:existing,jim\n",
            )

    def test_bash_profile_repair_sources_bashrc_for_login_shells(self):
        script = _bash_profile_sources_bashrc_script("jim", 1000, 1000)

        self.assertIn("profile=/home/jim/.bash_profile", script)
        self.assertIn("bashrc=/home/jim/.bashrc", script)
        self.assertIn('touch "$bashrc"', script)
        self.assertIn('if [ -f "$HOME/.bashrc" ]; then', script)
        self.assertIn('. "$HOME/.bashrc"', script)
        self.assertIn('chown 1000:1000 "$home" "$profile" "$bashrc"', script)

    def test_prompt_multi_select_accepts_modelsdk_display_name(self):
        with patch("builtins.print"), patch("builtins.input", return_value="ModelSDK"):
            self.assertEqual(prompt_multi_select(baseline_present=True), ["modelsdk"])

    def test_installer_profile_bootstrap_runs_before_alias_setup(self):
        installer = Path("scripts/install/linux-mac.sh").read_text(encoding="utf-8")

        self.assertIn("ensure_bashrc_sourced_from_profile()", installer)
        self.assertLess(
            installer.index('ensure_bashrc_sourced_from_profile "$RC_FILE"'),
            installer.index('add_venv_path "$RC_FILE"'),
        )
        self.assertLess(
            installer.index('ensure_bashrc_sourced_from_profile "$RC_FILE"'),
            installer.index('add_aliases "$RC_FILE"'),
        )

    def test_linux_installer_skips_apt_when_python_venv_and_pip_are_ready(self):
        installer = Path("scripts/install/linux-mac.sh").read_text(encoding="utf-8")

        self.assertIn("python_venv_and_pip_ready()", installer)
        self.assertIn('python3 -m venv "$tmp_dir/venv"', installer)
        self.assertIn('"$tmp_dir/venv/bin/python" -m pip --version', installer)
        self.assertIn("python3 venv and pip are already available; skipping apt", installer)
        self.assertIn("DPkg::Lock::Timeout=120", installer)
        self.assertLess(
            installer.index("elif python_venv_and_pip_ready; then"),
            installer.index("sudo apt-get install -y -o DPkg::Lock::Timeout=120"),
        )

    def test_installers_preserve_modelsdk_alias(self):
        shell_installer = Path("scripts/install/linux-mac.sh").read_text(encoding="utf-8")
        python_installer = Path("scripts/install/install.py").read_text(encoding="utf-8")

        self.assertIn("modelsdk", shell_installer)
        self.assertIn('"modelsdk": "sima-cli sdk model"', python_installer)
        self.assertNotIn("modelcompiler", shell_installer)
        self.assertNotIn('"modelcompiler": "sima-cli sdk model"', python_installer)

    def test_prepare_log_host_dir_makes_directory_container_writable(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "logs" / "supervisor"

            _prepare_log_host_dir(str(path))

            self.assertTrue(path.is_dir())
            self.assertEqual(path.stat().st_mode & 0o777, 0o777)

    def test_docker_user_mapping_error_matches_lookup_failure_only(self):
        self.assertTrue(
            is_docker_user_mapping_error(
                "unable to find user jimfan: no matching entries in passwd file"
            )
        )
        self.assertFalse(is_docker_user_mapping_error("bash: eixt: command not found"))

    def test_container_user_mapping_unavailable_probes_docker_user(self):
        failed = Mock(
            returncode=1,
            stdout="",
            stderr="unable to find user jimfan: no matching entries in passwd file",
        )
        with patch("sima_cli.sdk.utils.subprocess.run", return_value=failed) as run:
            self.assertTrue(container_user_mapping_unavailable("sdk", "jimfan"))

        run.assert_called_once_with(
            ["docker", "exec", "-u", "jimfan", "sdk", "true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_container_user_mapping_available_when_probe_succeeds_after_shell_error(self):
        succeeded = Mock(returncode=0, stdout="", stderr="")
        with patch("sima_cli.sdk.utils.subprocess.run", return_value=succeeded):
            self.assertFalse(container_user_mapping_unavailable("sdk", "jimfan"))

    def test_exec_container_cmd_does_not_retry_normal_shell_failure_without_user_mapping_error(self):
        with patch("sima_cli.sdk.cmdexec.get_all_containers", return_value=[{"Names": "sdk"}]), \
             patch("sima_cli.sdk.cmdexec.container_matches_sdk_keyword", return_value=True), \
             patch("sima_cli.sdk.cmdexec.check_os", return_value="macos"), \
             patch("sima_cli.sdk.cmdexec.detect_current_user", return_value=("jimfan", 1000, 1000)), \
             patch("sima_cli.sdk.cmdexec.container_user_mapping_unavailable", return_value=False) as unavailable, \
             patch(
                 "sima_cli.sdk.cmdexec.subprocess.run",
                 return_value=Mock(returncode=127),
             ) as run:
            with self.assertRaises(SystemExit) as raised:
                exec_container_cmd(None, "neat", "eixt")

        self.assertEqual(raised.exception.code, 127)
        self.assertEqual(run.call_count, 1)
        unavailable.assert_called_once_with("sdk", "jimfan")

    def test_exec_container_cmd_retries_only_when_user_mapping_probe_fails(self):
        with patch("sima_cli.sdk.cmdexec.get_all_containers", return_value=[{"Names": "sdk"}]), \
             patch("sima_cli.sdk.cmdexec.container_matches_sdk_keyword", return_value=True), \
             patch("sima_cli.sdk.cmdexec.check_os", return_value="macos"), \
             patch("sima_cli.sdk.cmdexec.detect_current_user", return_value=("jimfan", 1000, 1000)), \
             patch("sima_cli.sdk.cmdexec.container_user_mapping_unavailable", return_value=True), \
             patch(
                 "sima_cli.sdk.cmdexec.subprocess.run",
                 side_effect=[Mock(returncode=127), Mock(returncode=0)],
             ) as run:
            exec_container_cmd(None, "neat", "eixt")

        self.assertEqual(
            run.call_args_list[0].args[0],
            ["docker", "exec", "-it", "-u", "jimfan", "sdk", "bash", "-lc", "eixt"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["docker", "exec", "-it", "sdk", "bash", "-lc", "eixt"],
        )


def _fake_proc(returncode=0, stdout="", stderr=""):
    proc = Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestRefreshNeatCertificates(unittest.TestCase):
    """Re-issue the mounted /sdk-cert cert on re-point when missing or SAN-stale."""

    def setUp(self):
        from sima_cli.sdk import neat as neat_module
        self.neat = neat_module
        self.tmp = TemporaryDirectory()
        self.workspace = self.tmp.name
        self.container = "neat-sdk-latest"
        self.cert_dir = Path(self.workspace) / f".{self.container}" / "sdk-cert"
        self.cert_dir.mkdir(parents=True)
        self.cert_file = self.cert_dir / "neat-sdk.pem"
        self.cert_file.write_text("dummy-cert")

    def tearDown(self):
        self.tmp.cleanup()

    def test_noop_when_no_host_ip(self):
        with patch.object(self.neat, "_ensure_certificates") as ensure:
            self.assertFalse(
                self.neat.refresh_neat_certificates(self.workspace, self.container, {})
            )
            ensure.assert_not_called()

    def test_noop_when_cert_already_covers_host(self):
        with patch.object(self.neat, "_container_sdk_cert_dir", return_value=self.cert_dir), \
             patch.object(self.neat, "_cert_covers_hosts", return_value=True), \
             patch.object(self.neat, "_ensure_certificates") as ensure:
            self.assertFalse(
                self.neat.refresh_neat_certificates(
                    self.workspace, self.container, {"host_ip": "10.0.0.88"}
                )
            )
            ensure.assert_not_called()

    def test_reissues_when_host_ip_not_covered(self):
        with patch.object(self.neat, "_container_sdk_cert_dir", return_value=self.cert_dir), \
             patch.object(self.neat, "_cert_covers_hosts", return_value=False), \
             patch.object(self.neat, "_ensure_certificates") as ensure:
            self.assertTrue(
                self.neat.refresh_neat_certificates(
                    self.workspace, self.container, {"host_ip": "10.0.0.88"},
                    yes_to_all=True, noninteractive=True,
                )
            )
            ensure.assert_called_once()
            self.assertEqual(ensure.call_args.args[0], self.cert_dir)

    def test_generates_when_cert_absent(self):
        self.cert_file.unlink()
        with patch.object(self.neat, "_container_sdk_cert_dir", return_value=self.cert_dir), \
             patch.object(self.neat, "_cert_covers_hosts") as covers, \
             patch.object(self.neat, "_ensure_certificates") as ensure:
            self.assertTrue(
                self.neat.refresh_neat_certificates(
                    self.workspace, self.container, {"host_ip": "10.0.0.88"}
                )
            )
            ensure.assert_called_once()
            covers.assert_not_called()  # no cert to inspect

    def test_uses_container_mount_source_not_invocation_workspace(self):
        # The container's /sdk-cert is mounted from the workspace used at
        # `docker run`; re-issuing must target THAT dir, not one rebuilt from a
        # different workspace passed on this invocation.
        mounted = Path(self.tmp.name) / "orig-ws" / f".{self.container}" / "sdk-cert"
        mounted.mkdir(parents=True)
        other_workspace = str(Path(self.tmp.name) / "different-ws")
        with patch.object(self.neat, "_container_sdk_cert_dir", return_value=mounted), \
             patch.object(self.neat, "_cert_covers_hosts", return_value=False), \
             patch.object(self.neat, "_ensure_certificates") as ensure:
            self.assertTrue(
                self.neat.refresh_neat_certificates(
                    other_workspace, self.container, {"host_ip": "10.0.0.88"}
                )
            )
            ensure.assert_called_once()
            self.assertEqual(ensure.call_args.args[0], mounted)  # mounted dir, not other_workspace

    def test_reissues_when_alternate_host_ip_missing(self):
        # Older cert covers the primary host_ip but NOT a newly discovered
        # alternate address (e.g. a VPN/tunnel IP) that is in the current host
        # list -- must re-issue rather than skip.
        san = ("X509v3 Subject Alternative Name:\n"
               "    DNS:localhost, IP Address:127.0.0.1, IP Address:10.0.0.88\n")
        with patch.object(self.neat, "_container_sdk_cert_dir", return_value=self.cert_dir), \
             patch.object(self.neat, "_collect_cert_hosts",
                          return_value=["localhost", "127.0.0.1", "10.0.0.88", "10.8.0.9"]), \
             patch("sima_cli.sdk.neat.subprocess.run", return_value=_fake_proc(stdout=san)), \
             patch.object(self.neat, "_ensure_certificates") as ensure:
            self.assertTrue(
                self.neat.refresh_neat_certificates(
                    self.workspace, self.container, {"host_ip": "10.0.0.88"}
                )
            )
            ensure.assert_called_once()

    def test_noop_when_all_current_hosts_covered(self):
        san = ("X509v3 Subject Alternative Name:\n"
               "    DNS:localhost, IP Address:127.0.0.1, IP Address:10.0.0.88, "
               "IP Address:10.8.0.9\n")
        with patch.object(self.neat, "_container_sdk_cert_dir", return_value=self.cert_dir), \
             patch.object(self.neat, "_collect_cert_hosts",
                          return_value=["localhost", "127.0.0.1", "10.0.0.88", "10.8.0.9"]), \
             patch("sima_cli.sdk.neat.subprocess.run", return_value=_fake_proc(stdout=san)), \
             patch.object(self.neat, "_ensure_certificates") as ensure:
            self.assertFalse(
                self.neat.refresh_neat_certificates(
                    self.workspace, self.container, {"host_ip": "10.0.0.88"}
                )
            )
            ensure.assert_not_called()

    def test_cert_covers_hosts_requires_all(self):
        san = ("X509v3 Subject Alternative Name:\n"
               "    DNS:localhost, IP Address:10.0.0.88, IP Address:10.8.0.9\n")
        with patch("sima_cli.sdk.neat.subprocess.run", return_value=_fake_proc(stdout=san)):
            self.assertTrue(self.neat._cert_covers_hosts(self.cert_file, ["10.0.0.88", "10.8.0.9"]))
            self.assertTrue(self.neat._cert_covers_hosts(self.cert_file, ["localhost", "10.0.0.88"]))
            self.assertFalse(self.neat._cert_covers_hosts(self.cert_file, ["10.0.0.88", "192.168.0.1"]))

    def test_cert_covers_hosts_false_when_openssl_missing(self):
        with patch("sima_cli.sdk.neat.subprocess.run", side_effect=OSError):
            self.assertFalse(self.neat._cert_covers_hosts(self.cert_file, ["10.0.0.88"]))

    def test_container_sdk_cert_dir_reads_mount_source(self):
        with patch("sima_cli.sdk.neat.subprocess.run",
                   return_value=_fake_proc(stdout="/home/u/ws/.neat-sdk-latest/sdk-cert\n")):
            self.assertEqual(
                self.neat._container_sdk_cert_dir("neat-sdk-latest"),
                Path("/home/u/ws/.neat-sdk-latest/sdk-cert"),
            )

    def test_container_sdk_cert_dir_none_when_no_mount(self):
        with patch("sima_cli.sdk.neat.subprocess.run", return_value=_fake_proc(stdout="")):
            self.assertIsNone(self.neat._container_sdk_cert_dir("neat-sdk-latest"))

    def test_cert_covers_host_parses_openssl_san(self):
        san = "X509v3 Subject Alternative Name:\n    IP Address:10.0.0.88, DNS:localhost\n"
        with patch("sima_cli.sdk.neat.subprocess.run", return_value=_fake_proc(stdout=san)):
            self.assertTrue(self.neat._cert_covers_host(self.cert_file, "10.0.0.88"))
            self.assertFalse(self.neat._cert_covers_host(self.cert_file, "10.0.0.99"))

    def test_cert_covers_host_requires_exact_ip_not_prefix(self):
        # A routed IP that is a prefix of an existing SAN entry must NOT count as
        # covered (192.168.1.5 vs IP Address:192.168.1.50), or a stale cert would
        # wrongly skip re-issue and leave a SAN mismatch.
        san = "X509v3 Subject Alternative Name:\n    IP Address:192.168.1.50, DNS:localhost\n"
        with patch("sima_cli.sdk.neat.subprocess.run", return_value=_fake_proc(stdout=san)):
            self.assertFalse(self.neat._cert_covers_host(self.cert_file, "192.168.1.5"))
            self.assertTrue(self.neat._cert_covers_host(self.cert_file, "192.168.1.50"))

    def test_cert_covers_host_false_when_openssl_missing(self):
        with patch("sima_cli.sdk.neat.subprocess.run", side_effect=OSError):
            self.assertFalse(self.neat._cert_covers_host(self.cert_file, "10.0.0.88"))


class TestCertHostCollection(unittest.TestCase):
    """The cert SAN must cover both LAN and VPN/tunnel IPs (valid VPN on/off)."""

    def setUp(self):
        from sima_cli.sdk import neat as neat_module
        self.neat = neat_module

    def test_san_includes_vpn_and_lan_ips(self):
        with patch.object(self.neat, "get_local_ip_candidates",
                          return_value=[("en0", "192.168.1.50")]), \
             patch.object(self.neat, "_collect_physical_ipv4s_for_certs",
                          return_value=["192.168.1.50"]), \
             patch.object(self.neat, "_collect_all_ipv4s_for_certs",
                          return_value=["192.168.1.50", "10.8.0.6"]):  # 10.8.0.6 = VPN
            hosts = self.neat._collect_cert_hosts({"host_ip": "10.8.0.6"})
        self.assertIn("192.168.1.50", hosts)
        self.assertIn("10.8.0.6", hosts)
        self.assertEqual(len(hosts), len(set(hosts)))  # deduped

    def test_all_collector_includes_vpn_excludes_loopback(self):
        if __import__("importlib").util.find_spec("psutil") is None:
            self.skipTest("psutil not installed")

        class _Addr:
            def __init__(self, family, address):
                self.family = family
                self.address = address
        fake = {
            "lo": [_Addr(socket.AF_INET, "127.0.0.1")],
            "en0": [_Addr(socket.AF_INET, "192.168.1.50")],
            "utun3": [_Addr(socket.AF_INET, "10.8.0.6")],   # VPN — included
        }
        with patch("psutil.net_if_addrs", return_value=fake):
            ips = self.neat._collect_all_ipv4s_for_certs()
        self.assertIn("192.168.1.50", ips)
        self.assertIn("10.8.0.6", ips)
        self.assertNotIn("127.0.0.1", ips)

if __name__ == "__main__":
    unittest.main()
