import io
import json
import shlex
import subprocess
from unittest.mock import patch

import pytest

from sima_cli.update.emmc import finalize_emmc, prepare_emmc
from sima_cli.update import netboot


def tree(lvm=True):
    child = {'name': '/dev/mmcblk0p3', 'maj:min': '179:3', 'type': 'part'}
    if lvm:
        child['children'] = [{'name': '/dev/mapper/vg-data', 'maj:min': '253:1', 'type': 'lvm'}]
    return json.dumps({'blockdevices': [
        {'name': '/dev/mmcblk0', 'maj:min': '179:0', 'type': 'disk', 'children': [child]}
    ]})


def harness(mounted, fail=False, remaining=False):
    calls = []
    active = [mounted]

    def read(path, *args, **kwargs):
        return io.StringIO(active[0] if path == '/proc/self/mountinfo' else 'Filename Type Size Used Priority\n')

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == 'umount':
            if fail:
                raise subprocess.CalledProcessError(1, args)
            active[0] = '\n'.join(line for line in active[0].splitlines()
                                  if line.split()[4].replace('\\040', ' ') != args[-1])

    with patch('builtins.open', side_effect=read), \
            patch('subprocess.check_output', side_effect=[tree(), tree(), tree(remaining)]), \
            patch('subprocess.run', side_effect=run):
        prepare_emmc()
    return calls


def test_lvm_bind_mounts_nested_order_and_unrelated_nvme(capsys):
    calls = harness('20 1 253:1 / /data rw - ext4 /dev/mapper/vg-data rw\n'
                    '21 20 253:1 /tools /data/my\\040tools rw - ext4 /dev/mapper/vg-data rw\n'
                    '22 1 259:1 / /media/nvme rw - ext4 /dev/nvme0n1p1 rw\n')
    assert calls == [['umount', '--', '/data/my tools'], ['umount', '--', '/data'],
                     ['lvchange', '-an', '/dev/mapper/vg-data'], ['sync']]
    assert '/dev/mapper/vg-data -> /data' in capsys.readouterr().out


def test_legacy_partition_mount():
    calls = harness('20 1 179:3 / /legacy rw - ext4 /dev/mmcblk0p3 rw\n')
    assert calls[0] == ['umount', '--', '/legacy']


def test_busy_mount_aborts():
    with pytest.raises(subprocess.CalledProcessError):
        harness('20 1 253:1 / /data rw - ext4 /dev/mapper/vg-data rw\n', fail=True)


def test_root_on_lvm_aborts():
    with pytest.raises(RuntimeError, match='Root filesystem'):
        harness('20 1 253:1 / / rw - ext4 /dev/mapper/vg-data rw\n')


def test_mapping_remaining_aborts():
    with pytest.raises(RuntimeError, match='active mounts or device mappings'):
        harness('', remaining=True)


def test_prepare_lvm_without_udev_sync():
    calls = []

    with patch('builtins.open', side_effect=lambda path, *args, **kwargs: io.StringIO(
            '' if path == '/proc/self/mountinfo' else 'Filename Type Size Used Priority\n')), \
            patch('subprocess.check_output', side_effect=[tree(), tree(), tree(False)]), \
            patch('subprocess.run', side_effect=lambda args, **kwargs: calls.append(args)):
        prepare_emmc(udev_sync=False)

    assert ['lvchange', '-an', '--noudevsync', '/dev/mapper/vg-data'] in calls


def test_finalize_pauses_udev_releases_mappings_and_repairs_gpt():
    calls = []

    with patch('sima_cli.update.emmc.prepare_emmc') as prepare, \
            patch('subprocess.run', side_effect=lambda args, **kwargs: calls.append(args)):
        finalize_emmc()

    prepare.assert_called_once_with('/dev/mmcblk0', udev_sync=False)
    assert calls == [
        ['udevadm', 'settle'],
        ['udevadm', 'control', '--stop-exec-queue'],
        ['parted', '--script', '--fix', '/dev/mmcblk0', 'print'],
        ['blockdev', '--rereadpt', '/dev/mmcblk0'],
        ['udevadm', 'control', '--start-exec-queue'],
        ['udevadm', 'settle'],
    ]


def test_finalize_resumes_udev_after_partition_refresh_failure():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == 'blockdev':
            raise subprocess.CalledProcessError(1, args)

    with patch('sima_cli.update.emmc.prepare_emmc'), patch('subprocess.run', side_effect=run):
        with pytest.raises(subprocess.CalledProcessError):
            finalize_emmc()

    assert calls[-2:] == [
        ['udevadm', 'control', '--start-exec-queue'],
        ['udevadm', 'settle'],
    ]


def test_preparation_failure_prevents_dd(capsys):
    with patch.object(netboot, '_select_flash_target', return_value='192.0.2.1'), \
            patch.object(netboot, 'copy_file_to_remote_board', return_value=True), \
            patch.object(netboot, 'init_ssh_session', return_value=object()), \
            patch.object(netboot, 'run_remote_command', side_effect=RuntimeError('busy')) as run:
        flashed = netboot.flash_emmc(None, ['/images/test.img.gz'])
    run.assert_called_once()
    assert run.call_args.kwargs == {'check': True, 'command_label': 'Preparing eMMC for flashing'}
    assert flashed is False
    assert 'Flash completed' not in capsys.readouterr().out


def test_failed_image_write_never_reports_success(capsys):
    with patch.object(netboot, '_select_flash_target', return_value='192.0.2.1'), \
            patch.object(netboot, 'copy_file_to_remote_board', return_value=True), \
            patch.object(netboot, 'init_ssh_session', return_value=object()), \
            patch.object(netboot, 'run_remote_command', side_effect=[None, RuntimeError('write failed')]) as run:
        flashed = netboot.flash_emmc(None, ['/images/test.img.gz'])
    command = run.call_args.args[1]
    assert 'pipefail' in command and 'conv=fsync' in command
    assert run.call_args.kwargs == {'check': True}
    assert flashed is False
    assert 'Flash completed' not in capsys.readouterr().out


def test_finalization_failure_returns_false(capsys):
    with patch.object(netboot, '_select_flash_target', return_value='192.0.2.1'), \
            patch.object(netboot, 'copy_file_to_remote_board', return_value=True), \
            patch.object(netboot, 'init_ssh_session', return_value=object()), \
            patch.object(netboot, 'run_remote_command',
                         side_effect=[None, None, RuntimeError('refresh failed')]):
        flashed = netboot.flash_emmc(None, ['/images/test.img.gz'])

    assert flashed is False
    assert 'Flash completed' not in capsys.readouterr().out


@pytest.mark.parametrize('image', ['/images/test.img.gz', '/images/test.wic.gz'])
def test_all_images_finalize_partition_table_after_write(image, capsys):
    with patch.object(netboot, '_select_flash_target', return_value='192.0.2.1'), \
            patch.object(netboot, 'copy_file_to_remote_board', return_value=True), \
            patch.object(netboot, 'init_ssh_session', return_value=object()), \
            patch.object(netboot, 'run_remote_command') as run:
        flashed = netboot.flash_emmc(None, [image])

    finalization = run.call_args_list[2]
    remote_source = shlex.split(finalization.args[1])[-1]
    assert 'finalize_emmc()' in remote_source
    assert "['parted', '--script', '--fix', device, 'print']" in remote_source
    assert "['blockdev', '--rereadpt', device]" in remote_source
    assert finalization.kwargs == {
        'check': True,
        'command_label': 'Finalizing eMMC partitions',
    }
    assert flashed is True
    assert 'Flash completed' in capsys.readouterr().out
