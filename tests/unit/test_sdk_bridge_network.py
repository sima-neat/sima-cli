import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from sima_cli.sdk.install import setup_and_start
from sima_cli.sdk.utils import ensure_simasdkbridge_network


def completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def network(name, driver="bridge", subnet="172.31.0.0/16", gateway="172.31.0.1", containers=None):
    config = {"Subnet": subnet}
    if gateway:
        config["Gateway"] = gateway
    return {
        "Name": name,
        "Driver": driver,
        "IPAM": {"Config": [config]},
        "Containers": containers or {},
    }


class TestSdkBridgeNetwork(unittest.TestCase):
    def test_missing_bridge_is_created_with_non_overlapping_subnet(self):
        calls = []
        created = False

        def run(cmd, **kwargs):
            nonlocal created
            calls.append(cmd)
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["simasdkbridge"]:
                if created:
                    return completed(cmd, stdout=json.dumps([network("simasdkbridge")]))
                return completed(cmd, 1, stderr="No such network: simasdkbridge")
            if cmd[:3] == ["docker", "network", "ls"]:
                return completed(cmd, stdout="bridge\n")
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["bridge"]:
                return completed(cmd, stdout=json.dumps([network("bridge", subnet="172.17.0.0/16", gateway="172.17.0.1")]))
            if cmd[:3] == ["docker", "network", "create"]:
                created = True
                return completed(cmd, stdout="created\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sima_cli.sdk.utils.subprocess.run", side_effect=run):
            ensure_simasdkbridge_network()

        self.assertIn(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--subnet",
                "172.31.0.0/16",
                "simasdkbridge",
            ],
            calls,
        )

    def test_invalid_bridge_is_recreated_when_no_containers_are_attached(self):
        calls = []
        recreated = False

        def run(cmd, **kwargs):
            nonlocal recreated
            calls.append(cmd)
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["simasdkbridge"]:
                if recreated:
                    return completed(cmd, stdout=json.dumps([network("simasdkbridge")]))
                return completed(cmd, stdout=json.dumps([network("simasdkbridge", driver="overlay")]))
            if cmd[:3] == ["docker", "network", "ls"]:
                return completed(cmd, stdout="bridge\nsimasdkbridge\n")
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["bridge"]:
                return completed(cmd, stdout=json.dumps([network("bridge", subnet="172.17.0.0/16", gateway="172.17.0.1")]))
            if cmd[:3] == ["docker", "network", "rm"]:
                return completed(cmd, stdout="removed\n")
            if cmd[:3] == ["docker", "network", "create"]:
                recreated = True
                return completed(cmd, stdout="created\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sima_cli.sdk.utils.subprocess.run", side_effect=run):
            ensure_simasdkbridge_network()

        self.assertIn(["docker", "network", "rm", "simasdkbridge"], calls)
        self.assertTrue(any(cmd[:3] == ["docker", "network", "create"] for cmd in calls))

    def test_invalid_bridge_with_attached_container_is_not_removed(self):
        attached = {
            "abc123": {
                "Name": "ghcr.io-sima-neat-sdk-v2.1.2.2",
            }
        }

        def run(cmd, **kwargs):
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["simasdkbridge"]:
                return completed(cmd, stdout=json.dumps([network("simasdkbridge", driver="overlay", containers=attached)]))
            if cmd[:3] == ["docker", "network", "ls"]:
                return completed(cmd, stdout="bridge\nsimasdkbridge\n")
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["bridge"]:
                return completed(cmd, stdout=json.dumps([network("bridge", subnet="172.17.0.0/16", gateway="172.17.0.1")]))
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sima_cli.sdk.utils.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "containers are attached"):
                ensure_simasdkbridge_network()

    def test_failed_probe_recreates_valid_but_unusable_bridge(self):
        calls = []
        probe_attempts = 0

        def run(cmd, **kwargs):
            nonlocal probe_attempts
            calls.append(cmd)
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["simasdkbridge"]:
                return completed(cmd, stdout=json.dumps([network("simasdkbridge", subnet="172.18.0.0/16", gateway="172.18.0.1")]))
            if cmd[:3] == ["docker", "network", "ls"]:
                return completed(cmd, stdout="bridge\nsimasdkbridge\n")
            if cmd[:3] == ["docker", "network", "inspect"] and cmd[3:] == ["bridge"]:
                return completed(cmd, stdout=json.dumps([network("bridge", subnet="172.17.0.0/16", gateway="172.17.0.1")]))
            if cmd[:3] == ["docker", "run", "--rm"]:
                probe_attempts += 1
                return completed(cmd, returncode=0 if probe_attempts == 2 else 1)
            if cmd[:3] == ["docker", "network", "rm"]:
                return completed(cmd, stdout="removed\n")
            if cmd[:3] == ["docker", "network", "create"]:
                return completed(cmd, stdout="created\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sima_cli.sdk.utils.subprocess.run", side_effect=run):
            ensure_simasdkbridge_network(probe_image="ghcr.io/sima-neat/sdk:v2.1.2.2")

        self.assertEqual(2, probe_attempts)
        self.assertIn(["docker", "network", "rm", "simasdkbridge"], calls)

    def test_sdk_start_validates_bridge_before_starting_existing_container(self):
        image = "ghcr.io/sima-neat/sdk:latest"
        container = "ghcr.io-sima-neat-sdk-latest"

        with patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.get_container_status", return_value={container: "exited"}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value={}), \
             patch("sima_cli.sdk.install._setup_sdk_extensions", return_value=""), \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=container), \
             patch("sima_cli.sdk.install.ensure_simasdkbridge_network") as ensure_bridge, \
             patch("sima_cli.sdk.install.is_container_running", return_value=False), \
             patch("sima_cli.sdk.install.ensure_existing_neat_container_startable"), \
             patch("sima_cli.sdk.install.check_os", return_value="linux"), \
             patch("sima_cli.sdk.install.detect_current_user", return_value=("devuser", 1000, 1000)), \
             patch("sima_cli.sdk.install.configure_container_user"), \
             patch("sima_cli.sdk.install._refresh_mpk_config_json"), \
             patch("sima_cli.sdk.install.subprocess.run", return_value=Mock(returncode=0)):
            setup_and_start(start_only=True, no_model_sdk=True, yes_to_all=True, noninteractive=True)

        ensure_bridge.assert_called_once_with(probe_image="")

    def test_full_sdk_setup_probes_bridge_with_selected_neat_image(self):
        image = "ghcr.io/sima-neat/sdk:latest"

        with patch("sima_cli.sdk.install.syscheck"), \
             patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
             patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
             patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
             patch("sima_cli.sdk.install.get_container_status", return_value={}), \
             patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
             patch("sima_cli.sdk.install._setup_devkit_share", return_value={}), \
             patch("sima_cli.sdk.install._setup_sdk_extensions", return_value="/tmp/extensions"), \
             patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=None), \
             patch("sima_cli.sdk.install.ensure_simasdkbridge_network") as ensure_bridge, \
             patch("sima_cli.sdk.install.start_docker_container") as start_container, \
             patch("sima_cli.sdk.install._refresh_mpk_config_json"):
            setup_and_start(yes_to_all=True, noninteractive=True)

        ensure_bridge.assert_called_once_with(probe_image=image)
        start_container.assert_called_once()


if __name__ == "__main__":
    unittest.main()
