"""Slow recovery downloads must finish before SSH/autoflash can time out."""
import io
import os
import socket
import struct
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from sima_cli.update import netboot


IP = '192.0.2.2'
TRANSFER_ID = object()


def transfer_manager():
    manager = netboot.ClientManager()
    manager.BOOT_GRACE = 0
    manager.CONNECT_TIMEOUT = 5
    manager.POLL_INTERVAL = 3
    manager.clients[IP] = {
        'state': 'Booting', 'original_ip': IP, 'current_ip': IP,
        'mac': None, 'flash_claimed': False, 'last_transfer_progress': 0,
    }
    return manager


@pytest.mark.parametrize('transfer_ip', [IP, '192.0.2.22'])
@pytest.mark.parametrize('boot_grace', [0, 2])
def test_ssh_monitor_survives_transfer_longer_than_connection_timeout(transfer_ip, boot_grace):
    manager = transfer_manager()
    manager.BOOT_GRACE = boot_grace
    manager.clients[IP]['current_ip'] = transfer_ip
    now = [0]

    def wait(seconds):
        now[0] += seconds
        manager.record_transfer_progress(transfer_ip, 'recovery', TRANSFER_ID)
        return False

    with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(manager, '_capture_identity', return_value=True), \
            patch.object(manager, '_candidates', return_value=[transfer_ip]), \
            patch.object(netboot, '_ssh_matches_device', side_effect=lambda *_: now[0] >= 9):
        manager.monitor_client(IP, time.time())

    assert now[0] >= 9  # Beyond the original five-second connection budget.
    assert manager.clients[IP]['state'] == 'Connected'
    assert manager.clients[IP]['current_ip'] == transfer_ip


def test_stalled_transfer_still_expires_after_last_progress():
    manager = transfer_manager()
    now = [0]

    def wait(seconds):
        now[0] += seconds
        if now[0] <= 6:
            manager.clients[IP]['last_transfer_progress'] = now[0]
        return False

    with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(manager, '_capture_identity', return_value=True), \
            patch.object(manager, '_candidates', return_value=[IP]), \
            patch.object(netboot, '_ssh_matches_device', return_value=False):
        manager.monitor_client(IP, time.time())

    assert now[0] == 11  # Last progress at six, then five seconds to boot.
    assert manager.clients[IP]['state'] == 'SSH unavailable'


def test_autoflash_survives_transfer_longer_than_overall_timeout():
    manager = transfer_manager()
    now = [0]

    def wait(_seconds):
        now[0] += 4
        manager.clients[IP]['last_transfer_progress'] = now[0]
        if now[0] >= 12:
            manager.clients[IP]['state'] = 'Connected'
        return False

    with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(netboot, '_wait_for_flash_readiness', return_value=True), \
            patch.object(netboot, 'flash_emmc', return_value=True) as flash, \
            patch.object(netboot, '_schedule_reboot_into_emmc') as reboot:
        assert netboot.auto_flash(manager, IP, timeout=5)

    assert now[0] == 12
    flash.assert_called_once()
    reboot.assert_called_once_with(IP)


def test_real_tftp_transfer_reports_progress(tmp_path):
    from tftpy import TftpClient

    data = b'recovery image' * 1024
    (tmp_path / 'recovery').write_bytes(data)
    manager = MagicMock()
    server = netboot.InteractiveTftpServer(str(tmp_path), manager)
    listener = threading.Thread(target=server.listen, args=('127.0.0.1', 0), daemon=True)
    listener.start()
    try:
        assert server.is_running.wait(1)
        TftpClient('127.0.0.1', server.listenport).download(
            'recovery', str(tmp_path / 'received'), timeout=1,
        )
        assert (tmp_path / 'received').read_bytes() == data
        assert manager.record_transfer_progress.call_count > 1
        expected_file = os.path.normcase(os.path.realpath(tmp_path / 'recovery'))
        assert all(call.args[0] == '127.0.0.1' and call.args[1] == expected_file
                   for call in manager.record_transfer_progress.call_args_list)
    finally:
        server.stop(now=True)
        listener.join(timeout=2)
    assert not listener.is_alive()


def test_autoflash_still_expires_when_transfer_progress_stops():
    manager = transfer_manager()
    now = [0]

    def wait(_seconds):
        now[0] += 2
        assert now[0] <= 20, 'Autoflash did not expire after progress stopped'
        if now[0] <= 6:
            manager.record_transfer_progress(IP, 'recovery', TRANSFER_ID)
        return False

    with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(netboot, 'flash_emmc') as flash:
        with pytest.raises(netboot.NetbootDiscoveryTimeout):
            netboot.auto_flash(manager, IP, timeout=5)

    assert now[0] == 12  # Last progress at six, then the five-second window.
    flash.assert_not_called()


