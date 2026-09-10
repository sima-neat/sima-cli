"""Daily netboot fallback preserves build identity and verifies every file."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import click
import pytest
import requests
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.update import netboot_artifacts as net, swu_artifacts as artifacts, updater

VERSION = '3.0.0_daily_develop_B1247'
FILES = ['artifacts/minimal/modalix-tftp-boot-minimal.tar.gz',
         'artifacts/palette/elxr-palette-modalix-3.0.0-agate-arm64.img.gz',
         'artifacts/minimal/troot_blob.be']
DATA = b'netboot fixture bytes'


def manifest(version=VERSION, number=1247, paths=FILES):
    return {'name': version, 'build_number': number, 'files': [
        {'path': path, 'key': f'daily-platform-images/{version}/{path}',
         'size': len(DATA), 'sha256': hashlib.sha256(DATA).hexdigest()} for path in paths]}


def sources():
    return [artifacts.BundleSource(artifacts.DAILY_MIRROR + VERSION + '/' + path,
                                  VERSION, len(DATA), hashlib.sha256(DATA).hexdigest()) for path in FILES]


@pytest.mark.parametrize('query', ['3.0', '1247', VERSION])
def test_index_returns_complete_same_build_set(query):
    with patch.object(artifacts.requests, 'Session') as session:
        client = session.return_value.__enter__.return_value
        client.get.return_value.json.return_value = {
            'schema_version': 1, 'platform': 'modalix', 'builds': [manifest()]}
        builds = artifacts.mirror_bundles('modalix', query, netboot=True)
    assert list(builds[0]['artifacts']) == list(sources())
    assert all(source.version == VERSION for source in builds[0]['artifacts'])
    assert client.trust_env is False


@pytest.mark.parametrize('missing', FILES)
def test_missing_required_file_reports_error(missing):
    with patch.object(artifacts.requests, 'Session') as session:
        session.return_value.__enter__.return_value.get.return_value.json.return_value = {
            'schema_version': 1, 'platform': 'modalix',
            'builds': [manifest(paths=[p for p in FILES if p != missing])]}
        with pytest.raises(click.ClickException, match='no complete netboot files'):
            net._mirror_files(VERSION, 'modalix', 'Offline.')


@pytest.mark.parametrize('error', [requests.ConnectionError(), requests.Timeout(),
                                  artifacts.ArtifactoryUnavailable('Login required.')])
def test_discovery_failure_falls_back(error):
    with patch.object(net, '_list_available_firmware_versions_internal', side_effect=error), \
            patch.object(net, '_mirror_files', return_value=sources()) as mirror, \
            patch.object(net, '_download_set', return_value=['ready']) as download:
        assert net.download_netboot_image('1247', 'modalix', allow_daily_fallback=True) == ['ready']
    assert mirror.call_args.args[:2] == ('1247', 'modalix')
    assert mirror.call_args.kwargs['exact'] is False
    assert download.call_args.kwargs['mirror'] is True


def test_artifact_failure_retries_exact_selected_build():
    response = requests.Response()
    response.status_code = 404
    with patch.object(net, '_list_available_firmware_versions_internal', return_value=[{'version': VERSION}]), \
            patch.object(net, 'resolve_elxr_palette_image', return_value='internal.img.gz'), \
            patch.object(net, '_download_set', side_effect=[requests.HTTPError(response=response), ['ready']]), \
            patch.object(net, '_mirror_files', return_value=sources()) as mirror:
        assert net.download_netboot_image('3.0', 'modalix', allow_daily_fallback=True) == ['ready']
    assert mirror.call_args.args[0] == VERSION
    assert mirror.call_args.kwargs['exact'] is True


@pytest.mark.parametrize('bad_index', [None, 0, 1, 2])
def test_all_downloads_verified_before_extraction(tmp_path, bad_index):
    downloaded = []

    def download(url, directory, internal):
        assert internal is False
        path = Path(directory) / url.rsplit('/', 1)[-1]
        path.write_bytes(b'corrupt' if len(downloaded) == bad_index else DATA)
        downloaded.append(str(path))
        return str(path)

    def extract(*args):
        assert len(downloaded) == 3
        return [str(tmp_path / 'Image'), str(tmp_path / 'troot_blob.be')]

    staging = tmp_path / 'staging'
    staging.mkdir()
    with patch.object(net.tempfile, 'mkdtemp', return_value=str(staging)), \
            patch.object(net, 'download_file_from_url', side_effect=download), \
            patch.object(updater, '_extract_required_files', side_effect=extract) as unpack:
        if bad_index is None:
            result = net._download_set(sources(), 'modalix', 'headless', mirror=True)
            assert result == [str(tmp_path / 'Image')] + downloaded[1:]
        else:
            with pytest.raises(click.ClickException, match='SHA-256 verification. TFTP was not started'):
                net._download_set(sources(), 'modalix', 'headless', mirror=True)
            unpack.assert_not_called()
            assert not staging.exists()


def test_local_io_error_does_not_trigger_mirror():
    with patch.object(net, '_list_available_firmware_versions_internal', side_effect=OSError('Disk full')), \
            patch.object(net, '_mirror_files') as mirror:
        with pytest.raises(OSError):
            net.download_netboot_image('3.0', 'modalix')
    mirror.assert_not_called()


@pytest.mark.parametrize('flag', ['--netboot', '-n', '--autoflash', '-a'])
def test_offline_cli_reaches_netboot_dispatch(flag):
    with patch('sima_cli.cli.check_for_update', return_value=False), \
            patch('sima_cli.cli.internal_resource_exists', return_value=True), \
            patch('sima_cli.cli.check_artifactory_reachability', return_value=False), \
            patch('sima_cli.update.netboot.setup_netboot') as setup:
        result = CliRunner().invoke(main, ['-i', 'bootimg', '-v', '1247', '--boardtype',
                                          'modalix', '--fwtype', 'elxr', flag])
    assert result.exit_code == 0, result.output
    setup.assert_called_once()
    assert 'Use at your own risk' in result.output


@pytest.mark.parametrize('value', ['https://example.org/boot.tar.gz', __file__])
def test_explicit_source_keeps_existing_download_path(value):
    with patch.object(updater, '_download_image', return_value=['local']) as download, \
            patch.object(net, 'download_netboot_image') as mirror:
        assert updater.download_image(value, 'modalix', 'elxr', True, 'netboot') == ['local']
    download.assert_called_once()
    mirror.assert_not_called()


def test_legacy_elxr_uses_existing_downloader():
    with patch.object(net, '_list_available_firmware_versions_internal', return_value=[{'version': '2.1.3'}]), \
            patch.object(updater, '_download_image', return_value=['legacy']) as download, \
            patch.object(net, '_mirror_files') as mirror:
        assert net.download_netboot_image('2.1.3', 'modalix') == ['legacy']
    download.assert_called_once_with('2.1.3', 'modalix', True, 'netboot', 'headless', 'elxr')
    mirror.assert_not_called()


def test_failure_stops_before_tftp_start():
    from sima_cli.update import netboot
    with patch.object(netboot, 'get_environment_type', return_value=('host', 'mac')), \
            patch.object(netboot, 'download_image', side_effect=click.ClickException('Mirror verification failed')), \
            patch.object(netboot, 'InteractiveTftpServer') as server:
        with pytest.raises(RuntimeError, match='Mirror verification failed'):
            netboot.setup_netboot('1247', 'modalix', True, False, swtype='elxr')
    server.assert_not_called()


def test_empty_archive_is_failure_and_cleans_staging(tmp_path):
    with patch.object(net.tempfile, 'mkdtemp', return_value=str(tmp_path)), \
            patch.object(net, 'download_file_from_url', return_value='downloaded'), \
            patch.object(updater, '_extract_required_files', side_effect=SystemExit(0)):
        with pytest.raises(click.ClickException, match='contains no usable files'):
            net._download_set(['archive', 'image', 'troot'], 'modalix', 'headless', mirror=False)
    assert not tmp_path.exists()


@pytest.mark.parametrize('selected', [False, True])
def test_no_force_never_contacts_daily_mirror(selected):
    discovery = {'return_value': [{'version': VERSION}]} if selected else {'side_effect': requests.ConnectionError()}
    with patch.object(net, '_list_available_firmware_versions_internal', **discovery), \
            patch.object(net, 'resolve_elxr_palette_image', side_effect=requests.ConnectionError()), \
            patch.object(net, '_mirror_files') as mirror:
        with pytest.raises(click.ClickException, match='Retry with -f/--force'):
            net.download_netboot_image('1247', 'modalix')
    mirror.assert_not_called()


@pytest.mark.parametrize('force_args,expected', [([], False), (['-f'], True), (['--force'], True)])
def test_cli_passes_explicit_fallback_policy(force_args, expected):
    with patch('sima_cli.cli.check_for_update', return_value=False), \
            patch('sima_cli.cli.internal_resource_exists', return_value=True), \
            patch('sima_cli.cli.check_artifactory_reachability', return_value=False), \
            patch('sima_cli.update.netboot.setup_netboot') as setup:
        result = CliRunner().invoke(main, ['-i', 'bootimg', '-v', '1247', '--boardtype',
                                          'modalix', '--fwtype', 'elxr', '-n'] + force_args)
    assert result.exit_code == 0, result.output
    assert setup.call_args.kwargs['allow_daily_fallback'] is expected
