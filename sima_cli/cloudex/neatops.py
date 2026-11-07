"""NeatOps browser authentication and personal device allocation API."""

import base64
import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from .client import CloudExError, DEFAULT_ROOT

DASHBOARDS = {
    "production": "https://ops.neat.sima.ai",
    "staging": "https://ops.stg.neat.sima.ai",
}
REDIRECT_URI = "http://127.0.0.1:47182/callback"


def _origin(value):
    if not isinstance(value, str):
        raise CloudExError("NeatOps configuration requires an HTTPS origin")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise CloudExError("NeatOps configuration requires an HTTPS origin")
    return value.rstrip("/")


def _json_request(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=20, allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise CloudExError("NeatOps is unavailable; check your connection and retry") from None
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return response.status_code, payload


class LoginServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


class LoginCallback(BaseHTTPRequestHandler):
    """Only accept the matching loopback OAuth response; never log its code."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        valid = (self.headers.get("Host") == "127.0.0.1:47182"
                 and parsed.path == "/callback"
                 and params.get("state") == [self.server.oauth_state])
        code = params.get("code", [])
        error = params.get("error", [])
        valid = valid and ((len(code) == 1 and not error) or (len(error) == 1 and not code))
        if valid:
            self.server.oauth_result = {"code": code[0]} if code else {"error": True}
        self.send_response(200 if valid else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(b"Return to sima-cli to finish signing in." if valid else b"Invalid sign-in response.")


class LoginRequired(CloudExError):
    """Saved credentials cannot authorize a request without browser sign-in."""


class NeatOps:
    def __init__(self, environment="production", root=None, on_login_required=None):
        self.environment = environment
        self.dashboard = DASHBOARDS[environment]
        self.path = Path(root or DEFAULT_ROOT) / ("neatops-" + environment + ".json")
        self.config = None
        self.on_login_required = on_login_required
        self._login_attempted = False

    def configuration(self):
        if self.config is None:
            status, config = _json_request("GET", self.dashboard + "/cli-config.json")
            if status != 200:
                raise CloudExError("NeatOps CLI login is not configured on this dashboard yet")
            auth = config.get("auth", {})
            if (not isinstance(auth, dict) or auth.get("enabled") is not True
                    or not isinstance(auth.get("clientId"), str) or not auth["clientId"]
                    or auth.get("redirectUri") != REDIRECT_URI
                    or config.get("apiBase") != self.dashboard + "/api"):
                raise CloudExError("NeatOps CLI login configuration is unavailable or invalid")
            try:
                _origin(auth["domain"])
            except (KeyError, TypeError, ValueError):
                raise CloudExError("NeatOps sign-in domain is invalid") from None
            self.config = config
        return self.config

    def _save(self, tokens):
        auth = self.configuration()["auth"]
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
            raise CloudExError("NeatOps sign-in did not return an access token")
        lifetime = tokens.get("expires_in")
        if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or lifetime <= 0:
            raise CloudExError("NeatOps sign-in returned an invalid token lifetime")
        session = {"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token"),
                   "expires_at": time.time() + lifetime, "client_id": auth["clientId"], "domain": auth["domain"]}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".neatops-", dir=str(self.path.parent))
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(session, stream)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return session["access_token"]

    def _exchange(self, parameters):
        auth = self.configuration()["auth"]
        status, tokens = _json_request("POST", auth["domain"].rstrip("/") + "/oauth2/token",
                                       data={"client_id": auth["clientId"], **parameters})
        if status == 400 and parameters.get("grant_type") == "refresh_token" and tokens.get("error") == "invalid_grant":
            raise LoginRequired("NeatOps sign-in expired; run sima-cli -i cloudex login again")
        if status != 200:
            raise CloudExError("NeatOps sign-in expired or was rejected; run sima-cli -i cloudex login again")
        return tokens

    def login(self, announce, timeout=180):
        auth = self.configuration()["auth"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        parameters = {"response_type": "code", "client_id": auth["clientId"], "redirect_uri": REDIRECT_URI,
                      "scope": "openid email profile", "state": state, "code_challenge_method": "S256",
                      "code_challenge": challenge}
        if auth.get("identityProvider"):
            parameters["identity_provider"] = auth["identityProvider"]
        url = auth["domain"].rstrip("/") + "/oauth2/authorize?" + urlencode(parameters)
        try:
            server = LoginServer(("127.0.0.1", 47182), LoginCallback)
        except OSError:
            raise CloudExError("CloudEx sign-in needs local port 47182; close another login and retry") from None
        with server:
            server.oauth_state = state
            server.oauth_result = None
            server.timeout = 0.5
            announce(url)
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass  # The terminal also displays the URL for manual opening.
            deadline = time.monotonic() + timeout
            while server.oauth_result is None and time.monotonic() < deadline:
                server.handle_request()
            result = server.oauth_result
        if result is None:
            raise CloudExError("NeatOps sign-in timed out; run sima-cli -i cloudex login again")
        if result.get("error"):
            raise CloudExError("NeatOps sign-in was cancelled or rejected")
        self._save(self._exchange({"grant_type": "authorization_code", "code": result["code"],
                                   "code_verifier": verifier, "redirect_uri": REDIRECT_URI}))

    def _read_session(self):
        try:
            # Refuse symlinks and shared credential files, as for CloudEx keys.
            descriptor = os.open(str(self.path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor) as stream:
                mode = os.fstat(stream.fileno()).st_mode
                if not stat.S_ISREG(mode):
                    raise CloudExError("NeatOps credential file must be a regular file")
                if os.name != "nt" and mode & 0o077:
                    raise CloudExError("NeatOps credential file must have mode 0600")
                session = json.load(stream)
            return session
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            raise LoginRequired("Sign in first with sima-cli -i cloudex login --environment " + self.environment) from None
        except OSError:
            raise CloudExError("Sign in first with sima-cli -i cloudex login --environment " + self.environment) from None

    def access_token(self):
        auth = self.configuration()["auth"]
        session = self._read_session()
        try:
            if session["client_id"] != auth["clientId"] or session["domain"] != auth["domain"]:
                raise ValueError("Sign-in policy changed")
            if session["expires_at"] > time.time() + 60:
                return session["access_token"]
            refresh = session.get("refresh_token")
        except (ValueError, KeyError, TypeError):
            raise LoginRequired("Sign-in policy changed; run sima-cli -i cloudex login again") from None
        if not refresh:
            raise LoginRequired("NeatOps sign-in expired; run sima-cli -i cloudex login again")
        tokens = self._exchange({"grant_type": "refresh_token", "refresh_token": refresh})
        tokens.setdefault("refresh_token", refresh)
        return self._save(tokens)

    def logout(self):
        if self.path.exists():
            # Revoke the CLI's refresh token without signing the browser out.
            auth = self.configuration()["auth"]
            session = self._read_session()
            if session.get("refresh_token"):
                status, _ = _json_request("POST", auth["domain"].rstrip("/") + "/oauth2/revoke",
                                          data={"client_id": session["client_id"], "token": session["refresh_token"]})
                if status != 200:
                    raise CloudExError("NeatOps token revocation failed; retry logout")
            self.path.unlink()

    def _authenticate(self):
        if self.on_login_required is None or self._login_attempted:
            raise LoginRequired("Sign in again with sima-cli -i cloudex login --environment " + self.environment)
        self._login_attempted = True
        self.on_login_required(self)
        return self.access_token()

    def call(self, method, path, data=None):
        config = self.configuration()
        try:
            token = self.access_token()
        except LoginRequired:
            token = self._authenticate()

        def request(access_token):
            return _json_request(method, config["apiBase"] + path,
                                 headers={"Authorization": "Bearer " + access_token}, json=data)

        status, payload = request(token)
        # A 401 from the authorizer has no side effects, so retry the same
        # request once after sign-in. Permission failures and outages do not
        # trigger login or replay allocation requests.
        if status == 401 and self.on_login_required is not None and not self._login_attempted:
            status, payload = request(self._authenticate())
        if status in {401, 403}:
            raise CloudExError("NeatOps access denied; sign in again or check your device permissions")
        if not 200 <= status < 300:
            # Do not print backend response bodies, which could contain credentials.
            raise CloudExError("NeatOps request failed (HTTP {}); check availability and lease limits in My Devices".format(status))
        return payload
