import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sima_cli.network import network as net


@pytest.fixture(autouse=True)
def local_console():
    with patch.object(net, "_is_ssh_session", return_value=False), \
         patch.object(net.uuid, "uuid4", return_value=Mock(hex="test")):
        yield


@pytest.mark.parametrize("value,expected", [
    ("192.168.1.50", "192.168.1.50/24"),
    (" 10.20.30.40/16 ", "10.20.30.40/16"),
    ("10.0.0.0/31", "10.0.0.0/31"),
    ("10.0.0.1/32", "10.0.0.1/32"),
])
def test_parse_static_address(value, expected):
    assert net.parse_static_address(value) == expected


@pytest.mark.parametrize("value", [
    "", "garbage", "256.1.1.1", "::1", "10.0.0.1/33", "0.0.0.0",
    "127.0.0.1", "224.0.0.1", "255.255.255.255", "192.168.1.0/24",
    "192.168.1.255/24", "10.0.0.1; reboot",
])
def test_invalid_address_cannot_change_network(value):
    with patch.object(net, "_select_network_backend", return_value="nm"), \
         patch.object(net, "_default_static_template", return_value={"prefix": 24, "gateway": ""}), \
         patch.object(net.subprocess, "run") as run:
        assert not net.apply_custom_static_ip("end0", value)
        run.assert_not_called()


@pytest.mark.parametrize("exists", [False, True])
def test_nm_clones_default_without_overriding_boot_configuration(exists):
    with patch.object(net, "_select_network_backend", return_value="nm"), \
         patch.object(net, "_default_static_template", return_value={"prefix": 16, "gateway": "10.2.0.1"}), \
         patch.object(net, "_nm_custom_profiles", return_value=["old-uuid"] if exists else []), \
         patch.object(net.subprocess, "run") as run:
        assert net.apply_custom_static_ip("end0", "10.2.3.4")
    commands = [call.args[0] for call in run.call_args_list]
    replacement = "end0-sima-custom-static-next-test"
    assert commands[0] == ["sudo", "nmcli", "connection", "clone", "--temporary", "end0-static", replacement]
    command = commands[1]
    assert command[:5] == ["sudo", "nmcli", "connection", "modify", "--temporary"]
    assert command[command.index("ipv4.addresses") + 1] == "10.2.3.4/16"
    assert command[command.index("ipv4.gateway") + 1] == "10.2.0.1"
    assert command[command.index("connection.autoconnect") + 1] == "no"
    assert command[command.index("connection.autoconnect-priority") + 1] == "0"
    assert "ipv4.dns" not in command
    assert "ipv4.routes" not in command
    assert "ipv4.never-default" not in command
    assert commands[2] == ["sudo", "nmcli", "connection", "up", replacement]
    if exists:
        assert commands[3] == ["sudo", "nmcli", "connection", "delete", "uuid", "old-uuid"]
    assert commands[-1][-2:] == ["connection.id", "end0-sima-custom-static"]


@pytest.mark.parametrize("old_profile", [False, True])
def test_networkd_writes_runtime_profile_and_cleans_temporary_file(tmp_path, old_profile):
    factory = tmp_path / "02-end0-static.network"
    factory.write_text("[Network]\nAddress=192.168.1.20/24\n")
    old_custom = tmp_path / "01-end0-sima-custom-static.network"
    if old_profile:
        old_custom.write_text("[Network]\nAddress=10.2.3.5/24\n")
    captured = {}

    def capture(command, **kwargs):
        if command[1] == "install":
            captured["temp"] = command[5]
            captured["content"] = Path(command[5]).read_text()
            assert command[6] == str(tmp_path / "run" / "01-end0-sima-custom-static.network")

    with patch.object(net, "NETWORKD_DIR", str(tmp_path)), \
         patch.object(net, "NETWORKD_RUNTIME_DIR", str(tmp_path / "run")), \
         patch.object(net, "_select_network_backend", return_value="networkd"), \
         patch.object(net.subprocess, "run", side_effect=capture) as run:
        assert net.apply_custom_static_ip("end0", "10.2.3.4")
    assert captured["content"] == "[Network]\nAddress=10.2.3.4/24\n"
    assert not Path(captured["temp"]).exists()
    assert factory.read_text() == "[Network]\nAddress=192.168.1.20/24\n"
    commands = [call.args[0] for call in run.call_args_list]
    if old_profile:
        assert commands[-2] == ["sudo", "rm", "--", str(old_custom)]
    else:
        assert not any(command[1] == "rm" for command in commands)
    assert run.call_args_list[-1].args[0] == ["sudo", "systemctl", "restart", "systemd-networkd"]


