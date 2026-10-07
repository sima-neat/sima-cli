import subprocess
import socket
import unittest
from unittest.mock import Mock, patch

from sima_cli.sdk.container_registry import (
    ContainerRegistryConfig,
    REGISTRY_CONTAINER_NAME,
    REGISTRY_VOLUME_NAME,
    _require_available_host_port,
    ensure_container_registry,
    find_available_container_registry_port,
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
        self.assertIn("5000:5000", registry_run)
        self.assertIn(f"{REGISTRY_VOLUME_NAME}:/var/lib/registry", registry_run)
        port_check.assert_called_once_with(5000)
        wait.assert_called_once_with(5000)

    def test_reuses_running_registry(self):
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value={"port": 5000, "running": True},
        ), patch("sima_cli.sdk.container_registry._wait_for_registry") as wait, \
             patch("sima_cli.sdk.container_registry.subprocess.run") as run:
            config = ensure_container_registry("10.42.0.1")

        self.assertEqual(config.port, 5000)
        run.assert_not_called()
        wait.assert_called_once_with(5000)

    def test_reconfigures_port_without_deleting_registry_volume(self):
        completed = Mock(returncode=0, stdout="registry-id\n", stderr="")
        with patch(
            "sima_cli.sdk.container_registry._inspect_registry",
            return_value={"port": 5000, "running": True},
        ), patch("sima_cli.sdk.container_registry._require_available_host_port") as port_check, \
             patch("sima_cli.sdk.container_registry._wait_for_registry"), \
             patch("sima_cli.sdk.container_registry.subprocess.run", return_value=completed) as run:
            config = ensure_container_registry("10.42.0.1", requested_port=5050)

        self.assertEqual(config.port, 5050)
        commands = [item.args[0] for item in run.call_args_list]
        self.assertIn(["docker", "rm", "-f", REGISTRY_CONTAINER_NAME], commands)
        self.assertFalse(any(command[:3] == ["docker", "volume", "rm"] for command in commands))
        port_check.assert_any_call(5050)

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
             patch("sima_cli.sdk.install.ensure_container_registry", return_value=config) as ensure:
            result = _setup_devkit_container_registry(
                devkit_env,
                noninteractive=True,
            )

        select_port.assert_called_once_with()
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

    def test_bootstrap_configures_devkit_docker_for_registry(self):
        result = Mock(
            returncode=0,
            stdout=(
                "__SIMA_DEVKIT_BOOTSTRAP_STATUS=sourced_with_dk\n"
                "__SIMA_DEVKIT_REGISTRY_STATUS=ready\n"
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
        self.assertIn("insecure-registries", script)
        self.assertIn("10.42.0.1:5000", script)
        self.assertIn("Docker is not installed on the DevKit", script)
        self.assertIn("sima-cli-registry-address", script)
        self.assertIn("systemctl restart docker", script)
        syntax = subprocess.run(
            ["bash", "-n"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()
