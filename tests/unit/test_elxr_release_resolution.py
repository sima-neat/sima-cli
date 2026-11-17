"""Release metadata, rather than APT ordering, defines the public GA target."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import click
import pytest

from sima_cli.update import elxr
from sima_cli.utils import tag


@pytest.fixture
def ota(monkeypatch):
    for name, value in {
        'is_devkit_running_elxr': True,
        '_ensure_elxr_repo_channel': True,
        '_resolve_simaai_ota': 'simaai-ota',
        '_get_available_palette_versions': ['3.0.2', '3.0.1', '3.0.0'],
        '_get_installed_palette_version': '3.0.0',
        '_get_installed_elxr_distro_version': '3.0.0',
    }.items():
        monkeypatch.setattr(elxr, name, Mock(return_value=value))
    monkeypatch.setattr(elxr, 'print_current_versions', Mock())
    monkeypatch.setattr(elxr, '_prime_sudo_credentials', Mock())
    monkeypatch.setattr(elxr.subprocess, 'call', Mock(return_value=0))
    commands = Mock()
    monkeypatch.setattr(elxr.subprocess, 'check_call', commands)

    def download(url, dest_folder, internal=False):
        assert url == 'https://docs.sima.ai/pkg_downloads/releases.json'
        assert internal is False
        path = Path(dest_folder) / 'releases.json'
        path.write_text(json.dumps({'ga': '3.0.1', 'beta': '3.0.2'}))
        return str(path)

    monkeypatch.setattr(tag, 'download_file_from_url', download)
    return commands


def test_public_latest_pins_ga_even_when_apt_advertises_newer(ota):
    elxr.update_elxr(None, auto_confirm=True)
    assert ota.call_args.args[0] == ['sudo', '-n', 'simaai-ota', '-f', '-o', '-v', '3.0.1']


def test_interactive_latest_pins_ga(ota, monkeypatch, capsys):
    import sys
    inquirer = SimpleNamespace(select=lambda **kwargs: SimpleNamespace(execute=lambda: 'latest'))
    monkeypatch.setitem(sys.modules, 'InquirerPy', SimpleNamespace(inquirer=inquirer))
    elxr.update_elxr(None, dryrun=True)
    assert ota.call_count == 1  # APT refresh only; no OTA in dry run.
    assert 'Would run: sudo simaai-ota -f -o -v 3.0.1' in capsys.readouterr().out


@pytest.mark.parametrize('selector,version', [('GA', '3.0.1'), ('beta', '3.0.2')])
def test_explicit_tag_resolves_release(ota, selector, version):
    elxr.update_elxr(selector, auto_confirm=True)
    assert ota.call_args.args[0][-2:] == ['-v', version]


@pytest.mark.parametrize('options', [{'internal': True}, {'force_external_fallback': True}])
def test_prerelease_latest_does_not_pin_public_ga(ota, options):
    elxr.update_elxr(None, auto_confirm=True, **options)
    assert ota.call_args.args[0] == ['sudo', '-n', 'simaai-ota', '-f', '-o']


def test_missing_ga_aborts_without_ota(ota, monkeypatch):
    monkeypatch.setattr(elxr, '_get_available_palette_versions', lambda: ['3.0.2'])
    with pytest.raises(click.ClickException, match='GA release 3.0.1 is not available'):
        elxr.update_elxr(None, auto_confirm=True)
    assert ota.call_count == 1


def test_metadata_failure_aborts_without_ota(ota, monkeypatch):
    monkeypatch.setattr(tag, 'download_file_from_url', Mock(side_effect=OSError('offline')))
    with pytest.raises(click.ClickException, match='Failed to resolve tag'):
        elxr.update_elxr(None, auto_confirm=True)
    assert ota.call_count == 1


@pytest.mark.parametrize('value', [None, {}, 123, ''])
def test_invalid_release_value_is_an_error(ota, monkeypatch, value):
    def download(url, dest_folder, internal=False):
        path = Path(dest_folder) / 'releases.json'
        path.write_text(json.dumps({'ga': value}))
        return str(path)
    monkeypatch.setattr(tag, 'download_file_from_url', download)
    with pytest.raises(click.ClickException, match='no valid version'):
        tag.resolve_version(None)
