import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from sima_cli.discover import identity
from sima_cli.update import netboot

MAC = '68:e1:54:01:02:03'
OLD = '192.0.2.2'
NEW = '192.0.2.20'
OTHER = '68:e1:54:01:02:04'


def record(ip=OLD, mac=MAC, iface='en0'):
    return dict(ip=ip, mac=mac, iface=iface)


@pytest.mark.parametrize('system,output,expected', [
    ('Darwin', '? (192.0.2.2) at 68:e1:54:1:2:3 on en0 ifscope [ethernet]', [record()]),
    ('Linux', '192.0.2.2 dev en0 lladdr 68:e1:54:01:02:03 STALE', [record()]),
    ('Linux', '192.0.2.2 ether 68:e1:54:01:02:03 C en0', [record()]),
    ('Linux', '192.0.2.2 dev en0 INCOMPLETE\n192.0.2.3 dev en0 FAILED', []),
    ('Linux', '192.0.2.2 dev en0 lladdr ff:ff:ff:ff:ff:ff NOARP', []),
    ('Darwin', '? (192.0.2.2) at (incomplete) on en0 ifscope [ethernet]', []),
])
def test_neighbor_formats(system, output, expected):
    assert identity.parse_neighbors(output, system) == expected


@pytest.mark.parametrize('value,expected', [
    ('68-E1-54-1-2-3', MAC), ('00:00:00:00:00:00', None),
    ('ff:ff:ff:ff:ff:ff', None), ('bad', None), (None, None),
])
def test_valid_mac(value, expected):
    assert identity.valid_mac(value) == expected


def test_linux_arp_fallback_is_bounded():
    with patch.object(identity.platform, 'system', return_value='Linux'), \
            patch.object(identity.subprocess, 'check_output', side_effect=[
                FileNotFoundError(), '192.0.2.2 ether 68:e1:54:01:02:03 C en0'
            ]) as run:
        assert identity.read_neighbors() == [record()]
    assert run.call_args_list[0].args[0] == ['ip', '-4', 'neigh', 'show']
    assert run.call_args_list[1].args[0] == ['arp', '-n']
    assert all(call.kwargs['timeout'] == 1 for call in run.call_args_list)


def test_neighbor_timeout_is_quiet(capsys):
    with patch.object(identity.platform, 'system', return_value='Darwin'), \
            patch.object(identity.subprocess, 'check_output', side_effect=identity.subprocess.TimeoutExpired('arp', 1)):
        assert identity.read_neighbors() == []
    assert capsys.readouterr().out == ''


def test_no_gateway_or_ambiguous_identity():
    with patch.object(identity, 'read_neighbors', return_value=[record(ip='192.0.2.1')]):
        assert identity.identity_for_ip(OLD) is None
    with patch.object(identity, 'read_neighbors', return_value=[record(), record(iface='en1')]):
        assert identity.identity_for_ip(OLD) is None


def test_multicast_uses_source_and_ignores_bad_messages():
    cancel = threading.Event()
    sock = MagicMock()
    messages = iter([
        (b'invalid', (NEW, 50000)),
        (b'[]', (NEW, 50000)),
        (b'{"mac":"bad"}', (NEW, 50000)),
        (b'{"mac":"68:e1:54:1:2:3", "ip":"192.0.2.99"}', (NEW, 50000)),
    ])

    def receive(_size):
        result = next(messages)
        if b'192.0.2.99' in result[0]:
            cancel.set()
        return result

    sock.recvfrom.side_effect = receive
    with patch.object(identity, 'get_local_ip_candidates', return_value=[('en0', '192.0.2.10'), ('en1', '198.51.100.10')]), \
            patch.object(identity.socket, 'socket', return_value=sock), \
            patch.object(identity.select, 'select', return_value=([sock], [], [])):
        assert identity.query_multicast(iface='en0', cancel_event=cancel) == [record(NEW)]
    sock.bind.assert_called_once_with(('192.0.2.10', 60000))
    sock.sendto.assert_called_once_with(b'DISCOVER', ('239.255.42.1', 50000))
    sock.close.assert_called_once()


def test_multicast_closes_socket_when_bind_fails():
    sock = MagicMock()
    sock.bind.side_effect = OSError('busy')
    with patch.object(identity, 'get_local_ip_candidates', return_value=[('en0', '192.0.2.10')]), \
            patch.object(identity.socket, 'socket', return_value=sock):
        assert identity.query_multicast() == []
    sock.close.assert_called_once()


