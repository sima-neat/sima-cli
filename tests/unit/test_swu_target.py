import io
from unittest.mock import MagicMock, patch

import click
import pytest

from sima_cli.update.swu_target import Target


@pytest.mark.parametrize('offset', [-300, -1, 0, 300])
def test_clock_within_tolerance_is_not_changed(offset):
    with patch('sima_cli.update.swu_target.init_ssh_session'), \
            patch('sima_cli.update.swu_target.time.time', return_value=1800000000):
        target = Target('192.0.2.1')
        target.run = MagicMock(return_value=str(1800000000 + offset))
        target.sync_clock()
    target.run.assert_called_once_with('date -u +%s')


@pytest.mark.parametrize('offset', [-17000000, 17000000])
def test_remote_clock_is_corrected_in_either_direction(offset, capsys):
    with patch('sima_cli.update.swu_target.init_ssh_session'), \
            patch('sima_cli.update.swu_target.time.time', return_value=1800000000):
        target = Target('192.0.2.1')
        target.run = MagicMock(side_effect=[str(1800000000 + offset), '', '1800000000'])
        target.sync_clock()
    assert target.run.call_args_list[1].args == ('date -u -s @1800000000 >/dev/null',)
    assert target.run.call_args_list[2].args == ('date -u +%s',)
    assert 'DevKit clock synchronized.' in capsys.readouterr().out


def test_clock_dryrun_only_reports_drift(capsys):
    with patch('sima_cli.update.swu_target.init_ssh_session'), \
            patch('sima_cli.update.swu_target.time.time', return_value=1800000000):
        target = Target('192.0.2.1')
        target.run = MagicMock(return_value='1700000000')
        target.sync_clock(dryrun=True)
    target.run.assert_called_once_with('date -u +%s')
    assert 'would synchronize' in capsys.readouterr().out


def test_local_update_does_not_change_clock():
    target = Target()
    target.run = MagicMock()
    target.sync_clock()
    target.run.assert_not_called()


@pytest.mark.parametrize('output', ['invalid', ''])
def test_unreadable_clock_stops_update(output):
    with patch('sima_cli.update.swu_target.init_ssh_session'):
        target = Target('192.0.2.1')
        target.run = MagicMock(return_value=output)
        with pytest.raises(click.ClickException, match='Cannot read the DevKit clock'):
            target.sync_clock()
    target.run.assert_called_once()


def test_clock_correction_must_be_verified():
    with patch('sima_cli.update.swu_target.init_ssh_session'), \
            patch('sima_cli.update.swu_target.time.time', return_value=1800000000):
        target = Target('192.0.2.1')
        target.run = MagicMock(side_effect=['1700000000', '', '1700000000'])
        with pytest.raises(click.ClickException, match='synchronization failed'):
            target.sync_clock()


@pytest.mark.parametrize('failure', ['read', 'set', 'verify'])
def test_clock_failure_stops_before_certificate_or_firmware_work(failure):
    from sima_cli.update import swu

    error = click.ClickException('clock command failed')
    results = {
        'read': [error],
        'set': ['1700000000', error],
        'verify': ['1700000000', '', '1700000000'],
    }
    with patch('sima_cli.update.swu_target.init_ssh_session'), \
            patch('sima_cli.update.swu_target.time.time', return_value=1800000000):
        target = Target('192.0.2.1')
        target.run = MagicMock(side_effect=results[failure])
        with patch.object(swu, 'Target', return_value=target), \
                patch.object(swu, 'prepare_key') as certificate, \
                patch.object(swu, 'resolve_bundle') as bundle:
            with pytest.raises(click.ClickException):
                swu.update_system('3.0.0', 'modalix', ip=target.ip)
        certificate.assert_not_called()
        bundle.assert_not_called()
        target.ssh.close.assert_called_once()


def test_clock_is_synchronized_before_certificate_validation():
    from sima_cli.update import swu

    with patch('sima_cli.update.swu_target.init_ssh_session'), \
            patch('sima_cli.update.swu_target.time.time', return_value=1800000000):
        target = Target('192.0.2.1')
        target.run = MagicMock(side_effect=['1700000000', '', '1800000000'])

        def certificate(*args, **kwargs):
            assert target.run.call_count == 3
            raise click.ClickException('reached certificate validation')

        with patch.object(swu, 'Target', return_value=target), \
                patch.object(swu, 'prepare_key', side_effect=certificate):
            with pytest.raises(click.ClickException, match='reached certificate validation'):
                swu.update_system('3.0.0', 'modalix', ip=target.ip)


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


def test_local_download_is_moved_without_a_second_copy(tmp_path):
    import hashlib
    source = tmp_path / 'download.swu'
    source.write_bytes(b'bundle')
    target = Target()
    target.run = MagicMock(side_effect=['', hashlib.sha256(b'bundle').hexdigest() + ' bundle.swu'])
    target.transfer(str(source), '/tmp/staging/bundle.swu', move=True)
    assert target.run.call_args_list[0].args[0].startswith('mv -- ')
    assert target.run.call_args_list[1].args[0].startswith('sha256sum -- ')
