"""Host-side lifecycle support for temporary CloudEx WireGuard sessions."""

import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests


DEFAULT_ROOT = Path.home() / ".sima-cli" / "cloudex"
FORWARDER_NAME = "kerrigan-p2p-forwarder"
SAFE_FORWARDER_STAGES = {
    "session configuration accepted",
    "joined authenticated signaling session",
    "local ICE credentials sent",
    "remote ICE credentials received",
    "selected ICE path direct",
    "selected ICE path relay",
    "ICE connectivity established",
    "starting WireGuard tunnel",
    "WireGuard public key sent through signaling",
    "WireGuard public key received through signaling",
    "WireGuard public key acknowledged through signaling",
    "WireGuard handshake confirmed",
    "signaling authorization rejected",
    "signaling session not found",
    "signaling session conflict",
    "signaling rate limited",
}


class CloudExError(RuntimeError):
    """A user-actionable CloudEx failure."""


class APIError(CloudExError):
    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__("CloudEx API {}: {}".format(status, detail))


class DirectHandshakeError(CloudExError):
    """A direct path failed and may succeed with fresh ICE candidates."""


def decode_key(value):
    """Decode and validate an allocation key without persisting the key itself."""
    try:
        raw = base64.b64decode(value.strip(), validate=True)
        profile = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudExError("--key must be a valid Base64 CloudEx allocation key") from exc
    required = {"environment", "api_url", "requester", "allocation_id", "allocation_secret"}
    if not isinstance(profile, dict) or set(profile) != required:
        raise CloudExError("--key is not a valid CloudEx allocation key")
    if not all(isinstance(profile.get(name), str) and profile[name] for name in required):
        raise CloudExError("--key contains an invalid value")
    if len(profile["allocation_secret"]) < 32:
        raise CloudExError("--key contains an invalid allocation secret")
    if profile["environment"] != "vulcan-staging":
        raise CloudExError("CloudEx currently supports only vulcan-staging allocation keys")
    return profile


def _signature(secret, method, path, requester, timestamp, nonce, body):
    message = "\n".join((
        "kerrigan-p2p-v1",
        method.upper(),
        path,
        requester,
        timestamp,
        nonce,
        hashlib.sha256(body).hexdigest(),
    ))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class CloudExAPI:
    def __init__(self, url, requester, secret, allocation_id):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise CloudExError("CloudEx API must be an HTTPS origin")
        self.url = url.rstrip("/")
        self.requester = requester
        self.secret = secret
        self.allocation_id = allocation_id

    @classmethod
    def from_profile(cls, profile):
        return cls(
            profile["api_url"],
            profile["requester"],
            profile["allocation_secret"],
            profile["allocation_id"],
        )

    @classmethod
    def from_session(cls, session):
        return cls(
            session["api_url"],
            session["requester"],
            session["api_secret"],
            session["allocation_id"],
        )

    def call(self, method, path, data=None):
        body = json.dumps(data, separators=(",", ":")).encode() if data is not None else b""
        for attempt in range(4):
            timestamp = str(int(time.time()))
            nonce = uuid.uuid4().hex
            headers = {
                "content-type": "application/json",
                "x-p2p-requester": self.requester,
                "x-p2p-timestamp": timestamp,
                "x-p2p-nonce": nonce,
                "x-p2p-signature": _signature(
                    self.secret, method, path, self.requester, timestamp, nonce, body
                ),
                "x-p2p-allocation-id": self.allocation_id,
            }
            try:
                response = requests.request(
                    method,
                    self.url + path,
                    data=body or None,
                    headers=headers,
                    timeout=15,
                )
            except requests.RequestException as exc:
                if attempt == 3:
                    raise CloudExError("CloudEx API is unavailable") from exc
            else:
                if response.ok:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise CloudExError("CloudEx API returned an invalid response") from exc
                detail = response.text[:4096]
                if attempt == 3 or response.status_code not in {409, 429, 500, 502, 503, 504}:
                    raise APIError(response.status_code, detail)
            time.sleep(0.5 * (attempt + 1))
        raise AssertionError("unreachable")


