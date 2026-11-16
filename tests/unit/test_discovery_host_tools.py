"""Discovery on minimal Linux hosts must not fail with missing executables."""
import subprocess
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from sima_cli.discover import linuxll, discover
from sima_cli.update.netboot_device import resolve_device
from sima_cli.cli import bootimg_cmd


def tools(monkeypatch, available):
    monkeypatch.setattr(linuxll.shutil, 'which', lambda name: '/usr/bin/' + name if name in available else None)


def test_existing_ip_needs_no_install_or_prompt(monkeypatch):
    tools(monkeypatch, {'ip'})
    with patch.object(linuxll.subprocess, 'run') as run, patch.object(click, 'confirm') as confirm:
        linuxll.ensure_discovery_tools()
    run.assert_not_called()
    confirm.assert_not_called()


@pytest.mark.parametrize('root', [True, False])
def test_ubuntu_installs_missing_packages_after_confirmation(monkeypatch, root):
    available = {'apt-get', 'sudo'}
    tools(monkeypatch, available)
    monkeypatch.setattr(linuxll.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(linuxll.os, 'geteuid', lambda: 0 if root else 1000)
    def installed(args, **kwargs):
        if 'install' in args:
            available.update({'ip', 'ethtool'})
    with patch.object(click, 'confirm', return_value=True) as confirm, \
            patch.object(linuxll.subprocess, 'run', side_effect=installed) as run:
        linuxll.ensure_discovery_tools()
    prefix = [] if root else ['sudo']
    assert run.call_args_list[0].args[0] == prefix + ['apt-get', 'update']
    assert run.call_args_list[1].args[0] == prefix + ['apt-get', 'install', '-y', '--no-install-recommends', 'iproute2', 'ethtool']
    confirm.assert_called_once()


@pytest.mark.parametrize('interactive,apt,accept', [(False, True, True), (True, False, True), (True, True, False)])
def test_missing_required_tool_reports_packages_without_unapproved_install(monkeypatch, interactive, apt, accept):
    tools(monkeypatch, {'apt-get'} if apt else set())
    monkeypatch.setattr(linuxll.sys.stdin, 'isatty', lambda: interactive)
    with patch.object(click, 'confirm', return_value=accept), patch.object(linuxll.subprocess, 'run') as run:
        with pytest.raises(click.ClickException, match='iproute2') as error:
            linuxll.ensure_discovery_tools()
    assert '--devkit-ip' in str(error.value)
    run.assert_not_called()


def test_install_failure_has_discovery_context(monkeypatch):
    tools(monkeypatch, {'apt-get'})
    monkeypatch.setattr(linuxll.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(linuxll.os, 'geteuid', lambda: 0)
    with patch.object(click, 'confirm', return_value=True), \
            patch.object(linuxll.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['apt-get'])):
        with pytest.raises(click.ClickException, match='discovery package installation failed'):
            linuxll.ensure_discovery_tools()


def test_optional_tools_do_not_crash_interface_description(monkeypatch):
    tools(monkeypatch, {'ip'})
    with patch.object(linuxll, '_get_mac', return_value='aa:bb:cc:dd:ee:ff'), \
            patch.object(linuxll, '_run') as run:
        assert linuxll._describe_iface('eth0') == 'iface=eth0, mac=aa:bb:cc:dd:ee:ff'
        assert not linuxll._is_usb_ethernet('eth0')
        assert not linuxll._has_nmcli()
    run.assert_not_called()


def test_preflight_precedes_interface_scan(monkeypatch):
    with patch.object(linuxll, 'get_environment_type', return_value=('host', 'linux')), \
            patch.object(linuxll, 'ensure_discovery_tools', side_effect=click.ClickException('missing iproute2')), \
            patch.object(linuxll, '_run') as run:
        with pytest.raises(click.ClickException, match='iproute2'):
            linuxll.suggest_and_switch_to_linklocal()
    run.assert_not_called()


def test_arp_fallback_absent_allows_multicast(monkeypatch):
    tools(monkeypatch, set())
    with patch.object(discover.platform, 'system', return_value='Linux'), \
            patch.object(discover.subprocess, 'check_output', side_effect=FileNotFoundError('ip')) as run:
        assert discover.get_sima_devices_from_arp() == []
    run.assert_called_once_with(['ip', 'neigh', 'show'], text=True)


def test_explicit_devkit_bypasses_discovery_tools():
    with patch('sima_cli.sdk.commands.discover_and_probe') as scan:
        assert resolve_device('192.0.2.2') == '192.0.2.2'
    scan.assert_not_called()


def test_bootimg_reports_discovery_error_without_image_failure(tmp_path):
    with patch('sima_cli.update.netboot.setup_netboot', side_effect=click.ClickException('Device discovery needs ip (package iproute2)')):
        result = CliRunner().invoke(bootimg_cmd, ['--netboot', '--images', str(tmp_path)], obj={})
    assert result.exit_code == 1
    assert 'Device discovery needs ip' in result.output
    assert 'Failed to write boot image' not in result.output


# `ip -o link` output captured from a container on Docker's default bridge (eth0 holds
# 172.17.0.2), plus a VLAN link and a plain NIC, the shapes reported in issue #352.
IP_O_LINK = (
    "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT\\    link/loopback 00:00:00:00:00:00\n"
    "2: eth0@if58: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT\\    link/ether e2:b7:ba:2a:04:61\n"
    "3: eth1.100@eth1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT\\    link/ether 02:00:00:00:00:01\n"
    "4: enx5444a326738b: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode DEFAULT\\    link/ether 54:44:a3:26:73:8b\n"
)


def test_ip_link_names_drop_peer_suffix():
    assert linuxll._parse_ip_link(IP_O_LINK) == [
        ('lo', 'LOOPBACK,UP,LOWER_UP'),
        ('eth0', 'BROADCAST,MULTICAST,UP,LOWER_UP'),
        ('eth1.100', 'BROADCAST,MULTICAST,UP,LOWER_UP'),
        ('enx5444a326738b', 'BROADCAST,MULTICAST,UP,LOWER_UP'),
    ]


def _scan(ipv4_ifaces):
    """Run the link-local scan over IP_O_LINK; return (ifaces probed for IPv4, prompts shown)."""
    probed, prompts = [], []

    def has_ipv4(iface):
        probed.append(iface)
        return iface in ipv4_ifaces

    def answer(prompt=''):
        prompts.append(prompt)
        return 'n'

    with patch.object(linuxll, 'get_environment_type', return_value=('host', 'linux')), \
            patch.object(linuxll, 'ensure_discovery_tools'), \
            patch.object(linuxll, '_run', return_value=subprocess.CompletedProcess([], 0, IP_O_LINK, '')), \
            patch.object(linuxll, '_iface_has_ipv4', side_effect=has_ipv4), \
            patch.object(linuxll, '_describe_iface', side_effect=lambda iface: 'iface=' + iface), \
            patch.object(linuxll, '_is_usb_ethernet', return_value=False), \
            patch('builtins.input', side_effect=answer):
        linuxll.suggest_and_switch_to_linklocal()
    return probed, prompts


def test_configured_stacked_interface_is_not_offered_link_local(capsys):
    probed, prompts = _scan({'eth0', 'eth1.100', 'enx5444a326738b'})
    assert probed == ['eth0', 'eth1.100', 'enx5444a326738b']
    assert prompts == []
    assert 'No unconfigured network interfaces detected' in capsys.readouterr().out


def test_unconfigured_interface_is_still_offered_link_local(capsys):
    probed, prompts = _scan({'eth0', 'eth1.100'})
    assert probed == ['eth0', 'eth1.100', 'enx5444a326738b']
    assert len(prompts) == 1
    out = capsys.readouterr().out
    assert 'iface=enx5444a326738b' in out
    assert '@' not in out
