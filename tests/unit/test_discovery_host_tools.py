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
