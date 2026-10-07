import unittest
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sima_cli.sdk.preinstall import (
    _detect_colima_profile,
    _colima_config_path,
    _colima_port_forwarder,
    _ensure_colima_udp_forwarding_for_insight,
    ensure_colima_resources_for_neat_sdk,
    check_colima_resources,
    check_firewall,
    check_rosetta_and_firewall,
    check_cpu_ram,
    _parse_colima_status,
    warn_if_colima_devkit_network_may_need_bridged,
)


class TestSdkPreinstall(unittest.TestCase):
    def test_colima_config_path_honors_documented_precedence(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            legacy = home / ".colima"
            xdg = home / "xdg"
            custom = home / "custom"
            custom.mkdir()

            with patch("sima_cli.sdk.preinstall.Path.home", return_value=home), \
                 patch.dict("os.environ", {"COLIMA_HOME": str(custom)}, clear=True):
                self.assertEqual(
                    _colima_config_path("work"),
                    custom / "work" / "colima.yaml",
                )

            legacy.mkdir()
            with patch("sima_cli.sdk.preinstall.Path.home", return_value=home), \
                 patch.dict("os.environ", {
                     "COLIMA_HOME": str(home / "missing"),
                     "XDG_CONFIG_HOME": str(xdg),
                 }, clear=True):
                self.assertEqual(
                    _colima_config_path("work"),
                    legacy / "work" / "colima.yaml",
                )

            legacy.rmdir()
            with patch("sima_cli.sdk.preinstall.Path.home", return_value=home), \
                 patch.dict("os.environ", {"XDG_CONFIG_HOME": str(xdg)}, clear=True):
                self.assertEqual(
                    _colima_config_path("work"),
                    xdg / "colima" / "work" / "colima.yaml",
                )

    def test_detects_named_colima_profile_from_active_docker_context(self):
        inspect = '[{"Endpoints":{"docker":{"Host":"unix:///Users/me/.colima/work/docker.sock"}}}]'
        with patch("sima_cli.sdk.preinstall.subprocess.check_output", return_value=inspect):
            self.assertEqual(_detect_colima_profile(), "work")

    def test_colima_port_forwarder_prefers_effective_status(self):
        with patch("sima_cli.sdk.preinstall._colima_status", return_value={"portForwarder": "grpc"}), \
             patch("sima_cli.sdk.preinstall._colima_config", return_value={"portForwarder": "ssh"}):
            self.assertEqual(_colima_port_forwarder("work"), "grpc")

    def test_colima_port_forwarder_falls_back_to_profile_config(self):
        with patch("sima_cli.sdk.preinstall._colima_status", return_value={}), \
             patch("sima_cli.sdk.preinstall._colima_config", return_value={"portForwarder": "ssh"}):
            self.assertEqual(_colima_port_forwarder("work"), "ssh")

    def test_colima_port_forwarder_handles_older_missing_field(self):
        with patch("sima_cli.sdk.preinstall._colima_status", return_value={}), \
             patch("sima_cli.sdk.preinstall._colima_config", return_value={}):
            self.assertEqual(_colima_port_forwarder("default"), "")

    def test_colima_udp_check_skips_grpc(self):
        with patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="work"), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", return_value="grpc"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run:
            self.assertFalse(_ensure_colima_udp_forwarding_for_insight())
        run.assert_not_called()

    def test_colima_udp_check_rejects_missing_forwarder(self):
        with patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="work"), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", return_value=""), \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            with self.assertRaisesRegex(RuntimeError, "Upgrade Colima"):
                _ensure_colima_udp_forwarding_for_insight()

    def test_colima_udp_check_can_repair_disabled_forwarding(self):
        with patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="work"), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", return_value="none"), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="y"):
            self.assertTrue(_ensure_colima_udp_forwarding_for_insight())

        self.assertIn("grpc", run.call_args_list[1].args[0])

    def test_colima_udp_check_yes_to_all_repairs_without_prompt(self):
        with patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="work"), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", return_value="ssh"), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            self.assertTrue(_ensure_colima_udp_forwarding_for_insight(yes_to_all=True))

        self.assertEqual(run.call_count, 2)

    def test_colima_resource_check_forwards_yes_to_udp_repair(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch(
                 "sima_cli.sdk.preinstall._ensure_colima_udp_forwarding_for_insight",
                 return_value=True,
             ) as ensure_udp, \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="work"), \
             patch("sima_cli.sdk.preinstall._colima_status", return_value={"cpu": 8, "memory": 16}):
            self.assertTrue(ensure_colima_resources_for_neat_sdk(
                yes_to_all=True,
                require_udp=True,
            ))

        ensure_udp.assert_called_once_with(yes_to_all=True, noninteractive=False)

    def test_colima_udp_check_noninteractive_fails_with_remediation(self):
        with patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="work"), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", return_value="ssh"), \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            with self.assertRaises(RuntimeError) as raised:
                _ensure_colima_udp_forwarding_for_insight(noninteractive=True)
        self.assertIn(
            "colima stop --profile work && colima start --profile work --port-forwarder grpc",
            str(raised.exception),
        )

    def test_colima_udp_check_requires_confirmation_and_preserves_profile(self):
        with patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="work"), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", return_value="ssh"), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="y"):
            self.assertTrue(_ensure_colima_udp_forwarding_for_insight())

        self.assertEqual(run.call_args_list[0].args[0], [
            "/opt/homebrew/bin/colima", "stop", "--profile", "work",
        ])
        self.assertEqual(run.call_args_list[1].args[0], [
            "/opt/homebrew/bin/colima", "start", "--profile", "work",
            "--port-forwarder", "grpc", "--save-config",
        ])

    def test_colima_udp_check_decline_blocks_insight_setup(self):
        with patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", return_value="ssh"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="n"):
            with self.assertRaisesRegex(RuntimeError, "--no-insight"):
                _ensure_colima_udp_forwarding_for_insight()
        run.assert_not_called()

    def test_macos_skips_firewall_check(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall.platform.machine", return_value="x86_64"), \
             patch("sima_cli.sdk.preinstall.run_command") as run_command:
            fw_failed, results = check_firewall(use_sudo=True)

        self.assertFalse(fw_failed)
        self.assertEqual(results, [])
        run_command.assert_not_called()

    def test_rosetta_wrapper_does_not_check_rosetta(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall.platform.machine", return_value="arm64"), \
             patch("sima_cli.sdk.preinstall.subprocess.check_output") as check_output:
            rosetta_failed, fw_failed, results = check_rosetta_and_firewall(use_sudo=True)

        self.assertFalse(rosetta_failed)
        self.assertFalse(fw_failed)
        self.assertEqual(results, [])
        check_output.assert_not_called()

    def test_parse_colima_status_accepts_bytes_mib_and_gib(self):
        self.assertEqual(_parse_colima_status({"cpu": 4, "memory": 8589934592}), (4, 8.0))
        self.assertEqual(_parse_colima_status({"cpu": 4, "memory": 8192}), (4, 8.0))
        self.assertEqual(_parse_colima_status({"cpu": 4, "memory": 8}), (4, 8.0))

    def test_cpu_ram_check_uses_decimal_gb_for_physical_memory(self):
        fake_psutil = types.SimpleNamespace(
            cpu_count=lambda logical=False: 4,
            virtual_memory=lambda: types.SimpleNamespace(total=16_000_000_000),
        )

        with patch.dict("sys.modules", {"psutil": fake_psutil}):
            failed, row = check_cpu_ram(min_cores=4, min_ram_gb=16)

        self.assertFalse(failed)
        self.assertEqual(row, ["CPU/RAM", "≥4 cores / ≥16 GB", "4 / 16.0 GB", "✅ PASS"])

    def test_colima_resource_check_skips_non_colima_docker(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=False), \
             patch("sima_cli.sdk.preinstall._colima_status") as status:
            restarted = ensure_colima_resources_for_neat_sdk()

        self.assertFalse(restarted)
        status.assert_not_called()

    def test_colima_report_warns_on_low_resources(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_status", return_value={"cpu": 2, "memory": 4294967296}):
            rows = check_colima_resources()

        self.assertEqual(rows, [["Colima", "≥4 CPUs / ≥8 GB RAM", "2 CPUs / 4.0 GB RAM (default)", "⚠️ WARNING"]])

    def test_colima_report_passes_on_sufficient_resources(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_status", return_value={"cpu": 4, "memory": 8589934592}):
            rows = check_colima_resources()

        self.assertEqual(rows, [["Colima", "≥4 CPUs / ≥8 GB RAM", "4 CPUs / 8.0 GB RAM (default)", "✅ PASS"]])

    def test_colima_resource_check_warns_and_allows_decline(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_status", return_value={"cpu": 2, "memory": 4294967296}), \
             patch("sima_cli.sdk.preinstall._restart_colima_with_resources") as restart, \
             patch("builtins.input", return_value="n"):
            restarted = ensure_colima_resources_for_neat_sdk()

        self.assertFalse(restarted)
        restart.assert_not_called()

    def test_colima_resource_check_restarts_in_noninteractive_mode(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_status", return_value={"cpu": 2, "memory": 4294967296}), \
             patch("sima_cli.sdk.preinstall._restart_colima_with_resources") as restart, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = ensure_colima_resources_for_neat_sdk(noninteractive=True)

        self.assertTrue(restarted)
        restart.assert_called_once_with("default")

    def test_colima_devkit_network_warning_skips_when_bridged_address_enabled(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": True, "mode": "bridged", "ip_address": "10.0.0.211"}), \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)

    def test_colima_devkit_network_warning_requires_runtime_ip_address(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": True}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="n"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()

    def test_colima_devkit_network_warning_rejects_shared_mode_address(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "shared", "interface": "bridge100", "ip_address": "192.168.64.2"},
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="n"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()

    def test_colima_devkit_network_warning_allows_decline(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="n"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()

    def test_colima_devkit_network_warning_yes_to_all_restarts_without_prompt(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = warn_if_colima_devkit_network_may_need_bridged(
                "10.0.0.244",
                yes_to_all=True,
            )

        self.assertTrue(restarted)
        self.assertEqual(run.call_args_list[0].args[0], ["/opt/homebrew/bin/colima", "stop"])
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/opt/homebrew/bin/colima",
                "start",
                "--network-address",
                "--network-mode",
                "bridged",
                "--network-interface",
                "en0",
                "--save-config",
            ],
        )

    def test_colima_devkit_network_warning_restarts_with_detected_interface(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en7"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="y"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertTrue(restarted)
        self.assertEqual(run.call_args_list[0].args[0], ["/opt/homebrew/bin/colima", "stop"])
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/opt/homebrew/bin/colima",
                "start",
                "--network-address",
                "--network-mode",
                "bridged",
                "--network-interface",
                "en7",
                "--save-config",
            ],
        )

    def test_colima_devkit_network_warning_avoids_unsafe_route_interface(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="utun7"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="y"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertTrue(restarted)
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/opt/homebrew/bin/colima",
                "start",
                "--network-address",
                "--network-mode",
                "bridged",
                "--network-interface",
                "en0",
                "--save-config",
            ],
        )

    def test_colima_devkit_network_warning_restarts_with_network_address_only_when_bridged_flags_are_unsupported(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en7"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=False), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="y"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertTrue(restarted)
        self.assertEqual(run.call_args_list[0].args[0], ["/opt/homebrew/bin/colima", "stop"])
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/opt/homebrew/bin/colima",
                "start",
                "--network-address",
                "--save-config",
            ],
        )

    def test_colima_devkit_network_warning_does_not_restart_when_network_address_is_unsupported(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=False), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=False), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()
