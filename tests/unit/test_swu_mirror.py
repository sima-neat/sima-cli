"""Daily SWU discovery, fallback, integrity, and legacy isolation."""
import copy
import hashlib
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.update import swu, swu_artifacts as artifacts

VERSION = '3.0.0_daily_develop_B1247'
SWU = 'artifacts/palette/elxr-palette-modalix-3.0.0-agate-arm64.swu'
CONTENT = b'signed swu fixture'


def build(version=VERSION, number=1247):
    return {'name': version, 'build_number': number, 'files': [{
        'path': SWU, 'key': f'daily-platform-images/{version}/{SWU}',
        'size': len(CONTENT), 'sha256': hashlib.sha256(CONTENT).hexdigest(),
    }]}


def index(*builds):
    return {'schema_version': 1, 'platform': 'modalix', 'builds': list(builds)}


def mock_index(session, data):
    client = session.return_value.__enter__.return_value
    client.get.return_value.json.return_value = data
    return client


@pytest.mark.parametrize('query,expected', [
    (VERSION, [VERSION]), ('3.0', [VERSION, '3.0.0_daily_develop_B9']),
    ('develop', [VERSION, '3.0.0_daily_develop_B9']), ('B1247', [VERSION]),
    ('absent', []),
])
def test_index_filters_and_sorts_by_build_number(query, expected):
    with patch.object(artifacts.requests, 'Session') as session:
        client = mock_index(session, index(build('3.0.0_daily_develop_B9', 9), build(), build('13.0.0_other_B1', 1)))
        rows = artifacts.mirror_bundles('modalix', query)
    assert [row['version'] for row in rows] == expected
    assert client.trust_env is False
    client.get.assert_called_once_with(artifacts.DAILY_MIRROR + 'index.json', timeout=30)
    if rows:
        assert rows[0]['url'].sha256 == hashlib.sha256(CONTENT).hexdigest()


def test_exact_build_wins_over_longer_partial_match():
    with patch.object(artifacts.requests, 'Session') as session, patch('InquirerPy.inquirer.fuzzy') as menu:
        mock_index(session, index(build(), build(VERSION + '_extra', 1248)))
        source = artifacts.select_mirror_bundle(VERSION, 'modalix', 'Offline.')
    assert source.version == VERSION
    menu.assert_not_called()


def test_partial_query_menu_has_build_order_without_time():
    with patch.object(artifacts.requests, 'Session') as session, patch('InquirerPy.inquirer.fuzzy') as menu:
        mock_index(session, index(build('3.0.0_daily_B9', 9), build()))
        menu.return_value.execute.return_value = VERSION
        source = artifacts.select_mirror_bundle('3.0', 'modalix', 'Offline.')
    assert source == artifacts.DAILY_MIRROR + VERSION + '/' + SWU
    choices = menu.call_args.kwargs['choices']
    assert choices[0]['value'] == VERSION
    assert choices[0]['name'].index('Build') == choices[1]['name'].index('Build')
    assert all('Created' not in c['name'] for c in choices)


@pytest.mark.parametrize('status', [401, 403, 500, 503])
def test_artifactory_http_failures_fall_back(status):
    response = requests.Response()
    response.status_code = status
    with patch.object(artifacts, 'internal_bundles', side_effect=requests.HTTPError(response=response)), \
            patch.object(artifacts, 'select_mirror_bundle', return_value='mirror') as mirror:
        assert artifacts.resolve_bundle('3.0', 'modalix', True) == 'mirror'
    assert str(status) in mirror.call_args.args[2]


@pytest.mark.parametrize('error', [requests.Timeout(), requests.ConnectionError(), artifacts.ArtifactoryUnavailable('No login.')])
def test_artifactory_access_failures_fall_back(error):
    with patch.object(artifacts, 'internal_bundles', side_effect=error), \
            patch.object(artifacts, 'select_mirror_bundle', return_value='mirror'):
        assert artifacts.resolve_bundle('3.0', 'modalix', True) == 'mirror'