@pytest.mark.parametrize("mode", ["static", "dhcp"])
def test_networkd_switch_back_removes_custom_override(tmp_path, mode):
    (tmp_path / "02-end0-static.network").write_text("[Network]\nAddress=192.168.1.20/24\n")
    custom = tmp_path / "01-end0-sima-custom-static.network"
    custom.write_text("[Network]\nAddress=10.2.3.4/24\n")
    runtime = tmp_path / "run" / custom.name
    runtime.parent.mkdir()
    runtime.write_text(custom.read_text())
    with patch.object(net, "NETWORKD_DIR", str(tmp_path)), \
         patch.object(net, "NETWORKD_RUNTIME_DIR", str(tmp_path / "run")), \
         patch.object(net.subprocess, "run") as run, patch.object(net.time, "sleep"):
        net.move_network_file("end0", mode)
    commands = [call.args[0] for call in run.call_args_list]
    assert ["sudo", "rm", "--", str(custom)] in commands
    assert ["sudo", "rm", "--", str(runtime)] in commands
    assert commands[-1] == ["sudo", "systemctl", "restart", "systemd-networkd"]


@pytest.mark.parametrize("mode", ["static", "dhcp"])
def test_nm_switch_back_removes_custom_profile(mode):
    with patch.object(net, "_nm_custom_profiles", return_value=["old-uuid"]), \
         patch.object(net.subprocess, "run") as run:
        assert net._nm_connection_up("end0", mode)
    assert run.call_args_list[0].args[0][-1] == f"end0-{mode}"
    assert run.call_args_list[1].args[0][-3:] == ["delete", "uuid", "old-uuid"]


@pytest.mark.parametrize("backend", ["nm", "networkd"])
def test_apply_failure_is_reported(backend, capsys):
    with patch.object(net, "_select_network_backend", return_value=backend), \
         patch.object(net, "_nm_custom_profiles", return_value=["old-uuid"]), \
         patch.object(net, "_default_static_template", return_value={"prefix": 24, "gateway": "", "content": "[Network]\nAddress=192.168.1.20/24\n"}), \
         patch.object(net.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "configure")):
        assert not net.apply_custom_static_ip("end0", "10.2.3.4")
    assert "Unable to configure" in capsys.readouterr().out


def test_prompt_retries_invalid_input():
    with patch("InquirerPy.inquirer.text") as prompt, \
         patch.object(net, "apply_custom_static_ip") as apply:
        prompt.return_value.execute.side_effect = ["not an ip", "10.2.3.4"]
        net.prompt_custom_static_ip("end0")
    apply.assert_called_once_with("end0", "10.2.3.4")


@pytest.mark.parametrize("value", ["", "   ", None, KeyboardInterrupt(), EOFError()])
def test_prompt_cancel_does_not_apply(value):
    with patch("InquirerPy.inquirer.text") as prompt, \
         patch.object(net, "apply_custom_static_ip") as apply:
        if isinstance(value, BaseException):
            prompt.return_value.execute.side_effect = value
        else:
            prompt.return_value.execute.return_value = value
        net.prompt_custom_static_ip("end0")
    apply.assert_not_called()


def test_menu_routes_custom_choice_to_selected_interface():
    with patch.object(net, "is_sima_board", return_value=True), \
         patch.object(net, "get_interfaces", return_value=[
             {"name": "end0", "carrier": True, "ip": "192.168.1.20"}
         ]), patch("InquirerPy.inquirer.fuzzy") as fuzzy, \
         patch("InquirerPy.inquirer.select") as select, \
         patch.object(net, "prompt_custom_static_ip") as prompt:
        def choose(**kwargs):
            return Mock(execute=Mock(return_value=kwargs["choices"][1] if fuzzy.call_count == 1 else "🚪 Quit Menu"))
        fuzzy.side_effect = choose
        select.return_value.execute.return_value = "Set to Custom Static IP"
        net.network_menu()
    assert "Set to Custom Static IP" in select.call_args.kwargs["choices"]
    prompt.assert_called_once_with("end0")


@pytest.mark.parametrize('gateway', ['', '192.168.0.1'])
def test_compatible_gateway_does_not_prompt(gateway):
    with patch('InquirerPy.inquirer.text') as prompt:
        assert net._gateway_for_address(gateway, '192.168.0.20/24') == gateway
    prompt.assert_not_called()


