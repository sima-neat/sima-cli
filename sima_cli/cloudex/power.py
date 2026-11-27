"""Allocation-scoped CloudEx DevKit power cycling with durable, resumable state.

A power cycle is a physical action, so every step that could repeat it is
guarded by a mode-0600 operation record written *before* the request leaves
this host. The record never holds the allocation key: it names the allocation,
the local session whose record already carries the credential, the
client-selected request ID, the service command ID, and the current phase.
"""

import contextlib
import errno
import json
import os
import re
import time
import uuid

from .client import APIError, CleanupPendingError, CloudExError, DEFAULT_ROOT, DeviceNotJoinedError

PREPARED = "prepared"
CYCLE_SUBMITTED = "cycle_submitted"
CYCLE_COMPLETED = "cycle_completed"
WAITING_FOR_DEVICE = "waiting_for_device"
RECONNECTING = "reconnecting"
COMPLETED = "completed"
PHASES = (PREPARED, CYCLE_SUBMITTED, CYCLE_COMPLETED, WAITING_FOR_DEVICE, RECONNECTING, COMPLETED)

COMMAND_STATES = {"awaiting_disconnect", "queued", "received", "running", "completed", "failed", "rejected"}
TERMINAL_STATES = {"completed", "failed", "rejected"}
POWER_COMMANDS_PATH = "/v1/p2p/power-commands"
# A command ID is interpolated into a signed request path; accept only a
# conservative token so a malformed response cannot change the route.
_COMMAND_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_LOCK_HELD = {errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, getattr(errno, "EDEADLK", errno.EAGAIN),
              getattr(errno, "EDEADLOCK", errno.EAGAIN)}
_DIRECTORY_SYNC_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f]+")

COMMAND_TIMEOUT_SECONDS = 300
COMMAND_POLL_SECONDS = 2
PEER_CLEANUP_TIMEOUT_SECONDS = 120
PEER_CLEANUP_POLL_SECONDS = 2
DEVICE_RETURN_TIMEOUT_SECONDS = 600
DEVICE_RETURN_POLL_SECONDS = 5
# The allocation-scoped API reports DevKit connectability only by refusing a
# new ICE session with this error; it is the boot-readiness signal we wait on.
PREVIOUS_CLEANUP_PENDING_ERROR = "previous ICE session cleanup is pending"
# What the service answers once an allocation has ended: a released or expired
# one still authenticates for a day but is refused (409), then is removed (401).
# Other 401s (a skewed clock, a replayed nonce) are transient and not listed.
# The service authenticates before it looks up an already accepted request ID,
# so a 401/403 says nothing about an earlier attempt: one from an earlier run,
# or one the HTTP client abandoned after a timeout or 5xx and retried.
REFUSED_BEFORE_REPLAY_LOOKUP = {401, 403}
# Answered after that lookup, so it is another request's cycle, unless this
# call gave up on an attempt without an answer: that attempt may have committed
# in between and be the cycle "in progress". Other 409s and 429 prove the request was not accepted.
IN_PROGRESS_ERROR = "A power cycle is already in progress"
ALLOCATION_ENDED_ERRORS = {
    (401, "Invalid allocation credential"),
    (409, "Allocation is not ready or has expired"),
}

PARTIAL_SUCCESS = ("Power cycle completed; CloudEx reconnection is still pending. "
                   "Rerun sima-cli cloudex power-cycle to resume.")

sleep = time.sleep


class PowerCycleRejected(CloudExError):
    """The service or appliance authoritatively refused the cycle."""


class AllocationEnded(PowerCycleRejected):
    """The allocation is gone; its saved power cycle can never be resumed."""


class PowerCyclePending(CloudExError):
    """The outcome is not yet known; the saved operation must be resumed."""


class OperationExists(CloudExError):
    """Another invocation already recorded a power cycle for this allocation."""

    def __init__(self, allocation_id):
        self.allocation_id = allocation_id
        super().__init__("a power cycle for this DevKit is already pending or in progress")