def test_other_client_progress_cannot_extend_selected_device_deadline():
    manager = transfer_manager()
    other_ip = '192.0.2.99'
    manager.clients[other_ip] = {
        'state': 'Booting', 'original_ip': other_ip, 'current_ip': other_ip,
        'last_transfer_progress': 0,
    }
    now = [0]

    def wait(_seconds):
        now[0] += 4
        assert now[0] <= 20, 'Another client kept the selected device alive'
        manager.record_transfer_progress(other_ip, 'recovery', TRANSFER_ID)
        return False

    with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(netboot, 'flash_emmc') as flash:
        with pytest.raises(netboot.NetbootDiscoveryTimeout):
            netboot.auto_flash(manager, IP, timeout=5)

    assert now[0] == 8
    flash.assert_not_called()


@pytest.mark.parametrize('ambiguous_current_address', [False, True])
def test_reassigned_or_ambiguous_addresses_cannot_extend_device_deadline(ambiguous_current_address):
    manager = transfer_manager()
    current_ip = '192.0.2.22'
    manager.clients[IP]['current_ip'] = current_ip
    sender = IP  # The original address was reassigned to an unrelated client.
    if ambiguous_current_address:
        other_ip = '192.0.2.99'
        manager.clients[other_ip] = {
            'state': 'Booting', 'original_ip': other_ip, 'current_ip': current_ip,
            'last_transfer_progress': 0,
        }
        sender = current_ip  # Two provisional records claim the same address.
    now = [0]

    def wait(_seconds):
        now[0] += 4
        assert now[0] <= 20, 'A reused or ambiguous address kept the device alive'
        manager.record_transfer_progress(sender, 'recovery', TRANSFER_ID)
        return False

    with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(netboot, 'flash_emmc') as flash:
        with pytest.raises(netboot.NetbootDiscoveryTimeout):
            netboot.auto_flash(manager, IP, timeout=5)

    assert now[0] == 8
    assert manager.clients[IP]['last_transfer_progress'] == 0
    flash.assert_not_called()


def test_cancellation_stops_monitoring_during_a_progressing_download():
    manager = transfer_manager()
    now = [0]

    def wait(seconds):
        now[0] += seconds
        manager.record_transfer_progress(IP, 'recovery', TRANSFER_ID)
        if seconds:
            manager.shutdown_event.set()
        return manager.shutdown_event.is_set()

    with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(manager, '_capture_identity', return_value=True), \
            patch.object(manager, '_candidates', return_value=[IP]), \
            patch.object(netboot, '_ssh_matches_device', return_value=False) as probe:
        manager.monitor_client(IP, time.time())

    assert manager.shutdown_event.is_set()
    assert manager.clients[IP]['state'] != 'SSH unavailable'
    probe.assert_called_once()


@pytest.fixture
def tftp_server(tmp_path):
    # Two data blocks let us distinguish valid ACKs, duplicates and the final
    # ACK without sleeping or waiting for the production timeout constants.
    data = b'x' * 600
    (tmp_path / 'recovery').write_bytes(data)
    manager = MagicMock()
    server = netboot.InteractiveTftpServer(str(tmp_path), manager)
    listener = threading.Thread(target=server.listen, args=('127.0.0.1', 0), daemon=True)
    listener.start()
    try:
        assert server.is_running.wait(1)
        yield server, manager, data
    finally:
        server.stop(now=True)
        listener.join(timeout=2)
    assert not listener.is_alive()


def test_duplicate_and_ignored_packets_do_not_count_as_progress(tftp_server):
    server, manager, data = tftp_server
    completed = threading.Event()

    def record(_ip, *_transfer):
        if any(session.state is None for session in server.sessions.values()):
            completed.set()

    manager.record_transfer_progress.side_effect = record
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(1)
        client.sendto(b'\x00\x01recovery\x00octet\x00', ('127.0.0.1', server.listenport))
        first, peer = client.recvfrom(1024)
        assert struct.unpack('!HH', first[:4]) == (3, 1)
        client.sendto(struct.pack('!HH', 4, 1), peer)
        final, _ = client.recvfrom(1024)
        assert struct.unpack('!HH', final[:4]) == (3, 2)
        assert first[4:] + final[4:] == data

        client.sendto(struct.pack('!HH', 4, 1), peer)  # Duplicate ACK.
        client.sendto(struct.pack('!HH', 4, 3), peer)  # ACK for an unsent block.
        client.sendto(b'\x00\x01ignored\x00octet\x00', peer)  # Ignored RRQ on session socket.
        client.sendto(struct.pack('!HH', 4, 2), peer)  # Successful final ACK.
        assert completed.wait(1)

    # Only ACK 1 advancing to block 2 and the final ACK prove progress.
    assert manager.record_transfer_progress.call_count == 2


