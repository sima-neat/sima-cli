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


@pytest.mark.parametrize('failure', ['insufficient_ram', 'remount_failed'])
def test_eight_gib_target_falls_back_to_nvme(failure):
    commands = []

    def run(command):
        commands.append(command)
        if command == staging.TMPFS_INFO:
            return report(total=8 * GIB, available=3 * GIB if failure == 'insufficient_ram' else 7 * GIB)
        if 'remount,size=' in command:
            raise click.ClickException('remount failed')
        return ''

    with patch.object(swu, '_available_space', side_effect=[GIB, 100 * GIB]):
        assert swu._select_staging_root(Mock(run=run), 5 * GIB) == '/media/nvme/swupdate'
    assert any('mount -o remount,rw /media/nvme' in command for command in commands)
    if failure == 'insufficient_ram':
        assert not any('remount,size=' in command for command in commands)


def test_nvme_full_falls_back_to_data_on_small_ram_board():
    target = Mock()
    target.run.side_effect = lambda command: report(available=2 * GIB, total=8 * GIB) if command == staging.TMPFS_INFO else ''
    with patch.object(swu, '_available_space', side_effect=[GIB, GIB, 10 * GIB]):
        assert swu._select_staging_root(target, 5 * GIB) == '/data'
