import json
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.update import swu, swu_artifacts
from sima_cli.update.ab_state import parse_state

STATE = '''medium : /dev/mmcblk0
active slot : A
active version : 3.0.0_old
active os : eLxr 12
fallback slot : B
fallback version : 3.0.0_previous
fallback os : eLxr 12
running slot: A
  control block: valid A, valid B, upgrade_available: no, next-boot: A
normal boot
'''


def test_parse_state_keeps_running_next_boot_and_unknown_distinct():
    state = parse_state(STATE.replace('next-boot: A', 'next-boot: B').replace('upgrade_available: no', 'upgrade_available: yes'))
    assert state['running slot'] == 'A'
    assert state['next-boot'] == 'B'
    assert state['upgrade_available'] == 'yes'
    assert state['validity A'] == 'valid'
    assert parse_state('unexpected')['running slot'] == 'unknown'
    assert parse_state('unexpected')['upgrade_available'] == 'unknown'
    assert parse_state('rollback boot')['rollback'] == 'rollback'


def test_inspect_never_checks_for_self_update_or_installs():
    with patch('sima_cli.cli.check_for_update') as update, patch('sima_cli.cli.check_artifactory_reachability') as reach, \
            patch('sima_cli.cli.handle_update', return_value=True) as handle:
        result = CliRunner().invoke(main, ['-i', 'update', '--ip', '192.0.2.1', '--inspect'])
    assert result.exit_code == 0, result.output
    update.assert_not_called()
    reach.assert_not_called()
    assert handle.call_args.kwargs['inspect'] is True


@pytest.mark.parametrize('args', [['--reboot'], ['-v', '3.0'], ['--dryrun'], ['--force'], ['--troot_only']])
def test_inspection_rejects_update_options(args):
    with patch('sima_cli.cli.handle_update') as handle:
        result = CliRunner().invoke(main, ['update', '--inspect'] + args)
    assert result.exit_code != 0
    handle.assert_not_called()


@pytest.mark.parametrize('version,modern', [('2.1.3', False), ('3.0.0_daily_B1', True), ('10.0.0', True)])
def test_remote_dispatch_uses_running_board_version(version, modern):
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', version, '', False, 'elxr')), \
            patch.object(swu, 'update_system') as install:
        assert swu.handle_update(None, ip='192.0.2.1') is modern
        assert install.called is modern


@pytest.mark.parametrize('option', ['force', 'troot_only'])
def test_partial_or_unsigned_option_rejected(option):
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', '3.0.0', '', False, 'elxr')), \
            patch.object(swu, 'update_system') as install:
        with pytest.raises(click.ClickException):
            swu.handle_update('3.0', ip='192.0.2.1', **{option: True})
        install.assert_not_called()


def test_legacy_to_ab_migration_is_not_sent_to_ota():
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', '2.1.3', '', False, 'elxr')):
        with pytest.raises(click.ClickException, match='recovery'):
            swu.handle_update('3.0', ip='192.0.2.1')


def test_internal_swu_query_filters_and_sorts_real_artifacts():
    def item(version, created):
        return {'path': f'elxr/bsp/modalix/{version}/artifacts/palette', 'name': 'elxr-palette-modalix-3.0.0-agate-arm64.swu', 'created': created}
    with patch.object(swu_artifacts.requests, 'Session') as session, patch.object(swu_artifacts, 'get_auth_token', return_value='test'):
        session.return_value.post.return_value.json.return_value = {'results': [
            item('3.0.0_custom_B9', None), item('3.0.0_daily_B10', '2026-09-09T01:00:00-07:00'),
            item('3.0.0_daily_B11', '2026-09-09T09:00:00Z'), item('3.1.0_B1', None), item('13.0.0_B1', None),
        ]}
        builds = swu_artifacts.internal_bundles('modalix', '3.0')
        assert [b['version'] for b in builds] == ['3.0.0_daily_B11', '3.0.0_daily_B10', '3.0.0_custom_B9']
        assert builds[0]['url'].endswith('-agate-arm64.swu')
        query = session.return_value.post.call_args.kwargs['data']
        assert '"$match": "elxr-palette-modalix-*.swu"' in query