def test_failed_read_requests_do_not_count_as_progress(tftp_server):
    server, manager, _ = tftp_server
    for _ in range(2):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(1)
            client.sendto(b'\x00\x01missing\x00octet\x00', ('127.0.0.1', server.listenport))
            error, _ = client.recvfrom(1024)
            assert struct.unpack('!H', error[:2]) == (5,)
    manager.record_transfer_progress.assert_not_called()


def test_transfer_progress_survives_tftp_block_number_rollover(tmp_path):
    from tftpy import TftpClient

    # A minimum-sized block exercises 65535 -> 0 using only about 512 KiB.
    data = b'x' * (65536 * 8 + 1)
    (tmp_path / 'recovery').write_bytes(data)
    manager = MagicMock()
    server = netboot.InteractiveTftpServer(str(tmp_path), manager)
    rollover_progress = threading.Event()

    def record(_ip, *_transfer):
        if any(session.next_block == 0 for session in server.sessions.values()):
            rollover_progress.set()

    manager.record_transfer_progress.side_effect = record
    listener = threading.Thread(target=server.listen, args=('127.0.0.1', 0), daemon=True)
    listener.start()
    try:
        assert server.is_running.wait(1)
        TftpClient('127.0.0.1', server.listenport, options={'blksize': 8}).download(
            'recovery', str(tmp_path / 'received'), timeout=2,
        )
        assert (tmp_path / 'received').read_bytes() == data
        assert rollover_progress.is_set()
    finally:
        server.stop(now=True)
        listener.join(timeout=2)
    assert not listener.is_alive()


@pytest.mark.parametrize('progress_before_identity', [False, True])
@pytest.mark.parametrize('new_interface', ['eth0', 'eth1'])
def test_registered_ip_change_preserves_download_progress(progress_before_identity, new_interface):
    manager = netboot.ClientManager()
    manager.BOOT_GRACE = 0
    manager.CONNECT_TIMEOUT = 5
    manager.POLL_INTERVAL = 3
    new_ip = '192.0.2.22'
    mac = '68:e1:54:01:02:03'
    now = [0]

    def identity(ip):
        return dict(ip=ip, mac=mac, iface='eth0' if ip == IP else new_interface)

    def wait(seconds):
        if not seconds:
            return False
        now[0] += seconds
        assert now[0] <= 20, 'The changed-IP download did not finish'
        if new_ip not in manager.clients[IP]['ip_history']:
            manager.add_client(new_ip, 'initramfs')
            if progress_before_identity:
                manager.record_transfer_progress(new_ip, 'recovery', TRANSFER_ID)
            assert not manager._capture_identity(new_ip)
        else:
            manager.record_transfer_progress(new_ip, 'recovery', TRANSFER_ID)
        return False

    with patch.object(netboot.threading, 'Thread'), \
            patch.object(netboot, 'identity_for_ip', side_effect=identity), \
            patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
            patch.object(manager, '_candidates', side_effect=lambda info, _: [info['current_ip']]), \
            patch.object(netboot, '_ssh_matches_device',
                         side_effect=lambda ip, _: ip == new_ip and now[0] >= 12):
        manager.add_client(IP, 'boot.scr')
        manager.monitor_client(IP, time.time())

    assert list(manager.clients) == [IP]
    assert manager.clients[IP]['current_ip'] == new_ip
    assert manager.clients[IP]['ip_history'] == [IP, new_ip]
    assert manager.clients[IP]['state'] == 'Connected'
    assert manager.clients[IP]['last_transfer_progress'] >= 9


@pytest.mark.parametrize('progress_before_identity', [False, True])
def test_ip_changes_do_not_reset_transfer_retry_budget(progress_before_identity):
    manager = netboot.ClientManager()
    now = [0]
    with patch.object(netboot.threading, 'Thread'), \
            patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
            patch.object(netboot, 'identity_for_ip',
                         side_effect=lambda ip: dict(ip=ip, mac='68:e1:54:01:02:03', iface='eth0')):
        manager.add_client(IP, 'recovery')
        assert manager._capture_identity(IP)
        manager.record_transfer_progress(IP, 'recovery', object())
        for attempt in range(1, 5):
            now[0] = attempt * 10
            address = '192.0.2.%d' % (20 + attempt)
            transfer = object()
            manager.add_client(address, 'recovery')
            if progress_before_identity:
                manager.record_transfer_progress(address, 'recovery', transfer)
            assert not manager._capture_identity(address)
            manager.record_transfer_progress(address, 'recovery', transfer)
    assert list(manager.clients) == [IP]
    # The initial attempt and two retries can refresh the deadline. Later
    # addresses must not turn the same boot loop into fresh retry budgets.
    assert manager.clients[IP]['last_transfer_progress'] == 20


