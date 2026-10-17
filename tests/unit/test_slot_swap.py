import os
import subprocess
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.update import swap, swu
from sima_cli.update.ab_state import parse_state

STATE = '''active slot: A
active version: 3.0.0_develop_B2121
fallback slot: B
fallback version: 3.0.0_develop_B2038
running slot: A
control block: valid A, valid B, upgrade_available: no, next-boot: A
normal boot'''


@pytest.mark.parametrize('running', ['A', 'B'])
def test_destination_both_directions(running):
    raw = STATE if running == 'A' else STATE.translate(str.maketrans('AB', 'BA'))
    assert swap.swap_destination(parse_state(raw))[0] == ('B' if running == 'A' else 'A')


@pytest.mark.parametrize('key,value', [
    ('running slot', 'unknown'), ('active slot', 'B'), ('fallback slot', 'A'),
    ('validity B', 'blank'), ('validity B', 'invalid'), ('validity B', 'unknown'),
    ('rollback', 'rollback'), ('rollback', 'unknown'),
    ('upgrade_available', 'yes'), ('upgrade_available', 'unknown'),
    ('next-boot', 'B'), ('next-boot', 'unknown'),
    ('fallback version', ''), ('fallback version', 'unknown'),
])
def test_unavailable_or_unsafe_state_never_switches(key, value):
    state = dict(parse_state(STATE), **{key: value})
    target = MagicMock()
    with patch.object(swap, 'Target', return_value=target), patch.object(swap, 'inspect_target', return_value=state):
        with pytest.raises(click.ClickException):
            swap.swap_slots(auto_confirm=True)
    target.run.assert_not_called()
    target.close.assert_called_once()


@pytest.mark.parametrize('mode', ['yes', 'decline', 'dryrun', 'reboot', 'failed'])
def test_swap_confirmation_and_verification(mode, capsys):
    before = parse_state(STATE)
    after = parse_state(STATE.replace('next-boot: A', 'next-boot: B').replace('upgrade_available: no', 'upgrade_available: yes'))
    target = MagicMock()
    if mode == 'failed':
        target.run.side_effect = OSError('disconnected')
    with patch.object(swap, 'Target', return_value=target), \
            patch.object(swap, 'inspect_target', side_effect=[before, after]), \
            patch.object(swap, 'inspect_overlay'), \
            patch.object(swap.click, 'confirm', side_effect=click.Abort()), \
            patch.object(swu, '_reboot_and_verify') as verify:
        if mode in ('decline', 'failed'):
            with pytest.raises(click.Abort if mode == 'decline' else click.ClickException):
                swap.swap_slots(auto_confirm=mode != 'decline')
        else:
            swap.swap_slots(auto_confirm=True, dryrun=mode == 'dryrun', reboot=mode == 'reboot')
        assert target.run.call_count == (0 if mode in ('decline', 'dryrun') else 2 if mode == 'reboot' else 1)
        assert verify.called == (mode == 'reboot')
    target.close.assert_called_once()
    output = capsys.readouterr().out
    assert '3.0.0_develop_B2121' in output
    assert '3.0.0_develop_B2038' in output
    assert 'overlay' in output


@pytest.mark.parametrize('args', [['--inspect'], ['-v', '3.0'], ['3.0'], ['--force'], ['--troot_only'], ['--signing-cert', 'x'], ['--flavor', 'full']])
def test_cli_rejects_conflicting_options(args):
    with patch('sima_cli.cli.handle_update') as handle:
        result = CliRunner().invoke(main, ['update', '--swap'] + args)
    assert result.exit_code != 0
    assert '--swap cannot be combined' in result.output
    handle.assert_not_called()


def test_cli_swap_offline_and_global_yes():
    with patch('sima_cli.cli.check_for_update') as update, \
            patch('sima_cli.cli.check_artifactory_reachability') as reach, \
            patch('sima_cli.cli.handle_update', return_value=True) as handle:
        result = CliRunner().invoke(main, ['-i', '-y', 'update', '--ip', '192.0.2.1', '--swap', '--reboot'])
    assert result.exit_code == 0, result.output
    assert handle.call_args.kwargs['swap'] is True
    assert handle.call_args.kwargs['auto_confirm'] is True
    update.assert_not_called()
    reach.assert_not_called()


@pytest.mark.parametrize('version,fwtype', [('2.1.3', 'elxr'), ('3.0.0', 'yocto'), ('', '')])
def test_dispatch_refuses_unsupported_targets(version, fwtype):
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', version, '', False, fwtype)):
        with pytest.raises(click.ClickException, match='Slot swapping requires'):
            swu.handle_update(None, ip='192.0.2.1', swap=True)


@pytest.mark.parametrize('changed,busy', [(False, False), (True, False), (False, True)])
def test_shell_rechecks_state_and_installer_before_mutating(tmp_path, changed, busy):
    # Execute the actual shell transaction against harmless fake platform tools.
    tools = {
        'flock': 'exit 0',
        'pgrep': 'exit ' + ('0' if busy else '1'),
        'simaai-ab-info': "cat <<'STATE'\n" + (STATE.replace('B2038', 'B2222') if changed else STATE) + '\nSTATE',
        'simaai-trootctl': 'printf "%s\\n" "$*" > "$SWAP_RECORD"',
    }
    for name, body in tools.items():
        path = tmp_path / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    record = tmp_path / 'record'
    script = swap.switch_script(parse_state(STATE), 'B').replace('/run/lock/sima-cli-swupdate.lock', str(tmp_path / 'lock'))
    result = subprocess.run(['sh', '-c', script], env=dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'], SWAP_RECORD=str(record)), capture_output=True, text=True)
    assert (result.returncode == 0) == (not changed and not busy)
    assert record.exists() == (not changed and not busy)
    if record.exists():
        assert record.read_text() == 'switch-slot b\n'


def test_failed_post_switch_verification_never_reboots():
    target = MagicMock()
    with patch.object(swap, 'Target', return_value=target), \
            patch.object(swap, 'inspect_target', return_value=parse_state(STATE)), \
            patch.object(swap, 'inspect_overlay'), \
            patch.object(swu, '_reboot_and_verify') as verify:
        with pytest.raises(click.ClickException, match='could not be verified'):
            swap.swap_slots(auto_confirm=True, reboot=True)
    assert target.run.call_count == 1
    verify.assert_not_called()
    target.close.assert_called_once()


def test_local_dispatch_and_confirmation_names_target_version():
    target = MagicMock()
    after = parse_state(STATE.replace('next-boot: A', 'next-boot: B').replace('upgrade_available: no', 'upgrade_available: yes'))
    with patch('sima_cli.update.local.get_local_board_info', return_value=('modalix', '3.0.0', '', False, 'elxr')), \
            patch.object(swap, 'Target', return_value=target), \
            patch.object(swap, 'inspect_target', side_effect=[parse_state(STATE), after]), \
            patch.object(swap, 'inspect_overlay'), \
            patch.object(swap.click, 'confirm', return_value=True) as confirm:
        assert swu.handle_update(None, swap=True)
    assert 'slot B (3.0.0_develop_B2038)' in confirm.call_args.args[0]
    assert confirm.call_args.kwargs == {'default': False, 'abort': True}
