import subprocess
from unittest.mock import Mock, patch

import pytest

from sima_cli.network import network as net


def process_tree(names):
    processes = [Mock() for _ in names]
    for process, name in zip(processes, names):
        process.name.return_value = name
    processes[0].parents.return_value = processes[1:]
    return processes[0]


@pytest.mark.parametrize('key', ['SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY'])
def test_detects_ssh_environment(key):
    with patch.dict(net.os.environ, {key: 'present'}, clear=True):
        assert net._is_ssh_session()


@pytest.mark.parametrize('names', [
    ['python3', 'sudo', 'bash', 'sshd'],
    ['python3', 'bash', 'sudo', 'bash', 'sshd-session'],
])
def test_sudo_ssh_detected_without_ssh_environment(names):
    with patch.dict(net.os.environ, {'SUDO_USER': 'sima'}, clear=True), \
         patch.object(net.psutil, 'Process', return_value=process_tree(names)):
        assert net._is_ssh_session()


@pytest.mark.parametrize('names', [['python3', 'bash', 'login', 'systemd'],
                                   ['python3', 'sudo', 'bash', 'login', 'systemd']])
def test_serial_with_or_without_sudo_is_allowed(names):
    with patch.dict(net.os.environ, {'SUDO_USER': 'sima'}, clear=True), \
         patch.object(net.psutil, 'Process', return_value=process_tree(names)):
        assert net._network_changes_allowed()


def test_unreadable_ancestry_fails_closed(capsys):
    with patch.dict(net.os.environ, {}, clear=True), \
         patch.object(net.psutil, 'Process', side_effect=net.psutil.AccessDenied(1)):
        assert not net._network_changes_allowed()
    assert 'serial console' in capsys.readouterr().out


@pytest.mark.parametrize('through_sudo', [False, True])
@pytest.mark.parametrize('action', ['custom', 'dhcp', 'static', 'route', 'menu'])
def test_ssh_rejected_before_any_profile_access_or_change(action, through_sudo, capsys):
    env = {'SUDO_USER': 'sima'} if through_sudo else {'SSH_CONNECTION': 'present'}
    with patch.dict(net.os.environ, env, clear=True), \
         patch.object(net.psutil, 'Process', return_value=process_tree(['python3', 'sudo', 'bash', 'sshd'])), \
         patch.object(net, 'is_sima_board', return_value=True), \
         patch.object(net, '_select_network_backend') as select, \
         patch.object(net.subprocess, 'run') as run, \
         patch.object(net.subprocess, 'check_output') as read:
        if action == 'custom':
            assert not net.apply_custom_static_ip('end0', '192.168.1.40')
        elif action in ('dhcp', 'static'):
            net.apply_network_mode('end0', action)
        elif action == 'route':
            net.set_default_route('end0', '192.168.1.40')
        else:
            net.network_menu()
    select.assert_not_called()
    run.assert_not_called()
    read.assert_not_called()
    assert 'Use the DevKit serial console' in capsys.readouterr().out


def test_serial_applies_two_custom_addresses_in_safe_order():
    names = iter([Mock(hex='first'), Mock(hex='second')])
    with patch.dict(net.os.environ, {}, clear=True), \
         patch.object(net.psutil, 'Process', return_value=process_tree(['python3', 'sudo', 'bash', 'login'])), \
         patch.object(net, '_select_network_backend', return_value='nm'), \
         patch.object(net, '_default_static_template', return_value={'prefix': 24, 'gateway': ''}), \
         patch.object(net, '_nm_custom_profiles', side_effect=[[], ['first-uuid']]), \
         patch.object(net.uuid, 'uuid4', side_effect=lambda: next(names)), \
         patch.object(net.subprocess, 'run') as run:
        assert net.apply_custom_static_ip('end0', '192.168.1.40')
        run.reset_mock()
        assert net.apply_custom_static_ip('end0', '192.168.1.41')
    commands = [call.args[0] for call in run.call_args_list]
    assert [command[3] for command in commands] == ['clone', 'modify', 'up', 'delete', 'modify']
    assert commands[1][commands[1].index('ipv4.addresses') + 1] == '192.168.1.41/24'
    assert commands[2][-1] == 'end0-sima-custom-static-next-second'
    assert commands[3][-2:] == ['uuid', 'first-uuid']


