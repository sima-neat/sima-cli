import unittest
from unittest.mock import call, patch

from sima_cli.update import netboot


class FakeClientManager:
    def __init__(self, clients=None):
        self.clients = clients or []

    def get_client_info(self):
        return self.clients


class NetbootFlashTests(unittest.TestCase):
    @patch("sima_cli.update.netboot._print_troot_programming_warning")
    @patch("sima_cli.update.netboot.run_remote_command")
    @patch("sima_cli.update.netboot.init_ssh_session", return_value=object())
    @patch("sima_cli.update.netboot.copy_file_to_remote_board", return_value=True)
    @patch("sima_cli.update.netboot._validate_override_ip", return_value=True)
    def test_flash_override_ip_programs_troot_before_emmc(
        self,
        _mock_validate_ip,
        mock_copy,
        mock_init_ssh,
        mock_run_remote,
        mock_warning,
    ):
        netboot.flash_emmc(
            FakeClientManager(),
            ["/images/modalix.wic.gz", "/images/modalix.wic.bmap"],
            override_ip="192.168.4.20",
            troot_image_path="/images/troot_blob.be",
        )

        self.assertEqual(
            mock_copy.call_args_list,
            [
                call("192.168.4.20", "/images/troot_blob.be", "/tmp", passwd=netboot.DEFAULT_PASSWORD),
                call("192.168.4.20", "/images/modalix.wic.gz", "/tmp", passwd=netboot.DEFAULT_PASSWORD),
                call("192.168.4.20", "/images/modalix.wic.bmap", "/tmp", passwd=netboot.DEFAULT_PASSWORD),
            ],
        )
        mock_init_ssh.assert_called_once_with("192.168.4.20", password=netboot.DEFAULT_PASSWORD)
        mock_warning.assert_called_once_with()
        remote_commands = [args[0][1] for args in mock_run_remote.call_args_list]
        self.assertEqual(remote_commands[0], "sudo troot_upgrade /tmp/troot_blob.be")
        self.assertIn("[ -e /dev/mmcblk0 ]", remote_commands[1])
        self.assertIn("sudo bmaptool copy /tmp/modalix.wic.gz /dev/mmcblk0", remote_commands)

    @patch("sima_cli.update.netboot.init_ssh_session")
    @patch("sima_cli.update.netboot.copy_file_to_remote_board")
    @patch("sima_cli.update.netboot._validate_override_ip", return_value=False)
    def test_flash_override_ip_aborts_when_ping_fails(
        self,
        _mock_validate_ip,
        mock_copy,
        mock_init_ssh,
    ):
        netboot.flash_emmc(
            FakeClientManager(),
            ["/images/modalix.wic.gz"],
            override_ip="192.168.4.20",
            troot_image_path="/images/troot_blob.be",
        )

        mock_copy.assert_not_called()
        mock_init_ssh.assert_not_called()

    @patch("sima_cli.update.netboot.flash_emmc")
    @patch("builtins.input", side_effect=["f 192.168.4.20", "q"])
    def test_run_cli_passes_flash_override_ip(self, _mock_input, mock_flash):
        old_emmc = netboot.emmc_image_paths
        old_troot = netboot.troot_image_path
        try:
            netboot.emmc_image_paths = ["/images/modalix.img.gz"]
            netboot.troot_image_path = "/images/troot_blob.be"

            netboot.run_cli(FakeClientManager())

            mock_flash.assert_called_once_with(
                unittest.mock.ANY,
                ["/images/modalix.img.gz"],
                override_ip="192.168.4.20",
                troot_image_path="/images/troot_blob.be",
            )
        finally:
            netboot.emmc_image_paths = old_emmc
            netboot.troot_image_path = old_troot

    @patch("sima_cli.update.netboot._ping_host", return_value=True)
    def test_validate_override_ip_rejects_invalid_ip_without_ping(self, mock_ping):
        self.assertFalse(netboot._validate_override_ip("not-an-ip"))
        mock_ping.assert_not_called()


if __name__ == "__main__":
    unittest.main()