@pytest.mark.parametrize('replacement', ['', '192.168.0.1'])
def test_incompatible_gateway_prompts_and_validates(replacement):
    with patch('InquirerPy.inquirer.text') as prompt:
        prompt.return_value.execute.side_effect = ['invalid', '192.168.1.1', '192.168.0.20', replacement]
        assert net._gateway_for_address('192.168.1.1', '192.168.0.20/24') == replacement
    assert prompt.call_count == 4


def test_cancel_gateway_does_not_change_network():
    with patch.object(net, '_select_network_backend', return_value='nm'), \
         patch.object(net, '_default_static_template', return_value={'prefix': 24, 'gateway': '192.168.1.1'}), \
         patch('InquirerPy.inquirer.text') as prompt, patch.object(net.subprocess, 'run') as run:
        prompt.return_value.execute.side_effect = KeyboardInterrupt
        assert not net.apply_custom_static_ip('end0', '192.168.0.20')
    run.assert_not_called()


def test_nm_reads_default_prefix_and_gateway():
    with patch.object(net.subprocess, 'check_output', side_effect=['192.168.0.20/22\n', '192.168.0.1\n']) as read:
        assert net._default_static_template('end0', 'nm') == {'prefix': 22, 'gateway': '192.168.0.1'}
    assert all(call.args[0][-1] == 'end0-static' for call in read.call_args_list)


def test_networkd_clone_preserves_dns_ipv6_and_other_settings():
    source = ('[Match]\nName=end0\nKernelCommandLine=!netcfg=dhcp\n\n'
              '[Link]\nMTUBytes=9000\n\n[Network]\nDHCP=no\n'
              'Address=192.168.0.20/22\nAddress=2001:db8::1/64\n'
              'DNS=1.1.1.1\nDNS=8.8.8.8\nGateway=192.168.0.1\n\n'
              '[Route]\nDestination=10.0.0.0/8\nGateway=192.168.0.1\n')
    result = net._custom_networkd_content(source, '192.168.0.30/22')
    assert result == source.replace('Address=192.168.0.20/22', 'Address=192.168.0.30/22').replace('KernelCommandLine=!netcfg=dhcp\n', '')


def test_networkd_omits_route_when_incompatible_gateway_is_omitted():
    source = '[Network]\nDNS=1.1.1.1\nAddress=192.168.1.20/24\n[Route]\nGateway=192.168.1.1\nDestination=0.0.0.0/0\n'
    with patch('InquirerPy.inquirer.text') as prompt:
        prompt.return_value.execute.return_value = ''
        result = net._custom_networkd_content(source, '192.168.0.20/24')
    assert '[Route]' not in result
    assert 'DNS=1.1.1.1' in result
    assert 'Address=192.168.0.20/24' in result


def test_networkd_replaces_address_sections_without_duplicate_ipv4():
    source = '[Match]\nName=end0\n[Address]\nAddress=192.168.1.20/24\n[Address]\nAddress=192.168.1.21/24\n[Address]\nAddress=2001:db8::1/64\n'
    result = net._custom_networkd_content(source, '192.168.0.20/24')
    assert '192.168.1.' not in result
    assert result.count('Address=192.168.0.20/24') == 1
    assert 'Address=2001:db8::1/64' in result


def test_missing_default_profile_does_not_create_empty_custom_profile():
    with patch.object(net, '_select_network_backend', return_value='nm'), \
         patch.object(net.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'nmcli')), \
         patch.object(net.subprocess, 'run') as run:
        assert not net.apply_custom_static_ip('end0', '192.168.0.20')
    run.assert_not_called()


def test_failed_clone_configuration_is_cleaned_up():
    with patch.object(net, '_select_network_backend', return_value='nm'), \
         patch.object(net, '_default_static_template', return_value={'prefix': 24, 'gateway': ''}), \
         patch.object(net, '_nm_custom_profiles', return_value=[]), \
         patch.object(net.subprocess, 'run', side_effect=[Mock(), subprocess.CalledProcessError(1, 'modify'), Mock()]) as run:
        assert not net.apply_custom_static_ip('end0', '192.168.0.20')
    assert run.call_args_list[-1].args[0] == ['sudo', 'nmcli', 'connection', 'delete', 'end0-sima-custom-static-next-test']
    assert not any('up' in call.args[0] for call in run.call_args_list)
