"""Regression coverage for the eLxr 3.0 Artifactory BSP layout."""
import json
from unittest.mock import patch

import pytest

from sima_cli.update.query import list_available_firmware_versions
from sima_cli.update.updater import _resolve_firmware_url


@pytest.mark.parametrize('version,root', [
    ('2.1.3_pre-release_master_B1423', 'elxr/modalix'),
    ('2.10.0', 'elxr/modalix'),
    ('3.0', 'elxr/bsp/modalix'),
    ('3.0.0_daily_develop_B1168', 'elxr/bsp/modalix'),
    ('3.1.0', 'elxr/bsp/modalix'),
    ('10.0.0', 'elxr/bsp/modalix'),
])
@pytest.mark.parametrize('operation', ['netboot', 'bootimg'])
def test_internal_url_uses_version_layout(version, root, operation):
    config = {'internal': {
        'download': {'download_url': 'artifactory'},
        'artifactory': {'url': 'https://artifacts.example.com'},
    }}
    with patch('sima_cli.update.updater.load_resource_config', return_value=config):
        url = _resolve_firmware_url(version, 'modalix', internal=True,
                                    swtype='elxr', update_type=operation)
    assert url.startswith(f'https://artifacts.example.com/artifactory/soc-images/{root}/{version}/artifacts/')
    if operation == 'netboot':
        assert url.endswith('/minimal/modalix-tftp-boot-minimal.tar.gz')


@pytest.mark.parametrize('keyword,expected', [
    ('3.0', ['3.0.0_daily_develop_B1168']),
    ('2.1', ['2.1.3_daily_B100']),
    ('daily', ['2.1.3_daily_B100', '3.0.0_daily_develop_B1168']),
    (None, ['2.1.3_daily_B100', '3.0.0_daily_develop_B1168']),
])
def test_discovery_extracts_versions_from_both_layouts(keyword, expected):
    with patch('sima_cli.update.query.requests.Session') as session, \
            patch('sima_cli.update.query.get_auth_token', return_value='test-token'):
        response = session.return_value.post.return_value
        response.status_code = 200
        response.json.return_value = {'results': [
            {'path': 'elxr/modalix/2.1.3_daily_B100/artifacts/palette'},
            {'path': 'elxr/bsp/modalix/3.0.0_daily_develop_B1168/artifacts/palette'},
            {'path': 'elxr/bsp/modalix/3.0.0_daily_develop_B1168/artifacts/palette'},
            # Builds in a layout inconsistent with their version cannot resolve.
            {'path': 'elxr/modalix/3.0.1_wrong_layout/artifacts/palette'},
        ]}
        assert list_available_firmware_versions(
            'modalix', keyword, internal=True, swtype='elxr', update_type='netboot'
        ) == expected
        query = session.return_value.post.call_args.kwargs['data']
        criteria = json.loads(query[len('items.find('):query.index(').include')])
        assert criteria['$and'][0]['$or'] == [
            {'path': {'$match': 'elxr/modalix/*/artifacts/palette'}},
            {'path': {'$match': 'elxr/bsp/modalix/*/artifacts/palette'}},
        ]


def test_direct_url_is_unchanged():
    url = 'https://example.com/custom/netboot.tar.gz'
    assert _resolve_firmware_url(url, 'modalix', internal=True, swtype='elxr') == url