def manager(configuration=None):
    result = netboot.ClientManager(configuration=configuration)
    result.BOOT_GRACE = 0
    result.CONNECT_TIMEOUT = 0.05
    result.POLL_INTERVAL = 0
    result.clients[OLD] = dict(state='Booting', filename='Image', board_info=None,
                               timestamp=time.time(), original_ip=OLD, current_ip=OLD,
                               ip_history=[OLD], flash_claimed=False)
    return result


def test_changed_ip_is_verified_and_used_for_manual_flash(capsys):
    target = manager()
    with patch.object(netboot, 'identity_for_ip', return_value=record()), \
            patch.object(netboot, 'query_multicast', return_value=[record(NEW), record('192.0.2.50', OTHER)]), \
            patch.object(netboot, 'read_neighbors', return_value=[record()]), \
            patch.object(netboot, '_ssh_matches_device', side_effect=lambda ip, mac: ip == NEW and mac == MAC) as verify:
        target.monitor_client(OLD, time.time())
    assert target.clients[OLD]['state'] == 'Connected'
    assert target.clients[OLD]['ip_history'] == [OLD, NEW]
    assert target.get_client_info()[0][0] == NEW
    assert netboot._select_flash_target(target) == NEW
    assert verify.call_args.args == (NEW, MAC)
    assert f'{OLD} → {NEW}' in capsys.readouterr().out


def test_stale_neighbor_and_wrong_interface_do_not_retarget(capsys):
    target = manager()
    with patch.object(netboot, 'identity_for_ip', return_value=record()), \
            patch.object(netboot, 'query_multicast', return_value=[record(NEW, iface='en1'), record(NEW, OTHER)]), \
            patch.object(netboot, 'read_neighbors', return_value=[record()]), \
            patch.object(netboot, '_ssh_matches_device', return_value=False) as verify:
        target.monitor_client(OLD, time.time())
    assert target.clients[OLD]['state'] == 'SSH unavailable'
    assert target.clients[OLD]['current_ip'] == OLD
    assert all(call.args == (OLD, MAC) for call in verify.call_args_list)
    assert 'Timed out' in capsys.readouterr().out


def test_repeated_tftp_and_same_mac_do_not_create_another_monitor():
    target = manager()
    target.clients[OLD].update(mac=MAC, iface='en0')
    with patch.object(netboot.threading, 'Thread') as thread:
        target.add_client(OLD, 'rootfs')
    thread.assert_not_called()
    target.clients[NEW] = dict(state='Booting')
    with patch.object(netboot, 'identity_for_ip', return_value=record(NEW)):
        assert target._capture_identity(NEW) is False
    assert list(target.clients) == [OLD]


def test_monitor_cancellation_wakes_boot_grace():
    target = manager()
    target.BOOT_GRACE = 60
    with patch.object(netboot, 'identity_for_ip', return_value=record()), \
            patch.object(netboot, '_ssh_matches_device') as verify:
        thread = threading.Thread(target=target.monitor_client, args=(OLD, time.time()))
        target.monitor_threads.append(thread)
        thread.start()
        target.stop_monitoring()
        assert not thread.is_alive()
    verify.assert_not_called()
    assert target.clients[OLD]['state'] == 'SSH check stopped'


def test_ssh_identity_is_authenticated_and_session_closed():
    ssh = MagicMock()
    stdout = MagicMock()
    stdout.read.return_value = (MAC + '\n').encode()
    ssh.exec_command.return_value = (None, stdout, None)
    with patch.object(netboot.paramiko, 'SSHClient', return_value=ssh):
        assert netboot._ssh_matches_device(NEW, MAC)
        assert not netboot._ssh_matches_device(NEW, OTHER)
    assert ssh.connect.call_args.kwargs['auth_timeout'] == 2
    assert ssh.close.call_count == 2


def test_repeated_mac_on_another_host_interface_is_not_flashed_twice():
    target = manager()
    target.clients[OLD].update(mac=MAC, iface='en0')
    target.clients[NEW] = dict(state='Booting')
    with patch.object(netboot, 'identity_for_ip', return_value=record(NEW, iface='en1')):
        assert not target._capture_identity(NEW)
    assert list(target.clients) == [OLD]


@pytest.mark.parametrize('cancelled,timeout', [(True, 1), (False, 0)])
def test_multicast_cancellation_and_deadline_do_not_send(cancelled, timeout):
    cancel = threading.Event()
    if cancelled:
        cancel.set()
    with patch.object(identity, 'get_local_ip_candidates', return_value=[('en0', '192.0.2.10')]), \
            patch.object(identity.socket, 'socket') as socket_factory:
        assert identity.query_multicast(cancel_event=cancel, timeout=timeout) == []
    socket_factory.assert_not_called()