def test_internal_picker_preserves_full_build_and_timestamp():
    builds = [{'version': '3.0.0_daily_B11', 'created': swu_artifacts._created('2026-09-09T09:00:00Z'), 'url': 'https://example/B11.swu'},
              {'version': '3.0.0_custom_B9', 'created': None, 'url': 'https://example/B9.swu'}]
    with patch.object(swu_artifacts, 'internal_bundles', return_value=builds), patch('InquirerPy.inquirer.fuzzy') as picker:
        picker.return_value.execute.return_value = builds[0]['url']
        assert swu_artifacts.resolve_bundle('3.0', 'modalix', True) == builds[0]['url']
        choices = picker.call_args.kwargs['choices']
        assert '2026-09-09 09:00:00 UTC' in choices[0]['name']
        assert 'unknown' in choices[1]['name']


def test_no_matching_swu_is_failure():
    with patch.object(swu_artifacts, 'internal_bundles', return_value=[]):
        with pytest.raises(click.ClickException, match='No matching'):
            swu_artifacts.resolve_bundle('3.0', 'modalix', True)


def test_external_release_lookup_is_explicitly_deferred():
    with patch.object(swu_artifacts, '_portal_session') as session:
        with pytest.raises(click.ClickException, match='not published'):
            swu_artifacts.resolve_bundle('3.0', 'modalix')
        session.assert_not_called()


def test_installer_uses_signed_full_collection_and_progress_without_reboot():
    script = swu.install_script('/data/test/bundle.swu', '/etc/swupdate/public.pem')
    assert 'swupdate-progress -w &' in script
    assert '-e update,full' in script and '-k /etc/swupdate/public.pem' in script
    assert 'simaai-ota' not in script and 'full-flash' not in script
    assert 'reboot' not in script
    assert script.index('swupdate-progress -w') < script.index('swupdate -v')


def run_install(tmp_path, *, fail=False, dryrun=False):
    bundle = tmp_path / 'bundle.swu'
    bundle.write_bytes(b'signed bundle fixture')
    target = MagicMock()
    def run(script, **kwargs):
        if 'mktemp' in script:
            return '/data/sima-cli-update.ABC12345'
        if 'df -Pk' in script:
            return '999999999'
        if 'swupdate -v' in script and fail:
            raise click.ClickException('install failed')
        return ''
    target.run.side_effect = run
    with patch.object(swu, 'Target', return_value=target), patch.object(swu, 'preflight', return_value=parse_state(STATE)), \
            patch.object(swu, 'resolve_bundle', return_value=str(bundle)), patch.object(swu, 'inspect_target', return_value=parse_state(STATE.replace('next-boot: A', 'next-boot: B').replace('upgrade_available: no', 'upgrade_available: yes'))), \
            patch.object(swu, '_reboot_and_verify') as reboot:
        if fail:
            with pytest.raises(click.ClickException, match='install failed'):
                swu.update_system('3.0', 'modalix', ip='192.0.2.1', auto_confirm=True, reboot=True)
            reboot.assert_not_called()
        else:
            swu.update_system('3.0', 'modalix', ip='192.0.2.1', auto_confirm=True, dryrun=dryrun)
    return target


def test_remote_install_stages_under_data_and_checks_transfer(tmp_path):
    target = run_install(tmp_path)
    assert target.transfer.call_args.args[1] == '/data/sima-cli-update.ABC12345/bundle.swu'
    assert any('-e update,full' in c.args[0] for c in target.run.call_args_list)
    assert any('rm -rf' in c.args[0] for c in target.run.call_args_list)