class OperationBusy(CloudExError):
    """Another sima-cli run currently drives this allocation's power cycle."""


class UnreadableOperation(CloudExError):
    """A saved record cannot be trusted; only explicit abandonment clears it."""

    def __init__(self, path):
        self.path = path
        super().__init__(
            "CloudEx power-cycle state {} is unreadable; inspect it, then remove it or run "
            "sima-cli -i cloudex power-cycle --abandon".format(path)
        )


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

    def _stage(self, operation):
        """Write a fully synced private copy beside its final name."""
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        path = self._path(operation["allocation_id"])
        # A unique name keeps concurrent writers from sharing one temporary file.
        temporary = "{}.{}.tmp".format(path, uuid.uuid4().hex)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(operation, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary, path

    def _sync_directory(self):
        """Make the new directory entry itself survive a crash, not only its contents."""
        if os.name == "nt":
            return
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        except OSError as exc:
            # Some network and FUSE filesystems cannot sync a directory; the
            # renamed entry is still the best durability they offer.
            if exc.errno not in _DIRECTORY_SYNC_UNSUPPORTED:
                raise CloudExError("CloudEx power-cycle state could not be synced to disk") from exc
        finally:
            os.close(descriptor)

    def create(self, operation):
        """Record a new operation only if none exists for its allocation.

        Concurrent invocations would otherwise each choose a request ID,
        overwrite one another and submit separate physical cycles.
        """
        temporary, path = self._stage(operation)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise OperationExists(operation["allocation_id"]) from None
        except OSError:
            # No hard links on this filesystem (exFAT, some SMB/FUSE mounts):
            # create the final name exclusively instead. A crash mid-write
            # leaves an unreadable record, which blocks new cycles until abandoned.
            self._create_in_place(operation, path)
        finally:
            os.unlink(temporary)
        self._sync_directory()

    @staticmethod
    def _create_in_place(operation, path):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise OperationExists(operation["allocation_id"]) from None
        except OSError as exc:
            raise CloudExError("CloudEx power-cycle state could not be created") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(operation, stream, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            # This run created the name exclusively, so removing it is safe and
            # avoids leaving an unreadable record that blocks later runs.
            try:
                os.unlink(path)
            except OSError:
                pass
            raise CloudExError("CloudEx power-cycle state could not be created") from exc

    def write(self, operation):
        temporary, path = self._stage(operation)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        self._sync_directory()

    def delete(self, allocation_id):
        try:
            os.unlink(self._path(allocation_id))
        except FileNotFoundError:
            pass

    def discard(self, path):
        """Remove an unreadable record named by UnreadableOperation."""
        if os.path.dirname(path) != self.directory or not path.endswith(".json"):
            raise CloudExError("refusing to remove a file outside the power-cycle state directory")
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CloudExError("power-cycle state {} could not be removed".format(path)) from exc

    @contextlib.contextmanager
    def lock(self, allocation_id):
        """Hold an exclusive, non-blocking per-allocation lock for one run.

        Yields False when the filesystem cannot lock at all, so the caller can
        warn that concurrent runs are no longer serialized.

        The OS releases it when the process exits, so a crash never leaves the
        allocation locked. The lock file is never removed, which keeps two runs
        from locking different inodes under the same name.
        """
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        path = self._path(allocation_id)[:-len(".json")] + ".lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = True
        try:
            try:
                _try_lock(descriptor)
            except OSError as exc:
                if exc.errno in _LOCK_HELD:
                    raise OperationBusy(
                        "another sima-cli run is already handling the power cycle for this DevKit; "
                        "wait for it to finish"
                    ) from None
                # The filesystem cannot lock (NFS without lockd, some FUSE/SMB
                # mounts). Refusing would strand resume and --abandon; exclusive
                # record creation still prevents a second fresh cycle.
                locked = False
            yield locked
        finally:
            os.close(descriptor)

    def load(self, allocation_id):
        """Return this allocation's pending operation, or None; other records are not read."""
        try:
            return self._read(self._path(allocation_id))
        except FileNotFoundError:
            return None

    def _read(self, path):
        name = os.path.basename(path)
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
        except FileNotFoundError:
            if os.path.lexists(path):
                valid = False  # e.g. a dangling symlink: present, but not a record
            else:
                raise
        except (OSError, ValueError, TypeError):
            valid = False
        if not valid:
            raise UnreadableOperation(path)
        return operation

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
            try:
                operations.append(self._read(os.path.join(self.directory, name)))
            except FileNotFoundError:
                continue  # removed by a concurrent run since the listing
        return operations


def valid_allocation_id(value):
    return isinstance(value, str) and bool(_HEX32.fullmatch(value))


def _try_lock(descriptor):
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _timestamp(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _submitted_at(operation):
    # Older records lack submitted_at; while in cycle_submitted, updated_at is that moment.
    for key in ("submitted_at", "updated_at", "created_at"):
        if _timestamp(operation.get(key)):
            return operation[key]
    return time.time()


def cycle_completed_at(operation):
    """When the relay cycle finished; older records fall back to their last update.

    Kept with sub-second precision, like a tunnel's connected_at, so a tunnel
    that came up within the same second as the cycle is not taken for older.
    """
    for key in ("cycle_completed_at", "updated_at"):
        value = operation.get(key)
        if _timestamp(value):
            # A whole second comes from an older record and was rounded down:
            # round up instead, so an uncertain tunnel is stopped, not kept.
            return value + 1 if isinstance(value, int) else value
    return time.time()


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
    # A rerun of a request that may already have left this host in an earlier
    # run: that attempt can have been accepted even though no response arrived.
    retry = _timestamp(operation.get("submitted_at"))
    if not retry:
        # Persist the first attempt before it leaves this host. A retry after a
        # lost response keeps it: the cycle cannot predate the first POST, and a
        # later timestamp would make cleanup stop a tunnel opened after the cycle.
        advance(store, operation, PREPARED, submitted_at=time.time())
    try:
        response = api.call("POST", POWER_COMMANDS_PATH,
                            {"request_id": operation["request_id"], "action": "cycle"})
    except APIError as exc:
        # A missing route is configuration, not a lost reply; it is final unless
        # a request may already have been accepted before power control was disabled.
        # Only an attempt this call gave up on without an answer can have been
        # accepted unseen; the HTTP client also retries definite 409/429 replies.
        call_retried = getattr(api, "last_uncertain", False) is True
        if (exc.status == 409 and api_error_message(exc) == IN_PROGRESS_ERROR and call_retried):
            raise PowerCyclePending(
                "a power cycle is already in progress on this DevKit; it may be this request (an earlier "
                "attempt timed out) or another one. A rerun sends this same request again, which cycles the "
                "DevKit once more if the current cycle was someone else's. If you do not want another cycle, "
                "sima-cli -i cloudex power-cycle --abandon discards it"
            ) from exc
        maybe_accepted = retry or call_retried
        if (exc.status in REFUSED_BEFORE_REPLAY_LOOKUP and (maybe_accepted or allocation_ended(exc))) \
                or (retry and exc.status == 404):
            if allocation_ended(exc):
                # Nothing is ever sent once the allocation is gone.
                store.delete(operation["allocation_id"])
                raise AllocationEnded(
                    "The DevKit's allocation has ended; no power cycle is sent after an allocation ends. "
                    "The saved power-cycle state was removed."
                ) from exc
            # An earlier attempt may still have been accepted: keep its request ID,
            # which the service answers with that command if it exists.
            raise PowerCyclePending(
                "the service refused the request ({}), but an earlier attempt may have been accepted, so "
                "the same request will be retried. If the DevKit was not power-cycled and you no longer "
                "want it to be, sima-cli -i cloudex power-cycle --abandon discards it".format(api_error_message(exc))
            ) from exc
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
    return response.get("status")


_STATE_LABELS = {
    "awaiting_disconnect": "Disconnecting the CloudEx peer before the power cycle",
    "queued": "Power cycle requested",
    "received": "Power cycle accepted by the appliance",
    "running": "Power cycle in progress",
}


def wait_for_cycle(api, store, operation, progress, timeout=None):
    """Poll the submitted command to its terminal state."""
    path = POWER_COMMANDS_PATH + "/" + operation["command_id"]
    deadline = time.monotonic() + (COMMAND_TIMEOUT_SECONDS if timeout is None else timeout)
    # Seen in progress in this run: the cutoff is the last reply that still said
    # "running" (the relay may already be cutting power then, and the cycle ends
    # after it), else the "completed" reply itself; an earlier state such as
    # "queued" precedes the cut, so a tunnel made after it may be dead. Seen
    # completed on the first poll (e.g. on resume): it may have finished long
    # ago, so only the submission time is a safe bound.
    watched_in_progress = False
    last_running = None
    while True:
        try:
            status = api.call("GET", path)
        except APIError as exc:
            if allocation_ended(exc):
                # The service never sends a cycle once the allocation is gone, so
                # dropping the record cannot lead to a second one.
                store.delete(operation["allocation_id"])
                raise AllocationEnded(
                    "The DevKit's allocation has ended, so power command {} can no longer be checked; "
                    "no power cycle is sent after an allocation ends. The saved power-cycle state was "
                    "removed.".format(operation["command_id"])
                ) from exc
            if exc.status == 404:
                raise PowerCyclePending(
                    "power command {} is no longer known to CloudEx; its outcome cannot be confirmed. "
                    "Run sima-cli -i cloudex power-cycle --abandon to discard it".format(operation["command_id"])
                ) from exc
            raise PowerCyclePending("power command status is unavailable ({})".format(api_error_message(exc))) from exc
        except CloudExError as exc:
            raise PowerCyclePending("power command status is unavailable ({})".format(exc)) from exc
        state = status.get("status") if isinstance(status, dict) else None
        if state not in COMMAND_STATES:
            raise PowerCyclePending("CloudEx API returned an invalid power command state")
        if state == "completed":
            if not watched_in_progress:
                completed_by = _submitted_at(operation)
            else:
                completed_by = last_running if last_running is not None else time.time()
            advance(store, operation, CYCLE_COMPLETED, cycle_completed_at=completed_by,
                    completion_observed=watched_in_progress)
            return
        if state in {"failed", "rejected"}:
            # The command is final; keeping it would block every later attempt.
            store.delete(operation["allocation_id"])
            reason = sanitize(status.get("error")) or "no reason was reported"
            label = "rejected" if state == "rejected" else "failed"
            raise PowerCycleRejected("Power cycle {} (command {}): {}".format(label, operation["command_id"], reason))
        watched_in_progress = True
        if state == "running":
            last_running = time.time()
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


def allocation_ended(error):
    """Whether a failure (or what it wraps) says the allocation itself has ended."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, APIError) and (error.status, api_error_message(error)) in ALLOCATION_ENDED_ERRORS:
            return True
        error = error.__cause__
    return False


def device_not_ready(error):
    """Whether a failed reconnect means the DevKit has not come back yet.

    `/v1/p2p/ice-sessions` never checks the DevKit before accepting a session.
    A DevKit that is still booting shows up as a session it never joins, or as
    a previous session's cleanup still waiting on the appliance (seen by the
    client while reconciling, or by the service refusing a new session).
    connect() may wrap these in a cleanup error, so follow the cause chain.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (DeviceNotJoinedError, CleanupPendingError)):
            return True
        if (isinstance(error, APIError) and error.status == 409
                and api_error_message(error) == PREVIOUS_CLEANUP_PENDING_ERROR):
            return True
        error = error.__cause__
    return False
