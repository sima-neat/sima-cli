"""Legacy APT updates and eLxr 3.0+ public release channels stay separate."""
import json
from pathlib import Path
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from sima_cli import cli
from sima_cli.update import elxr, local, swu, swu_artifacts, updater
from sima_cli.utils import tag


@pytest.fixture
def legacy_ota(monkeypatch):
    board_info = lambda: ('modalix', '2.1.3', 'modalix', False, 'elxr')
    for module in (cli, updater):
        monkeypatch.setattr(module, 'get_environment_type', lambda: ('board', 'elxr'))
        monkeypatch.setattr(module, 'is_devkit_running_elxr', lambda: True)
    monkeypatch.setattr(local, 'get_local_board_info', board_info)
    monkeypatch.setattr(updater, 'get_local_board_info', board_info)
    monkeypatch.setattr(cli, 'check_for_update', Mock(return_value=False))
    for name, value in {
        'is_devkit_running_elxr': True, '_ensure_elxr_repo_channel': True,
        '_resolve_simaai_ota': 'simaai-ota',
        '_get_available_palette_versions': ['2.1.3.1', '2.1.3'],
        '_get_installed_palette_version': '2.1.3', '_get_installed_elxr_distro_version': '2.1.3',
    }.items():
        monkeypatch.setattr(elxr, name, Mock(return_value=value))
    monkeypatch.setattr(elxr, 'print_current_versions', Mock())
    monkeypatch.setattr(elxr, '_prime_sudo_credentials', Mock())
    monkeypatch.setattr(elxr.subprocess, 'call', Mock(return_value=0))
    commands = Mock()
    monkeypatch.setattr(elxr.subprocess, 'check_call', commands)
    download = Mock(side_effect=AssertionError('eLxr 2.1.3 must not read release metadata'))
    monkeypatch.setattr(tag, 'download_file_from_url', download)
    return commands, download


@pytest.mark.parametrize('args,version', [(['update', '-y'], None),
    (['update', '-y', '-v', '2.1.3.1'], '2.1.3.1')])
def test_213_cli_uses_apt_without_release_metadata(legacy_ota, args, version):
    commands, download = legacy_ota
    result = CliRunner().invoke(cli.main, args, obj={})
    assert result.exit_code == 0, result.output
    expected = ['sudo', '-n', 'simaai-ota', '-f', '-o']
    if version:
        expected += ['-v', version]
    assert commands.call_args.args[0] == expected
    download.assert_not_called()


@pytest.mark.parametrize('selector', ['ga', 'beta', 'alpha', 'qa'])
def test_213_cli_rejects_channels_without_release_metadata(legacy_ota, selector):
    commands, download = legacy_ota
    result = CliRunner().invoke(cli.main, ['update', selector, '-y'], obj={})
    assert result.exit_code == 1, result.output
    assert 'Release-channel selectors are not supported' in result.output
    commands.assert_not_called()
    download.assert_not_called()


@pytest.mark.parametrize('selector,expected', [(None, '3.0.1'), ('GA', '3.0.1'),
                                            ('beta', '3.0.2'), ('alpha', '3.0.2')])
def test_swu_public_channels_use_release_metadata(monkeypatch, selector, expected):
    def download(url, dest_folder, internal=False):
        path = Path(dest_folder) / 'releases.json'
        path.write_text(json.dumps({'ga': '3.0.1', 'beta': '3.0.2', 'alpha': '3.0.2'}))
        return str(path)
    monkeypatch.setattr(tag, 'download_file_from_url', download)
    source = swu_artifacts.resolve_bundle(selector, 'modalix')
    assert source.version == expected
    assert '/SDK{}/'.format(expected) in source


def test_3_cli_uses_swu_dispatch(monkeypatch):
    monkeypatch.setattr(cli, 'check_for_update', Mock(return_value=False))
    monkeypatch.setattr(cli, 'get_environment_type', lambda: ('board', 'elxr'))
    monkeypatch.setattr(cli, 'is_devkit_running_elxr', lambda: True)
    monkeypatch.setattr(local, 'get_local_board_info', lambda: ('modalix', '3.0.0', 'modalix', False, 'elxr'))
    install = Mock()
    monkeypatch.setattr(swu, 'update_system', install)
    legacy = Mock(side_effect=AssertionError('3.0+ must not use legacy OTA'))
    monkeypatch.setattr(cli, 'perform_update', legacy)
    result = CliRunner().invoke(cli.main, ['update', '-y'], obj={})
    assert result.exit_code == 0, result.output
    install.assert_called_once()
    assert install.call_args.args[:2] == (None, 'modalix')
    legacy.assert_not_called()


@pytest.mark.parametrize('value', [None, {}, 123, ''])
def test_invalid_release_value_is_an_error(monkeypatch, value):
    def download(url, dest_folder, internal=False):
        path = Path(dest_folder) / 'releases.json'
        path.write_text(json.dumps({'ga': value}))
        return str(path)
    monkeypatch.setattr(tag, 'download_file_from_url', download)
    with pytest.raises(click.ClickException, match='no valid version'):
        swu_artifacts.resolve_bundle(None, 'modalix')


@pytest.mark.parametrize('failure,message', [
    ('metadata', 'Failed to resolve tag'),
    ('legacy_release', 'Full-system SWU releases require eLxr 3.0 or newer'),
])
def test_3_cli_reports_public_release_resolution_errors(monkeypatch, failure, message):
    monkeypatch.setattr(cli, 'check_for_update', Mock(return_value=False))
    monkeypatch.setattr(cli, 'get_environment_type', lambda: ('board', 'elxr'))
    monkeypatch.setattr(cli, 'is_devkit_running_elxr', lambda: True)
    monkeypatch.setattr(local, 'get_local_board_info', lambda: ('modalix', '3.0.0', 'modalix', False, 'elxr'))
    # Run real public resolution without executing any device installation.
    monkeypatch.setattr(swu, 'update_system', lambda requested, board, **kwargs:
                        swu_artifacts.resolve_bundle(requested, board))
    if failure == 'metadata':
        monkeypatch.setattr(tag, 'download_file_from_url', Mock(side_effect=OSError('offline')))
    else:
        monkeypatch.setattr(swu_artifacts, 'resolve_version', lambda requested: '2.1.3')
    result = CliRunner().invoke(cli.main, ['update', '-y'], obj={})
    assert result.exit_code == 1, result.output
    assert 'Error: ' + message in result.output
