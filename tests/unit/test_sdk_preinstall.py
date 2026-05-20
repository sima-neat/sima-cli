import unittest
import types
from unittest.mock import patch

from sima_cli.sdk.preinstall import (
    ensure_colima_resources_for_neat_sdk,
    check_rosetta_and_firewall,
    check_cpu_ram,
    _parse_colima_status,
)


class TestSdkPreinstall(unittest.TestCase):
    def test_macos_skips_firewall_check(self):
        with patch("sima_cli.sdk.preinstall.platform.system", return_value="Darwin"), \
             patch("sima_cli.sdk.preinstall.platform.machine", return_value="x86_64"), \
             patch("sima_cli.sdk.preinstall.run_command") as run_command:
            rosetta_failed, fw_failed, results = check_rosetta_and_firewall(use_sudo=True)

        self.assertFalse(rosetta_failed)
        self.assertFalse(fw_failed)
        self.assertEqual(results, [])
        run_command.assert_not_called()

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
