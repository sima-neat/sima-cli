import base64
import contextlib
import json
import os
import stat
from unittest.mock import Mock, patch

import pytest
import requests
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.cloudex import commands, power
from sima_cli.cloudex.client import (APIError, CleanupPendingError, CloudExAPI, CloudExError,
                                     DeviceNotJoinedError, DirectHandshakeError, SessionStore)

ALLOCATION = "a" * 32
OTHER_ALLOCATION = "c" * 32
SECRET = "S3cr3t-allocation-secret-value-0123456789"
COMMAND = "p2p-power-" + "0123abcd" * 4


def _record(session_id="b" * 32, allocation_id=ALLOCATION, device="modalix-123"):
    return {
        "version": 1,
        "session_id": session_id,
        "environment": "vulcan-staging",
        "api_url": "https://p2p.example.test",
        "requester": "user-1",
        "allocation_id": allocation_id,
        "api_secret": SECRET,
        "device": device,
        "target": "10.252.4.2",
        "status": "connected",
        "expires_at": 2_000_000_000,
    }


def _key(allocation_id=ALLOCATION):
    profile = {
        "environment": "vulcan-staging",
        "api_url": "https://p2p.example.test",
        "requester": "user-1",
        "allocation_id": allocation_id,
        "allocation_secret": SECRET,
    }
    return base64.b64encode(json.dumps(profile).encode()).decode()


def server_command(status, **fields):
    """A power command exactly as Vulcan's public() serializes it."""
    command = {"command_id": COMMAND, "allocation_id": ALLOCATION, "action": "cycle", "status": status,
               "terminal": status in {"completed", "failed", "rejected"}, "disconnect_forced": False,
               "error": None, "created_at": 1_000, "updated_at": 1_000}
    command.update(fields)
    return command


