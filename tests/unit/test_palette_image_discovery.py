from unittest.mock import patch

import pytest

from sima_cli.update.query import ARTIFACTORY_BASE_URL, resolve_elxr_palette_image
from sima_cli.update.updater import _download_image, _resolve_firmware_url

ROOT = ARTIFACTORY_BASE_URL + '/soc-images/elxr/bsp/modalix/3.0.0_daily_develop_B1211/artifacts/'
IMAGE = 'elxr-palette-modalix-3.0.0-agate-arm64.img.gz'


@pytest.mark.parametrize('name', [IMAGE, 'elxr-palette-modalix-2.1.3-arm64.img.gz'])
def test_resolves_actual_image_name(name):
    with patch('sima_cli.update.query.requests.Session') as session, \
            patch('sima_cli.update.query.get_auth_token', return_value='test-token'):
        response = session.return_value.get.return_value
        response.json.return_value = {'children': [
            {'uri': '/' + name, 'folder': False},
            {'uri': '/elxr-recovery-palette-modalix-3.0.0-agate-arm64.img.gz', 'folder': False},
            {'uri': '/elxr-palette-modalix-3.0.0-arm64.swu', 'folder': False},
        ]}
        assert resolve_elxr_palette_image(ROOT + 'palette/', 'modalix') == ROOT + 'palette/' + name
        response.raise_for_status.assert_called_once()


@pytest.mark.parametrize('names', [[], [IMAGE, 'elxr-palette-modalix-3.0.0-other-arm64.img.gz']])
def test_missing_or_ambiguous_image_is_an_error(names):
    with patch('sima_cli.update.query.requests.Session') as session, \
            patch('sima_cli.update.query.get_auth_token', return_value='test-token'):
        session.return_value.get.return_value.json.return_value = {
            'children': [{'uri': '/' + name, 'folder': False} for name in names],
        }
        with pytest.raises(RuntimeError, match='Expected one palette disk image'):
            resolve_elxr_palette_image(ROOT + 'palette/', 'modalix')


def test_unrelated_host_is_rejected_before_authentication():
    with patch('sima_cli.update.query.get_auth_token') as token:
        with pytest.raises(ValueError):
            resolve_elxr_palette_image('https://example.com/palette/', 'modalix')
        token.assert_not_called()


@pytest.mark.parametrize('direct_url', [False, True])
def test_netboot_extra_image_uses_discovered_name(tmp_path, direct_url):
    archive_url = ROOT + 'minimal/modalix-tftp-boot-minimal.tar.gz'
    image_url = ROOT + 'palette/' + IMAGE
    archive = str(tmp_path / 'netboot.tar.gz')
    disk = str(tmp_path / IMAGE)
    with patch('sima_cli.update.updater._resolve_firmware_url', return_value=archive_url), \
            patch('sima_cli.update.updater.resolve_elxr_palette_image', return_value=image_url) as resolve, \
            patch('sima_cli.update.updater.download_file_from_url', side_effect=[archive, disk]) as download, \
            patch('sima_cli.update.updater._extract_required_files', return_value=['Image']):
        files = _download_image(archive_url if direct_url else '3.0.0_daily_develop_B1211',
                                'modalix', internal=True, swtype='elxr', update_type='netboot')
        assert files == ['Image', disk]
        resolve.assert_called_once_with(ROOT + 'palette/', 'modalix')
        assert download.call_args_list[1].args[0] == image_url


def test_bootimg_uses_discovered_name():
    with patch('sima_cli.update.updater.resolve_elxr_palette_image', return_value=ROOT + 'palette/' + IMAGE) as resolve:
        url = _resolve_firmware_url('3.0.0_daily_develop_B1211', 'modalix', internal=True,
                                    swtype='elxr', update_type='bootimg')
        assert url == ROOT + 'palette/' + IMAGE
        resolve.assert_called_once_with(ROOT + 'palette/', 'modalix')


def test_failed_extra_download_does_not_return_incomplete_netboot_files(tmp_path, capsys):
    with patch('sima_cli.update.updater.resolve_elxr_palette_image', return_value=ROOT + 'palette/' + IMAGE), \
            patch('sima_cli.update.updater.download_file_from_url',
                  side_effect=[str(tmp_path / 'netboot.tar.gz'), RuntimeError('404')]), \
            patch('sima_cli.update.updater._extract_required_files', return_value=['Image']):
        with pytest.raises(SystemExit) as error:
            _download_image(ROOT + 'minimal/modalix-tftp-boot-minimal.tar.gz', 'modalix',
                            internal=True, swtype='elxr', update_type='netboot')
        assert error.value.code == 1
        assert 'Failed to download required eMMC image' in capsys.readouterr().out