@pytest.mark.parametrize('status', [400, 404])
def test_non_access_http_errors_do_not_fall_back(status):
    response = requests.Response()
    response.status_code = status
    with patch.object(artifacts, 'internal_bundles', side_effect=requests.HTTPError(response=response)), \
            patch.object(artifacts, 'select_mirror_bundle') as mirror:
        with pytest.raises(requests.HTTPError):
            artifacts.resolve_bundle('3.0', 'modalix', True)
    mirror.assert_not_called()


def test_no_artifactory_match_does_not_fall_back():
    with patch.object(artifacts, 'internal_bundles', return_value=[]), \
            patch.object(artifacts, 'select_mirror_bundle') as mirror:
        with pytest.raises(click.ClickException, match='No matching'):
            artifacts.resolve_bundle('3.0', 'modalix', True)
    mirror.assert_not_called()


@pytest.mark.parametrize('bad', ['key', 'sha256', 'size', 'schema', 'platform', 'duplicate'])
def test_invalid_manifest_fails_clearly(bad):
    data = index(build())
    if bad in ('key', 'sha256', 'size'):
        data['builds'][0]['files'][0][bad] = '../escape' if bad != 'size' else -1
    elif bad == 'schema':
        data['schema_version'] = 99
    elif bad == 'platform':
        data['platform'] = 'davinci'
    else:
        data['builds'].append(copy.deepcopy(data['builds'][0]))
    with patch.object(artifacts.requests, 'Session') as session:
        mock_index(session, data)
        with pytest.raises(click.ClickException, match='No firmware was installed'):
            artifacts.mirror_bundles('modalix', '3.0')


def test_missing_swu_has_clear_error():
    data = build()
    data['files'] = []
    with patch.object(artifacts.requests, 'Session') as session:
        mock_index(session, index(data))
        with pytest.raises(click.ClickException, match='may not contain a palette SWU'):
            artifacts.select_mirror_bundle(VERSION, 'modalix', 'Offline.')


@pytest.mark.parametrize('failure_stage', ['none', 'size', 'download'])
@pytest.mark.parametrize('corrupt', [False, 'size', 'checksum'])
def test_mirror_install_verifies_integrity_and_preserves_version(tmp_path, failure_stage, corrupt):
    local = tmp_path / 'bundle.swu'
    local.write_bytes(b'bad' if corrupt == 'size' else b'x' * len(CONTENT) if corrupt else CONTENT)
    mirror = artifacts.BundleSource(artifacts.DAILY_MIRROR + VERSION + '/' + SWU,
                                    VERSION, len(CONTENT), hashlib.sha256(CONTENT).hexdigest())
    internal = artifacts.BundleSource(artifacts.ARTIFACTORY_BASE_URL + '/bsp/' + VERSION + '/bundle.swu', VERSION)
    target = MagicMock()
    target.run.return_value = '/tmp/sima-cli-update.ABC12345'
    with patch.object(swu, 'Target', return_value=target), \
            patch.object(swu, 'prepare_key', return_value=(swu.DEFAULT_KEY, None)), \
            patch.object(swu, 'preflight', return_value={'running slot': 'A'}), \
            patch.object(swu, 'resolve_bundle', return_value=mirror if failure_stage == 'none' else internal), \
            patch.object(swu, 'bundle_size', side_effect=requests.Timeout() if failure_stage == 'size' else None, return_value=len(CONTENT)), \
            patch.object(swu, '_select_staging_root', return_value='/tmp'), \
            patch.object(swu, '_check_space'), \
            patch.object(swu, 'inspect_target', return_value={'next-boot': 'B', 'upgrade_available': 'yes'}), \
            patch.object(swu, '_reboot_and_verify') as reboot, \
            patch.object(artifacts, 'select_mirror_bundle', return_value=mirror) as fallback, \
            patch.object(swu, 'download_file_from_url', side_effect=[requests.Timeout(), str(local)] if failure_stage == 'download' else None, return_value=str(local)) as download:
        if corrupt:
            with pytest.raises(click.ClickException, match='failed size or SHA-256 verification'):
                swu.update_system('3.0', 'modalix', ip='192.0.2.1', internal=True, auto_confirm=True, reboot=True)
            target.transfer.assert_not_called()
            reboot.assert_not_called()
        else:
            swu.update_system('3.0', 'modalix', ip='192.0.2.1', internal=True, auto_confirm=True, reboot=True)
            assert reboot.call_args.kwargs['expected'] == VERSION
            assert any('-k ' in c.args[0] and '-e update,full' in c.args[0] for c in target.run.call_args_list)
        assert download.call_args.kwargs['internal'] is False
        if failure_stage != 'none':
            assert fallback.call_args.args[0] == VERSION
            assert fallback.call_args.kwargs['exact'] is True


