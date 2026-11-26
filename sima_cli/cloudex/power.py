"""Allocation-scoped CloudEx DevKit power cycling with durable, resumable state.

A power cycle is a physical action, so every step that could repeat it is
guarded by a mode-0600 operation record written *before* the request leaves
this host. The record never holds the allocation key: it names the allocation,
the local session whose record already carries the credential, the
client-selected request ID, the service command ID, and the current phase.
"""

import json
import os
import re
import time
import uuid

from .client import APIError, CloudExError, DEFAULT_ROOT

PREPARED = "prepared"
CYCLE_SUBMITTED = "cycle_submitted"
CYCLE_COMPLETED = "cycle_completed"
WAITING_FOR_DEVICE = "waiting_for_device"
RECONNECTING = "reconnecting"
COMPLETED = "completed"
PHASES = (PREPARED, CYCLE_SUBMITTED, CYCLE_COMPLETED, WAITING_FOR_DEVICE, RECONNECTING, COMPLETED)

COMMAND_STATES = {"awaiting_disconnect", "published", "received", "running", "completed", "failed", "rejected"}
TERMINAL_STATES = {"completed", "failed", "rejected"}
POWER_COMMANDS_PATH = "/v1/p2p/power-commands"
# A command ID is interpolated into a signed request path; accept only a
# conservative token so a malformed response cannot change the route.
_COMMAND_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f]+")

COMMAND_TIMEOUT_SECONDS = 300
COMMAND_POLL_SECONDS = 2
PEER_CLEANUP_TIMEOUT_SECONDS = 120
PEER_CLEANUP_POLL_SECONDS = 2
DEVICE_RETURN_TIMEOUT_SECONDS = 600
DEVICE_RETURN_POLL_SECONDS = 5
# The allocation-scoped API reports DevKit connectability only by refusing a
# new ICE session with this error; it is the boot-readiness signal we wait on.
DEVICE_OFFLINE_ERROR = "Allocated device is offline, stale, unhealthy, in maintenance or no longer Development"

PARTIAL_SUCCESS = ("Power cycle completed; CloudEx reconnection is still pending. "
                   "Rerun sima-cli cloudex power-cycle to resume.")

sleep = time.sleep


class PowerCycleRejected(CloudExError):
    """The service or appliance authoritatively refused the cycle."""


class PowerCyclePending(CloudExError):
    """The outcome is not yet known; the saved operation must be resumed."""


def sanitize(value, limit=300):
    """Render service-provided text safely for a terminal."""
    if isinstance(value, dict):
        for key in ("message", "detail", "error", "code"):
            if isinstance(value.get(key), str) and value[key].strip():
                value = value[key]
                break
        else:
            return ""
    if not isinstance(value, str):
        return ""
    return _UNPRINTABLE.sub(" ", value).strip()[:limit]


def api_error_message(error):
    """Extract the sanitized `{"error": ...}` text from an API failure."""
    try:
        detail = json.loads(error.detail)
    except (TypeError, ValueError):
        detail = error.detail
    if isinstance(detail, dict):
        detail = detail.get("error", detail)
    return sanitize(detail) or "HTTP {}".format(error.status)


class OperationStore:
    """One mode-0600 power operation record per allocation."""

    def __init__(self, root=None):
        self.directory = os.path.join(str(root or DEFAULT_ROOT), "operations")

    def _path(self, allocation_id):
        if not _HEX32.fullmatch(allocation_id or ""):
            raise CloudExError("invalid allocation identifier in power-cycle state")
        return os.path.join(self.directory, allocation_id + ".json")

    def write(self, operation):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        path = self._path(operation["allocation_id"])
        temporary = path + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(operation, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)

    def delete(self, allocation_id):
        try:
            os.unlink(self._path(allocation_id))
        except FileNotFoundError:
            pass

    def list(self):
        """Return every pending operation; an unreadable one is an error, not absent.

        Silently skipping a damaged record could let a rerun submit a second
        physical cycle, so the user is told where it is instead.
        """
        try:
            names = sorted(os.listdir(self.directory))
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise CloudExError("CloudEx power-cycle state cannot be read") from exc
        operations = []
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.directory, name)
            try:
                with open(path, encoding="utf-8") as stream:
                    operation = json.load(stream)
                valid = (
                    isinstance(operation, dict)
                    and operation.get("allocation_id") == name[:-5]
                    and _HEX32.fullmatch(operation.get("allocation_id") or "")
                    and _HEX32.fullmatch(operation.get("request_id") or "")
                    and operation.get("phase") in PHASES
                    and (operation["phase"] == PREPARED
                         or _COMMAND_ID.fullmatch(operation.get("command_id") or ""))
                )
            except (OSError, ValueError, TypeError):
                valid = False
            if not valid:
                raise CloudExError(
                    "CloudEx power-cycle state {} is unreadable; inspect it, then remove it or run "
                    "sima-cli -i cloudex power-cycle --abandon".format(path)
                )
            operations.append(operation)
        return operations


