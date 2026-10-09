import subprocess
import socket
import unittest
from unittest.mock import Mock, patch

from sima_cli.sdk.container_registry import (
    ContainerRegistryConfig,
    REGISTRY_CONTAINER_NAME,
    REGISTRY_VOLUME_NAME,
    _inspect_registry,
    _require_available_host_port,
    ensure_container_registry,
    find_available_container_registry_port,
    repair_existing_container_registry,
    resolve_container_registry_bind_ip,
)
from sima_cli.sdk.install import _setup_devkit_container_registry
from sima_cli.sdk.utils import (
    _configure_container_registry_environment,
    bootstrap_devkit_container,
)


class TestSdkContainerRegistry(unittest.TestCase):
    def test_reports_host_port_conflict_before_starting_registry(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        try:
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                _require_available_host_port(port)
        finally:
            listener.close()

    def test_chooses_next_registry_port_when_default_is_busy(self):
        with patch(
            "sima_cli.sdk.container_registry._host_port_is_available",
            side_effect=[False, False, True],
        ) as available:
            port = find_available_container_registry_port(5050)

        self.assertEqual(port, 5052)
        self.assertEqual(
            [item.args[0] for item in available.call_args_list],
            [5050, 5051, 5052],
        )

    def test_creates_persistent_registry_and_returns_both_addresses(self):
        completed = Mock(returncode=0, stdout="registry-id\n", stderr="")
        with patch("sima_cli.sdk.container_registry._inspect_registry", return_value=None), \
             patch("sima_cli.sdk.container_registry._require_available_host_port") as port_check, \
             patch("sima_cli.sdk.container_registry._wait_for_registry") as wait, \
             patch("sima_cli.sdk.container_registry.subprocess.run", return_value=completed) as run:
            config = ensure_container_registry("10.42.0.1", requested_port=5000)

        self.assertEqual(config.sdk_address, "localhost:5000")
        self.assertEqual(config.devkit_address, "10.42.0.1:5000")
        commands = [item.args[0] for item in run.call_args_list]
        self.assertIn(["docker", "volume", "create", REGISTRY_VOLUME_NAME], commands)
        registry_run = next(command for command in commands if command[:3] == ["docker", "run", "-d"])
        self.assertIn("127.0.0.1:5000:5000", registry_run)
        self.assertIn("10.42.0.1:5000:5000", registry_run)
        self.assertNotIn("5000:5000", registry_run)
        self.assertIn(f"{REGISTRY_VOLUME_NAME}:/var/lib/registry", registry_run)
        port_check.assert_called_once_with(5000)
        wait.assert_called_once_with(5000)

    def test_reads_published_registry_ports(self):
        inspected = Mock(
            returncode=0,
            stdout=(
                'container-registry|5050|10.42.0.1|true|'
                '{"5000/tcp":[{"HostIp":"127.0.0.1","HostPort":"5050"},'
                '{"HostIp":"10.42.0.1","HostPort":"5050"}]}\n'
            ),
        )
        with patch("sima_cli.sdk.container_registry.subprocess.run", return_value=inspected):
            existing = _inspect_registry()

        self.assertEqual(existing["published"], {"127.0.0.1:5050", "10.42.0.1:5050"})

    def test_reads_registry_without_published_ports(self):
        inspected = Mock(returncode=0, stdout='container-registry|5050|10.42.0.1|true|{"5000/tcp":null}\n')
        with patch("sima_cli.sdk.container_registry.subprocess.run", return_value=inspected):
            existing = _inspect_registry()

        self.assertEqual(existing["published"], set())

    def test_explicit_registry_inspection_rejects_unmanaged_container(self):
        inspected = Mock(
            returncode=0,
            stdout='<no value>|<no value>|<no value>|true|{"5000/tcp":null}\n',
        )
        with patch("sima_cli.sdk.container_registry.subprocess.run", return_value=inspected), \
             self.assertRaisesRegex(RuntimeError, "not created by sima-cli"):
            _inspect_registry()

    def test_automatic_registry_repair_ignores_unmanaged_container(self):
        inspected = Mock(
            returncode=0,
            stdout='<no value>|<no value>|<no value>|true|{"5000/tcp":null}\n',
        )
        with patch("sima_cli.sdk.container_registry.subprocess.run", return_value=inspected), \
             patch("sima_cli.sdk.container_registry.ensure_container_registry") as ensure:
            repair_existing_container_registry()

        ensure.assert_not_called()

    def test_reuses_running_registry(self):
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value={
                "port": 5000,
                "bind_ip": "10.42.0.1",
                "running": True,
                "published": {"127.0.0.1:5000", "10.42.0.1:5000"},
            },
        ), patch("sima_cli.sdk.container_registry._wait_for_registry") as wait, \
             patch("sima_cli.sdk.container_registry.subprocess.run") as run:
            config = ensure_container_registry("10.42.0.1")

        self.assertEqual(config.port, 5000)
        run.assert_not_called()
        wait.assert_called_once_with(5000)

    def _assert_recreates_registry(self, existing):
        completed = Mock(returncode=0, stdout="registry-id\n", stderr="")
        with patch("sima_cli.sdk.container_registry._inspect_registry", return_value=existing), \
             patch("sima_cli.sdk.container_registry._require_available_host_port") as port_check, \
             patch("sima_cli.sdk.container_registry._wait_for_registry"), \
             patch("sima_cli.sdk.container_registry.subprocess.run", return_value=completed) as run:
            ensure_container_registry("10.42.0.1")

        port_check.assert_not_called()
        commands = [item.args[0] for item in run.call_args_list]
        self.assertIn(["docker", "rm", "-f", REGISTRY_CONTAINER_NAME], commands)
        registry_run = next(command for command in commands if command[:3] == ["docker", "run", "-d"])
        self.assertIn("127.0.0.1:5050:5000", registry_run)
        self.assertIn("10.42.0.1:5050:5000", registry_run)
        self.assertFalse(any(command[:3] == ["docker", "volume", "rm"] for command in commands))

    def test_recreates_running_registry_without_published_ports(self):
        self._assert_recreates_registry(
            {"port": 5050, "bind_ip": "10.42.0.1", "running": True, "published": set()}
        )

    def test_recreates_stopped_registry(self):
        self._assert_recreates_registry(
            {"port": 5050, "bind_ip": "10.42.0.1", "running": False, "published": set()}
        )

    def test_repair_recreates_registry_from_recorded_address(self):
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value={"port": 5050, "bind_ip": "10.42.0.1", "running": True, "published": set()},
        ), patch(
            "sima_cli.sdk.container_registry._registry_bind_ip_is_available",
            return_value=True,
        ), patch("sima_cli.sdk.container_registry.ensure_container_registry") as ensure:
            repair_existing_container_registry()

        ensure.assert_called_once_with("10.42.0.1", requested_port=5050)

    def test_repair_is_deferred_when_recorded_address_is_offline(self):
        existing = {
            "port": 5050,
            "bind_ip": "10.42.0.1",
            "running": False,
            "published": set(),
        }
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value=existing,
        ), patch(
            "sima_cli.sdk.container_registry._registry_bind_ip_is_available",
            return_value=False,
        ), patch("sima_cli.sdk.container_registry.ensure_container_registry") as ensure, \
             patch("builtins.print") as print_mock:
            repair_existing_container_registry()

        ensure.assert_not_called()
        print_mock.assert_called_once()
        self.assertIn("repair deferred", print_mock.call_args.args[0])

    def test_repair_leaves_healthy_registry_unchanged(self):
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value={
                "port": 5050,
                "bind_ip": "10.42.0.1",
                "running": True,
                "published": {"127.0.0.1:5050", "10.42.0.1:5050"},
            },
        ), patch("sima_cli.sdk.container_registry.ensure_container_registry") as ensure:
            repair_existing_container_registry()

        ensure.assert_not_called()

    def test_reconfigures_port_without_deleting_registry_volume(self):
        completed = Mock(returncode=0, stdout="registry-id\n", stderr="")
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value={"port": 5000, "bind_ip": "10.42.0.1", "running": True},
        ), patch("sima_cli.sdk.container_registry._require_available_host_port") as port_check, \
             patch("sima_cli.sdk.container_registry._wait_for_registry"), \
             patch("sima_cli.sdk.container_registry.subprocess.run", return_value=completed) as run:
            config = ensure_container_registry("10.42.0.1", requested_port=5050)

        self.assertEqual(config.port, 5050)
        commands = [item.args[0] for item in run.call_args_list]
        self.assertIn(["docker", "rm", "-f", REGISTRY_CONTAINER_NAME], commands)
        self.assertFalse(any(command[:3] == ["docker", "volume", "rm"] for command in commands))
        port_check.assert_any_call(5050)

    def test_reconfigures_legacy_all_interface_registry_without_deleting_volume(self):
        completed = Mock(returncode=0, stdout="registry-id\n", stderr="")
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value={"port": 5050, "bind_ip": None, "running": True},
        ), patch("sima_cli.sdk.container_registry._require_available_host_port"), \
             patch("sima_cli.sdk.container_registry._wait_for_registry"), \
             patch("sima_cli.sdk.container_registry.subprocess.run", return_value=completed) as run:
            ensure_container_registry("10.42.0.1", requested_port=5050)

        commands = [item.args[0] for item in run.call_args_list]
        self.assertIn(["docker", "rm", "-f", REGISTRY_CONTAINER_NAME], commands)
        registry_run = next(command for command in commands if command[:3] == ["docker", "run", "-d"])
        self.assertIn("127.0.0.1:5050:5000", registry_run)
        self.assertIn("10.42.0.1:5050:5000", registry_run)
        self.assertFalse(any(command[:3] == ["docker", "volume", "rm"] for command in commands))

    def test_colima_uses_bridged_vm_address_for_devkit_binding(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "bridged", "interface": "en7", "ip_address": "10.42.0.10"},
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en7"):
            self.assertEqual(
                resolve_container_registry_bind_ip("10.42.0.1", "10.42.0.2"),
                "10.42.0.10",
            )

    def test_colima_uses_internet_sharing_bridge_member_for_devkit_binding(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                return_value={
                    "address": False,
                    "mode": "shared",
                    "host_addresses": True,
                    "forwarded_host_ips": ["192.168.2.1"],
                },
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="bridge100"), \
             patch("sima_cli.sdk.preinstall._resolve_safe_colima_bridge_interface", return_value="en7"):
            self.assertEqual(
                resolve_container_registry_bind_ip("192.168.2.1", "192.168.2.3"),
                "192.168.2.1",
            )

    def test_colima_rejects_bridged_mode_for_internet_sharing(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "bridged", "interface": "en7", "ip_address": "192.168.2.2"},
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="bridge100"), \
             patch("sima_cli.sdk.preinstall._resolve_safe_colima_bridge_interface", return_value="en7"), \
             self.assertRaisesRegex(RuntimeError, "host-address forwarding"):
            resolve_container_registry_bind_ip("192.168.2.1", "192.168.2.3")

    def test_colima_rejects_stale_internet_sharing_forward(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={
                     "address": False,
                     "mode": "shared",
                     "host_addresses": True,
                     "forwarded_host_ips": ["10.0.0.210"],
                 },
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="bridge100"), \
             patch("sima_cli.sdk.preinstall._resolve_safe_colima_bridge_interface", return_value="en7"), \
             self.assertRaisesRegex(RuntimeError, "host-address forwarding"):
            resolve_container_registry_bind_ip("192.168.2.1", "192.168.2.3")

    def test_colima_uses_host_tunnel_address_for_cloudex_binding(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={
                     "address": False,
                     "mode": "shared",
                     "host_addresses": True,
                     "forwarded_host_ips": ["100.96.0.2"],
                 },
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="utun9"), \
             patch("sima_cli.sdk.preinstall._colima_vm_has_host_address", return_value=True), \
             patch("sima_cli.sdk.preinstall._colima_vm_tunnel_route_ready", return_value=True):
            self.assertEqual(
                resolve_container_registry_bind_ip("100.96.0.2", "100.96.0.10"),
                "100.96.0.2",
            )

    def test_colima_rejects_cloudex_binding_without_host_addresses(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={
                     "address": False,
                     "mode": "shared",
                     "host_addresses": False,
                 },
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="utun9"), \
             self.assertRaisesRegex(RuntimeError, "host-address forwarding"):
            resolve_container_registry_bind_ip("100.96.0.2", "100.96.0.10")

    def test_colima_rejects_bridge_on_wrong_devkit_interface(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "bridged", "interface": "en0", "ip_address": "10.42.0.10"},
             ), \
             patch("sima_cli.sdk.preinstall._route_interface_for_target", return_value="en7"), \
             self.assertRaisesRegex(RuntimeError, "bridged to en0"):
            resolve_container_registry_bind_ip("10.42.0.1", "10.42.0.2")

    def test_colima_rejects_shared_vm_address_for_devkit_binding(self):
        with patch("sima_cli.sdk.container_registry.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall._is_docker_using_colima", return_value=True), \
             patch("sima_cli.sdk.preinstall._detect_colima_profile", return_value="default"), \
             patch(
                 "sima_cli.sdk.preinstall._colima_network_config",
                 return_value={"address": True, "mode": "shared", "ip_address": "192.168.64.2"},
             ), self.assertRaisesRegex(RuntimeError, "bridged"):
            resolve_container_registry_bind_ip("10.42.0.1")

    def test_setup_can_skip_registry(self):
        devkit_env = {"host_ip": "10.42.0.1", "devkit_ip": "10.42.0.2"}
        with patch("sima_cli.sdk.install.ensure_container_registry") as ensure:
            result = _setup_devkit_container_registry(
                devkit_env,
                no_container_registry=True,
                noninteractive=True,
            )

        self.assertEqual(result, devkit_env)
        ensure.assert_not_called()

    def test_setup_adds_negotiated_registry_addresses_to_devkit_environment(self):
        devkit_env = {"host_ip": "10.42.0.1", "devkit_ip": "10.42.0.2"}
        config = ContainerRegistryConfig(
            port=5052,
            sdk_address="localhost:5052",
            devkit_address="10.42.0.1:5052",
        )
        with patch("sima_cli.sdk.install.existing_container_registry_port", return_value=None), \
             patch("sima_cli.sdk.install.find_available_container_registry_port", return_value=5052) as select_port, \
             patch("sima_cli.sdk.install.resolve_container_registry_bind_ip", return_value="10.42.0.1") as bind_ip, \
             patch("sima_cli.sdk.install.ensure_container_registry", return_value=config) as ensure:
            result = _setup_devkit_container_registry(
                devkit_env,
                noninteractive=True,
            )

        select_port.assert_called_once_with()
        bind_ip.assert_called_once_with("10.42.0.1", "10.42.0.2")
        ensure.assert_called_once_with("10.42.0.1", requested_port=5052)
        self.assertEqual(result["container_registry_sdk_address"], "localhost:5052")
        self.assertEqual(result["container_registry_devkit_address"], "10.42.0.1:5052")

    def test_saves_registry_addresses_for_existing_sdk_container(self):
        completed = Mock(returncode=0, stdout="", stderr="")
        with patch("sima_cli.sdk.utils.subprocess.run", return_value=completed) as run:
            _configure_container_registry_environment(
                "sdk-container",
                {
                    "container_registry_sdk_address": "localhost:5000",
                    "container_registry_devkit_address": "10.42.0.1:5000",
                },
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["docker", "exec", "-u", "root", "sdk-container"])
        self.assertIn("SIMA_CONTAINER_REGISTRY=localhost:5000", command[-1])
        self.assertIn("SIMA_DEVKIT_CONTAINER_REGISTRY=10.42.0.1:5000", command[-1])

    def test_bootstrap_defers_devkit_docker_registry_setup(self):
        result = Mock(
            returncode=0,
            stdout=(
                "__SIMA_DEVKIT_BOOTSTRAP_STATUS=sourced_with_dk\n"
            ),
            stderr="",
        )
        with patch("sima_cli.sdk.utils._configure_container_registry_environment"), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=result) as run:
            bootstrap_devkit_container(
                "sdk-container",
                {
                    "devkit_ip": "10.42.0.2",
                    "host_ip": "10.42.0.1",
                    "workspace": "/workspace",
                    "host_platform": "linux",
                    "host_nfs_available": True,
                    "noninteractive": True,
                    "container_registry_devkit_address": "10.42.0.1:5000",
                    "container_registry_sdk_address": "localhost:5000",
                },
            )

        script = run.call_args.args[0][-1]
        self.assertNotIn("insecure-registries", script)
        self.assertNotIn("command -v docker", script)
        self.assertNotIn("dk container setup --yes", script)
        syntax = subprocess.run(
            ["bash", "-n"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_bootstrap_reports_non_nfs_failure_without_registry_status(self):
        result = Mock(
            returncode=125,
            stdout="__SIMA_DEVKIT_BOOTSTRAP_STATUS=sourced_with_dk\n",
            stderr="docker exec failed\n",
        )
        with patch("sima_cli.sdk.utils._configure_container_registry_environment"), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=result), \
             patch("builtins.print") as print_mock:
            bootstrap_devkit_container(
                "sdk-container",
                {
                    "devkit_ip": "10.42.0.2",
                    "host_ip": "10.42.0.1",
                    "workspace": "/workspace",
                    "host_platform": "linux",
                    "host_nfs_available": True,
                    "noninteractive": True,
                    "container_registry_devkit_address": "10.42.0.1:5000",
                    "container_registry_sdk_address": "localhost:5000",
                },
            )

        print_mock.assert_any_call(
            "⚠️ DevKit bootstrap failed in container 'sdk-container' (exit=125)."
        )
        print_mock.assert_any_call("docker exec failed")

    def test_bootstrap_raises_when_credential_setup_fails(self):
        result = Mock(
            returncode=1,
            stdout="__SIMA_DEVKIT_BOOTSTRAP_STATUS=credential_setup_failed\n",
            stderr="invalid credentials\n",
        )
        with patch("sima_cli.sdk.utils._configure_container_registry_environment"), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "username and password"):
                bootstrap_devkit_container(
                    "sdk-container",
                    {
                        "devkit_ip": "10.42.0.2",
                        "devkit_user": "sima",
                        "devkit_password": "wrong",
                        "host_nfs_available": True,
                        "noninteractive": True,
                    },
                )

    def test_bootstrap_raises_when_sdk_image_is_missing_sshpass(self):
        result = Mock(
            returncode=1,
            stdout="__SIMA_DEVKIT_BOOTSTRAP_STATUS=missing_sshpass\n",
            stderr="sshpass is required\n",
        )
        with patch("sima_cli.sdk.utils._configure_container_registry_environment"), \
             patch("sima_cli.sdk.utils.subprocess.run", return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, "does not provide sshpass"):
                bootstrap_devkit_container(
                    "sdk-container",
                    {
                        "devkit_ip": "10.42.0.2",
                        "devkit_user": "sima",
                        "devkit_password": "edgeai",
                        "host_nfs_available": True,
                        "noninteractive": True,
                    },
                )

        script = run.call_args.args[0][-1]
        self.assertIn(
            '"$BOOTSTRAP_STATUS" != missing_sshpass',
            script,
        )

    def test_bootstrap_prompts_once_after_default_credentials_fail(self):
        failed = Mock(returncode=41, stdout="", stderr="")
        succeeded = Mock(returncode=0, stdout="", stderr="")
        with patch("sima_cli.sdk.utils._configure_container_registry_environment"), \
             patch("sima_cli.sdk.utils.subprocess.run", side_effect=[failed, succeeded]) as run, \
             patch("sima_cli.sdk.utils.sys.stdin.isatty", return_value=True), \
             patch("sima_cli.sdk.utils.sys.stdout.isatty", return_value=True), \
             patch("builtins.input", return_value="operator"), \
             patch("sima_cli.sdk.utils.getpass.getpass", return_value="secret"):
            bootstrap_devkit_container(
                "sdk-container",
                {
                    "devkit_ip": "10.42.0.2",
                    "devkit_user": "sima",
                    "devkit_password": "edgeai",
                    "host_nfs_available": True,
                    "bootstrap_interactive": True,
                },
            )

        self.assertEqual(run.call_count, 2)
        retry_script = run.call_args_list[1].args[0][-1]
        self.assertIn("export DEVKIT_SYNC_PASSWORD=secret", retry_script)
        self.assertIn("operator@10.42.0.2", retry_script)

if __name__ == "__main__":
    unittest.main()