def test_ssh_auth_failure_closes_session():
    ssh = MagicMock()
    ssh.connect.side_effect = OSError('offline')
    with patch.object(netboot.paramiko, 'SSHClient', return_value=ssh):
        assert not netboot._ssh_matches_device(NEW, MAC)
    ssh.close.assert_called_once()




def test_selected_configuration_seeds_identity_without_arp():
    from sima_cli.update.netboot_device import NetbootConfiguration
    config = NetbootConfiguration(OLD, hardware_address=MAC)
    target = manager(config)
    with patch.object(netboot, 'identity_for_ip', return_value=None):
        assert target._capture_identity(OLD)
    assert target.clients[OLD]['mac'] == MAC
    assert target.clients[OLD]['iface'] is None


def test_selected_configuration_changes_to_verified_address():
    from sima_cli.update.netboot_device import NetbootConfiguration
    config = NetbootConfiguration(OLD, hardware_address=MAC)
    target = manager(config)
    with patch.object(netboot, 'identity_for_ip', return_value=record()), \
            patch.object(netboot, 'query_multicast', return_value=[record(NEW)]), \
            patch.object(netboot, 'read_neighbors', return_value=[]), \
            patch.object(netboot, '_ssh_matches_device', return_value=True):
        target.monitor_client(OLD, time.time())
    assert config.devkit == NEW
    assert target.get_client_info()[0][0] == NEW


def test_autoflash_tracks_original_device_when_its_old_ip_is_reused():
    from sima_cli.update.netboot_device import NetbootConfiguration
    config = NetbootConfiguration(OLD, hardware_address=MAC)
    target = manager(config)
    target.clients[OLD].update(current_ip=NEW, mac=MAC, state='Connected')
    target.clients['192.0.2.30'] = dict(original_ip='192.0.2.30', current_ip=OLD,
                                      mac=OTHER, state='Connected')
    with patch.object(netboot, '_wait_for_flash_readiness', return_value=True) as ready, \
            patch.object(netboot, 'flash_emmc', return_value=True) as flash, \
            patch.object(netboot, '_schedule_reboot_into_emmc') as reboot:
        assert netboot.auto_flash(target, OLD, configuration=config)
        assert netboot.auto_flash(target, OLD, configuration=config) is False
    assert ready.call_args.args[0] == NEW
    flash.assert_called_once()
    assert flash.call_args.kwargs['override_ip'] == NEW
    reboot.assert_called_once_with(NEW)


@pytest.mark.parametrize('option', ['--devkit', '--devkit-ip'])
def test_user_command_keeps_force_and_device_alias_without_artifactory(option):
    from click.testing import CliRunner
    from sima_cli.cli import main
    with patch('sima_cli.cli.check_for_update', return_value=False), \
            patch('sima_cli.cli.internal_resource_exists', return_value=False), \
            patch('sima_cli.cli.check_artifactory_reachability', return_value=False), \
            patch('sima_cli.update.netboot.setup_netboot') as setup:
        result = CliRunner().invoke(main, ['-i', 'bootimg', '-v', '3.0.0', '--netboot',
                                          option, '10.0.0.244', '-f'])
    assert result.exit_code == 0, result.output
    assert setup.call_args.args == ('3.0.0', 'modalix', True, False)
    assert setup.call_args.kwargs['swtype'] == 'elxr'
    assert setup.call_args.kwargs['allow_daily_fallback'] is True
    assert setup.call_args.kwargs['devkit'] == '10.0.0.244'


def test_missing_identity_keeps_manual_recovery_available(capsys):
    target = manager()
    with patch.object(netboot, 'identity_for_ip', return_value=None), \
            patch.object(netboot, '_ssh_matches_device', return_value=True), \
            patch.object(netboot, 'query_multicast') as query:
        target.monitor_client(OLD, time.time())
    assert target.clients[OLD]['state'] == 'Connected'
    query.assert_not_called()
    assert 'f <ip>' in capsys.readouterr().out


def test_verified_new_ip_merges_provisional_tftp_record():
    target = manager()
    provisional = dict(state='Booting', current_ip=NEW, mac=None)
    target.clients[NEW] = provisional
    with patch.object(netboot, 'identity_for_ip', return_value=record()), \
            patch.object(netboot, 'query_multicast', return_value=[record(NEW)]), \
            patch.object(netboot, 'read_neighbors', return_value=[]), \
            patch.object(netboot, '_ssh_matches_device', return_value=True):
        target.monitor_client(OLD, time.time())
    assert list(target.clients) == [OLD]
    assert target.get_client_info()[0][0] == NEW
    assert provisional['superseded'] is True