class SessionStore:
    """Mode-0600, one-file-per-session state prepared for concurrent sessions."""

    def __init__(self, root=None):
        self.root = Path(root or DEFAULT_ROOT).expanduser()
        self.sessions_dir = self.root / "sessions"
        self.recovery_dir = self.root / "recovery"
        self.runtime_dir = self.root / "runtime"

    def ensure(self):
        self.sessions_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.recovery_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sessions_dir.chmod(0o700)
        self.recovery_dir.chmod(0o700)
        self.runtime_dir.chmod(0o700)

    def path(self, session_id):
        return self.sessions_dir / (session_id + ".json")

    def recovery_path(self, session_id):
        return self.recovery_dir / (session_id + ".json")

    def runtime_paths(self, session_id):
        directory = self.runtime_dir / session_id
        return directory, directory / "config.json", directory / "report.json", directory / "forwarder.log"

    def _write(self, session, path):
        self.ensure()
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(session, stream, separators=(",", ":"), sort_keys=True)
        os.replace(str(temporary), str(path))
        path.chmod(0o600)

    def write(self, session):
        self._write(session, self.path(session["session_id"]))

    def write_recovery(self, session):
        """Retain cleanup evidence without presenting it as a connection."""
        self._write(session, self.recovery_path(session["session_id"]))

    def clear_recovery(self, session_id):
        self.recovery_path(session_id).unlink(missing_ok=True)

    def delete(self, session_id):
        self.path(session_id).unlink(missing_ok=True)
        self.clear_recovery(session_id)
        directory, config, report, log = self.runtime_paths(session_id)
        for path in (config, report, log):
            path.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            pass

    @staticmethod
    def _read_records(directory):
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError as exc:
            raise CloudExError("CloudEx session state cannot be read") from exc
        sessions = []
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("session_id") == path.stem:
                    sessions.append(value)
            except (OSError, ValueError, AttributeError):
                continue
        return sessions

    def list(self):
        """List established connections, migrating old incomplete rows."""
        sessions = self._read_records(self.sessions_dir)
        visible = []
        for session in sessions:
            target = session.get("target")
            if isinstance(target, str) and target:
                visible.append(session)
                continue
            self.write_recovery(session)
            self.path(session["session_id"]).unlink(missing_ok=True)
        return visible

    def list_recovery(self):
        # Trigger migration of records written by older sima-cli builds.
        self.list()
        return self._read_records(self.recovery_dir)


def find_forwarder():
    override = os.environ.get("SIMA_CLOUDEX_FORWARDER")
    # An explicit path is authoritative. This supports controlled installations
    # and avoids silently using a different system binary after a bad override.
    if override:
        candidates = [override]
    else:
        candidates = []
        discovered = shutil.which(FORWARDER_NAME)
        if discovered:
            candidates.append(discovered)
        candidates.extend(["/usr/local/bin/" + FORWARDER_NAME, "/usr/bin/" + FORWARDER_NAME])
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    raise CloudExError(
        "CloudEx native forwarder is not installed; install the current Kerrigan P2P host package"
    )


def authorize_admin():
    try:
        result = subprocess.run(["sudo", "-v"], check=False)
    except OSError as exc:
        raise CloudExError("sudo is required to create the encrypted tunnel") from exc
    if result.returncode:
        raise CloudExError("administrator authorization was not granted")


def _read_report(path):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return report if isinstance(report, dict) else {}


def _safe_last_stage(log_path):
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    stages = []
    for line in lines:
        prefix = "kerrigan-p2p-forwarder:"
        stage = line[len(prefix):].strip() if line.startswith(prefix) else line.strip()
        if stage in SAFE_FORWARDER_STAGES:
            stages.append(stage)
    return stages[-1] if stages else ""


