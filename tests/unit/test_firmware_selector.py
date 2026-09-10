"""Shared firmware selector preserves version values and aligns build times."""
from unittest.mock import patch

import pytest

from sima_cli.update.query import list_available_firmware_versions
from sima_cli.update.updater import _pick_from_available_versions


@pytest.mark.parametrize('operation', ['recovery', 'netboot', 'bootimg'])
def test_selector_aligns_columns_and_returns_unformatted_version(operation, capsys):
    versions = [
        {'version': '3.0.0_daily_develop_B1247', 'created': '2026-09-09 12:30:00 UTC'},
        {'version': '3.0.0_' + 'long_branch_' * 5 + 'B1248', 'created': 'Unknown'},
    ]
    with patch('sima_cli.update.updater.list_available_firmware_versions', return_value=versions) as query, \
            patch('InquirerPy.inquirer.fuzzy') as fuzzy:
        fuzzy.return_value.execute.return_value = versions[1]['version']
        selected = _pick_from_available_versions('modalix', '3.0', True, 'headless', 'elxr', operation)
    assert selected == versions[1]['version']
    query.assert_called_once_with('modalix', '3.0', True, 'headless', 'elxr', operation, with_metadata=True)
    choices = fuzzy.call_args.kwargs['choices']
    assert [c['value'] for c in choices] == [v['version'] for v in versions]
    assert choices[0]['name'].index('2026-09-09') == choices[1]['name'].index('Unknown')
    assert len(choices[0]['name']) == len(choices[1]['name'])
    assert 'Build time' in capsys.readouterr().out


def test_single_version_is_returned_without_menu():
    with patch('sima_cli.update.updater.list_available_firmware_versions', return_value=[
        {'version': '3.0.0_daily_develop_B1247', 'created': 'Unknown'},
    ]), patch('InquirerPy.inquirer.fuzzy') as fuzzy:
        assert _pick_from_available_versions('modalix', '3.0', True, 'headless', 'elxr') == '3.0.0_daily_develop_B1247'
    fuzzy.assert_not_called()




@pytest.mark.parametrize('operation', ['recovery', 'netboot', 'bootimg'])
def test_selector_shows_newest_build_first_with_unknown_times_last(operation):
    timestamps = {
        '3.0.0_B99': None,
        '3.0.0_B10': '2026-09-09T23:00:00Z',
        '3.0.0_B2': '2026-09-10T01:00:00+02:00',
        '3.0.0_B1': '2026-09-09T18:00:00-07:00',
        '3.0.0_B100': 'invalid',
    }
    with patch('sima_cli.update.query.requests.Session') as session, \
            patch('sima_cli.update.query.get_auth_token', return_value='test'), \
            patch('InquirerPy.inquirer.fuzzy') as fuzzy:
        response = session.return_value.post.return_value
        response.status_code = 200
        response.json.return_value = {'results': [
            {'path': f'elxr/bsp/modalix/{version}/artifacts/palette', 'created': created}
            for version, created in timestamps.items()
        ]}
        fuzzy.return_value.execute.return_value = '3.0.0_B1'
        assert _pick_from_available_versions('modalix', '3.0', True, 'headless', 'elxr', operation) == '3.0.0_B1'
        choices = fuzzy.call_args.kwargs['choices']
        assert [choice['value'] for choice in choices] == [
            '3.0.0_B1', '3.0.0_B10', '3.0.0_B2', '3.0.0_B100', '3.0.0_B99',
        ]
        assert '2026-09-10 01:00:00 UTC' in choices[0]['name']
        assert 'Unknown' in choices[-1]['name']