class FakeAPI:
    """In-memory allocation-scoped CloudEx P2P API."""

    def __init__(self):
        self.calls = []
        self.states = ["running", "completed"]
        self.post_error = None
        self.on_post = None
        self.get_error = None
        self.ice_sessions = [APIError(404, '{"error":"not found"}')]
        self.profiles = []

    def from_profile(self, profile):
        self.profiles.append(profile)
        return self

    def call(self, method, path, data=None, deadline=None):
        self.calls.append((method, path, data))
        if (method, path) == ("POST", power.POWER_COMMANDS_PATH):
            if self.on_post:
                self.on_post(data)
            if self.post_error:
                raise self.post_error
            return server_command("awaiting_disconnect")
        if (method, path) == ("GET", power.POWER_COMMANDS_PATH + "/" + COMMAND):
            if self.get_error:
                raise self.get_error
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            if isinstance(state, BaseException):
                raise state
            return state if isinstance(state, dict) else server_command(state)
        if (method, path) == ("GET", "/v1/p2p/ice-sessions"):
            result = self.ice_sessions.pop(0) if len(self.ice_sessions) > 1 else self.ice_sessions[0]
            if isinstance(result, BaseException):
                raise result
            return result
        raise AssertionError("unexpected call {} {}".format(method, path))

    def posts(self):
        return [c for c in self.calls if c[:2] == ("POST", power.POWER_COMMANDS_PATH)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    api = FakeAPI()
    monkeypatch.setattr(commands, "_store", lambda: store)
    monkeypatch.setattr(commands, "CloudExAPI", api)
    monkeypatch.setattr(commands, "authorize_admin", Mock())
    monkeypatch.setattr(commands, "disconnect_requires_admin", Mock(return_value=False))
    monkeypatch.setattr(commands, "stop_local_forwarder", Mock())
    monkeypatch.setattr(commands, "_interactive_session_selection_available", lambda: False)
    monkeypatch.setattr(power, "sleep", Mock())

    def fake_connect(profile, session_store, progress, **_kwargs):
        # Mirrors connect(): stale records for the allocation are reconciled away.
        for old in session_store.list():
            if old["allocation_id"] == profile["allocation_id"]:
                session_store.delete(old["session_id"])
        new = dict(_record(session_id="d" * 32, allocation_id=profile["allocation_id"]), target="10.252.4.9")
        session_store.write(new)
        return new

    connect = Mock(side_effect=fake_connect)
    monkeypatch.setattr(commands, "connect", connect)

    class Env:
        pass

    result = Env()
    result.store, result.api, result.connect = store, api, connect
    result.operations = power.OperationStore(tmp_path)
    result.op_path = tmp_path / "operations" / (ALLOCATION + ".json")
    return result


def invoke(arguments, **kwargs):
    with patch("sima_cli.cli._initialize_main_context"):
        return CliRunner().invoke(main, ["-i", "cloudex", "power-cycle"] + arguments, **kwargs)


def _assert_no_secret(env, result):
    assert SECRET not in result.output
    assert _key() not in result.output
    if env.op_path.exists():
        assert SECRET not in env.op_path.read_text()


def _saved_operation(env, phase, session_id="b" * 32):
    operation = power.new_operation(ALLOCATION, session_id, "modalix-123")
    operation["phase"] = phase
    if phase != power.PREPARED:
        operation["command_id"] = COMMAND
    env.operations.write(operation)
    return operation


# Command surface ---------------------------------------------------------


def test_power_cycle_is_a_direct_hidden_cloudex_command():
    with patch("sima_cli.cli._initialize_main_context"):
        group = CliRunner().invoke(main, ["-i", "cloudex", "--help"])
        root = CliRunner().invoke(main, ["--help"])
        control = CliRunner().invoke(main, ["-i", "cloudex", "control", "--help"])
    assert "power-cycle" in group.output
    assert "cloudex" not in root.output
    assert control.exit_code == 2


@pytest.mark.parametrize("option", ["--key", "--user", "--target", "--cluster", "--action", "--topic"])
def test_power_cycle_rejects_raw_key_and_routing_overrides(option):
    result = invoke([option, "value"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_power_cycle_requires_internal_cloudex():
    with patch("sima_cli.cli._initialize_main_context"):
        result = CliRunner().invoke(main, ["cloudex", "power-cycle"])
    assert result.exit_code != 0
    assert "sima-cli -i cloudex" in result.output


# Selection and confirmation ---------------------------------------------


def test_only_recorded_session_is_confirmed_cycled_and_reconnected(env, monkeypatch):
    env.store.write(_record())
    monkeypatch.setattr(commands, "_interactive_session_selection_available", lambda: True)

    result = invoke([], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Power-cycle modalix-123? Power will be interrupted for 5 seconds." in result.output
    assert result.output.count("Power-cycle modalix-123?") == 1
    (_method, _path, body), = env.api.posts()
    assert set(body) == {"request_id", "action"} and body["action"] == "cycle"
    assert len(body["request_id"]) == 32 and body["request_id"] == body["request_id"].lower()
    int(body["request_id"], 16)
    assert "Relay cycle completed" in result.output
    assert "modalix-123 power-cycled and CloudEx reconnected" in result.output
    assert "ssh sima@10.252.4.9" in result.output
    assert env.api.profiles[0]["allocation_secret"] == SECRET
    commands.stop_local_forwarder.assert_called_once()
    assert not env.op_path.exists()
    _assert_no_secret(env, result)


def test_declined_confirmation_sends_nothing_and_saves_nothing(env, monkeypatch):
    env.store.write(_record())
    monkeypatch.setattr(commands, "_interactive_session_selection_available", lambda: True)

    result = invoke([], input="n\n")

    assert result.exit_code == 0
    assert "Power cycle cancelled" in result.output
    assert env.api.posts() == []
    assert not env.op_path.exists()


def test_noninteractive_requires_yes_before_any_request(env):
    env.store.write(_record())

    result = invoke([])

    assert result.exit_code == 2
    assert "--yes is required" in result.output
    assert env.api.calls == []
    assert not env.op_path.exists()


def test_yes_skips_confirmation_noninteractively(env):
    env.store.write(_record())

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert "Power-cycle modalix-123?" not in result.output
    assert len(env.api.posts()) == 1


def test_multiple_sessions_noninteractive_require_session(env):
    env.store.write(_record("b" * 32, ALLOCATION, "modalix-123"))
    env.store.write(_record("e" * 32, OTHER_ALLOCATION, "modalix-456"))

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert "use --session ID" in result.output
    assert env.api.calls == []


def test_session_prefix_selects_the_matching_allocation(env):
    env.store.write(_record("b" * 32, ALLOCATION, "modalix-123"))
    env.store.write(_record("e" * 32, OTHER_ALLOCATION, "modalix-456"))

    result = invoke(["--session", "eee", "--yes", "--no-reconnect"])

    assert result.exit_code == 0, result.output
    assert env.api.profiles[0]["allocation_id"] == OTHER_ALLOCATION
    assert "modalix-456" in result.output


def test_unknown_session_prefix_fails_without_prompting_for_a_key(env):
    result = invoke(["--session", "zzz", "--yes"])

    assert result.exit_code == 1
    assert "No CloudEx session matches 'zzz'" in result.output
    assert env.api.calls == []


def test_multiple_sessions_interactive_use_the_single_device_picker(env, monkeypatch):
    env.store.write(_record("b" * 32, ALLOCATION, "modalix-123"))
    env.store.write(_record("e" * 32, OTHER_ALLOCATION, "modalix-456"))
    monkeypatch.setattr(commands, "_interactive_session_selection_available", lambda: True)
    chooser = Mock(side_effect=lambda sessions: [s for s in sessions if s["device"] == "modalix-456"])
    monkeypatch.setattr(commands, "_choose_session", chooser)

    result = invoke(["--no-reconnect"], input="y\n")

    assert result.exit_code == 0, result.output
    chooser.assert_called_once()
    assert "Power-cycle modalix-456?" in result.output
    assert env.api.profiles[0]["allocation_id"] == OTHER_ALLOCATION


# Secure key handling ------------------------------------------------------


def test_no_session_prompts_for_hidden_key(env, monkeypatch):
    monkeypatch.setattr(commands, "_interactive_session_selection_available", lambda: True)

    result = invoke(["--no-reconnect"], input=_key() + "\ny\n")

    assert result.exit_code == 0, result.output
    assert "CloudEx allocation key" in result.output
    assert env.api.profiles[0]["allocation_id"] == ALLOCATION
    _assert_no_secret(env, result)


def test_no_session_noninteractive_requires_key_option(env):
    result = invoke(["--yes"])

    assert result.exit_code == 2
    assert "--key-file or --key-stdin" in result.output
    assert env.api.calls == []


def test_key_stdin_requires_yes(env):
    result = invoke(["--key-stdin"], input=_key() + "\n")

    assert result.exit_code == 2
    assert "--key-stdin requires --yes" in result.output
    assert env.api.calls == []


def test_key_stdin_with_yes_cycles_without_echoing_the_key(env):
    result = invoke(["--key-stdin", "--yes"], input=_key() + "\n")

    assert result.exit_code == 0, result.output
    assert len(env.api.posts()) == 1
    _assert_no_secret(env, result)


def test_key_file_must_be_mode_0600(env, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text(_key())
    key_file.chmod(0o644)

    result = invoke(["--key-file", str(key_file), "--yes"])

    assert result.exit_code == 1
    assert "mode 0600" in result.output
    assert env.api.calls == []


def test_protected_key_file_is_accepted(env, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text(_key())
    key_file.chmod(0o600)

    result = invoke(["--key-file", str(key_file), "--yes", "--no-reconnect"])

    assert result.exit_code == 0, result.output
    assert len(env.api.posts()) == 1
    _assert_no_secret(env, result)


def test_session_and_key_options_are_exclusive(env, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text(_key())
    key_file.chmod(0o600)

    result = invoke(["--session", "bbb", "--key-file", str(key_file), "--yes"])

    assert result.exit_code == 2


# Durable state ------------------------------------------------------------


def test_operation_is_persisted_with_mode_0600_before_post(env):
    env.store.write(_record())
    seen = {}

    def inspect_record(body):
        seen["mode"] = stat.S_IMODE(os.stat(env.op_path).st_mode)
        seen["text"] = env.op_path.read_text()
        seen["record"] = json.loads(seen["text"])
        seen["body"] = body

    env.api.on_post = inspect_record

    result = invoke(["--yes", "--no-reconnect"])

    assert result.exit_code == 0, result.output
    assert seen["mode"] == 0o600
    assert seen["record"]["phase"] == power.PREPARED
    assert seen["record"]["request_id"] == seen["body"]["request_id"]
    assert seen["record"]["allocation_id"] == ALLOCATION
    assert seen["record"]["session_id"] == "b" * 32
    assert SECRET not in seen["text"]
    assert "api_secret" not in seen["text"] and "allocation_secret" not in seen["text"]


def test_ambiguous_post_keeps_prepared_record_and_rerun_reuses_request_id(env):
    env.store.write(_record())
    env.api.post_error = CloudExError("CloudEx API is unavailable")

    first = invoke(["--yes"])

    assert first.exit_code == 1
    assert "outcome is unknown" in first.output and "rerun" in first.output.lower()
    saved = json.loads(env.op_path.read_text())
    assert saved["phase"] == power.PREPARED

    env.api.post_error = None
    second = invoke(["--yes"])

    assert second.exit_code == 0, second.output
    first_body, second_body = [c[2] for c in env.api.posts()]
    assert first_body == second_body == {"request_id": saved["request_id"], "action": "cycle"}


def test_prepared_resume_requires_confirmation(env):
    _saved_operation(env, power.PREPARED)
    env.store.write(_record())

    result = invoke([])

    assert result.exit_code == 2
    assert "--yes is required" in result.output
    assert env.api.calls == []


@pytest.mark.parametrize("status,detail", [
    (404, '{"error":"route not found"}'),
    (409, '{"error":"allocation is not ready"}'),
    (429, '{"error":"wait 60 seconds between power cycles"}'),
])
def test_authoritative_rejection_clears_record(env, status, detail):
    env.store.write(_record())
    env.api.post_error = APIError(status, detail)

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert not env.op_path.exists()
    if status == 404:
        assert "not enabled" in result.output
    else:
        assert json.loads(detail)["error"] in result.output
    env.connect.assert_not_called()


@pytest.mark.parametrize("phase", [
    power.CYCLE_SUBMITTED, power.CYCLE_COMPLETED, power.WAITING_FOR_DEVICE, power.RECONNECTING,
])
def test_rerun_in_every_post_submit_phase_never_cycles_again(env, phase):
    _saved_operation(env, phase)
    env.store.write(_record())

    result = invoke([])  # non-interactive, no --yes: no confirmation is due

    assert result.exit_code == 0, result.output
    assert env.api.posts() == []
    assert "no new cycle will be sent" in result.output
    polled = [c for c in env.api.calls if c[1].startswith(power.POWER_COMMANDS_PATH + "/")]
    assert bool(polled) == (phase == power.CYCLE_SUBMITTED)
    env.connect.assert_called_once()
    assert not env.op_path.exists()


def test_completed_phase_rerun_only_clears_record(env):
    _saved_operation(env, power.COMPLETED)
    env.store.write(_record())

    result = invoke([])

    assert result.exit_code == 0, result.output
    assert env.api.calls == []
    env.connect.assert_not_called()
    assert not env.op_path.exists()


def test_poll_timeout_keeps_record_and_rerun_resumes_polling(env, monkeypatch):
    env.store.write(_record())
    env.api.states = ["running"]
    monkeypatch.setattr(power, "COMMAND_TIMEOUT_SECONDS", 0)

    first = invoke(["--yes"])

    assert first.exit_code == 1
    assert "has not finished yet" in first.output
    assert json.loads(env.op_path.read_text())["phase"] == power.CYCLE_SUBMITTED

    env.api.states = ["completed"]
    second = invoke([])

    assert second.exit_code == 0, second.output
    assert len(env.api.posts()) == 1


def test_ctrl_c_keeps_record_for_resume(env):
    env.store.write(_record())
    env.api.states = [KeyboardInterrupt()]

    result = invoke(["--yes"])

    assert result.exit_code == 130
    assert "Rerun sima-cli -i cloudex power-cycle to resume" in result.output
    assert json.loads(env.op_path.read_text())["phase"] == power.CYCLE_SUBMITTED


def test_failed_command_reports_sanitized_reason_and_clears_record(env):
    env.store.write(_record())
    env.api.states = [server_command("failed", error="relay\x1b[31m unavailable\nnow")]

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert "Power cycle failed (command {}): relay [31m unavailable now".format(COMMAND) in result.output
    assert "\x1b" not in result.output
    assert not env.op_path.exists()
    env.connect.assert_not_called()


def test_unknown_command_on_resume_is_kept_and_points_to_abandon(env):
    _saved_operation(env, power.CYCLE_SUBMITTED)
    env.store.write(_record())
    env.api.get_error = APIError(404, '{"error":"not found"}')

    result = invoke([])

    assert result.exit_code == 1
    assert "--abandon" in result.output
    assert env.op_path.exists()
    assert env.api.posts() == []


def test_abandon_discards_record_without_sending(env):
    _saved_operation(env, power.CYCLE_SUBMITTED)
    env.store.write(_record())

    result = invoke(["--abandon", "--yes"])

    assert result.exit_code == 0, result.output
    assert not env.op_path.exists()
    assert env.api.calls == []


def test_unreadable_record_blocks_new_cycles(env):
    env.store.write(_record())
    env.op_path.parent.mkdir(parents=True, exist_ok=True)
    env.op_path.write_text("{not json")

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert "unreadable" in result.output
    assert env.api.calls == []


def test_resume_without_session_record_requires_matching_key(env, tmp_path):
    _saved_operation(env, power.CYCLE_COMPLETED)
    other = tmp_path / "other"
    other.write_text(_key(OTHER_ALLOCATION))
    other.chmod(0o600)
    mine = tmp_path / "mine"
    mine.write_text(_key())
    mine.chmod(0o600)

    missing = invoke([])
    assert missing.exit_code == 2 and "--key-file or --key-stdin" in missing.output

    # A key for another allocation selects no pending operation; it starts a
    # fresh, separately confirmed cycle for that allocation instead.
    mismatched = invoke(["--key-file", str(other)])
    assert mismatched.exit_code == 2 and "--yes is required" in mismatched.output

    resumed = invoke(["--key-file", str(mine)])
    assert resumed.exit_code == 0, resumed.output
    assert env.api.posts() == []


# Reconnection --------------------------------------------------------------


def test_no_reconnect_stops_after_power_event(env):
    env.store.write(_record())

    result = invoke(["--yes", "--no-reconnect"])

    assert result.exit_code == 0, result.output
    assert "Relay cycle completed" in result.output
    assert "may still be booting" in result.output and "stale" in result.output
    assert "reconnected" not in result.output
    env.connect.assert_not_called()
    commands.stop_local_forwarder.assert_not_called()
    assert not env.op_path.exists()
    assert env.store.list(), "the allocation's record is never released by power-cycle"


def _not_joined():
    return DeviceNotJoinedError("CloudEx connection setup deadline expired")


def test_waits_while_device_has_not_joined_then_reconnects(env):
    env.store.write(_record())
    offline = _not_joined()
    successful = env.connect.side_effect
    attempts = []

    def flaky(profile, store, progress, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            for old in store.list():
                store.delete(old["session_id"])
            raise offline
        return successful(profile, store, progress, **kwargs)

    env.connect.side_effect = flaky

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert len(attempts) == 3
    assert "power-cycled and CloudEx reconnected" in result.output
    assert power.sleep.call_count >= 2


def _connect_failing_with(env, errors):
    successful = env.connect.side_effect
    attempts = []

    def flaky(profile, store, progress, **kwargs):
        attempts.append(1)
        if errors:
            for old in store.list():
                store.delete(old["session_id"])
            raise errors.pop(0)
        return successful(profile, store, progress, **kwargs)

    env.connect.side_effect = flaky
    return attempts


def test_not_joined_wrapped_in_a_cleanup_error_is_still_waited_out(env):
    env.store.write(_record())
    try:
        try:
            raise _not_joined()
        except DeviceNotJoinedError as inner:
            raise CloudExError("Connection failed and cleanup is pending: remote tunnel cleanup is "
                               "still pending. Run cloudex disconnect again.") from inner
    except CloudExError as wrapped:
        attempts = _connect_failing_with(env, [wrapped])

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert len(attempts) == 2


def test_previous_ice_cleanup_pending_is_waited_out(env):
    env.store.write(_record())
    pending = APIError(409, json.dumps({"error": power.PREVIOUS_CLEANUP_PENDING_ERROR}))
    attempts = _connect_failing_with(env, [pending])

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert len(attempts) == 2


def test_retry_waits_for_the_failed_attempts_ice_session_to_clear(env):
    env.store.write(_record())
    attempts = _connect_failing_with(env, [_not_joined()])
    held = APIError(409, json.dumps({"error": "signaling session is closed or expired"}))
    gone = APIError(404, json.dumps({"error": "ICE session not found"}))
    # First 404 belongs to the post-cycle peer wait; the retry then sees the lock clear.
    env.api.ice_sessions = [gone, held, held, gone]

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert len(attempts) == 2
    ice_checks = [c for c in env.api.calls if c[:2] == ("GET", "/v1/p2p/ice-sessions")]
    assert len(ice_checks) == 4


def test_previous_tunnel_cleanup_pending_while_reconciling_is_waited_out(env):
    # connect() reconciles the pre-cycle tunnel first; a booting DevKit cannot confirm its stop.
    env.store.write(_record())
    attempts = _connect_failing_with(env, [CleanupPendingError("remote tunnel cleanup is still pending")])

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert len(attempts) == 2


def test_failed_cleanup_probe_reports_the_reconnect_failure(env):
    env.store.write(_record())
    _connect_failing_with(env, [_not_joined()])
    gone = APIError(404, json.dumps({"error": "ICE session not found"}))
    env.api.ice_sessions = [gone, APIError(503, json.dumps({"error": "busy"}))]

    result = invoke(["--yes"])

    assert result.exit_code == 1
    output = result.output.replace("\n", " ")
    assert power.PARTIAL_SUCCESS in output
    assert "Last error: CloudEx connection setup deadline expired" in output
    assert json.loads(env.op_path.read_text())["phase"] == power.RECONNECTING


def test_device_that_joined_and_then_failed_is_not_retried(env):
    env.store.write(_record())
    attempts = _connect_failing_with(env, [DirectHandshakeError("ICE connection state Failed")])

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert len(attempts) == 1
    assert power.PARTIAL_SUCCESS in result.output.replace("\n", " ")
    assert json.loads(env.op_path.read_text())["phase"] == power.RECONNECTING


def test_legacy_device_offline_conflict_is_not_a_readiness_signal():
    offline = APIError(409, json.dumps({"error": "Allocated device is offline, stale, unhealthy, "
                                                 "in maintenance or no longer Development"}))
    assert not power.device_not_ready(offline)
    assert power.device_not_ready(_not_joined())
    assert power.device_not_ready(CleanupPendingError("remote tunnel cleanup is still pending"))
    assert not power.device_not_ready(DirectHandshakeError("deadline expired"))


def test_reconnect_timeout_is_partial_success_and_resumable(env, monkeypatch):
    env.store.write(_record())
    offline = _not_joined()
    successful = env.connect.side_effect

    def offline_connect(profile, store, progress, **kwargs):
        for old in store.list():
            store.delete(old["session_id"])
        raise offline

    env.connect.side_effect = offline_connect
    monkeypatch.setattr(power, "DEVICE_RETURN_TIMEOUT_SECONDS", 0)

    first = invoke(["--yes"])

    assert first.exit_code == 1
    assert "Relay cycle completed" in first.output
    assert power.PARTIAL_SUCCESS in first.output.replace("\n", " ")
    assert json.loads(env.op_path.read_text())["phase"] == power.RECONNECTING
    # The stale record (and its credential) survives connect()'s reconciliation.
    assert [s["session_id"] for s in env.store.list()] == ["b" * 32]
    _assert_no_secret(env, first)

    env.connect.side_effect = successful
    second = invoke([])

    assert second.exit_code == 0, second.output
    assert len(env.api.posts()) == 1
    assert "Power-cycle modalix-123?" not in second.output
    assert not env.op_path.exists()


def test_local_cleanup_failure_is_partial_success(env):
    env.store.write(_record())
    commands.stop_local_forwarder.side_effect = CloudExError("the local tunnel process could not be stopped")

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert power.PARTIAL_SUCCESS in result.output.replace("\n", " ")
    assert json.loads(env.op_path.read_text())["phase"] == power.CYCLE_COMPLETED
    env.connect.assert_not_called()


def test_waits_for_backend_peer_cleanup_before_reconnect(env):
    env.store.write(_record())
    env.api.ice_sessions = [{"session_id": "f" * 32, "status": "closing"},
                            APIError(409, '{"error":"session closed or expired"}'),
                            APIError(404, '{"error":"not found"}')]

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    ice_checks = [c for c in env.api.calls if c[:2] == ("GET", "/v1/p2p/ice-sessions")]
    assert len(ice_checks) == 3
    assert "Previous CloudEx connection closed" in result.output


# Signed transport -----------------------------------------------------------


def test_submit_signs_every_retry_with_a_fresh_nonce_and_identical_body(tmp_path, monkeypatch):
    monkeypatch.setattr("sima_cli.cloudex.client.time.sleep", Mock())
    responses = [Mock(ok=False, status_code=503, text="busy"),
                 Mock(ok=True, status_code=202, json=Mock(return_value={"command_id": COMMAND, "status": "queued"}))]
    request = Mock(side_effect=responses)
    monkeypatch.setattr(requests, "request", request)
    api = CloudExAPI("https://p2p.example.test", "user-1", SECRET, ALLOCATION)
    store = power.OperationStore(tmp_path)
    operation = power.new_operation(ALLOCATION, None, "modalix-123")
    store.write(operation)

    power.submit_cycle(api, store, operation)

    first, second = request.call_args_list
    assert first.kwargs["data"] == second.kwargs["data"]
    assert json.loads(first.kwargs["data"]) == {"request_id": operation["request_id"], "action": "cycle"}
    assert first.kwargs["headers"]["x-p2p-nonce"] != second.kwargs["headers"]["x-p2p-nonce"]
    for call in (first, second):
        headers = call.kwargs["headers"]
        assert headers["x-p2p-allocation-id"] == ALLOCATION
        assert headers["x-p2p-requester"] == "user-1"
        assert {"x-p2p-timestamp", "x-p2p-signature"} <= set(headers)
        assert SECRET not in json.dumps(headers)
    assert store.list()[0]["phase"] == power.CYCLE_SUBMITTED
    assert store.list()[0]["command_id"] == COMMAND


def test_invalid_command_id_is_not_interpolated_into_a_path(tmp_path):
    api = Mock()
    api.call.return_value = {"command_id": "../admin", "status": "queued"}
    store = power.OperationStore(tmp_path)
    operation = power.new_operation(ALLOCATION, None, "x")
    store.write(operation)

    with pytest.raises(power.PowerCyclePending):
        power.submit_cycle(api, store, operation)
    assert store.list()[0]["phase"] == power.PREPARED


def test_abandon_discards_an_unreadable_record(env):
    env.op_path.parent.mkdir(parents=True, exist_ok=True)
    env.op_path.write_text("{not json")

    result = invoke(["--abandon", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Discarded unreadable power-cycle state" in result.output
    assert not env.op_path.exists()
    assert env.api.calls == []


def test_retained_record_after_failed_reconnect_drops_stopped_forwarder(env, monkeypatch):
    env.store.write(dict(_record(), forwarder_pid=4242))

    def offline_connect(profile, store, progress, **kwargs):
        for old in store.list():
            store.delete(old["session_id"])
        raise _not_joined()

    env.connect.side_effect = offline_connect
    monkeypatch.setattr(power, "DEVICE_RETURN_TIMEOUT_SECONDS", 0)

    result = invoke(["--yes"])

    assert result.exit_code == 1
    retained, = env.store.list()
    assert "forwarder_pid" not in retained
    assert retained["status"] == "disconnected"
    assert retained["api_secret"] == SECRET  # the credential survives for resume


def test_targeted_abandon_never_discards_another_allocations_unreadable_record(env):
    env.store.write(_record())
    other = env.op_path.parent / (OTHER_ALLOCATION + ".json")
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text('{"phase":"from_a_newer_client"}')

    result = invoke(["--abandon", "--session", "bbb", "--yes"])

    assert result.exit_code == 0, result.output
    assert "No pending CloudEx power cycle" in result.output
    assert other.exists()
    assert env.api.calls == []


def test_selected_devkit_cycles_past_another_allocations_unreadable_record(env):
    env.store.write(_record())
    other = env.op_path.parent / (OTHER_ALLOCATION + ".json")
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("{not json")

    result = invoke(["--session", "bbb", "--yes"])

    assert result.exit_code == 0, result.output
    assert len(env.api.posts()) == 1
    assert other.read_text() == "{not json"


def test_unselected_run_still_reports_any_unreadable_record(env):
    env.store.write(_record())
    other = env.op_path.parent / (OTHER_ALLOCATION + ".json")
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("{not json")

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert "unreadable" in result.output
    assert env.api.calls == []


def test_targeted_abandon_discards_its_own_unreadable_record(env):
    env.store.write(_record())
    env.op_path.parent.mkdir(parents=True, exist_ok=True)
    env.op_path.write_text("{not json")

    result = invoke(["--abandon", "--session", "bbb", "--yes"])

    assert result.exit_code == 0, result.output
    assert not env.op_path.exists()


def test_unremovable_unreadable_entry_is_a_clean_error(env):
    env.op_path.mkdir(parents=True)

    result = invoke(["--abandon", "--yes"])

    assert result.exit_code == 1
    assert "could not be removed" in result.output
    assert env.op_path.exists()


def test_concurrent_creation_loses_the_race_without_posting(env, monkeypatch):
    env.store.write(_record())
    rival = power.new_operation(ALLOCATION, "b" * 32, "modalix-123")
    original_create = power.OperationStore.create

    def racing_create(store, operation):
        # Another run records its operation between our scan and our create.
        original_create(store, rival)
        return original_create(store, operation)

    monkeypatch.setattr(power.OperationStore, "create", racing_create)

    result = invoke(["--yes"])

    assert result.exit_code == 1
    assert "already pending or in progress" in result.output
    assert env.api.posts() == []
    assert json.loads(env.op_path.read_text())["request_id"] == rival["request_id"]


def test_create_never_replaces_an_existing_record(tmp_path):
    store = power.OperationStore(tmp_path)
    first = power.new_operation(ALLOCATION, None, "x")
    store.create(first)

    with pytest.raises(power.OperationExists):
        store.create(power.new_operation(ALLOCATION, None, "x"))

    saved, = store.list()
    assert saved["request_id"] == first["request_id"]
    assert stat.S_IMODE(os.stat(store._path(ALLOCATION)).st_mode) == 0o600
    assert [n for n in os.listdir(store.directory) if n.endswith(".tmp")] == []


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is POSIX-only")
@pytest.mark.parametrize("method", ["create", "write"])
def test_record_writes_sync_the_directory_entry(tmp_path, monkeypatch, method):
    store = power.OperationStore(tmp_path)
    synced = []
    real_fsync = os.fsync

    def tracking_fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(power.os, "fsync", tracking_fsync)

    getattr(store, method)(power.new_operation(ALLOCATION, None, "x"))

    assert synced, "the containing directory must be fsynced after the rename"


def test_create_falls_back_to_exclusive_open_without_hard_links(tmp_path, monkeypatch):
    def no_links(_source, _target):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(power.os, "link", no_links)
    store = power.OperationStore(tmp_path)
    first = power.new_operation(ALLOCATION, None, "x")

    store.create(first)
    with pytest.raises(power.OperationExists):
        store.create(power.new_operation(ALLOCATION, None, "x"))

    saved, = store.list()
    assert saved["request_id"] == first["request_id"]
    assert stat.S_IMODE(os.stat(store._path(ALLOCATION)).st_mode) == 0o600
    assert [n for n in os.listdir(store.directory) if n.endswith(".tmp")] == []


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is POSIX-only")
def test_directory_sync_is_best_effort_where_unsupported(tmp_path, monkeypatch):
    real_fsync = os.fsync

    def no_directory_sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(22, "Invalid argument")
        real_fsync(descriptor)

    monkeypatch.setattr(power.os, "fsync", no_directory_sync)
    store = power.OperationStore(tmp_path)

    store.write(power.new_operation(ALLOCATION, None, "x"))

    assert len(store.list()) == 1


def test_failed_fallback_write_leaves_no_unreadable_record(tmp_path, monkeypatch):
    monkeypatch.setattr(power.os, "link", Mock(side_effect=OSError(1, "Operation not permitted")))
    real_fsync = os.fsync
    store = power.OperationStore(tmp_path)
    final = store._path(ALLOCATION)

    def failing_fsync(descriptor):
        if os.path.exists(final) and os.fstat(descriptor).st_ino == os.stat(final).st_ino:
            raise OSError(28, "No space left on device")
        real_fsync(descriptor)

    monkeypatch.setattr(power.os, "fsync", failing_fsync)

    with pytest.raises(CloudExError, match="could not be created"):
        store.create(power.new_operation(ALLOCATION, None, "x"))
    assert store.list() == []
    assert os.listdir(store.directory) == []


def test_concurrent_run_for_the_same_allocation_is_refused(env):
    _saved_operation(env, power.CYCLE_SUBMITTED)
    env.store.write(_record())

    with env.operations.lock(ALLOCATION):
        result = invoke([])

    assert result.exit_code == 1
    assert "already handling the power cycle" in result.output
    assert env.api.calls == []
    env.connect.assert_not_called()


def test_fresh_run_is_refused_while_another_run_holds_the_lock(env):
    env.store.write(_record())

    with env.operations.lock(ALLOCATION):
        result = invoke(["--yes"])

    assert result.exit_code == 1
    assert env.api.posts() == []
    assert not env.op_path.exists()


def test_resume_rereads_the_record_under_the_lock(env, monkeypatch):
    _saved_operation(env, power.PREPARED)
    env.store.write(_record())
    real_lock = power.OperationStore.lock

    @contextlib.contextmanager
    def lock_after_other_run_finished(store, allocation_id):
        # Another run completed and removed the record while we waited to start.
        store.delete(allocation_id)
        with real_lock(store, allocation_id):
            yield

    monkeypatch.setattr(power.OperationStore, "lock", lock_after_other_run_finished)

    result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert "already finished by another sima-cli run" in result.output
    assert env.api.calls == []
    assert not env.op_path.exists()


def test_cleanup_keeps_connections_created_after_the_cycle(env):
    operation = _saved_operation(env, power.CYCLE_COMPLETED)
    operation["cycle_completed_at"] = 1_000
    env.operations.write(operation)
    env.store.write(dict(_record("b" * 32), created_at=900))
    env.store.write(dict(_record("e" * 32), created_at=2_000))

    result = invoke([])

    assert result.exit_code == 0, result.output
    stopped = [c.args[0]["session_id"] for c in commands.stop_local_forwarder.call_args_list]
    assert stopped == ["b" * 32]


def test_completed_cycle_records_its_completion_time(env):
    env.store.write(_record())
    seen = {}
    real_stop = commands._stop_obsolete_forwarders

    def capture(store, allocation_id, cutoff):
        seen["cutoff"] = cutoff
        seen["record"] = json.loads(env.op_path.read_text())
        return real_stop(store, allocation_id, cutoff)

    with patch.object(commands, "_stop_obsolete_forwarders", capture):
        result = invoke(["--yes"])

    assert result.exit_code == 0, result.output
    assert seen["record"]["phase"] == power.CYCLE_COMPLETED
    assert seen["cutoff"] == seen["record"]["cycle_completed_at"]


def test_lock_unsupported_filesystem_does_not_block_runs(env, monkeypatch):
    _saved_operation(env, power.CYCLE_SUBMITTED)
    env.store.write(_record())
    monkeypatch.setattr(power, "_try_lock", Mock(side_effect=OSError(37, "No locks available")))

    result = invoke([])

    assert result.exit_code == 0, result.output
    assert env.api.posts() == []
    assert commands.LOCK_UNSUPPORTED_WARNING in result.output


def test_supported_locking_prints_no_warning(env):
    env.store.write(_record())

    result = invoke(["--yes", "--no-reconnect"])

    assert result.exit_code == 0, result.output
    assert "does not support file locking" not in result.output


def test_completion_first_seen_on_resume_keeps_tunnels_made_after_submission(env):
    operation = _saved_operation(env, power.CYCLE_SUBMITTED)
    operation["submitted_at"] = 1_000
    env.operations.write(operation)
    env.api.states = ["completed"]
    env.store.write(dict(_record("b" * 32), created_at=900))
    # The user reconnected by hand after the relay cycled but before this resume.
    env.store.write(dict(_record("e" * 32), created_at=1_500))

    result = invoke([])

    assert result.exit_code == 0, result.output
    stopped = [c.args[0]["session_id"] for c in commands.stop_local_forwarder.call_args_list]
    assert stopped == ["b" * 32]


def test_cleanup_cutoff_uses_the_connection_time_not_the_attempt_start(env):
    operation = _saved_operation(env, power.CYCLE_SUBMITTED)
    operation["submitted_at"] = 1_000
    env.operations.write(operation)
    env.api.states = ["completed"]
    env.store.write(dict(_record("b" * 32), created_at=900, connected_at=905))
    # A parallel connect() started before the cycle but its tunnel came up after it.
    env.store.write(dict(_record("e" * 32), created_at=950, connected_at=1_500))

    result = invoke([])

    assert result.exit_code == 0, result.output
    stopped = [c.args[0]["session_id"] for c in commands.stop_local_forwarder.call_args_list]
    assert stopped == ["b" * 32]


def test_tunnel_up_within_the_cycles_second_is_ordered_by_subsecond_time(tmp_path, monkeypatch):
    api = Mock()
    api.call.side_effect = [server_command("running"), server_command("completed")]
    monkeypatch.setattr(power, "sleep", Mock())
    monkeypatch.setattr(power.time, "time", lambda: 1_000.25)
    store = power.OperationStore(tmp_path)
    operation = power.new_operation(ALLOCATION, None, "x")
    store.write(operation)

    operation.update(phase=power.CYCLE_SUBMITTED, command_id=COMMAND, submitted_at=999.5)
    store.write(operation)
    power.wait_for_cycle(api, store, operation, Mock())

    cutoff = power.cycle_completed_at(store.load(ALLOCATION))
    assert cutoff == 1_000.25
    assert not commands._created_before({"created_at": 999, "connected_at": 1_000.75}, cutoff)
    assert commands._created_before({"created_at": 990, "connected_at": 1_000.10}, cutoff)


def test_whole_second_cutoff_from_an_older_record_rounds_up():
    assert power.cycle_completed_at({"cycle_completed_at": 1_000}) == 1_001
    assert power.cycle_completed_at({"cycle_completed_at": 1_000.25}) == 1_000.25
    # A tunnel up in the same (rounded-down) second is uncertain: stop it.
    assert commands._created_before({"connected_at": 1_000.3}, power.cycle_completed_at({"cycle_completed_at": 1_000}))


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_dangling_record_symlink_is_unreadable_not_absent(tmp_path):
    store = power.OperationStore(tmp_path)
    os.makedirs(store.directory)
    os.symlink(str(tmp_path / "missing"), os.path.join(store.directory, ALLOCATION + ".json"))

    with pytest.raises(power.UnreadableOperation):
        store.load(ALLOCATION)
    with pytest.raises(power.UnreadableOperation):
        store.list()


def test_first_submission_time_keeps_subsecond_precision(tmp_path, monkeypatch):
    api = Mock()
    api.call.return_value = server_command("queued")
    monkeypatch.setattr(power.time, "time", lambda: 1_234.5)
    store = power.OperationStore(tmp_path)
    operation = power.new_operation(ALLOCATION, None, "x")
    store.write(operation)

    power.submit_cycle(api, store, operation)

    assert store.load(ALLOCATION)["submitted_at"] == 1_234.5


def test_resume_hints_name_the_internal_invocation(env, monkeypatch):
    env.store.write(_record())
    _connect_failing_with(env, [DirectHandshakeError("ICE connection state Failed")])

    partial = invoke(["--yes"])

    # The partial-success sentence stays as agreed in vulcan#238; the working command follows it.
    assert power.PARTIAL_SUCCESS in partial.output.replace("\n", " ")
    assert "Resume with: sima-cli -i cloudex power-cycle" in partial.output

    env.api.states = [KeyboardInterrupt()]
    env.operations.delete(ALLOCATION)
    interrupted = invoke(["--yes"])

    assert interrupted.exit_code == 130
    assert "Rerun sima-cli -i cloudex power-cycle to resume" in interrupted.output


def test_exact_server_response_shape_drives_submit_and_wait_to_completed(tmp_path, monkeypatch):
    # Field names and status values as returned by Vulcan's /v1/p2p/power-commands.
    api = Mock()
    api.call.side_effect = [
        server_command("awaiting_disconnect"),
        server_command("awaiting_disconnect", disconnect_forced=True),
        server_command("queued"),
        server_command("received"),
        server_command("running"),
        server_command("completed", updated_at=1_090),
    ]
    monkeypatch.setattr(power, "sleep", Mock())
    store = power.OperationStore(tmp_path)
    operation = power.new_operation(ALLOCATION, None, "modalix-123")
    store.write(operation)
    progress = Mock()

    assert power.submit_cycle(api, store, operation) == "awaiting_disconnect"
    power.wait_for_cycle(api, store, operation, progress)

    assert api.call.call_args_list[0].args == (
        "POST", power.POWER_COMMANDS_PATH, {"request_id": operation["request_id"], "action": "cycle"})
    for call in api.call.call_args_list[1:]:
        assert call.args == ("GET", power.POWER_COMMANDS_PATH + "/" + COMMAND)
    assert store.load(ALLOCATION)["phase"] == power.CYCLE_COMPLETED
    assert store.load(ALLOCATION)["command_id"] == COMMAND
    labels = [c.args[0] for c in progress.update.call_args_list]
    assert "Power cycle requested" in labels
    assert "Power cycle accepted by the appliance" in labels


def test_legacy_state_field_is_not_mistaken_for_status(tmp_path, monkeypatch):
    api = Mock()
    api.call.return_value = {"command_id": COMMAND, "state": "completed"}
    monkeypatch.setattr(power, "sleep", Mock())
    store = power.OperationStore(tmp_path)
    operation = dict(power.new_operation(ALLOCATION, None, "x"), phase=power.CYCLE_SUBMITTED,
                     command_id=COMMAND, submitted_at=1_000)
    store.write(operation)

    with pytest.raises(power.PowerCyclePending):
        power.wait_for_cycle(api, store, operation, Mock())
    assert store.load(ALLOCATION)["phase"] == power.CYCLE_SUBMITTED


def test_completion_watched_live_uses_the_observed_time(tmp_path, monkeypatch):
    api = Mock()
    api.call.side_effect = [{"status": "running"}, {"status": "completed"}]
    monkeypatch.setattr(power, "sleep", Mock())
    monkeypatch.setattr(power.time, "time", lambda: 5_000)
    store = power.OperationStore(tmp_path)
    operation = dict(power.new_operation(ALLOCATION, None, "x"), phase=power.CYCLE_SUBMITTED,
                     command_id=COMMAND, submitted_at=1_000)
    store.write(operation)

    power.wait_for_cycle(api, store, operation, Mock())

    assert store.load(ALLOCATION)["cycle_completed_at"] == 5_000


def _lock_that_runs(monkeypatch, action):
    real_lock = power.OperationStore.lock

    @contextlib.contextmanager
    def lock(store, allocation_id):
        action(store)  # what another run did while we waited for confirmation
        with real_lock(store, allocation_id) as locked:
            yield locked

    monkeypatch.setattr(power.OperationStore, "lock", lock)


def test_abandon_keeps_a_newer_operation_recorded_while_confirming(env, monkeypatch):
    _saved_operation(env, power.CYCLE_SUBMITTED)
    env.store.write(_record())
    newer = dict(power.new_operation(ALLOCATION, "b" * 32, "modalix-123"),
                 phase=power.CYCLE_SUBMITTED, command_id="p2p-power-newer")
    _lock_that_runs(monkeypatch, lambda store: store.write(newer))

    result = invoke(["--abandon", "--yes"])

    assert result.exit_code == 0, result.output
    assert "nothing was discarded" in result.output
    assert json.loads(env.op_path.read_text())["request_id"] == newer["request_id"]


def test_abandon_keeps_a_record_repaired_while_confirming(env, monkeypatch):
    env.op_path.parent.mkdir(parents=True, exist_ok=True)
    env.op_path.write_text("{not json")
    replacement = dict(power.new_operation(ALLOCATION, None, "modalix-123"),
                       phase=power.CYCLE_SUBMITTED, command_id=COMMAND)
    _lock_that_runs(monkeypatch, lambda store: store.write(replacement))

    result = invoke(["--abandon", "--yes"])

    assert result.exit_code == 0, result.output
    assert "nothing was discarded" in result.output
    assert json.loads(env.op_path.read_text())["request_id"] == replacement["request_id"]


def test_retained_newer_connection_skips_old_peer_wait(env):
    operation = _saved_operation(env, power.WAITING_FOR_DEVICE)
    operation.update(cycle_completed_at=1_000, completion_observed=True)
    env.operations.write(operation)
    env.store.write(dict(_record("e" * 32), created_at=2_000))
    env.api.ice_sessions = [{"session_id": "f" * 32, "status": "connected_direct"}]

    result = invoke([])

    assert result.exit_code == 0, result.output
    assert [c for c in env.api.calls if c[:2] == ("GET", "/v1/p2p/ice-sessions")] == []
    assert "made after the power cycle is kept" in result.output
    env.connect.assert_called_once()


def test_unobserved_completion_still_waits_for_old_peer_cleanup(env):
    # The cutoff is only the submission time: a "newer" tunnel may predate the power cut.
    operation = _saved_operation(env, power.WAITING_FOR_DEVICE)
    operation.update(cycle_completed_at=1_000, completion_observed=False)
    env.operations.write(operation)
    env.store.write(dict(_record("e" * 32), created_at=2_000))

    result = invoke([])

    assert result.exit_code == 0, result.output
    assert [c for c in env.api.calls if c[:2] == ("GET", "/v1/p2p/ice-sessions")]
    assert "made after the power cycle is kept" not in result.output


def test_completion_observed_flag_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(power, "sleep", Mock())
    store = power.OperationStore(tmp_path)
    for states, observed in (([server_command("running"), server_command("completed")], True),
                             ([server_command("completed")], False)):
        api = Mock()
        api.call.side_effect = states
        operation = dict(power.new_operation(ALLOCATION, None, "x"), phase=power.CYCLE_SUBMITTED,
                         command_id=COMMAND, submitted_at=1_000)
        store.write(operation)
        power.wait_for_cycle(api, store, operation, Mock())
        assert store.load(ALLOCATION)["completion_observed"] is observed


def test_retry_after_lost_response_keeps_the_first_submission_time(env, monkeypatch):
    env.store.write(_record())
    env.api.post_error = CloudExError("CloudEx API is unavailable")
    monkeypatch.setattr(power.time, "time", lambda: 1_000.0)

    first = invoke(["--yes"])

    assert first.exit_code == 1
    saved = json.loads(env.op_path.read_text())
    assert saved["phase"] == power.PREPARED and saved["submitted_at"] == 1_000

    env.api.post_error = None
    env.api.states = ["completed"]  # already finished: completion is not watched live
    monkeypatch.setattr(power.time, "time", lambda: 9_000.0)
    seen = {}
    real_stop = commands._stop_obsolete_forwarders

    def capture(store, allocation_id, cutoff):
        seen["cutoff"] = cutoff
        return real_stop(store, allocation_id, cutoff)

    monkeypatch.setattr(commands, "_stop_obsolete_forwarders", capture)
    second = invoke(["--yes"])

    assert second.exit_code == 0, second.output
    first_body, second_body = [c[2] for c in env.api.posts()]
    assert first_body == second_body
    assert seen["cutoff"] == 1_000


def test_submission_time_is_persisted_before_the_post(env):
    env.store.write(_record())
    seen = {}
    env.api.on_post = lambda body: seen.update(json.loads(env.op_path.read_text()))

    result = invoke(["--yes", "--no-reconnect"])

    assert result.exit_code == 0, result.output
    assert seen["phase"] == power.PREPARED
    assert isinstance(seen["submitted_at"], (int, float)) and not isinstance(seen["submitted_at"], bool)