def test_failed_installer_retains_staging_and_never_reboots(tmp_path):
    target = run_install(tmp_path, fail=True)
    assert not any('rm -rf' in c.args[0] for c in target.run.call_args_list)


def test_dryrun_does_not_transfer_or_install(tmp_path):
    target = run_install(tmp_path, dryrun=True)
    target.transfer.assert_not_called()
    assert not any('mktemp' in c.args[0] or '-e update,full' in c.args[0] for c in target.run.call_args_list)


def test_pending_upgrade_preflight_stops_install():
    with patch.object(swu, 'inspect_target', return_value=parse_state(STATE.replace('upgrade_available: no', 'upgrade_available: yes'))):
        with pytest.raises(click.ClickException, match='pending'):
            swu.preflight(MagicMock(), swu.DEFAULT_KEY)


def test_progress_uses_real_artifact_percentage():
    with patch.object(swu, 'Progress') as progress:
        with swu.InstallProgress() as display:
            display('[ ==== ] 2 of 4 37% (rootfs.ext4.gz), dwl 0% of 0 bytes')
        kwargs = progress.return_value.update.call_args.kwargs
        assert kwargs['completed'] == 37
        assert '2/4' in kwargs['description']
        assert 'rootfs.ext4.gz' in kwargs['description']


def test_low_staging_space_fails_before_install():
    target = MagicMock()
    target.run.return_value = '1'
    with pytest.raises(click.ClickException, match='Insufficient'):
        swu._check_space(target, 1000)


def test_signature_key_argument_is_shell_quoted():
    import shlex
    key = '/data/key name;touch injected.pem'
    script = swu.install_script('/data/bundle name.swu', key)
    command = script.splitlines()[-1]
    assert shlex.split(command) == ['swupdate', '-v', '-i', '/data/bundle name.swu', '-k', key, '-e', 'update,full']


