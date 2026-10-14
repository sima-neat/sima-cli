"""The Sentinel socket client against a real HTTP server on a Unix socket."""

import json
import os
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn, UnixStreamServer

import pytest

from sima_cli.peripherals.sentinel import SentinelClient, SentinelError
from tests.unit.peripherals_fixtures import devkit

pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Sentinel's socket is AF_UNIX; not on this OS")


class _Server(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    routes = {}

    def _serve(self, method):
        status, body = self.routes.get((method, self.path), (404, {"error": "no such route"}))
        payload = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def log_message(self, *args):
        pass

    # http.server fills client_address from a Unix socket with a string; keep logging code happy.
    def address_string(self):
        return "unix"


@pytest.fixture
def sentinel(tmp_path):
    """A Sentinel look-alike on a socket path short enough for AF_UNIX."""
    sock_dir = tempfile.mkdtemp(prefix="sentinel-", dir="/tmp")
    path = os.path.join(sock_dir, "api.sock")
    catalog = devkit()
    _Handler.routes = {
        ("GET", "/v1/peripherals"): (200, catalog),
        ("POST", "/v1/peripherals/refresh"): (200, dict(catalog, revision=catalog["revision"] + 1)),
        ("GET", "/v1/health"): (200, {"version": "0.6.0"}),
    }
    server = _Server(path, _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path, catalog
    finally:
        server.shutdown()
        server.server_close()
        os.unlink(path)
        os.rmdir(sock_dir)


def test_catalog_and_refresh_read_the_v1_documents(sentinel):
    path, catalog = sentinel
    client = SentinelClient(path)
    assert client.catalog()["revision"] == catalog["revision"]
    assert client.catalog(refresh=True)["revision"] == catalog["revision"] + 1
    assert client.health() == {"version": "0.6.0"}


def test_a_missing_socket_is_sentinel_unavailable(tmp_path):
    with pytest.raises(SentinelError) as raised:
        SentinelClient(str(tmp_path / "missing.sock")).catalog()
    assert raised.value.code == "sentinel_unavailable"
    assert "does not exist" in raised.value.message
    assert raised.value.as_dict()["hint"]


def test_a_refused_connection_is_sentinel_unavailable(tmp_path):
    sock_dir = tempfile.mkdtemp(prefix="sentinel-", dir="/tmp")
    path = os.path.join(sock_dir, "api.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.close()  # the path stays, nothing listens: ECONNREFUSED
    try:
        with pytest.raises(SentinelError) as raised:
            SentinelClient(path).catalog()
        assert raised.value.code == "sentinel_unavailable"
    finally:
        os.unlink(path)
        os.rmdir(sock_dir)


@pytest.mark.parametrize("status,code", [(503, "sentinel_unavailable"), (504, "timeout"), (429, "busy"), (500, "protocol")])
def test_http_failures_map_to_stable_codes_with_sentinels_text(sentinel, status, code):
    path, _ = sentinel
    _Handler.routes[("GET", "/v1/peripherals")] = (status, {"error": "discovery stopped"})
    with pytest.raises(SentinelError) as raised:
        SentinelClient(path).catalog()
    assert raised.value.code == code
    assert "discovery stopped" in raised.value.message
    if code == "protocol":
        # Codex on #290: the error document's detail is the CLI's own description, never the body.
        assert raised.value.as_dict()["detail"] == {"status": 500, "received": "a JSON object with keys error"}


def test_a_body_that_is_not_a_catalog_is_a_protocol_error(sentinel):
    path, _ = sentinel
    _Handler.routes[("GET", "/v1/peripherals")] = (200, {"devices": "nope", "secret": {"nested": 1}})
    with pytest.raises(SentinelError) as raised:
        SentinelClient(path).catalog()
    assert raised.value.code == "protocol"
    assert raised.value.as_dict()["detail"] == {"received": "a JSON object with keys devices, secret"}
    _Handler.routes[("GET", "/v1/peripherals")] = (200, [1, 2])
    with pytest.raises(SentinelError) as raised:
        SentinelClient(path).catalog()
    assert raised.value.as_dict()["detail"] == {"received": "a JSON array of 2"}