def new_operation(allocation_id, session_id, device):
    return {
        "version": 1,
        "allocation_id": allocation_id,
        "session_id": session_id,
        "device": device,
        "request_id": uuid.uuid4().hex,
        "action": "cycle",
        "phase": PREPARED,
        "created_at": int(time.time()),
    }


def advance(store, operation, phase, **fields):
    operation.update(fields, phase=phase, updated_at=int(time.time()))
    store.write(operation)


def submit_cycle(api, store, operation):
    """POST the cycle once per request ID; any retry reuses the same body."""
    try:
        response = api.call("POST", POWER_COMMANDS_PATH,
                            {"request_id": operation["request_id"], "action": "cycle"})
    except APIError as exc:
        if exc.status == 404:
            store.delete(operation["allocation_id"])
            raise PowerCycleRejected("CloudEx power control is not enabled for this allocation") from exc
        if 400 <= exc.status < 500:
            # 4xx is the service refusing before publishing anything.
            store.delete(operation["allocation_id"])
            raise PowerCycleRejected("Power cycle was rejected: " + api_error_message(exc)) from exc
        raise PowerCyclePending("power-cycle request outcome is unknown ({})".format(api_error_message(exc))) from exc
    except CloudExError as exc:
        raise PowerCyclePending("power-cycle request outcome is unknown ({})".format(exc)) from exc
    command_id = response.get("command_id") if isinstance(response, dict) else None
    if not isinstance(command_id, str) or not _COMMAND_ID.fullmatch(command_id):
        raise PowerCyclePending("CloudEx API returned an invalid power command identifier")
    advance(store, operation, CYCLE_SUBMITTED, command_id=command_id)
    return response.get("state")


_STATE_LABELS = {
    "awaiting_disconnect": "Disconnecting the CloudEx peer before the power cycle",
    "published": "Power cycle requested",
    "received": "Power cycle accepted by the appliance",
    "running": "Power cycle in progress",
}


def wait_for_cycle(api, store, operation, progress, timeout=None):
    """Poll the submitted command to its terminal state."""
    path = POWER_COMMANDS_PATH + "/" + operation["command_id"]
    deadline = time.monotonic() + (COMMAND_TIMEOUT_SECONDS if timeout is None else timeout)
    while True:
        try:
            status = api.call("GET", path)
        except APIError as exc:
            if exc.status == 404:
                raise PowerCyclePending(
                    "power command {} is no longer known to CloudEx; its outcome cannot be confirmed. "
                    "Run sima-cli -i cloudex power-cycle --abandon to discard it".format(operation["command_id"])
                ) from exc
            raise PowerCyclePending("power command status is unavailable ({})".format(api_error_message(exc))) from exc
        except CloudExError as exc:
            raise PowerCyclePending("power command status is unavailable ({})".format(exc)) from exc
        state = status.get("state") if isinstance(status, dict) else None
        if state not in COMMAND_STATES:
            raise PowerCyclePending("CloudEx API returned an invalid power command state")
        if state == "completed":
            advance(store, operation, CYCLE_COMPLETED)
            return
        if state in {"failed", "rejected"}:
            # The command is final; keeping it would block every later attempt.
            store.delete(operation["allocation_id"])
            reason = sanitize(status.get("error")) or "no reason was reported"
            label = "rejected" if state == "rejected" else "failed"
            raise PowerCycleRejected("Power cycle {} (command {}): {}".format(label, operation["command_id"], reason))
        progress.update(_STATE_LABELS[state])
        if time.monotonic() >= deadline:
            raise PowerCyclePending("power command {} has not finished yet".format(operation["command_id"]))
        sleep(COMMAND_POLL_SECONDS)


def wait_for_peer_cleanup(api, progress, timeout=None):
    """Wait until the backend has closed the allocation's previous ICE session.

    Returns False when the service still reports a session at the deadline;
    reconnection then reconciles it through the normal connect path.
    """
    deadline = time.monotonic() + (PEER_CLEANUP_TIMEOUT_SECONDS if timeout is None else timeout)
    while True:
        try:
            api.call("GET", "/v1/p2p/ice-sessions")
        except APIError as exc:
            if exc.status == 404:
                return True
            if exc.status != 409:
                raise
        if time.monotonic() >= deadline:
            return False
        progress.update("Waiting for the previous CloudEx connection to close")
        sleep(PEER_CLEANUP_POLL_SECONDS)


def device_offline(error):
    return isinstance(error, APIError) and error.status == 409 and api_error_message(error) == DEVICE_OFFLINE_ERROR