@pytest.mark.parametrize('factory', [False, True])
@pytest.mark.parametrize('code', [0, 23])
def test_installer_wrapper_preserves_exit_code_and_cleans_monitor(tmp_path, code, factory):
    import os
    import subprocess
    # Exercise the actual shell wrapper with harmless stand-in executables.
    for name, body in {
        'pgrep': 'exit 1', 'flock': 'exit 0',
        'simaai-trootctl': ('printf \'control block : blank/invalid -> factory (boots slot A)\\nrunning slot: A\\nnormal boot\\n\''
                           if factory else 'echo "upgrade_available: no"'),
        'swupdate-progress': 'echo "[ ==== ] 1 of 2 50% (rootfs.ext4.gz)"; exec sleep 20',
        'swupdate': f'sleep 0.1; echo "installer finished"; exit {code}',
    }.items():
        path = tmp_path / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    script = swu.install_script('/data/bundle.swu', '/etc/key.pem').replace('/run/lock/sima-cli-swupdate.lock', str(tmp_path / 'lock'))
    result = subprocess.run(['sh', '-c', script], env={**os.environ, 'PATH': str(tmp_path) + ':' + os.environ['PATH']},
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == code
    assert 'installer finished' in result.stdout
    assert '1 of 2 50%' in result.stdout


def test_explicit_long_swu_url_is_not_treated_as_filesystem_path():
    url = 'https://example.com/bundle.swu?token=' + 'a' * 1000
    assert swu_artifacts.resolve_bundle(url, 'modalix') == url


def test_remote_cli_dryrun_keeps_version_search_and_skips_legacy():
    with patch('sima_cli.cli.check_for_update', return_value=False), \
            patch('sima_cli.cli.internal_resource_exists', return_value=True), \
            patch('sima_cli.cli.check_artifactory_reachability', return_value=True), \
            patch('sima_cli.cli.handle_update', return_value=True) as handle, \
            patch('sima_cli.cli.perform_update') as legacy:
        result = CliRunner().invoke(main, ['-i', 'update', '--ip', '192.0.2.1', '-v', '3.0', '--dryrun'])
    assert result.exit_code == 0, result.output
    assert handle.call_args.args[0] == '3.0'
    assert handle.call_args.kwargs['dryrun'] is True
    assert handle.call_args.kwargs['internal'] is True
    legacy.assert_not_called()


def test_local_elxr_dispatches_to_swu():
    with patch('sima_cli.update.local.get_local_board_info', return_value=('modalix', '3.0.0', '', False, 'ELXR')), \
            patch.object(swu, 'update_system') as install:
        assert swu.handle_update('3.0', internal=True, local_elxr=True)
        assert install.call_args.kwargs['ip'] is None


@pytest.mark.parametrize('outcome', ['committed', 'rollback', 'wrong_version'])
def test_reboot_checks_health_and_version_without_committing_it(outcome):
    state = parse_state(STATE.replace('active slot : A', 'active slot : B').replace('running slot: A', 'running slot: B'))
    state['active version'] = '3.0.0_new' if outcome != 'wrong_version' else '3.0.0_unexpected'
    state['rollback'] = 'rollback' if outcome == 'rollback' else 'normal'
    target, peer = MagicMock(), MagicMock()
    with patch.object(swu, 'Target', return_value=peer), patch.object(swu, 'inspect_target', return_value=state), \
            patch.object(swu.time, 'sleep'), patch.object(swu.time, 'monotonic', side_effect=[0, 1]):
        if outcome == 'committed':
            swu._reboot_and_verify(target, parse_state(STATE), '192.0.2.1', 'test', expected='3.0.0_new')
        else:
            with pytest.raises(click.ClickException):
                swu._reboot_and_verify(target, parse_state(STATE), '192.0.2.1', 'test', expected='3.0.0_new')
    assert 'reboot' in target.run.call_args.args[0]
    peer.run.assert_not_called()


FACTORY_STATE = STATE.replace(
    'control block: valid A, valid B, upgrade_available: no, next-boot: A',
    'control block       : blank/invalid -> factory (boots slot A)',
).replace('normal boot', 'boot mode: normal boot')


def test_factory_control_block_is_explicit_without_inventing_slot_validity():
    state = parse_state(FACTORY_STATE)
    assert state['factory'] is True
    assert state['next-boot'] == 'A (factory default)'
    assert state['validity A'] == state['validity B'] == 'not recorded'
    assert state['upgrade_available'] == 'unknown'
    assert parse_state('control block: invalid')['factory'] is False


def test_factory_preflight_allows_first_update_and_uses_actual_root_device():
    target = MagicMock()
    with patch.object(swu, 'inspect_target', return_value=parse_state(FACTORY_STATE)):
        state = swu.preflight(target, swu.DEFAULT_KEY)
    assert state['factory'] is True
    command = target.run.call_args.args[0]
    assert 'findmnt -n -o SOURCE /' in command
    assert '/sys/class/block/$dev/dm/name' in command
    assert 'test -b /dev/mapper/rootfs' not in command


def test_factory_mode_does_not_override_a_pending_flag():
    with patch.object(swu, 'inspect_target', return_value=parse_state(FACTORY_STATE + '\nupgrade_available: yes')):
        with pytest.raises(click.ClickException, match='pending'):
            swu.preflight(MagicMock(), swu.DEFAULT_KEY)


def test_artifactory_daily_prefix_is_not_part_of_on_device_build_identity():
    state = parse_state(STATE.replace('active slot : A', 'active slot : B').replace('running slot: A', 'running slot: B'))
    state['active version'] = '3.0.0_develop_B1211'
    target, peer = MagicMock(), MagicMock()
    with patch.object(swu, 'Target', return_value=peer), patch.object(swu, 'inspect_target', return_value=state), \
            patch.object(swu.time, 'sleep'), patch.object(swu.time, 'monotonic', side_effect=[0, 1]):
        swu._reboot_and_verify(target, parse_state(STATE), '192.0.2.1', 'test', expected='3.0.0_daily_develop_B1211')
