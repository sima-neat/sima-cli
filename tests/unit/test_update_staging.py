"""Update staging capacity checks, without remounting the test host."""
from unittest.mock import Mock, patch

import click
import pytest

from sima_cli.update import staging, swu

GIB = 1024**3


def report(fstype='tmpfs', capacity=GIB, used=GIB // 2, free=GIB // 2,
           available=6 * GIB, total=8 * GIB):
    return f'{fstype}\n{capacity // 1024} {used // 1024} {free // 1024}\n{available // 1024} {total // 1024}\n'


@pytest.mark.parametrize('dryrun', [True, False])
def test_expansion_is_sized_for_used_space_and_incoming_payload(dryrun):
    run = Mock(side_effect=[report(), '', str(2 * GIB // 1024)])
    assert staging.expand_tmpfs(run, 2 * GIB, dryrun=dryrun)
    commands = [call.args[0] for call in run.call_args_list]
    if dryrun:
        assert commands == [staging.TMPFS_INFO]
    else:
        assert f'mount -o remount,size={5 * GIB // 2} /tmp' in commands[1]
        assert 'findmnt -n -M /tmp' in commands[1]
        assert commands[-1] == staging.TMP_FREE


@pytest.mark.parametrize('info', [
    report(fstype='ext4'), '', 'tmpfs\ninvalid data',
    report(available=0), report(total=0), report(used=-1024),
    report(available=2 * GIB),  # Enough for the file, but not the system reserve.
    report(available=3 * GIB, total=32 * GIB),  # Ten-percent reserve matters.
])
def test_expansion_refused_when_mount_or_ram_is_unsuitable(info):
    run = Mock(return_value=info)
    assert not staging.expand_tmpfs(run, 2 * GIB)
    run.assert_called_once_with(staging.TMPFS_INFO)


def test_memory_check_accounts_for_entire_file_not_only_capacity_increase():
    run = Mock(return_value=report(capacity=4 * GIB, used=3 * GIB,
                                   free=GIB, available=2 * GIB))
    assert not staging.expand_tmpfs(run, 2 * GIB)
    assert run.call_count == 1


@pytest.mark.parametrize('result', [click.ClickException('remount denied'), '0'])
def test_expansion_failure_is_reported(result):
    responses = [report(), result] if isinstance(result, Exception) else [report(), '', result]
    run = Mock(side_effect=responses)
    assert not staging.expand_tmpfs(run, 2 * GIB)


def test_legacy_enough_space_needs_no_mount_check():
    run = Mock(return_value=str(4 * GIB // 1024))
    staging.ensure_tmp_space(run, GIB)
    run.assert_called_once_with(staging.TMP_FREE)


def test_legacy_insufficient_space_stops_before_upload():
    run = Mock(side_effect=['0', report(available=GIB)])
    with pytest.raises(click.ClickException, match='Insufficient /tmp staging space'):
        staging.ensure_tmp_space(run, 2 * GIB)
    assert run.call_count == 2


@pytest.mark.parametrize('ip', [None, '192.0.2.1'])
def test_swu_selects_expanded_tmp_on_local_and_remote_target(ip):
    # Both invocation modes use Target.run for the same target-side commands.
    target = Mock(ip=ip)
    target.run.side_effect = ['', report(), '', str(3 * GIB // 1024)]
    with patch.object(swu, '_available_space', return_value=0):
        assert swu._select_staging_root(target, 2 * GIB) == '/tmp'
    assert any('remount,size=' in call.args[0] for call in target.run.call_args_list)


def test_swu_preserves_nvme_fallback_when_ram_is_insufficient():
    target = Mock()
    target.run.side_effect = lambda command: report(available=GIB) if command == staging.TMPFS_INFO else ''
    with patch.object(swu, '_available_space', side_effect=[0, 5 * GIB]):
        assert swu._select_staging_root(target, 2 * GIB) == '/media/nvme/swupdate'
    assert not any('remount,size=' in call.args[0] for call in target.run.call_args_list)


def test_direct_legacy_extraction_checks_combined_member_sizes(tmp_path):
    import io
    import tarfile
    from sima_cli.update import updater
    archive = tmp_path / 'firmware.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, size in [('troot-upgrade-simaai-ev.swu', 100),
                           ('simaai-image-palette-upgrade-modalix.swu', 200)]:
            member = tarfile.TarInfo(name)
            member.size = size
            tar.addfile(member, io.BytesIO(b'x' * size))
    run = Mock()
    with patch.object(updater, '_ensure_local_staging_space') as check:
        paths = updater._extract_required_files(str(archive), 'modalix', staging_run=run)
    check.assert_called_once_with(str(tmp_path), 300, run)
    assert len(paths) == 2


def test_legacy_remote_checks_both_images_before_transfer(tmp_path):
    from sima_cli.update import remote
    troot, palette = tmp_path / 'troot.swu', tmp_path / 'palette.swu'
    troot.write_bytes(b'x' * 100)
    palette.write_bytes(b'x' * 200)
    with patch.object(remote.paramiko, 'SSHClient'), \
         patch.object(remote, 'ensure_tmp_space', side_effect=click.ClickException('no space')) as check, \
         patch.object(remote, '_scp_file') as upload, \
         patch.object(remote, 'get_remote_boot_mmc') as boot:
        with pytest.raises(click.ClickException, match='no space'):
            remote.push_and_update_remote_board('192.0.2.1', str(troot), str(palette),
                                                'password', False)
    assert check.call_args.args[1] == 300
    upload.assert_not_called()
    boot.assert_not_called()


def test_direct_legacy_download_checks_space_before_fetch(tmp_path):
    from sima_cli.update import updater
    run = Mock()
    with patch.object(updater.tempfile, 'gettempdir', return_value=str(tmp_path)), \
         patch('sima_cli.update.swu_artifacts.bundle_size', return_value=1234), \
         patch.object(updater, '_ensure_local_staging_space', side_effect=click.ClickException('no space')) as check, \
         patch.object(updater, 'download_file_from_url') as download:
        with pytest.raises(SystemExit):
            updater._download_image('https://example.com/image.tar.gz', 'modalix', staging_run=run)
    check.assert_called_once_with(str(tmp_path), 1234, run)
    download.assert_not_called()


def test_direct_legacy_tmp_space_uses_target_runner():
    from sima_cli.update import updater
    run = Mock()
    with patch('sima_cli.update.staging.ensure_tmp_space') as check:
        updater._ensure_local_staging_space('/tmp/firmware', 1234, run)
        check.assert_called_once_with(run, 1234)
        updater._ensure_local_staging_space('/data/firmware', 9999, run)
        updater._ensure_local_staging_space('/tmp/firmware', 9999, None)
        assert check.call_count == 1
