"""Custom and downloaded SWUpdate certificate selection and failure handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from sima_cli.cli import main
from sima_cli.update import swu, swu_certificate as cert


@pytest.fixture(scope='module')
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'User-signed image')])
    now = datetime.now(timezone.utc)
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))


def response_for(data):
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = [data]
    return response


@pytest.mark.parametrize('source', [cert.INTERNAL_SIGNING_CERT_URL, 'https://updates.example/cert.pem', 'http://updates.example/cert.pem'])
def test_default_and_custom_url(source, pem):
    with patch.object(cert.requests, 'get', return_value=response_for(pem)) as get:
        text, before, after = cert.load_certificate(source)
    assert text.encode() == pem
    assert before < datetime.now(timezone.utc).timestamp() < after
    get.assert_called_once_with(source, stream=True, timeout=(10, 30))


def test_local_file_is_read_without_network(tmp_path, pem):
    path = tmp_path / 'my certificate.pem'
    path.write_bytes(pem)
    with patch.object(cert.requests, 'get') as get:
        assert cert.load_certificate(str(path))[0].encode() == pem
    get.assert_not_called()
    assert path.read_bytes() == pem


@pytest.mark.parametrize('data', [b'', b'<html>not found</html>', b'-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----',
    b'-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----', b'x' * (cert.MAX_CERTIFICATE_BYTES + 1)])
def test_invalid_response_rejected(data):
    with patch.object(cert.requests, 'get', return_value=response_for(data)):
        with pytest.raises(click.ClickException, match='Cannot load SWUpdate signing certificate'):
            cert.load_certificate(cert.INTERNAL_SIGNING_CERT_URL)


def test_extra_certificates_or_private_keys_rejected(pem):
    for extra in (pem, b'-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----', b'junk'):
        with patch.object(cert.requests, 'get', return_value=response_for(pem + extra)):
            with pytest.raises(click.ClickException):
                cert.load_certificate(cert.INTERNAL_SIGNING_CERT_URL)


@pytest.mark.parametrize('error', [requests.Timeout('timeout'), requests.HTTPError('403 Forbidden')])
def test_failed_request_does_not_fall_back(error):
    with patch.object(cert.requests, 'get', side_effect=error) as get:
        with pytest.raises(click.ClickException, match='--signing-cert'):
            cert.load_certificate(cert.INTERNAL_SIGNING_CERT_URL)
    assert get.call_count == 1


def test_missing_local_certificate(tmp_path):
    with pytest.raises(click.ClickException, match='--signing-cert'):
        cert.load_certificate(str(tmp_path / 'missing.pem'))


@pytest.mark.parametrize('clock', ['99', '201', 'unknown'])
def test_invalid_device_clock_blocks_staging(clock):
    target = MagicMock()
    target.run.return_value = clock
    with patch.object(swu, 'load_certificate', return_value=('public cert', 100, 200)):
        with pytest.raises(click.ClickException, match='clock|system time'):
            swu.prepare_key(target, internal=True)
    target.run.assert_called_once_with('date -u +%s')


@pytest.mark.parametrize('source', ['./custom.pem', 'https://updates.example/cert.pem'])
def test_cli_passes_custom_certificate(source):
    with patch('sima_cli.cli.check_for_update', return_value=False), patch('sima_cli.cli.handle_update', return_value=True) as handle:
        result = CliRunner().invoke(main, ['update', '--ip', '192.0.2.1', '--signing-cert', source])
    assert result.exit_code == 0, result.output
    assert handle.call_args.kwargs['signing_cert'] == source
    assert 'key' not in handle.call_args.kwargs


def test_inspection_rejects_certificate_option():
    args = ['--inspect']
    with patch('sima_cli.cli.check_for_update', return_value=False), patch('sima_cli.cli.handle_update') as handle:
        result = CliRunner().invoke(main, ['update', '--signing-cert', './cert.pem'] + args)
    assert result.exit_code != 0
    handle.assert_not_called()


def test_legacy_board_rejects_certificate_option():
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', '2.1.3', '', False, 'elxr')):
        with pytest.raises(click.ClickException, match='requires eLxr 3.0'):
            swu.handle_update(None, ip='192.0.2.1', signing_cert='custom.pem')


@pytest.mark.parametrize('ip', [None, '192.0.2.1'])
def test_certificate_error_blocks_bundle_download_and_install(ip):
    target = MagicMock()
    with patch.object(swu, 'Target', return_value=target), \
            patch.object(swu, 'load_certificate', side_effect=click.ClickException('bad certificate')), \
            patch.object(swu, 'resolve_bundle') as resolve:
        with pytest.raises(click.ClickException, match='bad certificate'):
            swu.update_system(None, 'modalix', ip=ip, internal=True)
    resolve.assert_not_called()
    target.run.assert_not_called()
    target.transfer.assert_not_called()
    target.close.assert_called_once()


def test_custom_certificate_reaches_installer_and_is_removed(tmp_path):
    bundle = tmp_path / 'custom.swu'
    bundle.write_bytes(b'signed bundle')
    target = MagicMock()
    directory = '/tmp/sima-cli-key.ABC12345'
    def run(script, **kwargs):
        if script == 'date -u +%s':
            return '150'
        if 'mktemp -d /tmp/sima-cli-key.' in script:
            return directory
        if 'mktemp' in script:
            return '/tmp/sima-cli-update.ABC12345'
        return ''
    target.run.side_effect = run
    with patch.object(swu, 'Target', return_value=target), \
            patch.object(swu, 'load_certificate', return_value=('custom public certificate', 100, 200)) as load, \
            patch.object(swu, 'preflight', return_value={'running slot': 'A'}), \
            patch.object(swu, 'resolve_bundle', return_value=str(bundle)), \
            patch.object(swu, '_select_staging_root', return_value='/tmp'), \
            patch.object(swu, '_check_space'), \
            patch.object(swu, 'inspect_target', return_value={'next-boot': 'B', 'upgrade_available': 'yes'}):
        swu.update_system(str(bundle), 'modalix', ip='192.0.2.1', auto_confirm=True, signing_cert='custom.pem')
    load.assert_called_once_with('custom.pem')
    commands = [c.args[0] for c in target.run.call_args_list]
    assert any('custom public certificate' in c and '> ' + directory + '/public.pem' in c for c in commands)
    assert any('swupdate -v' in c and '-k ' + directory + '/public.pem' in c for c in commands)
    assert 'rm -rf -- ' + directory in commands


def test_unreleased_key_option_is_removed():
    with patch('sima_cli.cli.check_for_update', return_value=False), patch('sima_cli.cli.handle_update') as handle:
        result = CliRunner().invoke(main, ['update', '--key', '/data/cert.pem'])
        help_result = CliRunner().invoke(main, ['update', '--help'])
    assert result.exit_code == 2
    assert 'No such option' in result.output and '--key' in result.output
    handle.assert_not_called()
    assert '--key' not in help_result.output
    assert '--signing-cert' in help_result.output


@pytest.mark.parametrize('internal', [False, True])
def test_explicit_certificate_overrides_both_channels(internal):
    assert cert.certificate_source('custom.pem', internal) == 'custom.pem'


def test_channel_defaults_are_separate():
    assert cert.certificate_source(internal=True) == cert.INTERNAL_SIGNING_CERT_URL
    assert cert.certificate_source() is None
    with patch.object(cert, 'PRODUCTION_SIGNING_CERT_URL', 'https://production.example/cert.pem'):
        assert cert.certificate_source() == 'https://production.example/cert.pem'
        assert cert.certificate_source(internal=True) == cert.INTERNAL_SIGNING_CERT_URL


def test_unconfigured_production_certificate_is_silently_skipped(capsys):
    target = MagicMock()
    with patch.object(swu, 'load_certificate') as load:
        assert swu.prepare_key(target) == (None, None)
    load.assert_not_called()
    target.run.assert_not_called()
    assert capsys.readouterr().out == ''
    command = swu.install_script('/tmp/bundle.swu', None).splitlines()[-1]
    assert command == 'swupdate -v -i /tmp/bundle.swu -e update,full'


@pytest.mark.parametrize('internal', [False, True])
def test_legacy_devices_never_prepare_certificates(internal):
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', '2.1.3', '', False, 'elxr')), \
            patch.object(swu, 'prepare_key') as prepare:
        assert swu.handle_update(None, ip='192.0.2.1', internal=internal) is False
    prepare.assert_not_called()


@pytest.mark.parametrize('internal', [False, True])
def test_modern_device_preserves_certificate_channel(internal):
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', '3.0.0', '', False, 'elxr')), \
            patch.object(swu, 'update_system') as install:
        assert swu.handle_update(None, ip='192.0.2.1', internal=internal) is True
    assert install.call_args.kwargs['internal'] is internal
