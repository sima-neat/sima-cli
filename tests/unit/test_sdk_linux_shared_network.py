import unittest
from unittest.mock import Mock, patch

from sima_cli.sdk import linux_shared_network as net


class TestLinuxSharedNetwork(unittest.TestCase):
    def test_iptables_forward_detection_requires_nm_before_docker_user(self):
        self.assertTrue(net._iptables_forward_jumps_nm_before_docker_user(
            "-A FORWARD -m comment --comment nm-shared-eno1 -j nm-sh-fw-eno1\n"
            "-A FORWARD -j DOCKER-USER\n",
            "nm-sh-fw-eno1",
        ))
        self.assertFalse(net._iptables_forward_jumps_nm_before_docker_user(
            "-A FORWARD -j DOCKER-USER\n"
            "-A FORWARD -m comment --comment nm-shared-eno1 -j nm-sh-fw-eno1\n",
            "nm-sh-fw-eno1",
        ))

    def test_iptables_forward_detection_ignores_unrelated_substrings(self):
        self.assertFalse(net._iptables_forward_jumps_nm_before_docker_user(
            "-A INPUT -m comment --comment '-j nm-sh-fw-eno1'\n"
            "-A FORWARD -j DOCKER-USER\n",
            "nm-sh-fw-eno1",
        ))

    def test_nm_shared_chain_blocks_iface_requires_same_rule(self):
        self.assertFalse(net._iptables_nm_shared_chain_blocks_iface(
            "-A nm-sh-fw-eno1 -o eno1 -j ACCEPT\n"
            "-A nm-sh-fw-eno1 -o other0 -j REJECT\n",
            "eno1",
        ))
        self.assertTrue(net._iptables_nm_shared_chain_blocks_iface(
            "-A nm-sh-fw-eno1 -o eno1 -j REJECT --reject-with icmp-port-unreachable\n",
            "eno1",
        ))

    def test_configure_nm_shared_iptables_forwarding_requires_nm_shared_method(self):
        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.78 dev eno1 src 10.42.0.1\n", stderr="")
            if cmd[:4] == ["nmcli", "-g", "GENERAL.CONNECTION", "device"]:
                return Mock(returncode=0, stdout="Shared Internet\n", stderr="")
            if cmd[:4] == ["nmcli", "-g", "ipv4.method", "connection"]:
                return Mock(returncode=0, stdout="auto\n", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.linux_shared_network._is_wsl", return_value=False), \
             patch("sima_cli.sdk.linux_shared_network._find_executable", side_effect=lambda name: name), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = net._configure_nm_shared_devkit_iptables_forwarding("10.42.0.78")

        self.assertFalse(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(cmd[:3] == ["sudo", "iptables", "-I"] for cmd in commands))

    def test_configure_nm_shared_iptables_forwarding_inserts_before_reject(self):
        docker_inspect = """[
          {
            "Id": "ca007b7ec8f512345678",
            "Options": {"com.docker.network.bridge.name": "br-sdk"},
            "IPAM": {"Config": [{"Subnet": "172.19.0.0/16"}]}
          }
        ]"""
        forward_chain = """-P FORWARD DROP
-A FORWARD -m comment --comment nm-shared-eno1 -j nm-sh-fw-eno1
-A FORWARD -j DOCKER-USER
"""
        nm_chain = """-N nm-sh-fw-eno1
-A nm-sh-fw-eno1 -o eno1 -j REJECT --reject-with icmp-port-unreachable
"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.78 dev eno1 src 10.42.0.1\n", stderr="")
            if cmd[:6] == ["ip", "-o", "-4", "addr", "show", "dev"]:
                return Mock(returncode=0, stdout="2: eno1 inet 10.42.0.1/24 brd 10.42.0.255 scope global eno1\n", stderr="")
            if cmd[:4] == ["nmcli", "-g", "GENERAL.CONNECTION", "device"]:
                return Mock(returncode=0, stdout="Shared Internet\n", stderr="")
            if cmd[:4] == ["nmcli", "-g", "ipv4.method", "connection"]:
                return Mock(returncode=0, stdout="shared\n", stderr="")
            if cmd[:4] == ["docker", "network", "inspect", "simasdkbridge"]:
                return Mock(returncode=0, stdout=docker_inspect, stderr="")
            if cmd == ["sudo", "iptables", "-S", "FORWARD"]:
                return Mock(returncode=0, stdout=forward_chain, stderr="")
            if cmd == ["sudo", "iptables", "-S", "nm-sh-fw-eno1"]:
                return Mock(returncode=0, stdout=nm_chain, stderr="")
            if cmd[:3] == ["sudo", "iptables", "-C"]:
                return Mock(returncode=1, stdout="", stderr="missing")
            if cmd[:3] == ["sudo", "iptables", "-I"]:
                return Mock(returncode=0, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.linux_shared_network._is_wsl", return_value=False), \
             patch("sima_cli.sdk.linux_shared_network._find_executable", side_effect=lambda name: name), \
             patch("sima_cli.sdk.linux_shared_network.Path.exists", return_value=False), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            applied = net._configure_nm_shared_devkit_iptables_forwarding("10.42.0.78")

        self.assertTrue(applied)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "sudo", "iptables", "-I", "nm-sh-fw-eno1", "1",
                "-i", "br-sdk", "-s", "172.19.0.0/16", "-d", "10.42.0.0/24",
                "-o", "eno1", "-j", "ACCEPT",
            ],
            commands,
        )

    def test_insert_iptables_nm_shared_allow_rule_is_idempotent(self):
        with patch("sima_cli.sdk.linux_shared_network._run_captured", return_value=Mock(returncode=0)) as run:
            inserted = net._insert_iptables_nm_shared_allow_rule(
                "iptables",
                "nm-sh-fw-eno1",
                "br-sdk",
                "172.19.0.0/16",
                "eno1",
                "10.42.0.0/24",
            )

        self.assertFalse(inserted)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:3], ["sudo", "iptables", "-C"])

    def test_iptables_rule_args_fall_back_without_bridge_iface(self):
        args = net._iptables_nm_shared_allow_rule_args(
            "nm-sh-fw-eno1",
            "",
            "172.19.0.0/16",
            "eno1",
            "10.42.0.0/24",
        )

        self.assertEqual(
            args,
            [
                "nm-sh-fw-eno1", "-s", "172.19.0.0/16", "-d", "10.42.0.0/24",
                "-o", "eno1", "-j", "ACCEPT",
            ],
        )

    def test_nm_shared_dispatcher_script_rechecks_shared_method_and_docker_network(self):
        script = net._nm_shared_dispatcher_script("eno1", "10.42.0.0/24")

        self.assertIn("DEVKIT_IFACE=eno1", script)
        self.assertIn("DEVKIT_SUBNET=10.42.0.0/24", script)
        self.assertIn("SDK_NETWORK=simasdkbridge", script)
        self.assertIn("ipv4.method", script)
        self.assertIn('[ "$METHOD" = "shared" ] || exit 0', script)
        self.assertIn("-S FORWARD", script)
        self.assertIn("FORWARD_ORDER_OK", script)
        self.assertIn("CHAIN_BLOCKS_IFACE", script)
        self.assertIn("println .Subnet", script)
        self.assertIn("[0-9][0-9.]*\\/[0-9][0-9]*", script)
        self.assertIn('DOCKER" network inspect "$SDK_NETWORK"', script)
        self.assertIn('[ "$SDK_BRIDGE" = "<no value>" ]', script)
        self.assertIn('-C "$CHAIN" -i "$SDK_BRIDGE"', script)

    def test_install_nm_shared_dispatcher_repair_writes_root_hook(self):
        status = {
            "applicable": True,
            "devkit_iface": "eno1",
            "devkit_subnet": "10.42.0.0/24",
        }
        with patch("sima_cli.sdk.linux_shared_network.nm_shared_iptables_repair_status", return_value=status), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", return_value=Mock(returncode=0, stdout="", stderr="")) as run:
            installed = net.install_nm_shared_dispatcher_repair("10.42.0.78")

        self.assertTrue(installed)
        install_cmd = run.call_args.args[0]
        self.assertEqual(install_cmd[:3], ["sudo", "sh", "-c"])
        self.assertIn(net.NM_SHARED_DISPATCHER_PATH, install_cmd[3])
        self.assertIn("mktemp /etc/NetworkManager/dispatcher.d/.90-sima-sdk-shared-network", install_cmd[3])
        self.assertIn('mv "$tmp"', install_cmd[3])
        self.assertIn("DEVKIT_IFACE=eno1", run.call_args.kwargs["input"])

    def test_install_nm_shared_dispatcher_repair_fails_when_not_applicable(self):
        status = {
            "applicable": False,
            "reason": "not-nm-shared",
        }
        with patch("sima_cli.sdk.linux_shared_network.nm_shared_iptables_repair_status", return_value=status):
            with self.assertRaisesRegex(RuntimeError, "not-nm-shared"):
                net.install_nm_shared_dispatcher_repair("10.42.0.78")

    def test_status_can_inspect_iptables_without_sudo_prompt(self):
        docker_inspect = """[
          {
            "Id": "ca007b7ec8f512345678",
            "Options": {"com.docker.network.bridge.name": "br-sdk"},
            "IPAM": {"Config": [{"Subnet": "172.19.0.0/16"}]}
          }
        ]"""

        def run_side_effect(cmd, **_kwargs):
            if cmd[:4] == ["ip", "-o", "-4", "route"]:
                return Mock(returncode=0, stdout="10.42.0.78 dev eno1 src 10.42.0.1\n", stderr="")
            if cmd[:6] == ["ip", "-o", "-4", "addr", "show", "dev"]:
                return Mock(returncode=0, stdout="2: eno1 inet 10.42.0.1/24 brd 10.42.0.255 scope global eno1\n", stderr="")
            if cmd[:4] == ["nmcli", "-g", "GENERAL.CONNECTION", "device"]:
                return Mock(returncode=0, stdout="Shared Internet\n", stderr="")
            if cmd[:4] == ["nmcli", "-g", "ipv4.method", "connection"]:
                return Mock(returncode=0, stdout="shared\n", stderr="")
            if cmd[:4] == ["docker", "network", "inspect", "simasdkbridge"]:
                return Mock(returncode=0, stdout=docker_inspect, stderr="")
            if cmd == ["sudo", "-n", "iptables", "-S", "FORWARD"]:
                return Mock(returncode=0, stdout="-A FORWARD -j nm-sh-fw-eno1\n-A FORWARD -j DOCKER-USER\n", stderr="")
            if cmd == ["sudo", "-n", "iptables", "-S", "nm-sh-fw-eno1"]:
                return Mock(returncode=0, stdout="-A nm-sh-fw-eno1 -o eno1 -j REJECT\n", stderr="")
            if cmd[:4] == ["sudo", "-n", "iptables", "-C"]:
                return Mock(returncode=1, stdout="", stderr="")
            return Mock(returncode=1, stdout="", stderr="unexpected command")

        with patch("sima_cli.sdk.linux_shared_network.platform.system", return_value="Linux"), \
             patch("sima_cli.sdk.linux_shared_network._is_wsl", return_value=False), \
             patch("sima_cli.sdk.linux_shared_network._find_executable", side_effect=lambda name: name), \
             patch("sima_cli.sdk.linux_shared_network.Path.exists", return_value=False), \
             patch("sima_cli.sdk.linux_shared_network.subprocess.run", side_effect=run_side_effect) as run:
            status = net.nm_shared_iptables_repair_status("10.42.0.78", allow_sudo_prompt=False)

        self.assertTrue(status["applicable"])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(cmd[:3] == ["sudo", "-n", "iptables"] for cmd in commands))
        self.assertFalse(any(cmd[:2] == ["sudo", "iptables"] for cmd in commands))

    def test_configure_persist_skips_dispatcher_when_iptables_backend_not_applicable(self):
        with patch("sima_cli.sdk.linux_shared_network._configure_nm_shared_devkit_forwarding"), \
             patch("sima_cli.sdk.linux_shared_network._configure_nm_shared_devkit_iptables_forwarding"), \
             patch("sima_cli.sdk.linux_shared_network.nm_shared_iptables_repair_status", return_value={"applicable": False, "reason": "forward-order-not-applicable"}), \
             patch("sima_cli.sdk.linux_shared_network.install_nm_shared_dispatcher_repair") as install, \
             patch("sima_cli.sdk.linux_shared_network._configure_nm_shared_devkit_internet"), \
             patch("sima_cli.sdk.linux_shared_network._configure_nm_shared_devkit_ipv6_internet", return_value=True):
            net.configure_linux_shared_devkit_network("10.42.0.78", persist=True)

        install.assert_not_called()

    def test_maybe_install_dispatcher_prompts_and_installs_by_default(self):
        status = {
            "applicable": True,
            "dispatcher_installed": False,
            "devkit_iface": "eno1",
            "devkit_subnet": "10.42.0.0/24",
        }
        with patch("sima_cli.sdk.linux_shared_network.nm_shared_iptables_repair_status", return_value=status), \
             patch("sima_cli.sdk.linux_shared_network.sys.stdin.isatty", return_value=True), \
             patch("sima_cli.sdk.linux_shared_network.sys.stdout.isatty", return_value=True), \
             patch("builtins.input", return_value=""), \
             patch("sima_cli.sdk.linux_shared_network.install_nm_shared_dispatcher_repair", return_value=True) as install:
            installed = net.maybe_install_nm_shared_dispatcher_repair("10.42.0.78")

        self.assertTrue(installed)
        install.assert_called_once_with("10.42.0.78", docker_network="simasdkbridge", status=status)

    def test_maybe_install_dispatcher_respects_no_response(self):
        status = {
            "applicable": True,
            "dispatcher_installed": False,
            "devkit_iface": "eno1",
            "devkit_subnet": "10.42.0.0/24",
        }
        with patch("sima_cli.sdk.linux_shared_network.nm_shared_iptables_repair_status", return_value=status), \
             patch("sima_cli.sdk.linux_shared_network.sys.stdin.isatty", return_value=True), \
             patch("sima_cli.sdk.linux_shared_network.sys.stdout.isatty", return_value=True), \
             patch("builtins.input", return_value="n"), \
             patch("sima_cli.sdk.linux_shared_network.install_nm_shared_dispatcher_repair") as install:
            installed = net.maybe_install_nm_shared_dispatcher_repair("10.42.0.78")

        self.assertFalse(installed)
        install.assert_not_called()

    def test_maybe_install_dispatcher_skips_noninteractive_without_explicit_profile(self):
        status = {
            "applicable": True,
            "dispatcher_installed": False,
            "devkit_iface": "eno1",
            "devkit_subnet": "10.42.0.0/24",
        }
        with patch("sima_cli.sdk.linux_shared_network.nm_shared_iptables_repair_status", return_value=status) as repair_status, \
             patch("sima_cli.sdk.linux_shared_network.install_nm_shared_dispatcher_repair", return_value=True) as install:
            installed = net.maybe_install_nm_shared_dispatcher_repair("10.42.0.78", noninteractive=True)

        self.assertFalse(installed)
        repair_status.assert_called_once_with("10.42.0.78", docker_network="simasdkbridge", allow_sudo_prompt=False)
        install.assert_not_called()

    def test_maybe_install_dispatcher_auto_installs_with_explicit_profile(self):
        status = {
            "applicable": True,
            "dispatcher_installed": False,
            "devkit_iface": "eno1",
            "devkit_subnet": "10.42.0.0/24",
        }
        with patch("sima_cli.sdk.linux_shared_network.nm_shared_iptables_repair_status", return_value=status) as repair_status, \
             patch("sima_cli.sdk.linux_shared_network.install_nm_shared_dispatcher_repair", return_value=True) as install:
            installed = net.maybe_install_nm_shared_dispatcher_repair(
                "10.42.0.78",
                noninteractive=True,
                persistent_network_profile=True,
            )

        self.assertTrue(installed)
        repair_status.assert_called_once_with("10.42.0.78", docker_network="simasdkbridge", allow_sudo_prompt=True)
        install.assert_called_once_with("10.42.0.78", docker_network="simasdkbridge", status=status)


if __name__ == "__main__":
    unittest.main()
