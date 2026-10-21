"""Stage only the verified public mkcert CA for the SDK login download."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa


def publish_browser_ca(cert_dir: Path, mkcert=None) -> bool:
    destination = cert_dir / "sima-sdk-rootCA.pem"
    temporary = None
    try:
        mkcert = mkcert or shutil.which("mkcert")
        if not mkcert:
            raise ValueError("mkcert unavailable")
        result = subprocess.run([mkcert, "-CAROOT"], check=True, capture_output=True,
                                text=True, timeout=5)
        root = Path(result.stdout.strip())
        if not root.is_absolute():
            raise ValueError("CA directory must be absolute")
        ca = x509.load_pem_x509_certificate((root / "rootCA.pem").read_bytes())
        leaf = x509.load_pem_x509_certificate((cert_dir / "neat-sdk.pem").read_bytes())
        if leaf.issuer != ca.subject or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError("not the issuing CA")
        key = ca.public_key()
        if isinstance(key, rsa.RSAPublicKey):
            key.verify(leaf.signature, leaf.tbs_certificate_bytes, padding.PKCS1v15(), leaf.signature_hash_algorithm)
        elif isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(leaf.signature_hash_algorithm))
        else:
            raise ValueError("unsupported CA key")
        # Serialize the parsed certificate, never copy arbitrary CA file contents.
        with tempfile.NamedTemporaryFile(dir=cert_dir, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(ca.public_bytes(serialization.Encoding.PEM))
        temporary.chmod(0o644)
        os.replace(temporary, destination)
        return True
    except Exception:
        # Optional browser help must not break SDK setup or retain stale trust.
        destination.unlink(missing_ok=True)
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
