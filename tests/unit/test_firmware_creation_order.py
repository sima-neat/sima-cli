"""Creation ordering and picker labels for internal firmware archives."""
from unittest.mock import patch

from sima_cli.update.query import list_available_firmware_versions
from sima_cli.update.updater import _pick_from_available_versions


def test_archive_creation_order_normalizes_offsets_and_deduplicates():
    def archive(version, created):
        return {'path': f'elxr/bsp/modalix/{version}/artifacts/palette', 'created': created}

    with patch('sima_cli.update.query.requests.Session') as session, \
            patch('sima_cli.update.query.get_auth_token', return_value='test-token'):
        response = session.return_value.post.return_value
        response.status_code = 200
        response.json.return_value = {'results': [
            archive('3.0.0_B9', '2026-09-09T08:00:00Z'),
            archive('3.0.0_B10', '2026-09-09T02:00:00-07:00'),
            archive('3.0.0_B10', '2026-09-08T00:00:00Z'),
            archive('3.0.0_B11', None),
            archive('3.0.0_B12', 'invalid'),
            archive('3.0.0_B13', '2026-09-09T10:00:00'),
        ]}
        entries = list_available_firmware_versions(
            'modalix', '3.0', internal=True, swtype='elxr', with_metadata=True,
        )
        assert [entry['version'] for entry in entries] == [
            '3.0.0_B10', '3.0.0_B9', '3.0.0_B11', '3.0.0_B12', '3.0.0_B13',
        ]
        assert entries[0]['created'] == '2026-09-09 09:00:00 UTC'
        assert entries[1]['created'] == '2026-09-09 08:00:00 UTC'
        assert all(entry['created'] is None for entry in entries[2:])
        assert '"created"' in session.return_value.post.call_args.kwargs['data']
        # Default callers still receive plain version strings.
        assert list_available_firmware_versions(
            'modalix', '3.0', internal=True, swtype='elxr'
        ) == [entry['version'] for entry in entries]


def test_picker_displays_creation_and_returns_plain_version():
    entries = [
        {'version': '3.0.0_B10', 'created': '2026-09-09 09:00:00 UTC'},
        {'version': '3.0.0_B9', 'created': None},
    ]
    with patch('sima_cli.update.updater.list_available_firmware_versions', return_value=entries), \
            patch('InquirerPy.inquirer.fuzzy') as fuzzy:
        fuzzy.return_value.execute.return_value = '3.0.0_B10'
        assert _pick_from_available_versions('modalix', '3.0', True, 'headless', 'elxr') == '3.0.0_B10'
        assert fuzzy.call_args.kwargs['choices'] == [
            {'value': '3.0.0_B10', 'name': '3.0.0_B10  (created: 2026-09-09 09:00:00 UTC)'},
            {'value': '3.0.0_B9', 'name': '3.0.0_B9  (created: unknown)'},
        ]


def test_single_match_returns_version_without_prompt():
    with patch('sima_cli.update.updater.list_available_firmware_versions', return_value=[
        {'version': '3.0.0_B10', 'created': '2026-09-09 09:00:00 UTC'},
    ]), patch('InquirerPy.inquirer.fuzzy') as fuzzy:
        assert _pick_from_available_versions('modalix', 'B10', True, 'headless', 'elxr') == '3.0.0_B10'
        fuzzy.assert_not_called()


def test_public_picker_still_accepts_url_strings():
    with patch('sima_cli.update.updater.list_available_firmware_versions', return_value=['https://example.com/netboot.tar.gz']):
        assert _pick_from_available_versions('modalix', '3.0', False, 'headless', 'elxr') == 'https://example.com/netboot.tar.gz'
