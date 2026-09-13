import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from sima_cli.update import netboot, remote


def test_wait_returns_success_and_closes_socket():
    connection = MagicMock()
    with patch.object(remote.socket, 'create_connection', return_value=connection):
        assert remote.wait_for_ssh('192.168.2.3') is True
    connection.__exit__.assert_called_once()


def test_timeout_is_not_success(capsys):
    with patch.object(remote.socket, 'create_connection') as connect:
        assert remote.wait_for_ssh('192.168.2.3', timeout=0) is False
    connect.assert_not_called()
    assert 'Timeout' in capsys.readouterr().out


def test_cancellation_during_connection_is_not_success(capsys):
    cancel = threading.Event()
    connection = MagicMock()

    def connect(*args, **kwargs):
        cancel.set()
        return connection

    with patch.object(remote.socket, 'create_connection', side_effect=connect):
        assert remote.wait_for_ssh('192.168.2.3', cancel_event=cancel) is False
    connection.__exit__.assert_called_once()
    output = capsys.readouterr().out
    assert 'Board is online' not in output
    assert 'Timeout' not in output


def test_cancellation_wakes_retry_delay():
    cancel = threading.Event()
    attempted = threading.Event()
    result = []

    def connect(*args, **kwargs):
        attempted.set()
        raise OSError('unreachable')

    with patch.object(remote.socket, 'create_connection', side_effect=connect):
        worker = threading.Thread(
            target=lambda: result.append(remote.wait_for_ssh('192.168.2.3', cancel_event=cancel))
        )
        worker.start()
        try:
            assert attempted.wait(1)
            cancel.set()
            worker.join(1)
            assert not worker.is_alive()
            assert result == [False]
        finally:
            cancel.set()
            worker.join(4)


def test_spinner_is_cleaned_up_on_interrupt():
    threads = []
    real_thread = threading.Thread

    def record_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    with patch.object(remote.threading, 'Thread', side_effect=record_thread), \
            patch.object(remote.socket, 'create_connection', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            remote.wait_for_ssh('192.168.2.3')
    assert threads and all(not thread.is_alive() for thread in threads)


@pytest.mark.parametrize('online', [True, False])
def test_monitor_only_marks_success_connected(online, capsys):
    manager = netboot.ClientManager()
    manager.clients['192.168.2.3'] = {'state': 'Booting', 'board_info': None}
    with patch.object(netboot, 'wait_for_ssh', return_value=online) as wait, \
            patch.object(manager.shutdown_event, 'wait', side_effect=[False, True]):
        manager.monitor_client('192.168.2.3', time.time() - 61)
    wait.assert_called_once_with('192.168.2.3', timeout=120, cancel_event=manager.shutdown_event)
    assert manager.clients['192.168.2.3']['state'] == ('Connected' if online else 'SSH unavailable')
    if not online:
        output = capsys.readouterr().out
        assert 'SSH is available' not in output
        assert "f <ip>" in output


def test_prompt_interrupt_discover_then_flash(capsys):
    manager = netboot.ClientManager()
    manager.clients['192.168.2.3'] = {'state': 'Booting', 'board_info': None}
    with patch('builtins.input', side_effect=[KeyboardInterrupt, 'd', 'f 192.168.2.20', 'q']), \
            patch.object(netboot, '_discover_netboot_devices') as discover, \
            patch.object(netboot, 'flash_emmc') as flash:
        assert netboot.run_cli(manager) is True
    assert manager.shutdown_event.is_set()
    assert manager.clients['192.168.2.3']['state'] == 'SSH check stopped'
    discover.assert_called_once_with()
    assert flash.call_args.kwargs['override_ip'] == '192.168.2.20'
    output = capsys.readouterr().out
    assert 'Ctrl+C' in output
    assert 'serial' in output
    assert 'ip -4 addr' in output
    assert 'TFTP server is still running' in output


def test_discovery_failure_still_prints_recovery(capsys):
    with patch('sima_cli.discover.discover.discover_and_probe', side_effect=OSError('offline')) as discover:
        netboot._discover_netboot_devices()
    discover.assert_called_once_with(mdns_only=True)
    output = capsys.readouterr().out
    assert 'Device discovery failed' in output
    assert 'f <ip>' in output


def test_stopped_monitoring_does_not_restart_for_new_client():
    manager = netboot.ClientManager()
    manager.stop_monitoring()
    with patch.object(netboot.threading, 'Thread') as thread:
        manager.add_client('192.168.2.20', 'image')
    thread.assert_not_called()
    assert manager.clients['192.168.2.20']['state'] == 'SSH check stopped'
