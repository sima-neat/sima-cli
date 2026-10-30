from unittest.mock import MagicMock, patch
import threading
import click
import pytest
from sima_cli.update import netboot, netboot_device


@pytest.mark.parametrize('cmdline,expected', [('root=/dev/ram0 rw', True), ('root=/dev/dm-0 rw', False)])
def test_autoflash_requires_selected_netboot_device(cmdline, expected):
    manager = netboot.ClientManager()
    manager.clients = {'192.0.2.1': {'state': 'Connected'}, '192.0.2.2': {'state': 'Connected'}}
    with patch.object(netboot, 'init_ssh_session') as connect, \
            patch('sima_cli.update.remote.run_remote_command_capture', side_effect=[(0,cmdline,''), (0,'','')]), \
            patch.object(netboot, 'flash_emmc') as flash, \
            patch.object(netboot, '_schedule_reboot_into_emmc') as reboot:
        if expected:
            assert netboot.auto_flash(manager, '192.0.2.2') is True
            flash.assert_called_once_with(manager, netboot.emmc_image_paths, override_ip='192.0.2.2',
                                          troot_image_path=netboot.troot_image_path,
                                          configuration=None, manual_reboot=False, transfer_attempts=3)
            reboot.assert_called_once_with('192.0.2.2')
        else:
            with pytest.raises(click.ClickException, match='not confirmed'):
                netboot.auto_flash(manager, '192.0.2.2')
            flash.assert_not_called()
            reboot.assert_not_called()
    connect.return_value.close.assert_called_once()


def test_other_device_does_not_trigger_flash():
    manager=netboot.ClientManager();manager.clients={'192.0.2.1':{'state':'Connected'}}
    with patch.object(netboot, 'flash_emmc') as flash:
        with pytest.raises(click.ClickException, match='Timed out'):
            netboot.auto_flash(manager, '192.0.2.2', timeout=0)
    flash.assert_not_called()


def test_autoflash_propagates_flash_failure():
    manager = netboot.ClientManager()
    manager.clients = {'192.0.2.2': {'state': 'Connected'}}
    with patch.object(netboot, 'init_ssh_session'), \
            patch('sima_cli.update.remote.run_remote_command_capture',
                  return_value=(0, 'root=/dev/ram0 rw', '')), \
            patch.object(netboot, 'flash_emmc', return_value=False):
        with pytest.raises(click.ClickException, match='Automatic eMMC flash failed'):
            netboot.auto_flash(manager, '192.0.2.2')


@pytest.mark.parametrize('confirmed', [False, True])
def test_manual_flash_offer_controls_reboot(confirmed):
    events = []

    with patch.object(click, 'confirm', return_value=confirmed) as prompt, \
            patch.object(netboot, '_schedule_reboot_into_emmc',
                         side_effect=lambda _ip: events.append('reboot')):
        assert netboot._offer_reboot_into_emmc('192.0.2.2') is confirmed

    assert prompt.call_args.args[0] == (
        'Reboot the DevKit now and boot from the flashed eMMC image?'
    )
    assert prompt.call_args.kwargs == {'default': True}
    assert events == (['reboot'] if confirmed else [])


def test_failed_ssh_wait_does_not_mark_connected():
    manager=netboot.ClientManager();manager.clients={'192.0.2.2':{'state':'Booting'}}
    def wait(*args,**kwargs):
        manager.shutdown_event.set()
        return False
    with patch.object(netboot, 'wait_for_ssh', side_effect=wait):
        manager.monitor_client('192.0.2.2',0)
    assert manager.clients['192.0.2.2']['state']=='Booting'


def test_autoflash_confirmation_explains_automatic_flash(capsys):
    with patch.object(netboot_device,'init_ssh_session'), \
            patch.object(netboot_device,'_checked'), \
            patch.object(netboot_device,'network_settings',return_value={'interface':'end0','netmask':'255.255.255.0','gateway':'0.0.0.0'}), \
            patch.object(click,'confirm',return_value=False) as confirm:
        assert netboot_device.configure_and_reboot('192.0.2.2','192.0.2.1',autoflash=True) is False
    output = capsys.readouterr().out
    assert 'reboot it from eMMC' in output
    assert 'overwrites its internal storage' not in output
    assert 'automatically flash' in confirm.call_args.args[0]
    assert 'reboot it from eMMC' in confirm.call_args.args[0]


def test_readiness_retries_authentication_before_flashing():
    manager = netboot.ClientManager()
    manager.clients = {'192.0.2.2': {'state': 'Connected'}}
    ssh = MagicMock()
    with patch.object(netboot, 'init_ssh_session', side_effect=[TimeoutError('Authentication timeout'), ssh]), \
            patch('sima_cli.update.remote.run_remote_command_capture',
                  side_effect=[(0, 'root=/dev/ram0 rw', ''), (0, '', '')]), \
            patch.object(manager.shutdown_event, 'wait', return_value=False), \
            patch.object(netboot, 'flash_emmc', return_value=True) as flash, \
            patch.object(netboot, '_schedule_reboot_into_emmc'):
        assert netboot.auto_flash(manager, '192.0.2.2')
    ssh.open_sftp.assert_called_once()
    ssh.close.assert_called_once()
    flash.assert_called_once()


