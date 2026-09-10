"""Recovery image selection, download errors, and removable-media handoff."""
from unittest.mock import patch

import pytest
import requests
from click.testing import CliRunner

from sima_cli.cli import bootimg_cmd
from sima_cli.update.query import _list_available_firmware_versions_external
from sima_cli.update.updater import _resolve_firmware_url

VERSION = '3.0.0_daily_develop_B1247'
IMAGE = 'elxr-recovery-palette-modalix-3.0.0-agate-arm64.img'
CONFIG = {
    'internal': {
        'download': {'download_url': 'artifactory'},
        'artifactory': {'url': 'https://artifacts.example.com'},
    },
    'public': {'download': {'download_url': 'https://docs.example.com/pkg_downloads/'}},
}


def test_recovery_resolves_selected_internal_build():
    with patch('sima_cli.update.updater.load_resource_config', return_value=CONFIG):
        url = _resolve_firmware_url(VERSION, 'modalix', internal=True,
                                    swtype='elxr', update_type='recovery')
    assert url == (
        f'https://artifacts.example.com/artifactory/soc-images/elxr/bsp/modalix/'
        f'{VERSION}/artifacts/palette/{IMAGE}'
    )


def test_public_recovery_normalizes_version():
    with patch('sima_cli.update.query.load_resource_config', return_value=CONFIG):
        urls = _list_available_firmware_versions_external(
            'modalix', '3.0', swtype='elxr', update_type='recovery')
    assert urls == [f'https://docs.example.com/pkg_downloads/SDK3.0.0/devkit/modalix/elxr/{IMAGE}']


def test_recovery_downloads_selected_image_and_writes_raw_media(tmp_path):
    image = tmp_path / IMAGE
    image.write_bytes(b'recovery disk image')
    with patch('sima_cli.update.updater.list_available_firmware_versions', return_value=[VERSION]) as versions, \
            patch('sima_cli.update.updater.load_resource_config', return_value=CONFIG), \
            patch('sima_cli.update.updater.download_file_from_url', return_value=str(image)) as download, \
            patch('sima_cli.update.bootimg.write_bootimg') as write:
        result = CliRunner().invoke(bootimg_cmd, ['-v', '3.0', '--recovery'], obj={'internal': True})
    assert result.exit_code == 0, result.output
    versions.assert_called_once_with('modalix', '3.0', True, 'headless', 'elxr', 'recovery', with_metadata=True)
    assert f'/{VERSION}/artifacts/palette/{IMAGE}' in download.call_args.args[0]
    assert download.call_args.kwargs['internal'] is True
    write.assert_called_once_with(str(image))
    assert image.read_bytes() == b'recovery disk image'
    assert 'automatically recovers eMMC' in result.output


@pytest.mark.parametrize('status', [404, 401, 403, 500])
def test_recovery_http_errors_stop_before_device_write(tmp_path, status):
    response = requests.Response()
    response.status_code = status
    error = requests.HTTPError(f'HTTP {status}', response=response)
    # Exercise the real downloader's wrapping of a HEAD failure.
    with patch('sima_cli.download.downloader.requests.Session') as session, \
            patch('sima_cli.update.updater.tempfile.gettempdir', return_value=str(tmp_path)), \
            patch('sima_cli.update.bootimg.write_bootimg') as write:
        session.return_value.head.return_value.raise_for_status.side_effect = error
        result = CliRunner().invoke(bootimg_cmd, [
            '-v', f'https://example.com/{VERSION}/{IMAGE}', '--recovery',
        ], obj={})
    assert result.exit_code == 1, result.output
    write.assert_not_called()
    assert ("doesn't contain a recovery image" in result.output) == (status == 404)
    if status != 404:
        assert f'HTTP {status}' in result.output


def test_recovery_network_error_is_not_reported_as_missing(tmp_path):
    with patch('sima_cli.update.updater.download_file_from_url', side_effect=requests.Timeout('timed out')), \
            patch('sima_cli.update.bootimg.write_bootimg') as write:
        result = CliRunner().invoke(bootimg_cmd, [
            '-v', f'https://example.com/{IMAGE}', '--recovery',
        ], obj={})
    assert result.exit_code == 1
    assert 'timed out' in result.output
    assert "doesn't contain" not in result.output
    write.assert_not_called()


@pytest.mark.parametrize('extra', [
    ['--netboot'], ['--autoflash'], ['--rootfs', '/tmp/rootfs'],
    ['--devkit-ip', '192.0.2.1'], ['--fwtype', 'yocto'], ['--boardtype', 'mlsoc'],
])
def test_recovery_rejects_incompatible_options(extra):
    with patch('sima_cli.update.bootimg.write_image') as write, \
            patch('sima_cli.update.netboot.setup_netboot') as netboot:
        result = CliRunner().invoke(bootimg_cmd, ['-v', '3.0', '--recovery'] + extra, obj={})
    assert result.exit_code == 2
    write.assert_not_called()
    netboot.assert_not_called()


def test_regular_bootimg_keeps_defaults():
    with patch('sima_cli.update.bootimg.write_image') as write:
        result = CliRunner().invoke(bootimg_cmd, ['-v', '2.1.2'], obj={})
    assert result.exit_code == 0
    write.assert_called_once_with('2.1.2', 'davinci', 'yocto', False, flavor='headless', recovery=False)


def test_local_recovery_image_is_written_without_download(tmp_path):
    image = tmp_path / IMAGE
    image.write_bytes(b'local recovery image')
    with patch('sima_cli.update.updater.download_file_from_url') as download, \
            patch('sima_cli.update.bootimg.write_bootimg') as write:
        result = CliRunner().invoke(bootimg_cmd, ['-v', str(image), '--recovery',
            '--boardtype', 'modalix', '--fwtype', 'elxr'], obj={})
    assert result.exit_code == 0, result.output
    download.assert_not_called()
    write.assert_called_once_with(str(image))
