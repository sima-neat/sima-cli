import unittest
import types
import yaml
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sima_cli.sdk.preinstall import (
    _detect_colima_profile,
    _colima_config_path,
    _colima_instance_config,
    _colima_network_config,
    _colima_port_forwarder,
    _colima_profile_recreation_safety,
    _colima_store_path,
    _restore_colima_profile_config,
    _stage_colima_profile_config,
    _ensure_colima_udp_forwarding_for_insight,
    ensure_colima_resources_for_neat_sdk,
    check_colima_resources,
    check_firewall,
    check_rosetta_and_firewall,
    check_cpu_ram,
    _default_colima_resource_targets,
    _parse_colima_status,
    _prompt_colima_resource_targets,
    _interface_ipv4_address,
    _interface_ipv4_network,
    _set_colima_devkit_route_provision,
    _is_safe_colima_bridge_interface,
    _resolve_safe_colima_bridge_interface,
    warn_if_colima_devkit_network_may_need_bridged,
)


class TestSdkPreinstall(unittest.TestCase):
    def test_interface_ipv4_address_reads_internet_sharing_bridge(self):
        output = (
            "bridge101: flags=8a63<UP,RUNNING>\n"
            "\tinet 192.168.2.1 netmask 0xffffff00 broadcast 192.168.2.255\n"
        )
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall.subprocess.check_output", return_value=output):
            self.assertEqual(_interface_ipv4_address("bridge101"), "192.168.2.1")

    def test_interface_ipv4_network_reads_hex_netmask(self):
        output = (
            "bridge101: flags=8a63<UP,RUNNING>\n"
            "\tinet 192.168.2.1 netmask 0xffffff00 broadcast 192.168.2.255\n"
        )
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall.subprocess.check_output", return_value=output):
            self.assertEqual(_interface_ipv4_network("bridge101"), "192.168.2.0/24")

    def test_colima_route_provision_replaces_previous_sima_cli_entry(self):
        config = {
            "provision": [
                {"mode": "system", "script": "echo keep"},
                {
                    "mode": "system",
                    "script": "# sima-cli: route Internet Sharing clients through macOS\nold",
                },
            ]
        }

        _set_colima_devkit_route_provision(
            config,
            "192.168.2.0/24",
            "192.168.2.1",
        )

        self.assertEqual(config["provision"][0]["script"], "echo keep")
        self.assertEqual(len(config["provision"]), 2)
        self.assertIn(
            "ip route del table local local 192.168.2.0/24 dev lo",
            config["provision"][1]["script"],
        )
        self.assertIn(
            "ip route replace table local local 192.168.2.1/32 dev lo",
            config["provision"][1]["script"],
        )

    def test_colima_bridge_interface_rejects_known_vpn_prefixes(self):
        for interface in ("utun7", "ppp0", "ipsec0", "tailscale0", "ztabc123", "wg0", "vmenet0"):
            with self.subTest(interface=interface):
                self.assertFalse(_is_safe_colima_bridge_interface(interface))

    def test_colima_bridge_interface_resolves_internet_sharing_member(self):
        outputs = {
            ("ifconfig", "bridge100"): """bridge100: flags=8a63<UP,RUNNING>
\tmember: en7 flags=3<LEARNING,DISCOVER>
\tstatus: active
""",
            ("ifconfig", "en7"): """en7: flags=8963<UP,RUNNING>
\tmedia: autoselect (1000baseT <full-duplex>)
\tstatus: active
""",
        }

        with patch(
            "sima_cli.sdk.preinstall.subprocess.check_output",
            side_effect=lambda command, **_kwargs: outputs[tuple(command)],
        ):
            interface = _resolve_safe_colima_bridge_interface("bridge100")

        self.assertEqual(interface, "en7")

    def test_colima_bridge_interface_ignores_active_vmenet_member(self):
        outputs = {
            ("ifconfig", "bridge100"): """bridge100: flags=8a63<UP,RUNNING>
\tmember: en7 flags=3<LEARNING,DISCOVER>
\tmember: vmenet0 flags=20803<LEARNING,DISCOVER,PRIVATE,VIRTIO>
\tstatus: active
""",
            ("ifconfig", "en7"): "en7: flags=8963<UP,RUNNING>\n\tstatus: active\n",
        }

        with patch(
            "sima_cli.sdk.preinstall.subprocess.check_output",
            side_effect=lambda command, **_kwargs: outputs[tuple(command)],
        ):
            interface = _resolve_safe_colima_bridge_interface("bridge100")

        self.assertEqual(interface, "en7")

    def test_colima_bridge_interface_refuses_ambiguous_active_members(self):
        outputs = {
            ("ifconfig", "bridge100"): """bridge100: flags=8a63<UP,RUNNING>
\tmember: en7 flags=3<LEARNING,DISCOVER>
\tmember: en8 flags=3<LEARNING,DISCOVER>
\tstatus: active
""",
            ("ifconfig", "en7"): "en7: flags=8963<UP,RUNNING>\n\tstatus: active\n",
            ("ifconfig", "en8"): "en8: flags=8963<UP,RUNNING>\n\tstatus: active\n",
        }

        with patch(
            "sima_cli.sdk.preinstall.subprocess.check_output",
            side_effect=lambda command, **_kwargs: outputs[tuple(command)],
        ):
            interface = _resolve_safe_colima_bridge_interface("bridge100")

        self.assertEqual(interface, "")

    def test_colima_bridge_interface_refuses_inactive_member(self):
        outputs = {
            ("ifconfig", "bridge100"): """bridge100: flags=8a63<UP,RUNNING>
\tmember: en7 flags=3<LEARNING,DISCOVER>
\tstatus: active
""",
            ("ifconfig", "en7"): "en7: flags=8863<UP>\n\tstatus: inactive\n",
        }

        with patch(
            "sima_cli.sdk.preinstall.subprocess.check_output",
            side_effect=lambda command, **_kwargs: outputs[tuple(command)],
        ):
            interface = _resolve_safe_colima_bridge_interface("bridge100")

        self.assertEqual(interface, "")

    def test_colima_instance_config_honors_lima_home(self):
        with TemporaryDirectory() as tmpdir:
            lima_home = Path(tmpdir) / "lima"
            instance_config = lima_home / "colima-work" / "lima.yaml"
            instance_config.parent.mkdir(parents=True)
            instance_config.write_text(
                "network:\n  address: true\n  mode: bridged\n  interface: en7\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"LIMA_HOME": str(lima_home)}, clear=True):
                config = _colima_instance_config("work")

        self.assertEqual(config["network"]["mode"], "bridged")
        self.assertEqual(config["network"]["interface"], "en7")

    def test_colima_network_config_uses_generated_instance_mode(self):
        with patch(
            "sima_cli.sdk.preinstall._colima_status",
            return_value={"ip_address": "192.168.64.2", "cpu": 4, "memory": 8589934592},
        ), patch(
            "sima_cli.sdk.preinstall._colima_config",
            return_value={"network": {"address": True, "mode": "bridged", "interface": "en0"}},
        ), patch(
            "sima_cli.sdk.preinstall._colima_instance_config",
            return_value={"network": {"address": True, "mode": "shared", "interface": "en0"}},
        ):
            network = _colima_network_config("default")

        self.assertEqual(network["mode"], "shared")

    def test_colima_network_config_reads_active_host_address_forward(self):
        forward = {
            "guestIP": "192.168.2.1",
            "guestPortRange": [1, 65535],
            "hostIP": "192.168.2.1",
            "hostPortRange": [1, 65535],
            "proto": "tcp",
        }
        with patch("sima_cli.sdk.preinstall._colima_status", return_value={}), \
             patch(
                 "sima_cli.sdk.preinstall._colima_config",
                 return_value={"network": {"address": False, "mode": "shared", "hostAddresses": True}},
             ), \
             patch(
                 "sima_cli.sdk.preinstall._colima_instance_config",
                 return_value={"portForwards": [forward]},
             ):
            network = _colima_network_config("default")

        self.assertEqual(network["forwarded_host_ips"], ["192.168.2.1"])

    def test_colima_profile_recreation_safety_requires_separate_docker_disk(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "default" / "colima.yaml"
            store_path = Path(tmpdir) / "_store" / "colima.json"
            config_path.parent.mkdir(parents=True)
            store_path.parent.mkdir(parents=True)
            config_path.write_text("kubernetes:\n  enabled: false\n", encoding="utf-8")
            store_path.write_text(
                '{"disk_formatted": true, "disk_runtime": "docker"}\n',
                encoding="utf-8",
            )

            with patch("sima_cli.sdk.preinstall._colima_config_path", return_value=config_path), \
                 patch("sima_cli.sdk.preinstall._colima_status", return_value={"kubernetes": False}):
                self.assertEqual(_colima_store_path("default"), store_path)
                self.assertEqual(_colima_profile_recreation_safety("default"), (True, ""))

                store_path.write_text(
                    '{"disk_formatted": false, "disk_runtime": ""}\n',
                    encoding="utf-8",
                )
                safe, reason = _colima_profile_recreation_safety("default")

            self.assertFalse(safe)
            self.assertIn("legacy profile", reason)

    def test_colima_profile_recreation_safety_rejects_kubernetes(self):
        with patch(
            "sima_cli.sdk.preinstall._colima_config",
            return_value={"kubernetes": {"enabled": True}},
        ):
            safe, reason = _colima_profile_recreation_safety("default")

        self.assertFalse(safe)
        self.assertIn("Kubernetes", reason)

    def test_colima_profile_recreation_safety_rejects_live_kubernetes_with_stale_config(self):
        with patch(
            "sima_cli.sdk.preinstall._colima_config",
            return_value={"kubernetes": {"enabled": False}},
        ), patch(
            "sima_cli.sdk.preinstall._colima_status",
            return_value={"kubernetes": True},
        ):
            safe, reason = _colima_profile_recreation_safety("default")

        self.assertFalse(safe)
        self.assertIn("running", reason)

    def test_colima_profile_recreation_safety_fails_closed_without_live_kubernetes_state(self):
        with patch(
            "sima_cli.sdk.preinstall._colima_config",
            return_value={"kubernetes": {"enabled": False}},
        ), patch("sima_cli.sdk.preinstall._colima_status", return_value={}):
            safe, reason = _colima_profile_recreation_safety("default")

        self.assertFalse(safe)
        self.assertIn("could not be verified", reason)

    def test_colima_profile_config_can_be_restored_after_profile_recreation(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "colima" / "default" / "colima.yaml"
            config_path.parent.mkdir(parents=True)
            original = (
                "cpu: 10\n"
                "portForwarder: grpc\n"
                "network:\n"
                "  mode: shared\n"
                "  subnet: 192.168.5.0/24\n"
                "  nat66Prefix: \"fd00:1234:5678:9abc::\"\n"
            )
            config_path.write_text(original, encoding="utf-8")
            config_path.chmod(0o640)
            config_path.parent.chmod(0o710)

            with patch("sima_cli.sdk.preinstall._colima_config_path", return_value=config_path):
                restored_path, snapshot_path, config_mode, config_dir_mode = \
                    _stage_colima_profile_config("default", "en7")

            self.assertEqual(snapshot_path.stat().st_mode & 0o777, 0o600)

            config_path.unlink()
            config_path.parent.rmdir()
            _restore_colima_profile_config(
                restored_path,
                snapshot_path,
                config_mode,
                config_dir_mode,
            )

            restored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["cpu"], 10)
            self.assertEqual(restored["portForwarder"], "grpc")
            self.assertEqual(
                restored["network"],
                {"address": True, "mode": "bridged", "interface": "en7"},
            )
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(config_path.parent.stat().st_mode & 0o777, 0o710)
            snapshot_path.unlink()

    def test_colima_profile_config_uses_host_forwarding_for_internet_sharing(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "colima" / "default" / "colima.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "network:\n"
                "  address: true\n"
                "  mode: bridged\n"
                "  interface: en14\n",
                encoding="utf-8",
            )

            with patch("sima_cli.sdk.preinstall._colima_config_path", return_value=config_path):
                _, snapshot_path, _, _ = _stage_colima_profile_config(
                    "default",
                    "en14",
                    internet_sharing=True,
                    shared_network="192.168.2.0/24",
                    host_ip="192.168.2.1",
                )

            staged = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(
                staged["network"],
                {"address": False, "mode": "shared", "hostAddresses": True},
            )
            self.assertIn(
                "192.168.2.0/24",
                staged["provision"][-1]["script"],
            )
            snapshot_path.unlink()

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
             patch("sima_cli.sdk.preinstall._host_colima_resource_limits", return_value=(32, 64)), \
             patch("sima_cli.sdk.preinstall._restart_colima_with_resources") as restart, \
             patch("builtins.input", side_effect=["", "", "n"]):
            restarted = ensure_colima_resources_for_neat_sdk()

        self.assertFalse(restarted)
        restart.assert_not_called()

    def test_colima_resource_check_restarts_in_noninteractive_mode(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_status", return_value={"cpu": 2, "memory": 4294967296}), \
             patch("sima_cli.sdk.preinstall._host_colima_resource_limits", return_value=(32, 64)), \
             patch("sima_cli.sdk.preinstall._restart_colima_with_resources") as restart, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = ensure_colima_resources_for_neat_sdk(noninteractive=True)

        self.assertTrue(restarted)
        restart.assert_called_once_with("default", 16, 32.0)

    def test_colima_resource_check_accepts_custom_resources(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_status", return_value={"cpu": 2, "memory": 2147483648}), \
             patch("sima_cli.sdk.preinstall._host_colima_resource_limits", return_value=(32, 64)), \
             patch("sima_cli.sdk.preinstall._restart_colima_with_resources") as restart, \
             patch("builtins.input", side_effect=["12", "24", ""]):
            restarted = ensure_colima_resources_for_neat_sdk()

        self.assertTrue(restarted)
        restart.assert_called_once_with("default", 12, 24.0)

    def test_colima_resource_prompt_rejects_values_above_host_capacity(self):
        with patch(
            "sima_cli.sdk.preinstall._host_colima_resource_limits",
            return_value=(8, 16),
        ), patch("builtins.input", side_effect=["9", "8", "17", "16"]):
            targets = _prompt_colima_resource_targets(2, 2)

        self.assertEqual(targets, (8, 16.0))

    def test_colima_resource_defaults_use_half_host_without_reducing_existing(self):
        with patch(
            "sima_cli.sdk.preinstall._host_colima_resource_limits",
            return_value=(22, 63),
        ):
            self.assertEqual(_default_colima_resource_targets(2, 2), (11, 31.5))
            self.assertEqual(_default_colima_resource_targets(20, 48), (20, 48))

    def test_colima_devkit_network_warning_skips_when_bridged_address_enabled(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": True, "mode": "bridged", "interface": "en0", "ip_address": "10.0.0.211"}), \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)

    def test_colima_devkit_network_warning_rejects_wrong_bridge_interface(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en7"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={
                     "address": True,
                     "mode": "bridged",
                     "interface": "en0",
                     "ip_address": "192.168.1.20",
                 },
             ), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="n"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()

    def test_colima_devkit_network_warning_requires_runtime_ip_address(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": True}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
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
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="n"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()

    def test_colima_devkit_network_warning_allows_decline(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", side_effect=[
                 {"address": False, "mode": "shared"},
                 {"address": True, "mode": "bridged", "ip_address": "10.0.0.211"},
             ]), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="n"):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()

    def test_colima_devkit_network_warning_yes_to_all_restarts_without_prompt(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", side_effect=[
                 {"address": False, "mode": "shared", "cpu": 2, "memory": 2147483648},
                 {"address": True, "mode": "bridged", "ip_address": "10.0.0.211", "cpu": 16, "memory": 34359738368},
             ]), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_port_forwarder", side_effect=["ssh", "grpc"]), \
             patch("sima_cli.sdk.preinstall._host_colima_resource_limits", return_value=(32, 64)), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch(
                 "sima_cli.sdk.preinstall._stage_colima_profile_config",
                 return_value=(Path("/profile/colima.yaml"), Path("/tmp/missing-colima-snapshot"), 0o600, 0o700),
             ), \
             patch("sima_cli.sdk.preinstall._restore_colima_profile_config") as restore, \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch.dict("os.environ", {"COLIMA_PROFILE": "unrelated"}), \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = warn_if_colima_devkit_network_may_need_bridged(
                "10.0.0.244",
                yes_to_all=True,
                require_udp=True,
            )

        self.assertTrue(restarted)
        self.assertEqual(
            run.call_args_list[0].args[0],
            ["/opt/homebrew/bin/colima", "stop", "--profile", "default"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/opt/homebrew/bin/colima", "delete", "--profile", "default", "--force"],
        )
        restore.assert_called_once_with(
            Path("/profile/colima.yaml"),
            Path("/tmp/missing-colima-snapshot"),
            0o600,
            0o700,
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            [
                "/opt/homebrew/bin/colima",
                "start",
                "--profile",
                "default",
                "--network-address",
                "--network-mode",
                "bridged",
                "--network-interface",
                "en0",
                "--port-forwarder",
                "grpc",
                "--cpu",
                "16",
                "--memory",
                "32",
                "--save-config",
            ],
        )

    def test_colima_internet_sharing_uses_shared_host_address_forwarding(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", side_effect=[
                 {
                     "address": False,
                     "mode": "shared",
                     "host_addresses": True,
                     "forwarded_host_ips": [],
                     "cpu": 10,
                     "memory": 21474836480,
                 },
                 {
                     "address": False,
                     "mode": "shared",
                     "host_addresses": True,
                     "forwarded_host_ips": ["192.168.2.1"],
                     "cpu": 10,
                     "memory": 21474836480,
                 },
             ]), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="bridge101"), \
             patch("sima_cli.sdk.preinstall._interface_ipv4_address", return_value="192.168.2.1"), \
             patch("sima_cli.sdk.preinstall._interface_ipv4_network", return_value="192.168.2.0/24"), \
             patch("sima_cli.sdk.preinstall._resolve_safe_colima_bridge_interface", return_value="en14"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", side_effect=AssertionError("bridged flags are not needed")), \
             patch("sima_cli.sdk.preinstall._colima_supports_host_addresses_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch(
                 "sima_cli.sdk.preinstall._stage_colima_profile_config",
                 return_value=(Path("/profile/colima.yaml"), Path("/tmp/missing-colima-snapshot"), 0o600, 0o700),
             ) as stage, \
             patch("sima_cli.sdk.preinstall._restore_colima_profile_config"), \
             patch("sima_cli.sdk.preinstall._ensure_colima_internet_sharing_route") as ensure_route, \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("sima_cli.sdk.preinstall.console.print") as console_print, \
             patch("builtins.input", return_value="y") as prompt:
            restarted = warn_if_colima_devkit_network_may_need_bridged("192.168.2.2")

        self.assertTrue(restarted)
        panel = console_print.call_args_list[0].args[0]
        self.assertEqual(panel.title, "Colima Network Update")
        self.assertNotIn("\n\n\n", panel.renderable)
        self.assertIn("one-time network update", panel.renderable)
        self.assertNotIn("data loss", panel.renderable.lower())
        prompt.assert_called_once_with(
            "Apply the Colima network update for Internet Sharing now? "
            "Running containers will stop briefly; Docker data will be kept. [y/N]: "
        )
        stage.assert_called_once_with(
            "default",
            "en14",
            internet_sharing=True,
            shared_network="192.168.2.0/24",
            host_ip="192.168.2.1",
        )
        ensure_route.assert_called_once_with(
            "default",
            "192.168.2.2",
            "192.168.2.0/24",
            "192.168.2.1",
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            [
                "/opt/homebrew/bin/colima",
                "start",
                "--profile",
                "default",
                "--network-address=false",
                "--network-mode",
                "shared",
                "--network-host-addresses",
                "--save-config",
            ],
        )

    def test_colima_internet_sharing_requires_host_address_support(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False, "mode": "shared"}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="bridge101"), \
             patch("sima_cli.sdk.preinstall._interface_ipv4_address", return_value="192.168.2.1"), \
             patch("sima_cli.sdk.preinstall._interface_ipv4_network", return_value="192.168.2.0/24"), \
             patch("sima_cli.sdk.preinstall._resolve_safe_colima_bridge_interface", return_value="en14"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_host_addresses_flag", return_value=False), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run:
            restarted = warn_if_colima_devkit_network_may_need_bridged("192.168.2.2")

        self.assertFalse(restarted)
        run.assert_not_called()

    def test_colima_devkit_network_warning_noninteractive_requires_explicit_yes(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "shared", "ip_address": "192.168.64.2"},
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            with self.assertRaisesRegex(RuntimeError, "pass --yes"):
                warn_if_colima_devkit_network_may_need_bridged(
                    "10.0.0.244",
                    noninteractive=True,
                )

        run.assert_not_called()

    def test_colima_devkit_network_warning_does_not_delete_unsafe_profile(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "shared", "ip_address": "192.168.64.2"},
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch(
                 "sima_cli.sdk.preinstall._colima_profile_recreation_safety",
                 return_value=(False, "This profile does not use Colima's separate runtime disk."),
             ), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            with self.assertRaisesRegex(RuntimeError, "will not recreate"):
                warn_if_colima_devkit_network_may_need_bridged(
                    "10.0.0.244",
                    yes_to_all=True,
                )

        run.assert_not_called()

    def test_colima_devkit_network_warning_verifies_recreated_profile(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "shared", "ip_address": "192.168.64.2"},
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en0"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch(
                 "sima_cli.sdk.preinstall._stage_colima_profile_config",
                 return_value=(Path("/profile/colima.yaml"), Path("/tmp/missing-colima-snapshot"), 0o600, 0o700),
             ), \
             patch("sima_cli.sdk.preinstall._restore_colima_profile_config"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", return_value="y"):
            with self.assertRaisesRegex(RuntimeError, "did not report"):
                warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/opt/homebrew/bin/colima", "delete", "--profile", "default", "--force"],
        )

    def test_colima_devkit_network_warning_restarts_with_detected_interface(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", side_effect=[
                 {"address": False, "mode": "shared", "cpu": 2, "memory": 2147483648},
                 {"address": True, "mode": "bridged", "interface": "en7", "ip_address": "10.0.0.212", "cpu": 10, "memory": 21474836480},
             ]), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en7"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=True), \
             patch("sima_cli.sdk.preinstall._host_colima_resource_limits", return_value=(32, 64)), \
             patch("sima_cli.sdk.preinstall._colima_profile_recreation_safety", return_value=(True, "")), \
             patch("sima_cli.sdk.preinstall.shutil.which", return_value="/opt/homebrew/bin/colima"), \
             patch(
                 "sima_cli.sdk.preinstall._stage_colima_profile_config",
                 return_value=(Path("/profile/colima.yaml"), Path("/tmp/missing-colima-snapshot"), 0o600, 0o700),
             ), \
             patch("sima_cli.sdk.preinstall._restore_colima_profile_config"), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=["10", "20", "y"]):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertTrue(restarted)
        self.assertEqual(
            run.call_args_list[0].args[0],
            ["/opt/homebrew/bin/colima", "stop", "--profile", "default"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/opt/homebrew/bin/colima", "delete", "--profile", "default", "--force"],
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            [
                "/opt/homebrew/bin/colima",
                "start",
                "--profile",
                "default",
                "--network-address",
                "--network-mode",
                "bridged",
                "--network-interface",
                "en7",
                "--cpu",
                "10",
                "--memory",
                "20",
                "--save-config",
            ],
        )

    def test_colima_devkit_network_warning_refuses_unsafe_route_interface(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="utun7"), \
             patch("sima_cli.sdk.preinstall._colima_network_config") as network_config, \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            with self.assertRaisesRegex(RuntimeError, "safe physical interface"):
                warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        network_config.assert_not_called()
        run.assert_not_called()

    def test_colima_devkit_network_warning_does_not_restart_when_bridged_flags_are_unsupported(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch("sima_cli.sdk.preinstall._colima_network_config", return_value={"address": False}), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en7"), \
             patch("sima_cli.sdk.preinstall._colima_supports_network_address_flag", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_supports_bridged_network_flags", return_value=False), \
             patch("sima_cli.sdk.preinstall.subprocess.run") as run, \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            restarted = warn_if_colima_devkit_network_may_need_bridged("10.0.0.244")

        self.assertFalse(restarted)
        run.assert_not_called()

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
