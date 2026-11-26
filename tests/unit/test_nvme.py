from unittest.mock import mock_open, patch

import pytest
from click.testing import CliRunner

from sima_cli.cli import nvme_cmd
from sima_cli.storage import nvme
from sima_cli.utils import env


@pytest.mark.parametrize("model,supported", [
    ("SiMa.ai Modalix SoM Board\x00", True),
    ("SiMa.ai Modalix DVT Board\x00", True),
    ("SiMa.ai DaVinci Half-Height Board\x00", False),
    ("Generic Linux workstation", False),
])
def test_modalix_nvme_board_models(model, supported):
    with patch.object(env.os.path, "exists", return_value=True), \
            patch("sima_cli.utils.env.open", mock_open(read_data=model), create=True):
        assert env.is_modalix_devkit() is supported


def test_missing_device_tree_is_not_a_devkit():
    with patch.object(env.os.path, "exists", return_value=False):
        assert env.is_modalix_devkit() is False


@pytest.mark.parametrize("operation", ["remount", "format"])
def test_dvt_reaches_nvme_operation(operation):
    with patch.object(env.os.path, "exists", return_value=True), \
            patch("sima_cli.utils.env.open", mock_open(read_data="SiMa.ai Modalix DVT Board\x00"), create=True), \
            patch("sima_cli.cli.nvme_remount") as remount, \
            patch("sima_cli.cli.nvme_format") as format_drive:
        result = CliRunner().invoke(nvme_cmd, [operation], obj={})
    assert result.exit_code == 0, result.output
    (remount if operation == "remount" else format_drive).assert_called_once_with()
    (format_drive if operation == "remount" else remount).assert_not_called()


def test_remount_dvt_with_nvme():
    with patch.object(env.os.path, "exists", return_value=True), \
            patch("sima_cli.utils.env.open", mock_open(read_data="SiMa.ai Modalix DVT Board\x00"), create=True), \
            patch.object(nvme, "scan_nvme", return_value="/dev/nvme0n1") as scan, \
            patch.object(nvme, "mount_nvme") as mount:
        nvme.nvme_remount()
    scan.assert_called_once_with()
    mount.assert_called_once_with()


def test_remount_without_nvme_does_not_change_mounts():
    with patch.object(nvme, "is_modalix_devkit", return_value=True), \
            patch.object(nvme, "scan_nvme", return_value=None), \
            patch.object(nvme, "mount_nvme") as mount:
        with pytest.raises(RuntimeError, match="No NVMe drive detected"):
            nvme.nvme_remount()
    mount.assert_not_called()


def test_workstation_cannot_reach_format_or_remount():
    with patch("sima_cli.cli.is_modalix_devkit", return_value=False), \
            patch("sima_cli.cli.nvme_remount") as remount, \
            patch("sima_cli.cli.nvme_format") as format_drive:
        for operation in ("format", "remount"):
            result = CliRunner().invoke(nvme_cmd, [operation], obj={})
            assert result.exit_code == 1
            assert "only be executed on the Modalix DevKit" in result.output
    remount.assert_not_called()
    format_drive.assert_not_called()
