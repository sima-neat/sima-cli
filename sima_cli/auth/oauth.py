import os
import re
import sys
import base64
import click
import requests
import webbrowser
import secrets
import string
import time
import importlib.resources as pkg_resources
from pathlib import Path
from urllib.parse import urlencode
from http.cookiejar import MozillaCookieJar
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding

# ----------------------------------------------------------------------
# Config
DISCOURSE_URL = "https://discourse-dev.sima.ai"
CLIENT_ID = "sima-cli"
APPLICATION_NAME = "SiMa CLI"
SCOPES = ["read", "one_time_password", "session_info"]

WORKER_BASE = "https://withered-block-db9d.francesca-9f4.workers.dev"

HOME_DIR = os.path.expanduser("~/.sima-cli")
KEY_PATH = os.path.join(HOME_DIR, "oauth.pem")
COOKIE_JAR_PATH = os.path.join(HOME_DIR, ".sima-cli-cookies.txt")

COMPLETION_HTML_PATH = Path(
    pkg_resources.files("sima_cli") / "data" / "completion.html"
)

os.makedirs(HOME_DIR, exist_ok=True)

# ----------------------------------------------------------------------
# Helpers
def is_headless() -> bool:
    """Detect if running in headless mode (no GUI/browser)."""
    if os.environ.get("SIMACLI_HEADLESS") == "1":
        return True
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return True
    try:
        webbrowser.get()
        return False
    except webbrowser.Error:
        return True


def generate_nonce(length: int = 24) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def load_or_create_keypair():
    """Load or create RSA keypair used for OTP decryption."""
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    else:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with open(KEY_PATH, "wb") as f:
            f.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_key, public_pem


def decrypt_blob(private_key, blob: str) -> str:
    """Decrypt a base64-encoded OTP blob using RSA private key."""
    stripped = blob.strip()
    b64_clean = re.sub(r"\s+", "", stripped)
    try:
        encrypted_bytes = base64.b64decode(b64_clean)
        try:
            return private_key.decrypt(
                encrypted_bytes,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            ).decode()
        except Exception:
            return private_key.decrypt(encrypted_bytes, padding.PKCS1v15()).decode()
    except Exception:
        return stripped


def save_cookies(session: requests.Session):
    """Save session cookies to disk."""
    cj = MozillaCookieJar(COOKIE_JAR_PATH)
    for c in session.cookies:
        cj.set_cookie(c)
    cj.save(ignore_discard=True)


def load_cookies(session: requests.Session):
    """Load cookies from disk if present."""
    if os.path.exists(COOKIE_JAR_PATH):
        cj = MozillaCookieJar()
        cj.load(COOKIE_JAR_PATH, ignore_discard=True)
        session.cookies.update(cj)


# ----------------------------------------------------------------------
# KV-based OTP flow
def wait_for_auth_flow_kv(private_key, worker_base: str, key_id: str, timeout=180):
    """Poll the Worker KV for OTP until found or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{worker_base}/getvalue?key={key_id}", timeout=5)
            if r.status_code == 200:
                data = r.json()
                blob = data.get("value")
                if blob:
                    otp_value = decrypt_blob(private_key, blob)
                    return otp_value
        except Exception:
            pass
        time.sleep(2)
    return None


# ----------------------------------------------------------------------
# Main OAuth flow
def oauth():
    """OTP-only login flow using Cloudflare KV Worker as the redirect handler."""
    private_key, public_pem = load_or_create_keypair()

    # Step 1. Get a temporary key from the Worker
    try:
        key_resp = requests.get(f"{WORKER_BASE}/getid").json()
        key_id = key_resp.get("key")
    except Exception as e:
        click.secho(f"❌ Failed to connect to Worker: {e}", fg="red")
        return

    # Step 2. Prepare redirect and Discourse auth URL
    auth_redirect = f"{WORKER_BASE}/update/{key_id}/"
    params = {
        "application_name": APPLICATION_NAME,
        "client_id": CLIENT_ID,
        "scopes": ",".join(SCOPES),
        "nonce": generate_nonce(),
        "auth_redirect": auth_redirect,
        "public_key": public_pem,
    }
    otp_url = f"{DISCOURSE_URL}/user-api-key/otp?{urlencode(params)}"

    # Step 3. Open browser or print URL
    if is_headless():
        click.echo("➡️  Open this URL in your browser to authorize:")
        click.secho(otp_url, fg="green")
    else:
        try:
            webbrowser.open(otp_url)
        except Exception:
            click.secho("No browser found", fg="red")
            click.echo("➡️  Open this URL manually:")
            click.secho(otp_url, fg="green")

    # Step 4. Poll KV Worker for OTP
    click.echo("⌛ Waiting for authorization (up to 3 minutes)...")
    otp_value = wait_for_auth_flow_kv(private_key, WORKER_BASE, key_id)

    if not otp_value:
        click.secho("❌ Timeout waiting for OTP", fg="red")
        return

    # Step 5. Exchange OTP with Discourse
    session = requests.Session()
    try:
        csrf = session.get(f"{DISCOURSE_URL}/session/csrf.json").json().get("csrf")
        session.headers.update({"X-CSRF-Token": csrf})
        resp = session.post(f"{DISCOURSE_URL}/session/otp/{otp_value}")
    except Exception as e:
        click.secho(f"❌ Failed to exchange OTP: {e}", fg="red")
        return

    if resp.status_code == 200:
        save_cookies(session)
        click.secho("✅ Login successful. Cookies saved!", fg="green")
    else:
        click.secho(f"❌ OTP exchange failed (status {resp.status_code})", fg="red")
        click.echo(resp.text)


if __name__ == "__main__":
    oauth()
