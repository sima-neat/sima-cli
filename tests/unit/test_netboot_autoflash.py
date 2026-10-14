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
            patch('sima_cli.update.remote.run_remote_command_capture', return_value=(0,cmdline,'')), \
            patch.object(netboot, 'flash_emmc') as flash, \
            patch.object(netboot, '_schedule_reboot_into_emmc') as reboot:
        if expected:
            assert netboot.auto_flash(manager, '192.0.2.2') is True
            flash.assert_called_once_with(manager, netboot.emmc_image_paths, override_ip='192.0.2.2',
                                          troot_image_path=netboot.troot_image_path,
                                          configuration=None, manual_reboot=False)
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


def test_autoflash_restores_environment_before_reboot():
    manager = netboot.ClientManager()
    manager.clients = {'192.0.2.2': {'state': 'Connected'}}
    configuration = netboot_device.NetbootConfiguration('192.0.2.2', changed=True)
    events = []

    def restore(saved, candidate_ips):
        assert saved is configuration
        assert candidate_ips == ['192.0.2.2']
        events.append('restore')
        saved.changed = False

    with patch.object(netboot, 'init_ssh_session'), \
            patch('sima_cli.update.remote.run_remote_command_capture',
                  return_value=(0, 'root=/dev/ram0 rw', '')), \
            patch.object(netboot, 'flash_emmc', return_value=True), \
            patch.object(netboot_device, 'restore_environment', side_effect=restore), \
            patch.object(netboot, '_schedule_reboot_into_emmc',
                         side_effect=lambda _ip: events.append('reboot')):
        assert netboot.auto_flash(
            manager, '192.0.2.2', configuration=configuration
        ) is True

    assert events == ['restore', 'reboot']
    assert configuration.changed is False


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
