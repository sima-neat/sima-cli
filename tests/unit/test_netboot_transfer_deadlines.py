"""Slow recovery downloads must finish before SSH/autoflash can time out."""
import socket
import struct
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from sima_cli.update import netboot


IP = '192.0.2.2'


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
        manager.record_transfer_progress(transfer_ip)
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
        assert all(call.args == ('127.0.0.1',)
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
            manager.record_transfer_progress(IP)
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
        manager.record_transfer_progress(other_ip)
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
        manager.record_transfer_progress(sender)
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
        manager.record_transfer_progress(IP)
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

    def record(_ip):
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

    def record(_ip):
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
