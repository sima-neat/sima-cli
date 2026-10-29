from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.update import reset_overlay as reset, swu

STATE = {'running slot': 'B', 'active slot': 'B', 'next-boot': 'B',
         'upgrade_available': 'no', 'rollback': 'normal', 'raw': 'board state\n'}
REPORT = {'status': 'enabled', 'upper': '/data/overlay/upper'}


@pytest.mark.parametrize('args', [[], ['--reboot'], ['--dryrun'], ['--ip', '192.0.2.1']])
def test_cli_dispatch_skips_login_self_update_and_host_install(args):
    with patch('sima_cli.cli.handle_update', return_value=True) as handle, \
            patch('sima_cli.cli.check_for_update') as self_update, \
            patch('sima_cli.cli.check_artifactory_reachability') as reach, \
            patch('sima_cli.cli.install_host_package') as host:
        result = CliRunner().invoke(main, ['-i', 'update', '--reset-overlay'] + args)
    assert result.exit_code == 0, result.output
    assert handle.call_args.kwargs['reset_overlay'] is True
    self_update.assert_not_called()
    reach.assert_not_called()
    host.assert_not_called()


@pytest.mark.parametrize('args', [['--inspect'], ['--swap'], ['-v', '3.0.0'], ['--force'],
                                 ['--troot_only'], ['--signing-cert', 'key.pem'], ['--flavor', 'full']])
def test_cli_rejects_installation_and_other_operations(args):
    with patch('sima_cli.cli.handle_update') as handle:
        result = CliRunner().invoke(main, ['update', '--reset-overlay'] + args)
    assert result.exit_code != 0
    handle.assert_not_called()


@pytest.mark.parametrize('version,board,fwtype', [('2.1.3', 'modalix', 'elxr'),
    ('3.0.0', 'davinci', 'elxr'), ('3.0.0', 'modalix', 'yocto')])
def test_unsupported_target_never_resets(version, board, fwtype):
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=(board, version, '', False, fwtype)), \
            patch.object(reset, 'reset_system_overlay') as operation:
        with pytest.raises(click.ClickException, match='Modalix eLxr 3.0'):
            swu.handle_update(None, ip='192.0.2.1', reset_overlay=True)
    operation.assert_not_called()


def test_modern_target_resets_without_installing():
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', '3.0.0', '', False, 'elxr')), \
            patch.object(reset, 'reset_system_overlay') as operation, patch.object(swu, 'update_system') as install:
        assert swu.handle_update(None, ip='192.0.2.1', reset_overlay=True, reboot=True)
    assert operation.call_args.kwargs['reboot'] is True
    install.assert_not_called()


@pytest.mark.parametrize('changes', [{'next-boot': 'A'}, {'upgrade_available': 'yes'},
                                   {'rollback': 'unknown'}, {'running slot': 'unknown'}])
def test_pending_or_unknown_state_prevents_reset(changes):
    with pytest.raises(click.ClickException):
        reset.reset_script({**STATE, **changes})


@pytest.mark.parametrize('dryrun,confirmed,backup_fails', [(True, True, False), (False, False, False),
                                                        (False, True, True), (False, True, False)])
def test_inventory_and_confirmation_gate_mutation(dryrun, confirmed, backup_fails):
    target = MagicMock()
    backup = ('/data/.overlay-backup/test', [], [])
    with patch.object(reset, 'Target', return_value=target), \
            patch.object(reset, 'inspect_target', return_value=STATE), \
            patch.object(reset, 'inspect_overlay', return_value=REPORT), \
            patch.object(reset.click, 'confirm', side_effect=None if confirmed else click.Abort()), \
            patch.object(swu, 'preserve_overlay_info', return_value=backup,
                         side_effect=click.ClickException('backup failed') if backup_fails else None) as preserve, \
            patch.object(swu, 'print_overlay_recovery_guidance'):
        if not dryrun and (not confirmed or backup_fails):
            with pytest.raises((click.Abort, click.ClickException)):
                reset.reset_system_overlay(dryrun=dryrun)
        else:
            reset.reset_system_overlay(dryrun=dryrun, reboot=True)
    assert target.run.called is (not dryrun and confirmed and not backup_fails)
    assert preserve.called is (not dryrun and confirmed)
    if target.run.called:
        script = target.run.call_args.args[0]
        assert 'mv /data/overlay/upper /data/overlay/upper.discarded' in script
        assert 'switch-slot' not in script and 'swupdate -v' not in script
        assert '/sbin/reboot' in script
    target.close.assert_called_once()


