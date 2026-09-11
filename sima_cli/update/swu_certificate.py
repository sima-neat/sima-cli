"""Retrieve the SWUpdate verification certificate without bundling a signing key."""
import calendar
from pathlib import Path
from urllib.parse import urlparse

import click
import requests
from cryptography import x509

INTERNAL_SIGNING_CERT_URL = 'https://debian.neat.sima.ai/daily/swupdate-signing-cert.pem'
# Configure this when the production signing certificate is published.
PRODUCTION_SIGNING_CERT_URL = None
MAX_CERTIFICATE_BYTES = 64 * 1024


def certificate_source(signing_cert=None, internal=False):
    """An explicit certificate overrides the default for the selected channel."""
    if signing_cert is not None:
        return signing_cert
    return INTERNAL_SIGNING_CERT_URL if internal else PRODUCTION_SIGNING_CERT_URL


def load_certificate(source):
    """Return a single public PEM certificate and its UTC validity bounds."""
    try:
        if urlparse(source).scheme in ('http', 'https'):
            with requests.get(source, stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    data.extend(chunk)
                    if len(data) > MAX_CERTIFICATE_BYTES:
                        raise ValueError('certificate exceeds 64 KiB')
                data = bytes(data)
        else:
            # Check files before rejecting URL schemes so Windows drive paths work.
            path = Path(source).expanduser()
            with path.open('rb') as certificate:
                data = certificate.read(MAX_CERTIFICATE_BYTES + 1)
        pem = data.strip()
        if (not pem.startswith(b'-----BEGIN CERTIFICATE-----')
                or not pem.endswith(b'-----END CERTIFICATE-----')
                or pem.count(b'-----BEGIN ') != 1 or pem.count(b'-----END ') != 1
                or len(data) > MAX_CERTIFICATE_BYTES):
            raise ValueError('expected one PEM public certificate, without private keys or extra content')
        certificate = x509.load_pem_x509_certificate(pem)
        if hasattr(certificate, 'not_valid_before_utc'):
            before, after = certificate.not_valid_before_utc, certificate.not_valid_after_utc
        else:
            before, after = certificate.not_valid_before, certificate.not_valid_after
        return pem.decode('ascii') + '\n', calendar.timegm(before.utctimetuple()), calendar.timegm(after.utctimetuple())
    except (OSError, ValueError, requests.RequestException) as exc:
        raise click.ClickException(
            f'Cannot load SWUpdate signing certificate: {exc}. '
            'Use --signing-cert with a reachable HTTP(S) URL or a local PEM certificate file. '
            'No firmware was installed.'
        ) from exc
