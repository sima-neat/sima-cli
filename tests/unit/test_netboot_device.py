"""Discovery, explicit confirmation, and TFTP-before-reboot ordering."""
import shlex
import subprocess
from unittest.mock import MagicMock, patch

import click
import pytest

from sima_cli.update import netboot_device as device, netboot
from sima_cli.sdk import commands as sdk

READ_NETWORK = device.network_settings

NETWORK = {"interface": "end0", "netmask": "255.255.255.0", "gateway": "0.0.0.0"}


@pytest.fixture(autouse=True)
def mock_network():
    with patch.object(device, "network_settings", return_value=NETWORK):
        yield



@pytest.mark.parametrize('devices,answer,expected', [
    ([{'ip': '192.0.2.1'}], None, '192.0.2.1'),
    ([{'ip': '192.0.2.1'}, {'ip': '192.0.2.2'}], 2, '192.0.2.2'),
])
def test_discovery_selects_device(devices, answer, expected):
    with patch.object(sdk, 'discover_and_probe', return_value=devices), \
            patch.object(click, 'prompt', return_value=answer) as prompt:
        assert device.resolve_device() == expected
    assert prompt.call_count == (1 if answer else 0)


def test_explicit_device_skips_discovery():
    with patch.object(sdk, 'discover_and_probe') as discover:
        assert device.resolve_device('192.0.2.8') == '192.0.2.8'
    discover.assert_not_called()


def test_no_discovered_device_is_actionable():
    with patch.object(sdk, 'discover_and_probe', return_value=[]):
        with pytest.raises(click.ClickException, match='--devkit'):
            device.resolve_device()


def test_decline_never_writes_or_reboots():
    with patch.object(device, 'init_ssh_session') as connect, \
            patch.object(device, '_checked') as command, \
            patch.object(click, 'confirm', return_value=False):
        with pytest.raises(click.Abort):
            device.configure_and_reboot('192.0.2.1', '192.0.2.10')
    assert command.call_count == 1
    assert not command.call_args.args[1].startswith('sudo')
    connect.return_value.close.assert_called_once()


def test_confirmation_precedes_writes_and_reboot(capsys):
    events = []
    with patch.object(device, 'init_ssh_session'), \
            patch.object(device, '_checked', side_effect=lambda ssh, cmd: events.append(cmd) or '') , \
            patch.object(click, 'confirm', side_effect=lambda *a, **k: events.append('confirmed') or True):
        device.configure_and_reboot('192.0.2.1', '192.0.2.10')
    assert events[1] == 'confirmed'
    script = shlex.split(events[2])[3]
    subprocess.run(['sh', '-n'], input=script, text=True, check=True)
    assert script.index('cp -p') < script.index('fw_setenv')
    assert 'boot_targets net' in script
    assert 'serverip 192.0.2.10' in script
    assert 'netboot.scr.uimg' in script
    assert 'ipaddr 192.0.2.1' in script
    assert 'forcenetcfg static' in script
    assert 'netmask 255.255.255.0' in script
    assert 'dhcp' not in script
    assert 'test "$(fw_printenv' in script
    assert events[3] == 'sudo systemd-run --on-active=3s /sbin/reboot'
    assert 'persist across reboots' in capsys.readouterr().out


@pytest.mark.parametrize('failed_call', [0, 1])
def test_remote_failure_prevents_reboot(failed_call):
    effects = [click.ClickException('preflight failed')] if failed_call == 0 else ['', click.ClickException('write failed')]
    with patch.object(device, 'init_ssh_session') as connect, \
            patch.object(device, '_checked', side_effect=effects) as command, \
            patch.object(click, 'confirm', return_value=True):
        with pytest.raises(click.ClickException):
            device.configure_and_reboot('192.0.2.1', '192.0.2.10')
    assert not any('systemd-run' in call.args[1] for call in command.call_args_list)
    connect.return_value.close.assert_called_once()


@pytest.mark.parametrize('bind_failure', [False, True])
def test_tftp_ready_before_remote_changes_and_always_cleaned_up(tmp_path, bind_failure):
    boot = tmp_path / 'netboot.scr.uimg'
    boot.write_bytes(b'boot')
    events = []
    server = MagicMock()
    server.is_running.wait.return_value = not bind_failure
    if bind_failure:
        server.listen.side_effect = PermissionError('port 69')
    else:
        server.listen.side_effect = lambda *a: events.append('listening')

    def start_thread(*args, **kwargs):
        thread = MagicMock()
        thread.start.side_effect = kwargs['target']
        return thread

    with patch.object(netboot, 'get_environment_type', return_value=('host', 'mac')), \
            patch.object(netboot, 'download_image', return_value=[str(boot)]), \
            patch.object(netboot, 'get_local_ip_candidates', return_value=[('en0', '192.0.2.10')]), \
            patch.object(netboot, 'InteractiveTftpServer', return_value=server), \
            patch.object(netboot, 'ClientManager') as manager, \
            patch.object(netboot.threading, 'Thread', side_effect=start_thread), \
            patch.object(netboot, 'run_cli', side_effect=lambda *a: events.append('cli')), \
            patch.object(device, 'resolve_device', return_value='192.0.2.1'), \
            patch.object(device, 'server_address', return_value='192.0.2.10'), \
            patch.object(device, 'configure_and_reboot', side_effect=lambda *a: events.append('reboot')) as reboot:
        if bind_failure:
            with pytest.raises(RuntimeError, match='Permission denied'):
                netboot.setup_netboot('3.0', 'modalix')
            reboot.assert_not_called()
        else:
            netboot.setup_netboot('3.0', 'modalix')
            assert events == ['listening', 'reboot', 'cli']
    server.stop.assert_called_once_with(now=True)
    manager.return_value.shutdown.assert_called_once()