def test_unverified_overlay_cannot_be_reset():
    with patch.object(reset, 'Target') as target, patch.object(reset, 'inspect_target', return_value=STATE), \
            patch.object(reset, 'inspect_overlay', return_value={'status': 'unavailable'}), \
            patch.object(swu, 'preserve_overlay_info') as preserve:
        with pytest.raises(click.ClickException, match='Cannot verify'):
            reset.reset_system_overlay(auto_confirm=True)
    preserve.assert_not_called()
    target.return_value.run.assert_not_called()


@pytest.mark.parametrize('changed,busy', [(False, False), (True, False), (False, True)])
def test_cleanup_script_preserves_data_and_boot_state(tmp_path, changed, busy):
    import os
    import subprocess

    data = tmp_path / 'data'
    upper = data / 'overlay/upper'
    work = data / 'overlay/work'
    upper.mkdir(parents=True)
    work.mkdir()
    (upper / 'custom-software').write_text('local')
    (data / 'keep').write_text('shared data')
    commands = tmp_path / 'bin'
    commands.mkdir()
    scripts = {
        'simaai-ab-info': "printf '%s\\n' '" + ('changed' if changed else 'board state') + "'",
        'pgrep': 'exit ' + ('0' if busy else '1'),
        'mountpoint': 'exit 1',
        'findmnt': 'case "$*" in *FSTYPE*) echo overlay;; *OPTIONS*) echo upperdir=/data/overlay/upper;; esac',
        'sync': 'exit 0',
        'flock': 'exit 0',
    }
    for name, body in scripts.items():
        path = commands / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    script = reset.reset_script(STATE).replace('/data', str(data)).replace(
        '/run/lock/sima-cli-swupdate.lock', str(tmp_path / 'lock'))
    # Prefer GNU rm on macOS so the platform cleanup runs with its safety flag.
    import shutil
    gnu_rm = shutil.which('grm')
    if gnu_rm:
        (commands / 'rm').symlink_to(gnu_rm)
    elif __import__('sys').platform == 'darwin':
        script = script.replace('--one-file-system ', '')
    # Mock device tools, but execute the real rename/delete sequence in the fixture.
    result = subprocess.run(['sh', '-c', script], env={**os.environ, 'PATH': str(commands) + ':' + os.environ['PATH']},
                            capture_output=True, text=True)
    assert (result.returncode == 0) is (not changed and not busy), result.stderr
    assert (upper / 'custom-software').exists() is (changed or busy)
    assert (data / 'keep').read_text() == 'shared data'
    if not changed and not busy:
        assert not upper.exists() and not work.exists()
        assert not (data / 'overlay/upper.discarded').exists()


def test_yes_skips_confirmation_and_preserves_inventory_before_reset():
    target = MagicMock()
    order = []
    target.run.side_effect = lambda script: order.append('reset')
    with patch.object(reset, 'Target', return_value=target), \
            patch.object(reset, 'inspect_target', return_value=STATE), \
            patch.object(reset, 'inspect_overlay', return_value=REPORT), \
            patch.object(reset.click, 'confirm') as confirm, \
            patch.object(swu, 'preserve_overlay_info', side_effect=lambda *args: (order.append('inventory') or ('/data/.overlay-backup/test', [], []))), \
            patch.object(swu, 'print_overlay_recovery_guidance'):
        reset.reset_system_overlay(auto_confirm=True)
    confirm.assert_not_called()
    assert order == ['inventory', 'reset']
    assert '/sbin/reboot' not in target.run.call_args.args[0]