def test_readiness_exhaustion_does_not_transfer_or_flash(capsys):
    manager = netboot.ClientManager()
    manager.clients = {'192.0.2.2': {'state': 'Connected'}}
    with patch.object(netboot, 'init_ssh_session', side_effect=TimeoutError('Authentication timeout')) as connect, \
            patch.object(manager.shutdown_event, 'wait', return_value=False), \
            patch.object(netboot, 'flash_emmc') as flash, \
            patch.object(netboot, 'copy_file_to_remote_board') as copy:
        with pytest.raises(click.ClickException, match='no automatic flash was started'):
            netboot.auto_flash(manager, '192.0.2.2')
    assert connect.call_count == 8
    flash.assert_not_called()
    copy.assert_not_called()
    assert 'attempt 8 failed: Authentication timeout' in capsys.readouterr().out


@pytest.mark.parametrize('stage', ['command', 'prerequisites', 'sftp'])
def test_readiness_retries_command_prerequisite_and_sftp_failures(stage):
    ssh = MagicMock()
    first = [(0, 'root=/dev/ram0', ''), (0, '', '')]
    if stage == 'command':
        first = [TimeoutError('command timeout')]
    elif stage == 'prerequisites':
        first[1] = (1, '', 'not ready')
    else:
        ssh.open_sftp.side_effect = [TimeoutError('SFTP timeout'), MagicMock()]
    cancel = threading.Event()
    with patch.object(netboot, 'init_ssh_session', return_value=ssh), \
            patch('sima_cli.update.remote.run_remote_command_capture',
                  side_effect=first + [(0, 'root=/dev/ram0', ''), (0, '', '')]), \
            patch.object(cancel, 'wait', return_value=False):
        assert netboot._wait_for_flash_readiness('192.0.2.2', cancel, netboot.time.monotonic() + 180)
    assert ssh.close.call_count == 2


def test_readiness_cancellation_interrupts_retry_delay():
    cancel = threading.Event()
    def stop(_delay):
        cancel.set()
        return True
    with patch.object(netboot, 'init_ssh_session', side_effect=TimeoutError('startup')) as connect, \
            patch.object(cancel, 'wait', side_effect=stop):
        assert not netboot._wait_for_flash_readiness('192.0.2.2', cancel, netboot.time.monotonic() + 180)
    connect.assert_called_once()


def test_readiness_deadline_prevents_connection():
    with patch.object(netboot, 'init_ssh_session') as connect:
        with pytest.raises(click.ClickException, match='deadline expired'):
            netboot._wait_for_flash_readiness('192.0.2.2', threading.Event(), netboot.time.monotonic() - 1)
    connect.assert_not_called()


def test_separate_transfer_connection_is_retried_before_programming():
    manager = netboot.ClientManager()
    with patch.object(netboot, 'copy_file_to_remote_board', side_effect=[False, True]) as copy, \
            patch.object(manager.shutdown_event, 'wait', return_value=False) as wait:
        assert netboot._copy_flash_image(manager, '192.0.2.2', '/image', '/tmp', 3)
    assert copy.call_count == 2
    wait.assert_called_once_with(10)


def test_transfer_retries_are_bounded():
    manager = netboot.ClientManager()
    with patch.object(netboot, 'copy_file_to_remote_board', return_value=False) as copy, \
            patch.object(manager.shutdown_event, 'wait', return_value=False):
        assert not netboot._copy_flash_image(manager, '192.0.2.2', '/image', '/tmp', 3)
    assert copy.call_count == 3


def test_readiness_deadline_closes_blocked_command():
    closed = threading.Event()
    ssh = MagicMock()
    ssh.close.side_effect = closed.set
    def blocked_command(*_args, **_kwargs):
        assert closed.wait(1), 'readiness deadline did not close the transport'
        raise EOFError('transport closed')
    with patch.object(netboot, 'init_ssh_session', return_value=ssh), \
            patch('sima_cli.update.remote.run_remote_command_capture', side_effect=blocked_command):
        with pytest.raises(click.ClickException, match='no automatic flash was started'):
            netboot._wait_for_flash_readiness('192.0.2.2', threading.Event(),
                                             netboot.time.monotonic() + 0.02)
    assert closed.is_set()


def test_ssh_authentication_timeout_closes_failed_session():
    from sima_cli.update import remote
    with patch.object(remote.paramiko, 'SSHClient') as client:
        client.return_value.connect.side_effect = TimeoutError('Authentication timeout')
        with pytest.raises(TimeoutError):
            remote.init_ssh_session('192.0.2.2', timeout=0.1)
    client.return_value.close.assert_called_once()
    assert client.return_value.connect.call_args.kwargs['auth_timeout'] == 0.1
    assert client.return_value.connect.call_args.kwargs['banner_timeout'] == 0.1
