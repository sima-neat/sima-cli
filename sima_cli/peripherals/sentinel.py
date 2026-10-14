"""Read the SiMa Sentinel peripheral catalog over its local Unix socket.

Sentinel (`simaai-sentinel`) runs as a service on the DevKit and serves HTTP over
`/run/simaai-sentinel/api.sock`. `GET /v1/peripherals` returns the catalog it holds in memory, which
never opens a device; `POST /v1/peripherals/refresh` rescans first. This client is the only thing
sima-cli knows about peripherals: the facts and the per-mode verdict come from the daemon, so the CLI,
Insight and any other client read the same answer.
"""

from __future__ import annotations

import errno
import http.client
import json
import socket
from typing import Any, Dict, Optional, Tuple

SOCKET_PATH = "/run/simaai-sentinel/api.sock"
TIMEOUT_SEC = 10.0
# A refresh waits for a scan that starts after the request; Sentinel gives up after 10 s.
REFRESH_TIMEOUT_SEC = 20.0
MAX_BODY_BYTES = 4 * 1024 * 1024

START_HINT = "Check `systemctl status simaai-sentinel`; the daemon ships with the DevKit image."


class SentinelError(Exception):
    """A failure to read Sentinel, with a stable `code` the CLI maps to an exit status."""

    def __init__(self, code: str, message: str, hint: str = "", detail: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.detail = detail

    def as_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            body["hint"] = self.hint
        if self.detail is not None:
            body["detail"] = self.detail
        return body


def _body_kind(body: Any) -> str:
    """What Sentinel sent, in the CLI's own words: the error document's `detail` never carries the body
    itself, so a field Sentinel adds cannot change the document's shape."""
    if body is None:
        return "no JSON body"
    if isinstance(body, dict):
        return "a JSON object with keys " + ", ".join(sorted(str(k) for k in body)[:8]) if body else "an empty JSON object"
    if isinstance(body, list):
        return f"a JSON array of {len(body)}"
    return f"JSON {type(body).__name__}"


class _UnixHTTPConnection(http.client.HTTPConnection):
    """`http.client` over an `AF_UNIX` socket; the host name is never used."""

    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class SentinelClient:
    def __init__(self, socket_path: str = SOCKET_PATH):
        self.socket_path = socket_path

    def catalog(self, refresh: bool = False) -> Dict[str, Any]:
        """The peripheral catalog; with `refresh`, the one from a scan started after this call."""
        if refresh:
            status, body = self._call("POST", "/v1/peripherals/refresh", timeout=REFRESH_TIMEOUT_SEC)
        else:
            status, body = self._call("GET", "/v1/peripherals")
        if not isinstance(body, dict) or not isinstance(body.get("devices"), list):
            raise SentinelError("protocol", "SiMa Sentinel returned a catalog that does not match its v1 API.",
                                START_HINT, detail={"received": _body_kind(body)})
        return body

    def health(self) -> Dict[str, Any]:
        status, body = self._call("GET", "/v1/health")
        return body if isinstance(body, dict) else {}

    def _call(self, method: str, path: str, timeout: float = TIMEOUT_SEC) -> Tuple[int, Any]:
        connection = _UnixHTTPConnection(self.socket_path, timeout)
        try:
            connection.request(method, path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            declared = response.getheader("Content-Length") or ""
            if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                raise SentinelError("protocol", "SiMa Sentinel's response is larger than sima-cli reads.", START_HINT)
            raw = response.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                raise SentinelError("protocol", "SiMa Sentinel's response is larger than sima-cli reads.", START_HINT)
            status = response.status
        except FileNotFoundError:
            raise SentinelError("sentinel_unavailable",
                                f"SiMa Sentinel is not running on this DevKit: {self.socket_path} does not exist.",
                                START_HINT)
        except PermissionError:
            raise SentinelError("permission_denied",
                                f"This user may not open {self.socket_path}.",
                                "Run as a user allowed to read the Sentinel socket, or check its permissions.")
        except ConnectionRefusedError:
            raise SentinelError("sentinel_unavailable",
                                f"SiMa Sentinel is not accepting connections on {self.socket_path}.", START_HINT)
        except socket.timeout:
            raise SentinelError("timeout", f"SiMa Sentinel did not answer {method} {path} within {timeout:.0f} s.",
                                "Retry; if it keeps happening, restart the daemon.")
        except (OSError, http.client.HTTPException) as exc:
            if getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM):
                raise SentinelError("permission_denied", f"This user may not open {self.socket_path}.",
                                    "Run as a user allowed to read the Sentinel socket, or check its permissions.")
            raise SentinelError("sentinel_unavailable", f"SiMa Sentinel could not be read: {exc}.", START_HINT)
        finally:
            connection.close()

        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            body = None

        if status == 200:
            return status, body
        # Sentinel's error body is {"error": "<text>"}; the status says what kind of failure it is.
        text = body.get("error") if isinstance(body, dict) else None
        text = str(text) if isinstance(text, str) and text else f"HTTP {status}"
        if status == 503:
            raise SentinelError("sentinel_unavailable",
                                f"SiMa Sentinel's peripheral discovery is disabled or has stopped: {text}", START_HINT)
        if status == 504:
            raise SentinelError("timeout", f"SiMa Sentinel's scan did not finish in time: {text}",
                                "Retry; a scan that keeps timing out points at a device driver that does not answer.")
        if status == 429:
            raise SentinelError("busy", f"Too many refreshes are already waiting on SiMa Sentinel: {text}",
                                "Retry in a moment.")
        raise SentinelError("protocol", f"SiMa Sentinel answered {method} {path} with {text}.", START_HINT,
                            detail={"status": status, "received": _body_kind(body)})