@pytest.mark.parametrize('running,requested,handled', [('3.0.0', '3.0', True), ('2.1.3', '2.1.3', False), ('2.1.3', '3.0', None)])
def test_offline_cli_defers_access_until_device_dispatch(running, requested, handled):
    with patch('sima_cli.cli.check_for_update', return_value=False), \
            patch('sima_cli.cli.internal_resource_exists', return_value=True), \
            patch('sima_cli.cli.check_artifactory_reachability', return_value=False), \
            patch('sima_cli.cli.is_devkit_running_elxr', return_value=False), \
            patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', running, '', False, 'elxr')), \
            patch.object(swu, 'update_system') as install, \
            patch('sima_cli.cli.perform_update') as legacy:
        result = CliRunner().invoke(main, ['-i', 'update', '--ip', '192.0.2.1', '-v', requested])
    assert 'Use at your own risk' in result.output
    if handled:
        assert result.exit_code == 0, result.output
        install.assert_called_once()
    else:
        assert result.exit_code == 1
        install.assert_not_called()
        assert ('does not support' if handled is None else 'Artifactory is unreachable') in result.output
    legacy.assert_not_called()


@pytest.mark.parametrize('args,env', [(['-i'], {}), (['--internal'], {}), ([], {'SIMA_CLI_INTERNAL': '1'})])
def test_internal_inspection_warning_is_informational(args, env):
    with patch('sima_cli.cli.handle_update', return_value=True), patch('sima_cli.cli.check_for_update') as update:
        result = CliRunner().invoke(main, args + ['update', '--inspect'], env=env)
    assert result.exit_code == 0
    assert 'Pre-release software may be unstable' in result.stderr
    assert 'Use at your own risk' in result.stderr
    update.assert_not_called()


def test_public_mirror_download_does_not_send_credentials(tmp_path):
    from sima_cli.download.downloader import download_file_from_url
    url = artifacts.DAILY_MIRROR + VERSION + '/' + SWU
    with patch('sima_cli.download.downloader.get_auth_token') as token, \
            patch('sima_cli.download.downloader.requests.Session') as session:
        client = session.return_value
        client.head.return_value.headers = {'content-length': str(len(CONTENT))}
        response = client.get.return_value.__enter__.return_value
        response.headers = {'content-length': str(len(CONTENT))}
        response.iter_content.return_value = [CONTENT]
        path = download_file_from_url(url, str(tmp_path), internal=False)
    assert client.trust_env is False
    assert client.head.call_args.kwargs['headers'] == {}
    assert client.get.call_args.kwargs['headers'] == {}
    token.assert_not_called()
    from pathlib import Path
    assert Path(path).read_bytes() == CONTENT


def test_successful_artifactory_keeps_source_and_exact_build_identity():
    url = artifacts.ARTIFACTORY_BASE_URL + '/bundle.swu'
    with patch.object(artifacts, 'internal_bundles', return_value=[
        {'version': VERSION, 'url': url, 'created': None},
        {'version': VERSION + '_extra', 'url': url + '?extra', 'created': None},
    ]), patch.object(artifacts, 'select_mirror_bundle') as mirror:
        source = artifacts.resolve_bundle(VERSION, 'modalix', internal=True)
    assert source == url
    assert source.version == VERSION
    mirror.assert_not_called()


def test_local_bundle_needs_no_index_or_artifactory(tmp_path):
    local = tmp_path / 'local.swu'
    local.write_bytes(CONTENT)
    with patch.object(artifacts, 'internal_bundles') as internal, \
            patch.object(artifacts, 'mirror_bundles') as mirror:
        assert artifacts.resolve_bundle(str(local), 'modalix', True) == str(local)
    internal.assert_not_called()
    mirror.assert_not_called()


