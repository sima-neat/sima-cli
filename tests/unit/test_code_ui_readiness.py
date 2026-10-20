import http.client
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from sima_cli.sdk import neat


def test_retries_closed_connection_and_startup_error_before_ready():
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            if len(paths) == 1:
                self.close_connection = True
                return
            self.send_response(503 if len(paths) == 2 else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/?tkn=secret&folder=/workspace"
        assert neat._wait_for_code_ui(url, timeout=5)
        assert paths == ["/", "/", "/"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("status", [200, 302, 401, 403])
def test_https_probe_supports_sdk_certificates_without_sending_token(status):
    with patch.object(neat.http.client, "HTTPSConnection") as factory:
        connection = factory.return_value
        connection.getresponse.return_value.status = status
        assert neat._wait_for_code_ui("https://localhost:18501/?tkn=secret")
    assert factory.call_args.args == ("localhost", 18501)
    assert factory.call_args.kwargs["context"].verify_mode == ssl.CERT_NONE
    connection.request.assert_called_once_with("GET", "/")
    connection.close.assert_called_once()


@pytest.mark.parametrize("failure", [ConnectionRefusedError(), http.client.RemoteDisconnected()])
def test_timeout_bounds_retries_and_closes_connection(failure):
    with patch.object(neat.http.client, "HTTPConnection") as factory, \
         patch.object(neat.time, "monotonic", side_effect=[0, 0, 0.8, 1]), \
         patch.object(neat.time, "sleep") as sleep:
        factory.return_value.getresponse.side_effect = failure
        assert not neat._wait_for_code_ui("http://localhost:18501", timeout=1)
    assert factory.call_args.kwargs["timeout"] == 1
    assert sleep.call_args.args[0] == pytest.approx(0.2)
    factory.return_value.close.assert_called_once()


@pytest.mark.parametrize("ready", [True, False])
def test_browser_only_opens_after_readiness(ready):
    events = []
    with patch.object(neat.webbrowser, "get"), \
         patch.object(neat, "_wait_for_code_ui", side_effect=lambda url: events.append("wait") or ready), \
         patch.object(neat.webbrowser, "open", side_effect=lambda url: events.append("open") or True), \
         patch.object(neat.console, "print") as output:
        assert neat._open_code_ui_if_browser_available("http://localhost:18501") is ready
    assert events == (["wait", "open"] if ready else ["wait"])
    if not ready:
        assert "Browser launch skipped" in output.call_args.args[0]


def test_no_browser_does_not_wait():
    with patch.object(neat.webbrowser, "get", side_effect=neat.webbrowser.Error), \
         patch.object(neat, "_wait_for_code_ui") as wait:
        assert not neat._open_code_ui_if_browser_available("http://localhost:18501")
    wait.assert_not_called()
