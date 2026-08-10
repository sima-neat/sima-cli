from unittest.mock import Mock, call, patch

from sima_cli.utils.pcie import (
    _ensure_remote_iperf3,
    _ssh_run_command,
    _start_remote_iperf3_server,
)


def _ssh_stream(data: bytes, exit_status: int = 0):
    stream = Mock()
    stream.read.return_value = data
    stream.channel.recv_exit_status.return_value = exit_status
    return stream


def test_ssh_run_command_returns_remote_exit_status_and_writes_stdin():
    client = Mock()
    stdin = Mock()
    stdout = _ssh_stream(b"output", exit_status=17)
    stderr = _ssh_stream(b"error")
    client.exec_command.return_value = (stdin, stdout, stderr)

    with patch("paramiko.SSHClient", return_value=client):
        result = _ssh_run_command(
            "10.0.0.2",
            "sima",
            "edgeai",
            "false",
            stdin_data="secret\n",
            get_pty=True,
        )

    assert result == (17, "output", "error")
    client.exec_command.assert_called_once_with("false", get_pty=True)
    stdin.write.assert_called_once_with("secret\n")
    stdin.flush.assert_called_once()
    client.close.assert_called_once()


def test_ensure_remote_iperf3_installs_and_verifies_binary():
    with patch(
        "sima_cli.utils.pcie._ssh_run_command",
        side_effect=[
            (1, "", "not found"),
            (0, "installed", ""),
            (0, "/usr/bin/iperf3\n", ""),
        ],
    ) as ssh_run:
        assert _ensure_remote_iperf3("10.0.0.2", "sima", "edgeai")

    install_call = ssh_run.call_args_list[1]
    assert install_call.kwargs == {"stdin_data": "edgeai\n", "get_pty": True}
    assert "edgeai" not in install_call.args[3]
    assert "apt-get install -y iperf3" in install_call.args[3]
    assert ssh_run.call_args_list[2] == call(
        "10.0.0.2", "sima", "edgeai", "command -v iperf3"
    )


def test_ensure_remote_iperf3_rejects_failed_install_or_verification():
    for results in (
        [(1, "", "not found"), (1, "", "install failed")],
        [(1, "", "not found"), (0, "", ""), (1, "", "not found")],
    ):
        with patch("sima_cli.utils.pcie._ssh_run_command", side_effect=results):
            assert not _ensure_remote_iperf3("10.0.0.2", "sima", "edgeai")


def test_start_remote_iperf3_waits_for_listener():
    with patch("sima_cli.utils.pcie._ssh_run_command", return_value=(0, "", "")) as ssh_run:
        assert _start_remote_iperf3_server("10.0.0.2", "sima", "edgeai", "10.0.0.2")

    command = ssh_run.call_args.args[3]
    assert "nohup iperf3 -s -1 -B 10.0.0.2" in command
    assert "ss -lnt" in command
    assert "10.0.0.2:5201" in command
    assert "kill -0" in command


def test_start_remote_iperf3_rejects_invalid_bind_ip():
    with patch("sima_cli.utils.pcie._ssh_run_command") as ssh_run:
        assert not _start_remote_iperf3_server(
            "10.0.0.2", "sima", "edgeai", "10.0.0.2; reboot"
        )

    ssh_run.assert_not_called()
