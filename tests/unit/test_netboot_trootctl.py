from unittest.mock import MagicMock, patch

import pytest

from sima_cli.update import netboot
from sima_cli.update.remote import run_remote_command


@pytest.mark.parametrize('version,command', [
    ('2.1.3', 'sudo troot_upgrade /tmp/troot_blob.be'),
    ('2.10.0_daily_B100', 'sudo troot_upgrade /tmp/troot_blob.be'),
    ('3.0', "sudo sh -c 'cd /tmp && exec simaai-trootctl full-flash'"),
    ('3.0.0_daily_develop_B1211', "sudo sh -c 'cd /tmp && exec simaai-trootctl full-flash'"),
    ('"3.1.0"', "sudo sh -c 'cd /tmp && exec simaai-trootctl full-flash'"),
    ('10.0.0', "sudo sh -c 'cd /tmp && exec simaai-trootctl full-flash'"),
])
def test_running_firmware_selects_troot_command_before_emmc(version, command):
    with patch.object(netboot, '_select_flash_target', return_value='192.0.2.1'), \
            patch.object(netboot, 'get_remote_board_info', return_value=('modalix', version, '', False, 'elxr')), \
            patch.object(netboot, 'copy_file_to_remote_board', return_value=True) as copy, \
            patch.object(netboot, 'init_ssh_session', return_value=object()), \
            patch.object(netboot, '_print_troot_programming_warning'), \
            patch.object(netboot, 'run_remote_command') as run:
        netboot.flash_emmc(None, ['/images/elxr-palette-modalix-3.0.0-agate-arm64.img.gz'],
                          troot_image_path='/images/troot_blob.be')
        assert copy.call_args_list[0].args[1:3] == ('/images/troot_blob.be', '/tmp')
        assert run.call_args_list[0].args[1] == command
        assert run.call_args_list[0].kwargs == {'check': True}
        assert 'dd of=/dev/mmcblk0' in run.call_args_list[-1].args[1]


@pytest.mark.parametrize('version', ['', 'unknown'])
def test_unknown_running_version_aborts_without_copying_or_flashing(version):
    with patch.object(netboot, '_select_flash_target', return_value='192.0.2.1'), \
            patch.object(netboot, 'get_remote_board_info', return_value=('', version, '', False, '')), \
            patch.object(netboot, 'copy_file_to_remote_board') as copy, \
            patch.object(netboot, 'init_ssh_session') as ssh:
        netboot.flash_emmc(None, ['/images/palette.img.gz'], troot_image_path='/images/troot_blob.be')
        copy.assert_not_called()
        ssh.assert_not_called()


def test_failed_troot_command_stops_before_emmc(capsys):
    with patch.object(netboot, '_select_flash_target', return_value='192.0.2.1'), \
            patch.object(netboot, 'get_remote_board_info', return_value=('modalix', '3.0.0', '', False, 'elxr')), \
            patch.object(netboot, 'copy_file_to_remote_board', return_value=True), \
            patch.object(netboot, 'init_ssh_session', return_value=object()), \
            patch.object(netboot, '_print_troot_programming_warning'), \
            patch.object(netboot, 'run_remote_command', side_effect=RuntimeError('tRoot failed')) as run:
        netboot.flash_emmc(None, ['/images/palette.img.gz'], troot_image_path='/images/troot_blob.be')
        run.assert_called_once()
        assert 'Flashing failed: tRoot failed' in capsys.readouterr().out


@pytest.mark.parametrize('exit_code', [0, 1, 127, -1])
def test_checked_remote_command_checks_exit_status(exit_code):
    ssh = MagicMock()
    stdin, stdout, stderr = MagicMock(), MagicMock(), MagicMock()
    ssh.exec_command.return_value = (stdin, stdout, stderr)
    stdout.channel.exit_status_ready.return_value = True
    stdout.channel.recv_exit_status.return_value = exit_code
    stdout.read.return_value = stderr.read.return_value = b''
    command = "sudo sh -c 'cd /tmp && exec simaai-trootctl full-flash'"
    if exit_code:
        with pytest.raises(RuntimeError, match=f'exit status {exit_code}'):
            run_remote_command(ssh, command, check=True)
    else:
        run_remote_command(ssh, command, check=True)
    ssh.exec_command.assert_called_once_with(
        "sudo -S sh -c 'cd /tmp && exec simaai-trootctl full-flash'", get_pty=True,
    )


@pytest.mark.parametrize('exit_code', [0, 1])
def test_command_label_hides_script_and_keeps_output(exit_code, capsys):
    ssh = MagicMock()
    stdin, stdout, stderr = MagicMock(), MagicMock(), MagicMock()
    ssh.exec_command.return_value = (stdin, stdout, stderr)
    stdout.channel.exit_status_ready.return_value = True
    stdout.channel.recv_exit_status.return_value = exit_code
    stdout.read.return_value = b'Mounted filesystems: /data\n'
    stderr.read.return_value = b''
    command = "sudo python3 -c 'print(\"embedded script\")'"
    if exit_code:
        with pytest.raises(RuntimeError) as exc:
            run_remote_command(ssh, command, check=True, command_label='Preparing eMMC')
        assert 'embedded script' not in str(exc.value)
        assert 'Preparing eMMC' in str(exc.value)
    else:
        run_remote_command(ssh, command, check=True, command_label='Preparing eMMC')
    output = capsys.readouterr().out
    assert 'Preparing eMMC' in output
    assert 'Mounted filesystems: /data' in output
    assert 'embedded script' not in output
    assert 'embedded script' in ssh.exec_command.call_args.args[0]