@pytest.mark.parametrize('gateway', [None, '192.0.2.254'])
def test_network_settings_reuses_selected_interface_and_route(gateway):
    import json
    addresses = [{'ifname': 'end1', 'addr_info': [{'family': 'inet', 'local': '192.0.2.1', 'prefixlen': 24}]},
                 {'ifname': 'end0', 'addr_info': [{'family': 'inet', 'local': '10.0.0.1', 'prefixlen': 8}]}]
    route = {'dev': 'end1'}
    if gateway:
        route['gateway'] = gateway
    with patch.object(device, '_checked', side_effect=[json.dumps(addresses), json.dumps([route])]):
        result = READ_NETWORK(MagicMock(), '192.0.2.1', '192.0.2.10')
    assert result == dict(NETWORK, interface='end1', gateway=gateway or '0.0.0.0')


def test_host_address_uses_route_to_selected_devkit():
    with patch.object(device.socket, 'socket') as socket:
        route = socket.return_value.__enter__.return_value
        route.getsockname.return_value = ('192.0.2.10', 45678)
        assert device.server_address('192.0.2.1') == '192.0.2.10'
    route.connect.assert_called_once_with(('192.0.2.1', 69))


@pytest.mark.parametrize('initial_mode,failure', [
    ('ro', None), ('rw', None), ('ro', 'rw'), ('ro', 'write'), ('ro', 'ro'),
])
def test_boot_mount_and_rollback_script(tmp_path, initial_mode, failure):
    """Execute the actual shell script against fake mount/fw tools and temp files."""
    import os
    import sys
    boot = tmp_path / 'boot'
    boot.mkdir()
    for name in ('uboot.env', 'uboot-redund.env'):
        (boot / name).write_text('original ' + name)
    state = tmp_path / 'mode'
    state.write_text(initial_mode)
    log = tmp_path / 'commands'
    binary = tmp_path / 'bin'
    binary.mkdir()
    stub = '''
import os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
boot = Path(os.environ['TEST_BOOT'])
mode = Path(os.environ['TEST_MODE'])
failure = os.environ['TEST_FAILURE']
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(name + ' ' + ' '.join(args) + '\\n')
if name == 'findmnt':
    print(str(boot) if 'TARGET' in args else mode.read_text() + ',relatime')
elif name == 'mount':
    wanted = 'rw' if 'remount,rw' in args else 'ro'
    if failure == wanted:
        sys.exit(1)
    mode.write_text(wanted)
elif name == 'fw_setenv':
    assert mode.read_text() == 'rw'
    (boot / 'uboot.env').write_text('changed')
    (boot / 'uboot-redund.env').write_text('changed')
    if failure == 'write':
        sys.exit(1)
elif name == 'fw_printenv':
    if '-n' in args:
        print('net' if args[-1] == 'boot_targets' else '192.0.2.10')
    else:
        print('boot_targets=mmc0')
'''
    for name in ('findmnt', 'mount', 'fw_setenv', 'fw_printenv', 'sync'):
        path = binary / name
        path.write_text('#!' + sys.executable + '\n' + stub)
        path.chmod(0o755)
    script = device._uboot_script('192.0.2.1', '192.0.2.10', NETWORK).replace('/boot', str(boot))
    env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
               TEST_BOOT=str(boot), TEST_MODE=str(state), TEST_LOG=str(log), TEST_FAILURE=failure or '')
    result = subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True)
    commands = log.read_text()
    assert (result.returncode == 0) == (failure is None), result.stdout + result.stderr
    if failure == 'rw':
        assert 'fw_setenv' not in commands
        assert 'Cannot remount' in result.stderr
    elif failure == 'write':
        assert 'Restored saved U-Boot' in result.stdout
    elif failure == 'ro':
        assert 'Could not restore' in result.stderr
    if failure in ('rw', 'write'):
        for name in ('uboot.env', 'uboot-redund.env'):
            assert (boot / name).read_text() == 'original ' + name
    if failure != 'ro':
        assert state.read_text() == initial_mode
    if initial_mode == 'rw':
        assert 'mount -o' not in commands
    if initial_mode == 'ro' and failure != 'rw':
        assert commands.index('mount -o remount,rw') < commands.index('fw_setenv')
        assert 'mount -o remount,ro' in commands


def test_netboot_sudo_does_not_allocate_echoing_pty():
    ssh = MagicMock()
    stdin, stdout, stderr = MagicMock(), MagicMock(), MagicMock()
    stdout.read.return_value = b'prepared\n'
    stderr.read.return_value = b''
    stdout.channel.recv_exit_status.return_value = 0
    ssh.exec_command.return_value = (stdin, stdout, stderr)
    assert device._checked(ssh, 'sudo true') == 'prepared'
    ssh.exec_command.assert_called_once_with('sudo -S true', get_pty=False)
