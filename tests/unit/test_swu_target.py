import io
from unittest.mock import MagicMock, patch

import click
import pytest

from sima_cli.update.swu_target import Target


@pytest.mark.parametrize('code', [0, 1, -1])
def test_local_command_stream_and_checked_exit(code):
    process = MagicMock()
    process.stdout = io.BytesIO(b'one\rtwo\nthree')
    process.wait.return_value = code
    lines = []
    with patch('sima_cli.update.swu_target.subprocess.Popen', return_value=process) as popen:
        if code:
            with pytest.raises(click.ClickException):
                Target(passwd='test password').run('echo test', stream=lines.append)
        else:
            Target(passwd='test password').run('echo test', stream=lines.append)
    assert lines == ['one', 'two', 'three']
    assert 'test password' not in str(popen.call_args.args)
    process.stdin.write.assert_called_once_with(b'test password\n')


def test_remote_transfer_verifies_checksum(tmp_path):
    file = tmp_path / 'bundle.swu'
    file.write_bytes(b'bundle')
    with patch('sima_cli.update.swu_target.init_ssh_session'):
        target = Target('192.0.2.1')
        target.run = MagicMock(return_value='incorrect-checksum bundle.swu')
        with pytest.raises(click.ClickException, match='checksum'):
            target.transfer(str(file), '/data/staging/bundle.swu')


def test_remote_command_sends_password_only_via_stdin():
    with patch('sima_cli.update.swu_target.init_ssh_session') as connect:
        stdin, stdout, stderr = MagicMock(), MagicMock(), MagicMock()
        stdout.read.side_effect = [b'o', b'k', b'\n', b'']
        stdout.channel.recv_exit_status.return_value = 0
        connect.return_value.exec_command.return_value = stdin, stdout, stderr
        target = Target('192.0.2.1', passwd='secret')
        assert target.run("printf '%s' test") == 'ok'
        command = connect.return_value.exec_command.call_args.args[0]
        assert 'secret' not in command
        stdin.write.assert_called_once_with('secret\n')
