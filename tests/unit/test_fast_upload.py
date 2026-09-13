from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sima_cli.update import fast_upload


def test_sftp_fallback_preserves_progress():
    sftp = MagicMock(); progress = MagicMock()
    with patch.object(fast_upload.shutil, 'which', return_value=None):
        fast_upload.upload(MagicMock(), sftp, '192.0.2.1', 'secret', 'local', '/tmp/image', progress)
    sftp.put.assert_called_once_with('local', '/tmp/image', callback=progress)


@pytest.mark.parametrize('failure', [None, 'ssh', 'size', 'broken_pipe'])
def test_native_upload_publishes_only_after_success(tmp_path, failure):
    source = tmp_path / 'source'; source.write_bytes(b'x' * (1024 * 1024 + 3))
    ssh = MagicMock();sftp = MagicMock();process = MagicMock();progress = MagicMock()
    key = ssh.get_transport.return_value.get_remote_server_key.return_value
    key.get_name.return_value = 'ssh-ed25519';key.get_base64.return_value = 'PINNEDKEY'
    process.wait.return_value = 1 if failure in ('ssh', 'broken_pipe') else 0
    process.poll.return_value = process.wait.return_value
    if failure == 'broken_pipe':process.stdin.write.side_effect = BrokenPipeError()
    sftp.stat.return_value = SimpleNamespace(st_size=0 if failure == 'size' else source.stat().st_size)
    destination = "/tmp/image with 'quote'.gz"

    def spawn(command, **kwargs):
        assert kwargs['start_new_session'] is True
        assert 'secret' not in str(command)
        assert kwargs['env']['SIMA_CLI_SSH_PASSWORD'] == 'secret'
        from pathlib import Path
        helper = Path(kwargs['env']['SSH_ASKPASS'])
        assert 'secret' not in helper.read_text()
        assert (helper.parent / 'known_hosts').read_text() == '192.0.2.1 ssh-ed25519 PINNEDKEY\n'
        assert 'StrictHostKeyChecking=yes' in command
        return process

    with patch.object(fast_upload.shutil, 'which', return_value='/usr/bin/ssh'), \
            patch.object(fast_upload.subprocess, 'Popen', side_effect=spawn):
        if failure:
            with pytest.raises(RuntimeError):
                fast_upload.upload(ssh,sftp,'192.0.2.1','secret',str(source),destination,progress)
            sftp.posix_rename.assert_not_called()
        else:
            fast_upload.upload(ssh,sftp,'192.0.2.1','secret',str(source),destination,progress)
            assert progress.call_args.args == (source.stat().st_size,source.stat().st_size)
            assert sftp.posix_rename.call_args.args[1] == destination
    assert sftp.remove.call_args.args[0].startswith(destination + '.sima-upload-')