@pytest.mark.parametrize('failure_step', ['clone', 'modify', 'up', 'delete'])
def test_replacement_failures_never_remove_old_profile_before_activation(failure_step):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[3] == failure_step and kwargs.get('check'):
            raise subprocess.CalledProcessError(1, command)
        return Mock(returncode=0)
    with patch.object(net, '_is_ssh_session', return_value=False), \
         patch.object(net, '_select_network_backend', return_value='nm'), \
         patch.object(net, '_default_static_template', return_value={'prefix': 24, 'gateway': ''}), \
         patch.object(net, '_nm_custom_profiles', return_value=['old-uuid']), \
         patch.object(net.subprocess, 'run', side_effect=run):
        assert not net.apply_custom_static_ip('end0', '192.168.1.41')
    if failure_step != 'delete':
        assert not any('old-uuid' in command for command in commands)
    else:
        assert [command[3] for command in commands] == ['clone', 'modify', 'up', 'delete']


def test_profile_discovery_includes_stale_replacements_but_not_other_interfaces():
    listing = ('one:end0-sima-custom-static\ntwo:end0-sima-custom-static-next-abc\n'
               'three:end1-sima-custom-static\nfour:end0-static\nfive:end0-sima-custom-static-other\n')
    with patch.object(net.subprocess, 'check_output', return_value=listing):
        assert net._nm_custom_profiles('end0') == ['one', 'two']


@pytest.mark.parametrize('version,networkd,nm,owner,expected', [
    ('2.1.3', True, True, True, 'nm'),
    ('2.1.0', True, True, True, 'nm'),
    ('2.1.3', True, True, False, 'networkd'),
    ('2.1.3', False, True, True, 'nm'),
    ('2.1.3', False, False, True, 'nm'),
    ('2.0.0', True, False, None, 'networkd'),
    ('2.0.0', True, True, None, 'networkd'),
    ('2.1.3', False, True, None, 'nm'),
])
def test_backend_selection(version, networkd, nm, owner, expected):
    with patch.object(net, 'get_sima_board_type', return_value='modalix'), \
         patch.object(net, 'is_devkit_running_elxr', return_value=True), \
         patch.object(net, 'get_sima_build_version', return_value=(version, '')), \
         patch.object(net, '_is_service_enabled', side_effect=lambda name: networkd if name == 'systemd-networkd' else nm), \
         patch.object(net, '_nm_manages_interface', return_value=owner) as query:
        assert net._select_network_backend('end0') == expected
    query.assert_called_once_with('end0')


def test_ambiguous_modern_backend_refuses_to_guess():
    with patch.object(net, 'get_sima_board_type', return_value='modalix'), \
         patch.object(net, 'is_devkit_running_elxr', return_value=True), \
         patch.object(net, 'get_sima_build_version', return_value=('2.1.3', '')), \
         patch.object(net, '_is_service_enabled', return_value=True), \
         patch.object(net, '_nm_manages_interface', return_value=None):
        with pytest.raises(ValueError, match='Cannot determine network manager'):
            net._select_network_backend('end0')


@pytest.mark.parametrize('returncode,stdout,expected', [(0, 'yes\n', True), (0, 'no\n', False), (1, '', None)])
def test_owner_query(returncode, stdout, expected):
    with patch.object(net.subprocess, 'run', return_value=Mock(returncode=returncode, stdout=stdout)) as run:
        assert net._nm_manages_interface('end0') is expected
    assert run.call_args.args[0][-1] == 'end0'