def _safe_selected_path(log_path):
    """Recover the non-sensitive selected path from forwarder lifecycle logs."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    selected = ""
    for line in lines:
        prefix = "kerrigan-p2p-forwarder:"
        stage = line[len(prefix):].strip() if line.startswith(prefix) else line.strip()
        if stage == "selected ICE path direct":
            selected = "direct"
        elif stage == "selected ICE path relay":
            selected = "relay"
    return selected


def _forwarder_failure(log_path):
    stage = _safe_last_stage(log_path)
    suffix = "; last stage: " + stage if stage else ""
    try:
        detail = log_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        detail = ""
    mappings = {
        "a password is required": "administrator authorization is required; run CloudEx from an interactive terminal",
        "a terminal is required": "administrator authorization is required; run CloudEx from an interactive terminal",
        "no tty present": "administrator authorization is required; run CloudEx from an interactive terminal",
        "receive wireguard public key": "WireGuard peer-key exchange timed out",
        "wireguard handshake timed out": "WireGuard handshake timed out after ICE connected",
        "join signaling": "the native forwarder could not join the signaling session",
        "wireguard is unavailable": "WireGuard support is unavailable on this host",
        "start wireguard-go": "the WireGuard interface could not start",
        "invalid session config": "the native forwarder rejected the session configuration",
    }
    for needle, message in mappings.items():
        if needle in detail:
            return message + suffix
    return "the native forwarder exited before connecting" + suffix


def _process_running(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _forwarder_running(pid):
    """Avoid signaling an unrelated process if a stale PID has been reused."""
    if not _process_running(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and FORWARDER_NAME in result.stdout


def _wait_forwarder(session_id, process, report_path, log_path, deadline, progress):
    previous = None
    while time.time() < deadline:
        stage = _safe_last_stage(log_path)
        if stage and stage != previous:
            progress.update(stage)
            previous = stage
        report = _read_report(report_path)
        if report.get("session_id") == session_id:
            if report.get("status") == "connected":
                return report
            if report.get("status") == "failed":
                raise CloudExError(_forwarder_failure(log_path))
        if stage == "WireGuard handshake confirmed":
            # The handshake log is emitted immediately before the final report
            # write. The preceding "checking" report already carries the
            # selected path; use it (or the sanitized selection stage) rather
            # than racing the write and presenting a connected path as unknown.
            path = report.get("path")
            if path not in {"direct", "relay"}:
                path = _safe_selected_path(log_path) or "unknown"
            return {"session_id": session_id, "status": "connected", "path": path}
        if process.poll() is not None:
            raise CloudExError(_forwarder_failure(log_path))
        time.sleep(0.2)
    raise DirectHandshakeError("WireGuard handshake timed out")


def _device_label(allocated, allocation_id):
    for key in ("target_id", "device_id", "devkit_id", "device_name", "name"):
        value = allocated.get(key)
        if isinstance(value, str) and value:
            return value
    device = allocated.get("device")
    if isinstance(device, dict):
        for key in ("display_name", "name", "target_id", "id"):
            value = device.get(key)
            if isinstance(value, str) and value:
                return value
    return "DevKit " + allocation_id[:8]


def _creation_definitely_rejected(error):
    if not isinstance(error, APIError):
        return False
    if error.status in {401, 403}:
        return True
    if error.status != 409:
        return False
    try:
        detail = json.loads(error.detail).get("error", "")
    except (TypeError, ValueError):
        detail = str(error.detail)
    return detail == "an ICE session is already active for this allocation"


def _valid_session_id(value):
    return isinstance(value, str) and len(value) == 32 and all(
        character in "0123456789abcdef" for character in value
    )


def _remote_session_id(session, store):
    """Resolve the service session ID, including records from older clients."""
    candidate = session.get("remote_session_id")
    if _valid_session_id(candidate):
        return candidate
    _runtime, _config, report_path, _log = store.runtime_paths(session["session_id"])
    candidate = _read_report(report_path).get("session_id")
    return candidate if _valid_session_id(candidate) else session["session_id"]


def _reconcile_existing_connection(profile, store, progress):
    """Reuse a live allocation owner or clean stale state before reconnecting."""
    existing = [
        session for session in store.list()
        if session.get("allocation_id") == profile["allocation_id"]
    ]
    inspected = [(session, inspect_session(session, store)) for session in existing]
    for session, status in inspected:
        if status["connected"]:
            progress.update("Existing CloudEx tunnel is already connected")
            session["ice_path"] = status["path"]
            session["remote_session_id"] = _remote_session_id(session, store)
            store.write(session)
            return session
    for session, _status in inspected:
        progress.update("Cleaning up a previous CloudEx tunnel")
        disconnect_session(session, store, progress, authorize=False)
    return None


def connect(profile, store, progress, attempts=3, transport="auto"):
    """Create a session and return its durable local record."""
    try:
        forwarder = find_forwarder()
    except CloudExError:
        # Import lazily: the installer reuses this module's privilege and error
        # handling, while ordinary CloudEx commands should remain quick to load.
        from .installer import DEFAULT_BRANCH, install_forwarder

        progress.update("CloudEx forwarder is missing; installing from {}".format(DEFAULT_BRANCH))
        forwarder = install_forwarder(DEFAULT_BRANCH, progress=progress)["path"]
    authorize_admin()
    api = CloudExAPI.from_profile(profile)
    existing = _reconcile_existing_connection(profile, store, progress)
    if existing:
        return existing
    choices = (["p2p"] * attempts + ["routed"] if transport == "auto"
               else ["routed"] if transport == "turn" else ["p2p"] * attempts)
    last_error = None
    for number, selected_transport in enumerate(choices, 1):
        session_id = uuid.uuid4().hex
        session = {
            "version": 1,
            "session_id": session_id,
            "environment": profile["environment"],
            "api_url": api.url,
            "requester": api.requester,
            "allocation_id": api.allocation_id,
            "api_secret": api.secret,
            "created_at": int(time.time()),
            "status": "creating",
        }
        store.write_recovery(session)
        progress.update("Requesting a DevKit session ({}/{})".format(number, len(choices)))
        try:
            allocated = api.call("POST", "/v1/p2p/ice-sessions", {"request_id": session_id})
            if not isinstance(allocated, dict):
                raise CloudExError("CloudEx API returned an invalid session configuration")
            remote_session_id = allocated.get("session_id")
            if not _valid_session_id(remote_session_id):
                raise CloudExError("CloudEx API returned an invalid session identifier")
            session["remote_session_id"] = remote_session_id
            store.write_recovery(session)
            try:
                expires_at = float(allocated["expires_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CloudExError("CloudEx API returned an invalid session expiration") from exc
            runtime, config_path, report_path, log_path = store.runtime_paths(session_id)
            runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
            report_path.touch(mode=0o600, exist_ok=False)
            config = dict(allocated)
            config["transport"] = selected_transport
            config_path.write_text(json.dumps(config, separators=(",", ":")), encoding="utf-8")
            config_path.chmod(0o600)
            with log_path.open("x", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [
                        "sudo", "-n", "--", str(forwarder), "session", "--role", "user",
                        "--config", str(config_path), "--report", str(report_path),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                    text=True,
                )
            log_path.chmod(0o600)
            session.update(status="connecting", forwarder_pid=process.pid)
            store.write_recovery(session)
            report = _wait_forwarder(
                remote_session_id,
                process,
                report_path,
                log_path,
                expires_at,
                progress,
            )
            wireguard = allocated.get("wireguard") or {}
            target = str(wireguard.get("remote_address") or "").partition("/")[0]
            if not target:
                raise CloudExError("CloudEx response did not include a WireGuard target")
            session.update(
                status="connected",
                connected_at=int(time.time()),
                expires_at=allocated.get("expires_at"),
                ice_path=report.get("path", "unknown"),
                target=target,
                device=_device_label(allocated, api.allocation_id),
            )
            store.write(session)
            store.clear_recovery(session_id)
            config_path.unlink(missing_ok=True)
            return session
        except BaseException as exc:
            last_error = exc
            if _creation_definitely_rejected(exc):
                store.delete(session_id)
                raise
            progress.update("Cleaning up the failed connection attempt")
            try:
                disconnect_session(session, store, progress, authorize=False)
            except Exception as cleanup_error:
                session["status"] = "cleanup-pending"
                store.write_recovery(session)
                raise CloudExError(
                    "Connection failed and cleanup is pending: {}. Run cloudex disconnect again.".format(cleanup_error)
                ) from exc
            if not isinstance(exc, DirectHandshakeError) or number == len(choices):
                raise
            progress.update("Direct path timed out; retrying with fresh ICE candidates")
    raise last_error or CloudExError("CloudEx connection failed")


def _cleanup_pending(error):
    if not isinstance(error, APIError) or error.status != 409:
        return False
    try:
        detail = json.loads(error.detail).get("error", "")
    except (TypeError, ValueError):
        detail = str(error.detail)
    return detail in {"signaling session is closed or expired", "session closed or expired"}


def _active_allocation_session(api, progress, timeout=120):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return api.call("GET", "/v1/p2p/ice-sessions")
        except APIError as exc:
            if exc.status == 404:
                return None
            if not _cleanup_pending(exc):
                raise
        if time.monotonic() >= deadline:
            raise CloudExError("remote tunnel cleanup is still pending")
        progress.update("Waiting for DevKit cleanup")
        time.sleep(1)


def _stop_forwarder(session):
    pid = session.get("forwarder_pid")
    if not _forwarder_running(pid):
        return
    result = subprocess.run(
        ["sudo", "-n", "kill", "-TERM", str(pid)],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise CloudExError("the local tunnel process could not be stopped")
    deadline = time.monotonic() + 10
    while _forwarder_running(pid):
        if time.monotonic() >= deadline:
            raise CloudExError("local tunnel cleanup is still pending")
        time.sleep(0.1)


def disconnect_session(session, store, progress, authorize=True):
    """Remove one local and remote session, preserving state until confirmed."""
    if authorize:
        authorize_admin()
    api = CloudExAPI.from_session(session)
    progress.update("Stopping the local encrypted tunnel")
    _stop_forwarder(session)
    progress.update("Requesting DevKit tunnel cleanup")
    session_id = session["session_id"]
    remote_session_id = _remote_session_id(session, store)
    recovery_record = store.recovery_path(session_id).exists()
    closed_id = None
    try:
        api.call("DELETE", "/v1/p2p/ice-sessions/" + remote_session_id)
        closed_id = remote_session_id
    except APIError as exc:
        if exc.status != 404 and not _cleanup_pending(exc):
            raise
        # A terminal session can remain allocation-owned while the DevKit
        # finishes cleanup.  The service reports that state as 409 rather
        # than accepting another DELETE, so reconcile through the
        # allocation-scoped endpoint before attempting a new connection.
        active = _active_allocation_session(api, progress)
        if active:
            candidate = active.get("session_id")
            if not isinstance(candidate, str) or len(candidate) != 32:
                raise CloudExError("the active CloudEx session has an invalid identifier")
            recorded_ids = {
                _remote_session_id(recorded, store)
                for recorded in store.list()
                if recorded.get("session_id") != session_id
            }
            if candidate in recorded_ids:
                # This record is stale, but the allocation has since been
                # reconnected and that newer owner is also known locally.
                # Removing it would violate the user's explicit selection.
                store.delete(session_id)
                return "stale"
            try:
                api.call("DELETE", "/v1/p2p/ice-sessions/" + candidate)
                closed_id = candidate
            except APIError as delete_error:
                if delete_error.status != 404:
                    raise
    if closed_id:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                remote = api.call("GET", "/v1/p2p/ice-sessions/" + closed_id)
            except APIError as exc:
                if exc.status == 404:
                    break
                if _cleanup_pending(exc):
                    progress.update("Waiting for DevKit cleanup")
                    time.sleep(1)
                    continue
                raise
            if remote.get("cleanup_confirmed"):
                break
            progress.update("Waiting for DevKit cleanup")
            time.sleep(1)
        else:
            session["status"] = "cleanup-pending"
            if recovery_record:
                store.write_recovery(session)
            else:
                store.write(session)
            raise CloudExError("remote tunnel cleanup is still pending")
    store.delete(session_id)
    return "disconnected"


def inspect_session(session, store):
    """Return a credential-free connection view; remote errors become status."""
    _runtime, _config, report_path, _log = store.runtime_paths(session["session_id"])
    report = _read_report(report_path)
    running = _forwarder_running(session.get("forwarder_pid"))
    remote_status = "unavailable"
    try:
        remote_session_id = _remote_session_id(session, store)
        remote = CloudExAPI.from_session(session).call(
            "GET", "/v1/p2p/ice-sessions/" + remote_session_id
        )
        remote_status = str(remote.get("status", "unknown"))
    except APIError as exc:
        if exc.status == 404:
            remote_status = "not found"
    except CloudExError:
        pass
    local_status = report.get("status", session.get("status", "unknown"))
    connected = running and local_status == "connected" and remote_status.startswith("connected_")
    status = (
        "connected"
        if connected
        else "cleanup pending" if session.get("status") == "cleanup-pending" else "disconnected"
    )
    return {
        "connected": connected,
        "status": status,
        "path": report.get("path", session.get("ice_path", "unknown")),
        "remote_status": remote_status,
    }


def run_benchmark(forwarder, target, duration_seconds):
    try:
        completed = subprocess.run(
            [str(forwarder), "benchmark", "--target", target, "--duration", "{}s".format(duration_seconds)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2 * duration_seconds + 20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CloudExError("benchmark could not run") from exc
    if completed.returncode:
        raise CloudExError("benchmark did not complete")
    try:
        report = json.loads(completed.stdout)
        result = {
            "parallel_streams": int(report["parallel_streams"]),
            "latency_ms": float(report["latency_ms"]),
            "host_to_device_throughput_mbps": float(report["host_to_device_throughput_mbps"]),
            "host_to_device_transferred_bytes": int(report["host_to_device_transferred_bytes"]),
            "device_to_host_throughput_mbps": float(report["device_to_host_throughput_mbps"]),
            "device_to_host_transferred_bytes": int(report["device_to_host_transferred_bytes"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CloudExError("benchmark returned an invalid bidirectional report") from exc
    if result["parallel_streams"] < 2 or any(value <= 0 for value in result.values()):
        raise CloudExError("benchmark returned an invalid bidirectional report")
    return result