def test_mirror_selection_cancelled():
    with patch.object(artifacts.requests, 'Session') as session, patch('InquirerPy.inquirer.fuzzy') as menu:
        mock_index(session, index(build(), build('3.0.0_daily_B9', 9)))
        menu.return_value.execute.return_value = None
        assert artifacts.select_mirror_bundle('3.0', 'modalix', 'Offline.') is None


def test_exact_retry_never_substitutes_another_build():
    with patch.object(artifacts.requests, 'Session') as session:
        mock_index(session, index(build(VERSION + '_other', 1248)))
        with pytest.raises(click.ClickException, match='No matching palette SWU'):
            artifacts.select_mirror_bundle(VERSION, 'modalix', 'Offline.', exact=True)


@pytest.mark.parametrize('error', [requests.Timeout(), requests.ConnectionError(), ValueError('invalid JSON')])
def test_unavailable_mirror_index_reports_failure_without_installation(error):
    with patch.object(artifacts.requests, 'Session') as session:
        session.return_value.__enter__.return_value.get.side_effect = error
        with pytest.raises(click.ClickException, match='Unable to read the daily platform build index.*No firmware was installed'):
            artifacts.select_mirror_bundle('3.0', 'modalix', 'Artifactory unavailable.')


@pytest.mark.parametrize('stage', ['size', 'download'])
@pytest.mark.parametrize('kind', ['404', '429', 'protocol'])
def test_post_selection_request_failures_retry_exact_build(stage, kind, tmp_path):
    local = tmp_path / 'bundle.swu'
    local.write_bytes(CONTENT)
    if kind == 'protocol':
        error = requests.exceptions.ChunkedEncodingError('broken response')
    else:
        response = requests.Response()
        response.status_code = int(kind)
        error = requests.HTTPError(response=response)
    wrapped = RuntimeError('Download failed')
    wrapped.__cause__ = error
    internal = artifacts.BundleSource(artifacts.ARTIFACTORY_BASE_URL + '/bundle.swu', VERSION)
    mirror = artifacts.BundleSource(artifacts.DAILY_MIRROR + VERSION + '/' + SWU,
                                    VERSION, len(CONTENT), hashlib.sha256(CONTENT).hexdigest())
    target = MagicMock()
    target.run.return_value = '/tmp/sima-cli-update.ABC12345'
    with patch.object(swu, 'Target', return_value=target), \
            patch.object(swu, 'prepare_key', return_value=(swu.DEFAULT_KEY, None)), \
            patch.object(swu, 'preflight', return_value={'running slot': 'A'}), \
            patch.object(swu, 'resolve_bundle', return_value=internal), \
            patch.object(swu, 'bundle_size', side_effect=error if stage == 'size' else None, return_value=len(CONTENT)), \
            patch.object(swu, '_select_staging_root', return_value='/tmp'), \
            patch.object(swu, '_check_space'), \
            patch.object(swu, 'inspect_target', return_value={'next-boot': 'B', 'upgrade_available': 'yes'}), \
            patch.object(artifacts, 'select_mirror_bundle', return_value=mirror) as fallback, \
            patch.object(swu, 'download_file_from_url', side_effect=[wrapped, str(local)] if stage == 'download' else None, return_value=str(local)) as download:
        swu.update_system('3.0', 'modalix', ip='192.0.2.1', internal=True, auto_confirm=True)
    assert fallback.call_args.args[0] == VERSION
    assert fallback.call_args.kwargs['exact'] is True
    assert download.call_args.args[0] == mirror
    assert download.call_args.kwargs['internal'] is False
    target.transfer.assert_called_once()


def test_discovery_protocol_error_does_not_fall_back():
    with patch.object(artifacts, 'internal_bundles', side_effect=requests.exceptions.ChunkedEncodingError()), \
            patch.object(artifacts, 'select_mirror_bundle') as mirror:
        with pytest.raises(requests.exceptions.ChunkedEncodingError):
            artifacts.resolve_bundle('3.0', 'modalix', True)
    mirror.assert_not_called()


def test_post_selection_local_io_error_does_not_fall_back():
    source = artifacts.BundleSource(artifacts.ARTIFACTORY_BASE_URL + '/bundle.swu', VERSION)
    with patch.object(artifacts, 'select_mirror_bundle') as mirror:
        assert artifacts.fallback_for_bundle(source, 'modalix', OSError('disk full')) is None
    mirror.assert_not_called()
