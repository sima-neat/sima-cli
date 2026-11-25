"""Opt-in eMMC-only flashing bypasses all tRoot operations."""
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sima_cli.cli import bootimg_cmd
from sima_cli.update import netboot


@pytest.mark.parametrize("mode", ["--netboot", "--autoflash"])
def test_cli_passes_skip_troot(mode):
    with patch.object(netboot, "setup_netboot") as setup:
        result = CliRunner().invoke(
            bootimg_cmd, ["-v", "3.0.1", mode, "--skip-troot"], obj={}
        )
    assert result.exit_code == 0, result.output
    assert setup.call_args.kwargs["skip_troot"] is True


def test_skip_troot_requires_netboot():
    result = CliRunner().invoke(bootimg_cmd, ["-v", "3.0.1", "--skip-troot"], obj={})
    assert result.exit_code == 2
    assert "--skip-troot requires --netboot or --autoflash" in result.output


@pytest.mark.parametrize("answer,accepted", [("y\n", True), ("n\n", False), ("\n", False), ("", False)])
@pytest.mark.parametrize("yes", [False, True])
def test_skip_troot_requires_explicit_session_acceptance(answer, accepted, yes):
    args = ["-v", "3.0.1", "--netboot", "--skip-troot"] + (["--yes"] if yes else [])
    with patch.object(netboot, "get_environment_type", return_value=("host", "linux")), \
            patch.object(netboot, "_prepare_netboot_assets", side_effect=RuntimeError("preparation reached")) as prepare:
        result = CliRunner().invoke(bootimg_cmd, args, obj={}, input=answer)
    assert "existing tRoot firmware unchanged" in result.output
    assert "every manual (f) and automatic flash" in result.output
    assert "Accept the risk" in result.output
    assert bool(prepare.call_count) is accepted
    assert result.exit_code != 0


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("image", ["modalix.img.gz", "modalix.wic.gz"])
def test_skip_troot_flashes_emmc_without_copying_or_programming_troot(monkeypatch, automatic, image):
    ip = "192.0.2.20"
    manager = netboot.ClientManager(skip_troot=True)
    monkeypatch.setattr(netboot, "emmc_image_paths", [f"/images/{image}"])
    monkeypatch.setattr(netboot, "troot_image_path", "/images/troot_blob.be")
    with patch.object(netboot, "_select_flash_target", return_value=ip), \
            patch.object(netboot, "copy_file_to_remote_board", return_value=True) as copy, \
            patch.object(netboot, "init_ssh_session"), \
            patch.object(netboot, "run_remote_command") as remote, \
            patch.object(netboot, "_print_troot_programming_warning") as warning, \
            patch.object(netboot, "get_remote_board_info", side_effect=AssertionError("tRoot version probe must be skipped")), \
            patch.object(netboot, "_wait_for_flash_readiness", return_value=True), \
            patch.object(netboot, "_schedule_reboot_into_emmc"), \
            patch.object(netboot, "_offer_reboot_into_emmc"), \
            patch("builtins.input", side_effect=["f", "q"]):
        if automatic:
            manager.clients[ip] = dict(state="Connected", current_ip=ip)
            assert netboot.auto_flash(manager, ip) is True
        else:
            netboot.run_cli(manager)
    copy.assert_called_once_with(ip, f"/images/{image}", "/tmp", passwd=netboot.DEFAULT_PASSWORD)
    commands = [call.args[1] for call in remote.call_args_list]
    assert all("troot_upgrade" not in command and "simaai-trootctl" not in command for command in commands)
    assert any("dd of=/dev/mmcblk0" in command if image.endswith(".img.gz")
               else "bmaptool copy" in command for command in commands)
    assert "finalize_emmc()" in commands[-1]
    warning.assert_not_called()


def test_setup_carries_accepted_skip_troot_into_session(monkeypatch, tmp_path):
    (tmp_path / "netboot.scr.uimg").write_bytes(b"script")
    assets = netboot.NetbootAssets(
        tftp_root=str(tmp_path), emmc_image_paths=["/prepared/root.img.gz"],
        troot_image_path="/prepared/troot_blob.be", cache_dir=None,
    )
    for name in ("emmc_image_paths", "troot_image_path", "custom_rootfs"):
        monkeypatch.setattr(netboot, name, getattr(netboot, name))
    with patch.object(netboot, "get_environment_type", return_value=("host", "linux")), \
            patch.object(netboot.click, "confirm", return_value=True) as confirm, \
            patch.object(netboot, "_prepare_netboot_assets", return_value=assets), \
            patch.object(netboot, "_acquire_cache_lease"), \
            patch.object(netboot, "_release_cache_lease"), \
            patch.object(netboot, "get_local_ip_candidates", return_value=[("eth0", "192.0.2.10")]), \
            patch.object(netboot, "ClientManager") as manager, \
            patch("sima_cli.update.netboot_device.resolve_device", return_value=None), \
            patch.object(netboot, "InteractiveTftpServer") as server, \
            patch.object(netboot.threading, "Thread") as thread, \
            patch.object(netboot, "run_cli") as run_cli:
        thread.return_value.is_alive.return_value = False
        server.return_value.is_running.wait.return_value = True
        netboot.setup_netboot("3.0.1", "modalix", skip_troot=True)
    confirm.assert_called_once_with(
        "Accept the risk and flash only eMMC in this session?", default=False, abort=True
    )
    manager.assert_called_once_with(configuration=None, skip_troot=True)
    assert run_cli.call_args.args[0] is manager.return_value
