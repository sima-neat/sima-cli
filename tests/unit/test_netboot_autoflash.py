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
            patch.object(netboot, 'flash_emmc') as flash:
        if expected:
            netboot.auto_flash(manager, '192.0.2.2')
            flash.assert_called_once_with(manager, netboot.emmc_image_paths, override_ip='192.0.2.2',
                                          troot_image_path=netboot.troot_image_path)
        else:
            with pytest.raises(click.ClickException, match='not confirmed'):
                netboot.auto_flash(manager, '192.0.2.2')
            flash.assert_not_called()
    connect.return_value.close.assert_called_once()


def test_other_device_does_not_trigger_flash():
    manager=netboot.ClientManager();manager.clients={'192.0.2.1':{'state':'Connected'}}
    with patch.object(netboot, 'flash_emmc') as flash:
        with pytest.raises(click.ClickException, match='Timed out'):
            netboot.auto_flash(manager, '192.0.2.2', timeout=0)
    flash.assert_not_called()


def test_failed_ssh_wait_does_not_mark_connected():
    manager=netboot.ClientManager();manager.clients={'192.0.2.2':{'state':'Booting'}}
    def wait(*args,**kwargs):
        manager.shutdown_event.set()
        return False
    with patch.object(netboot, 'wait_for_ssh', side_effect=wait):
        manager.monitor_client('192.0.2.2',0)
    assert manager.clients['192.0.2.2']['state']=='Booting'


def test_autoflash_confirmation_explains_overwrite(capsys):
    with patch.object(netboot_device,'init_ssh_session'), \
            patch.object(netboot_device,'_checked'), \
            patch.object(netboot_device,'network_settings',return_value={'interface':'end0','netmask':'255.255.255.0','gateway':'0.0.0.0'}), \
            patch.object(click,'confirm',return_value=False) as confirm:
        with pytest.raises(click.Abort):
            netboot_device.configure_and_reboot('192.0.2.2','192.0.2.1',autoflash=True)
    assert 'overwrites its internal storage' in capsys.readouterr().out
    assert 'automatically flash' in confirm.call_args.args[0]
