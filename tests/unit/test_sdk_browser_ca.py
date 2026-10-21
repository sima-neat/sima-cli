import datetime
from unittest.mock import Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from sima_cli.sdk.browser_ca import publish_browser_ca


def certificates(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    def cert(public_key, issuer_key, ca):
        return (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(public_key).serial_number(x509.random_serial_number())
                .not_valid_before(now-datetime.timedelta(minutes=1))
                .not_valid_after(now+datetime.timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
                .sign(issuer_key, hashes.SHA256()))
    ca = cert(key.public_key(), key, True)
    leaf = cert(other_key.public_key(), key, False)
    root = tmp_path / "CA"
    root.mkdir()
    (root / "rootCA.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "neat-sdk.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    return root, ca, key


def test_publishes_only_verified_certificate_not_appended_private_material(tmp_path):
    root, ca, key = certificates(tmp_path)
    with (root / "rootCA.pem").open('ab') as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with patch('sima_cli.sdk.browser_ca.subprocess.run', return_value=Mock(stdout=str(root))):
        assert publish_browser_ca(tmp_path, 'mkcert')
    published = tmp_path / 'sima-sdk-rootCA.pem'
    assert published.read_bytes() == ca.public_bytes(serialization.Encoding.PEM)
    assert published.stat().st_mode & 0o777 == 0o644


def test_rejects_same_issuer_name_with_wrong_key_and_removes_old_download(tmp_path):
    root, _, _ = certificates(tmp_path)
    elsewhere = tmp_path / 'other'
    elsewhere.mkdir()
    other_root, _, _ = certificates(elsewhere)
    (root / 'rootCA.pem').write_bytes((other_root / 'rootCA.pem').read_bytes())
    destination = tmp_path / 'sima-sdk-rootCA.pem'
    destination.write_text('stale')
    with patch('sima_cli.sdk.browser_ca.subprocess.run', return_value=Mock(stdout=str(root))):
        assert not publish_browser_ca(tmp_path, 'mkcert')
    assert not destination.exists()


def test_missing_mkcert_removes_stale_ca_without_failing_setup(tmp_path):
    destination = tmp_path / 'sima-sdk-rootCA.pem'
    destination.write_text('stale')
    with patch('sima_cli.sdk.browser_ca.shutil.which', return_value=None):
        assert not publish_browser_ca(tmp_path)
    assert not destination.exists()