def test_each_boot_file_has_its_own_transfer_retry_budget():
    manager = transfer_manager()
    with patch.object(netboot.time, 'monotonic', side_effect=range(10)):
        for _ in range(4):
            manager.record_transfer_progress(IP, 'kernel', object())
        assert manager.clients[IP]['last_transfer_progress'] == 2
        manager.record_transfer_progress(IP, 'initramfs', object())
    assert manager.clients[IP]['last_transfer_progress'] == 3


@pytest.mark.parametrize('wrong_peer', ['address', 'port'])
def test_wrong_tftp_peer_cannot_advance_or_refresh_transfer(tftp_server, wrong_peer):
    server, manager, _ = tftp_server
    warned = threading.Event()
    processed = threading.Event()
    completed = threading.Event()
    real_select = netboot.select.select

    def select_after_packet(*args):
        # Reaching the next receive loop proves the previous datagram has
        # finished dispatching, including tftpy's state-handler side effects.
        if warned.is_set():
            processed.set()
        return real_select(*args)

    def record(*_args):
        if any(session.state is None for session in server.sessions.values()):
            completed.set()

    manager.record_transfer_progress.side_effect = record
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger, \
            patch.object(netboot.select, 'select', side_effect=select_after_packet), \
            patch.object(netboot.log, 'warning', side_effect=lambda *_args: warned.set()), \
            patch('tftpy.TftpContexts.log.warning', side_effect=lambda *_args: warned.set()):
        client.bind(('127.0.0.1', 0))
        client.settimeout(1)
        if wrong_peer == 'address':
            try:
                stranger.bind(('127.0.0.2', client.getsockname()[1]))
            except OSError:
                pytest.skip('A second loopback address is unavailable')
        else:
            stranger.bind(('127.0.0.1', 0))
        client.sendto(b'\x00\x01recovery\x00octet\x00', ('127.0.0.1', server.listenport))
        first, peer = client.recvfrom(1024)
        assert struct.unpack('!HH', first[:4]) == (3, 1)
        stranger.sendto(struct.pack('!HH', 4, 1), peer)
        assert processed.wait(1)
        manager.record_transfer_progress.assert_not_called()
        assert next(iter(server.sessions.values())).next_block == 1

        client.sendto(struct.pack('!HH', 4, 1), peer)
        final, _ = client.recvfrom(1024)
        assert struct.unpack('!HH', final[:4]) == (3, 2)
        client.sendto(struct.pack('!HH', 4, 2), peer)
        assert completed.wait(1)
    assert manager.record_transfer_progress.call_count == 2


@pytest.mark.parametrize('watcher', ['ssh', 'autoflash'])
def test_repeated_real_downloads_cannot_keep_boot_loop_alive(tmp_path, watcher):
    from tftpy import TftpClient

    (tmp_path / 'recovery').write_bytes(b'x' * 600)
    manager = transfer_manager()
    info = manager.clients.pop(IP)
    address = '127.0.0.1'
    info.update(original_ip=address, current_ip=address)
    manager.clients[address] = info
    server = netboot.InteractiveTftpServer(str(tmp_path), manager)
    listener = threading.Thread(target=server.listen, args=(address, 0), daemon=True)
    listener.start()
    completed = threading.Event()
    now = [0]
    downloads = [0]
    record_progress = manager.record_transfer_progress

    def record(*args):
        record_progress(*args)
        if any(session.state is None for session in server.sessions.values()):
            completed.set()

    def wait(seconds):
        if not seconds:
            return False
        now[0] += 3
        assert now[0] <= 24, 'Repeated boot downloads kept extending the deadline'
        completed.clear()
        # All accepted spellings of one file must share the same retry budget.
        names = ['recovery', './recovery', 'sub/../recovery', str(tmp_path / 'recovery')]
        TftpClient(address, server.listenport).download(
            names[downloads[0] % len(names)], io.BytesIO(), timeout=1,
        )
        assert completed.wait(1)
        downloads[0] += 1
        return False

    try:
        assert server.is_running.wait(1)
        with patch.object(netboot.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(manager, 'record_transfer_progress', side_effect=record), \
                patch.object(manager.shutdown_event, 'wait', side_effect=wait), \
                patch.object(manager, '_capture_identity', return_value=True), \
                patch.object(manager, '_candidates', return_value=[]), \
                patch.object(netboot, 'flash_emmc') as flash:
            if watcher == 'ssh':
                manager.monitor_client(address, time.time())
                assert info['state'] == 'SSH unavailable'
            else:
                with pytest.raises(netboot.NetbootDiscoveryTimeout):
                    netboot.auto_flash(manager, address, timeout=5)
            flash.assert_not_called()
        assert downloads[0] > 3
        assert info['last_transfer_progress'] == 9
        assert now[0] == 15
    finally:
        server.stop(now=True)
        listener.join(timeout=2)
    assert not listener.is_alive()
