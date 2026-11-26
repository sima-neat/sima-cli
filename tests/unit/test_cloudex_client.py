import base64
import json
import os
import select
import signal
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sima_cli.cloudex import client, installer


@pytest.fixture(autouse=True)
def host_platform(monkeypatch):
    # Lifecycle tests must not depend on tools installed on the test host.
    monkeypatch.setattr(client, "sys", SimpleNamespace(platform="linux", version_info=sys.version_info))


def allocation_profile(environment="vulcan-staging"):
    return {
        "environment": environment,
        "api_url": "https://cloudex.example.test",
        "requester": "user-1",
        "allocation_id": "a" * 32,
        "allocation_secret": "s" * 48,
    }


def session_record(session_id="b" * 32):
    profile = allocation_profile()
    return {
        "version": 1,
        "session_id": session_id,
        "environment": profile["environment"],
        "api_url": profile["api_url"],
        "requester": profile["requester"],
        "allocation_id": profile["allocation_id"],
        "api_secret": profile["allocation_secret"],
        "forwarder_pid": 123,
        "status": "connected",
        "target": "10.252.1.2",
    }


@pytest.mark.parametrize("environment", [
    "vulcan-staging", "vulcan-production", "arthas-staging", "arthas-production",
    "custom-environment",
])
def test_decode_key_accepts_exact_allocation_contract(environment):
    profile = allocation_profile(environment)
    encoded = base64.b64encode(json.dumps(profile).encode()).decode()

    assert client.decode_key(encoded) == profile


@pytest.mark.parametrize("environment", ["", None, 123, [], {}])
def test_decode_key_rejects_invalid_environment(environment):
    profile = allocation_profile(environment)
    encoded = base64.b64encode(json.dumps(profile).encode()).decode()

    with pytest.raises(client.CloudExError, match="invalid value"):
        client.decode_key(encoded)


@pytest.mark.parametrize(
    "value",
    ["not-base64", base64.b64encode(b"[]").decode(), base64.b64encode(b"{}").decode()],
)
def test_decode_key_rejects_invalid_profiles(value):
    with pytest.raises(client.CloudExError, match="--key"):
        client.decode_key(value)


def test_session_store_keeps_independent_secure_records(tmp_path):
    store = client.SessionStore(tmp_path / "cloudex")
    first = session_record("a" * 32)
    second = session_record("b" * 32)

    store.write(first)
    store.write(second)

    assert [item["session_id"] for item in store.list()] == ["a" * 32, "b" * 32]
    assert stat.S_IMODE(store.path(first["session_id"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(store.sessions_dir.stat().st_mode) == 0o700


def test_session_store_preserves_only_failed_forwarder_log(tmp_path):
    store = client.SessionStore(tmp_path / "cloudex")
    session_id = "f" * 32
    runtime, config, report, log = store.runtime_paths(session_id)
    runtime.mkdir(parents=True)
    config.write_text('{"signaling_token":"secret"}')
    report.write_text('{"status":"failed"}')
    log.write_text("kerrigan-p2p-forwarder: session configuration accepted\n")
    log.chmod(0o600)
    store.write_recovery({"session_id": session_id, "status": "creating"})

    store.delete(session_id, preserve_log=True)

    assert log.read_text().startswith("kerrigan-p2p-forwarder")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert not config.exists()
    assert not report.exists()
    assert not store.recovery_path(session_id).exists()


def test_session_store_migrates_incomplete_rows_out_of_connection_list(tmp_path):
    store = client.SessionStore(tmp_path / "cloudex")
    incomplete = session_record("c" * 32)
    incomplete.pop("target")
    incomplete["status"] = "cleanup-pending"
    store.write(incomplete)

    assert store.list() == []
    assert not store.path(incomplete["session_id"]).exists()
    assert store.list_recovery() == [incomplete]
    assert stat.S_IMODE(store.recovery_path(incomplete["session_id"]).stat().st_mode) == 0o600


def test_forwarder_override_is_authoritative(tmp_path, monkeypatch):
    override = tmp_path / "missing-forwarder"
    monkeypatch.setenv("SIMA_CLOUDEX_FORWARDER", str(override))
    monkeypatch.setattr(client.shutil, "which", Mock(return_value="/usr/local/bin/kerrigan-p2p-forwarder"))

    with pytest.raises(client.CloudExError, match="not installed"):
        client.find_forwarder()

    client.shutil.which.assert_not_called()


@pytest.mark.parametrize("environment", [
    "vulcan-staging", "vulcan-production", "arthas-staging", "arthas-production",
    "custom-environment",
])
@pytest.mark.parametrize("restored", [False, True])
def test_api_signs_allocation_scoped_request(monkeypatch, environment, restored):
    response = Mock(ok=True)
    response.json.return_value = {"status": "connected_direct"}
    request = Mock(return_value=response)
    monkeypatch.setattr(client.requests, "request", request)
    profile = allocation_profile(environment)
    # Routing must use api_url, independently of the descriptive environment.
    profile["api_url"] = "https://independent-backend.example.test"
    encoded = base64.b64encode(json.dumps(profile).encode()).decode()
    decoded = client.decode_key(encoded)
    if restored:
        session = dict(decoded, api_secret=decoded["allocation_secret"])
        api = client.CloudExAPI.from_session(session)
    else:
        api = client.CloudExAPI.from_profile(decoded)

    assert api.call("POST", "/v1/p2p/ice-sessions", {"request_id": "b" * 32}) == {
        "status": "connected_direct"
    }

    assert request.call_args.args == ("POST", profile["api_url"] + "/v1/p2p/ice-sessions")
    kwargs = request.call_args.kwargs
    assert kwargs["headers"]["x-p2p-allocation-id"] == "a" * 32
    assert len(kwargs["headers"]["x-p2p-signature"]) == 64
    assert "allocation_secret" not in kwargs["data"].decode()


@pytest.mark.parametrize("path", ["direct", "relay"])
@pytest.mark.parametrize("host,bootstrap", [("linux", False), ("darwin", False), ("darwin", True)])
def test_connect_persists_connected_session_and_removes_ephemeral_config(tmp_path, monkeypatch, host, bootstrap, path):
    client.sys.platform = host
    which = Mock(return_value="/opt/homebrew/bin/tool")
    if bootstrap:
        which.side_effect = [None, None, "/opt/homebrew/bin/brew",
                             "/opt/homebrew/bin/wg", "/opt/homebrew/bin/wireguard-go"]
    monkeypatch.setattr(client.shutil, "which", which)
    brew = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(client.subprocess, "run", brew)
    store = client.SessionStore(tmp_path / "cloudex")
    forwarder = tmp_path / "forwarder"
    forwarder.write_text("binary")
    forwarder.chmod(0o755)
    process = Mock(pid=123, poll=Mock(return_value=None))
    allocated = {
        "session_id": "d" * 32,
        "diagnostics": True,
        "diagnostics_version": 2 if path == "direct" else 1,
        "expires_at": "9999999999",
        "target_id": "ll2",
        "wireguard": {"remote_address": "10.252.1.2/32"},
    }
    api = Mock()
    api.url = allocation_profile()["api_url"]
    api.requester = allocation_profile()["requester"]
    api.secret = allocation_profile()["allocation_secret"]
    api.allocation_id = allocation_profile()["allocation_id"]
    api.call.return_value = allocated
    monkeypatch.setattr(installer, "ensure_forwarder", Mock(return_value=forwarder))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))
    popen = Mock(return_value=process)
    monkeypatch.setattr(client.subprocess, "Popen", popen)
    wait_forwarder = Mock(return_value={"status": "connected", "path": path})
    monkeypatch.setattr(client, "_wait_forwarder", wait_forwarder)
    monkeypatch.setattr(client.time, "time", Mock(return_value=1000))

    cleanup = Mock()
    monkeypatch.setattr(client, "disconnect_session", cleanup)
    upload = Mock(side_effect=client.CloudExError("Diagnostics unavailable"))
    result = client.connect(allocation_profile(), store, Mock(), attempts=1, transport="p2p", report_diagnostics=upload)
    cleanup.assert_not_called()
    assert upload.call_args.args == (api.allocation_id, allocated["session_id"], path, [])
    if path == "direct":
        assert upload.call_args.kwargs["timing"]["setup_ms"] >= 0
    else:
        assert upload.call_args.kwargs == {}

    if bootstrap:
        assert brew.call_args.args[0] == ["/opt/homebrew/bin/brew", "install", "wireguard-tools", "wireguard-go"]
    else:
        brew.assert_not_called()
    assert result["device"] == "ll2"
    assert result["target"] == "10.252.1.2"
    assert result["ice_path"] == path
    assert result["expires_at"] == 9999999999.0
    assert store.list()[0]["api_secret"] == allocation_profile()["allocation_secret"]
    assert not store.runtime_paths(result["session_id"])[1].exists()
    command = popen.call_args.args[0]
    assert command[:3] == ["sudo", "-n", "--"]
    if host == "darwin":
        assert command[3:6] == ["/usr/bin/env", "PATH=" + client.MACOS_TUNNEL_PATH, str(forwarder)]
    else:
        assert command[3] == str(forwarder)
    assert popen.call_args.kwargs["stdin"] is client.subprocess.DEVNULL
    assert popen.call_args.kwargs.items() >= client._own_process_group().items()
    # A new session would drop the terminal that sudo -n's cached credentials need.
    assert "start_new_session" not in popen.call_args.kwargs
    api.call.assert_called_once_with(
        "POST", "/v1/p2p/ice-sessions", {"request_id": result["session_id"]},
        deadline=1060,
    )
    assert result["remote_session_id"] == "d" * 32
    assert wait_forwarder.call_args.args[4] == 1060


@pytest.mark.parametrize("version,expected", [
    ((3, 10, 12), {"preexec_fn": os.setpgrp}),
    ((3, 11, 0), {"process_group": 0}),
])
def test_own_process_group_uses_native_option_when_available(version, expected):
    client.sys.version_info = version
    assert client._own_process_group() == expected


@pytest.mark.skipif(not hasattr(os, "setpgrp"), reason="needs POSIX process groups")
@pytest.mark.parametrize("own_group,survives", [(False, False), (True, True)])
def test_forwarder_survives_exit_of_one_command_terminal_session(own_group, survives):
    # Issue #425: `wsl -e bash -lc '... cloudex connect'` makes connect the
    # session leader of its terminal. Its exit sends SIGHUP to the terminal's
    # foreground process group, which used to include the forwarder.
    import pty

    read_end, write_end = os.pipe()
    leader, terminal = pty.fork()
    if leader == 0:
        try:
            options = client._own_process_group() if own_group else {}
            forwarder = subprocess.Popen(
                ["sleep", "30"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, pass_fds=(write_end,), **options,
            )
            os.write(write_end, b"%d\n" % forwarder.pid)
        finally:
            os._exit(0)
    os.close(write_end)
    forwarder = None
    try:
        forwarder = int(os.read(read_end, 32))
        os.waitpid(leader, 0)
        # The pipe reaches EOF only once the forwarder, its last holder, exits.
        ready, _, _ = select.select([read_end], [], [], 1.0)
        exited = bool(ready) and os.read(read_end, 1) == b""
    finally:
        if forwarder:
            try:
                os.kill(forwarder, signal.SIGKILL)
            except ProcessLookupError:
                pass
        os.close(read_end)
        os.close(terminal)
    assert exited is not survives


def test_connection_deadline_honors_shorter_server_setup_expiry(monkeypatch):
    monkeypatch.setattr(client.time, "time", Mock(return_value=1000))

    assert client._connection_deadline({
        "setup_expires_at": 1045,
        "expires_at": 5000,
    }, 1060) == 1045


def test_wait_forwarder_timeout_prints_sanitized_diagnostic_log(tmp_path, monkeypatch):
    session_id = "b" * 32
    report = tmp_path / "report.json"
    log = tmp_path / "forwarder.log"
    log.write_text(
        "untrusted output that must not be printed\n"
        "kerrigan-p2p-forwarder: joined authenticated signaling session\n"
    )
    process = Mock(poll=Mock(return_value=None))
    clock = Mock(return_value=1998.9)
    monkeypatch.setattr(client.time, "time", clock)

    def reach_deadline(_seconds):
        clock.return_value = 1999
        with log.open("a") as stream:
            stream.write("kerrigan-p2p-forwarder: WireGuard public key received through signaling\n")
            stream.write("kerrigan-p2p-forwarder: ICE connection state Failed\n")

    monkeypatch.setattr(client.time, "sleep", reach_deadline)

    with pytest.raises(client.DirectHandshakeError, match="deadline expired") as raised:
        client._wait_forwarder(
            session_id, process, report, log, 1999, Mock()
        )

    message = str(raised.value)
    assert "Forwarder log" in message
    assert "last host stage: WireGuard public key received through signaling" in message
    assert "ICE connection state Failed" in message
    assert "untrusted output" not in message


@pytest.mark.parametrize("stages, remote, expected", [
    (["joined authenticated signaling session"], "waiting_for_device", client.DeviceNotJoinedError),
    # The host saw the DevKit's credentials even though the last poll predates them.
    (["joined authenticated signaling session", "remote ICE credentials received"],
     "waiting_for_device", client.DirectHandshakeError),
    (["joined authenticated signaling session"], "gathering_candidates", client.DirectHandshakeError),
])
def test_wait_forwarder_reports_a_devkit_that_never_joined(tmp_path, monkeypatch, stages, remote, expected):
    log = tmp_path / "forwarder.log"
    log.write_text("".join("kerrigan-p2p-forwarder: {}\n".format(stage) for stage in stages))
    clock = Mock(return_value=1998.9)
    monkeypatch.setattr(client.time, "time", clock)
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: setattr(clock, "return_value", 1999))

    with pytest.raises(client.DirectHandshakeError, match="deadline expired") as raised:
        client._wait_forwarder(
            "b" * 32, Mock(poll=Mock(return_value=None)), tmp_path / "report.json", log, 1999, Mock(),
            remote_status=lambda _deadline: {"status": remote},
        )

    assert type(raised.value) is expected


@pytest.mark.parametrize("confirmation", ["report", "log"])
def test_wait_forwarder_accepts_confirmation_at_deadline(tmp_path, monkeypatch, confirmation):
    session_id = "b" * 32
    report = tmp_path / "report.json"
    log = tmp_path / "forwarder.log"
    log.write_text("kerrigan-p2p-forwarder: selected ICE path relay\n")
    clock = Mock(return_value=99.9)
    monkeypatch.setattr(client.time, "time", clock)

    def confirm(_seconds):
        clock.return_value = 100
        if confirmation == "report":
            report.write_text(json.dumps({
                "session_id": session_id, "status": "connected", "path": "relay",
            }))
        else:
            with log.open("a") as stream:
                stream.write("kerrigan-p2p-forwarder: WireGuard handshake confirmed\n")

    monkeypatch.setattr(client.time, "sleep", confirm)
    process = Mock(poll=Mock(return_value=None))
    remote_status = Mock(return_value={})

    result = client._wait_forwarder(
        session_id, process, report, log, 100, Mock(), remote_status=remote_status,
    )

    assert result == {"session_id": session_id, "status": "connected", "path": "relay"}
    remote_status.assert_called_once_with(100)


def test_wait_forwarder_extends_p2p_attempt_when_it_selects_relay(tmp_path, monkeypatch):
    session_id = "b" * 32
    report = tmp_path / "report.json"
    log = tmp_path / "forwarder.log"
    log.write_text(
        "kerrigan-p2p-forwarder: selected ICE path relay\n"
        "kerrigan-p2p-forwarder: starting WireGuard tunnel\n"
    )
    clock = Mock(return_value=100)
    monkeypatch.setattr(client.time, "time", clock)

    def confirm(_seconds):
        clock.return_value = 105
        report.write_text(json.dumps({
            "session_id": session_id, "status": "connected", "path": "relay",
        }))

    monkeypatch.setattr(client.time, "sleep", confirm)
    process = Mock(poll=Mock(return_value=None))
    remote_status = Mock(return_value={})

    result = client._wait_forwarder(
        session_id, process, report, log, 100, Mock(),
        remote_status=remote_status, relay_deadline=115,
    )

    assert result["path"] == "relay"
    assert result["status"] == "connected"
    remote_status.assert_called_once_with(115)


def test_failed_connect_keeps_the_reported_host_diagnostics(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    forwarder = tmp_path / "forwarder"
    forwarder.write_text("binary")
    forwarder.chmod(0o755)
    process = Mock(pid=123, poll=Mock(return_value=None))
    allocated = {
        "session_id": "d" * 32,
        "expires_at": 9999999999,
        "target_id": "ll2",
        "wireguard": {"remote_address": "10.252.1.2/32"},
    }
    api = Mock()
    api.url = allocation_profile()["api_url"]
    api.requester = allocation_profile()["requester"]
    api.secret = allocation_profile()["allocation_secret"]
    api.allocation_id = allocation_profile()["allocation_id"]

    def api_call(method, _path, _data=None, **_kwargs):
        if method == "POST":
            return allocated
        if method == "DELETE":
            return {"status": "closing"}
        return {"status": "closed", "cleanup_confirmed": True}

    api.call.side_effect = api_call
    monkeypatch.setattr(installer, "ensure_forwarder", Mock(return_value=forwarder))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    monkeypatch.setattr(client.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(client, "_stop_forwarder", Mock())

    def fail_after_start(_session_id, _process, _report, log_path, *_args, **_kwargs):
        log_path.write_text("kerrigan-p2p-forwarder: joined authenticated signaling session\n")
        raise client.CloudExError("DevKit ICE startup failed; host diagnostics: " + str(log_path))

    monkeypatch.setattr(client, "_wait_forwarder", fail_after_start)

    with pytest.raises(client.CloudExError, match="host diagnostics") as raised:
        client.connect(allocation_profile(), store, Mock(), attempts=1, transport="p2p")

    log_path = store.runtime_paths(next(store.runtime_dir.iterdir()).name)[3]
    assert str(log_path) in str(raised.value)
    assert log_path.exists()
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert not store.runtime_paths(log_path.parent.name)[1].exists()
    assert not store.runtime_paths(log_path.parent.name)[2].exists()


def test_forwarder_failure_explains_sudo_terminal_requirement(tmp_path):
    log = tmp_path / "forwarder.log"
    log.write_text("sudo: a password is required\n")

    assert client._forwarder_failure(log) == (
        "administrator authorization is required; run CloudEx from an interactive terminal"
    )


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            "kernel WireGuard is unavailable and wireguard-go is unavailable",
            "this kernel lacks WireGuard support and the wireguard-go fallback is not installed",
        ),
        (
            "assign WireGuard address",
            "the host could not assign the CloudEx tunnel address",
        ),
        (
            "add WireGuard route",
            "the host could not add the CloudEx device route",
        ),
        (
            "configure WireGuard peer",
            "the host could not configure the CloudEx WireGuard peer",
        ),
    ],
)
def test_forwarder_failure_preserves_safe_linux_tunnel_diagnostic(
    tmp_path, detail, expected
):
    log = tmp_path / "forwarder.log"
    log.write_text(
        "kerrigan-p2p-forwarder: starting WireGuard tunnel\n"
        "kerrigan-p2p-forwarder: {}\n".format(detail)
    )

    assert client._forwarder_failure(log) == (
        expected + "; last stage: starting WireGuard tunnel"
    )


def test_wait_forwarder_preserves_path_when_handshake_log_wins_report_race(tmp_path):
    session_id = "b" * 32
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "session_id": session_id,
        "status": "checking",
        "path": "direct",
    }))
    log = tmp_path / "forwarder.log"
    log.write_text(
        "kerrigan-p2p-forwarder: selected ICE path direct\n"
        "kerrigan-p2p-forwarder: WireGuard handshake confirmed\n"
    )
    process = Mock(poll=Mock(return_value=None))

    result = client._wait_forwarder(
        session_id, process, report, log, 9999999999, Mock()
    )

    assert result == {"session_id": session_id, "status": "connected", "path": "direct"}


def test_wait_forwarder_surfaces_devkit_failure_and_diagnostic_paths(tmp_path):
    session_id = "b" * 32
    report = tmp_path / "report.json"
    log = tmp_path / "forwarder.log"
    log.write_text("kerrigan-p2p-forwarder: joined authenticated signaling session\n")
    process = Mock(poll=Mock(return_value=None))
    device_log = "/var/lib/kerrigan/ice-diagnostics/" + session_id + ".forwarder.log"

    with pytest.raises(client.CloudExError, match="DevKit ICE startup failed") as raised:
        client._wait_forwarder(
            session_id,
            process,
            report,
            log,
            9999999999,
            Mock(),
            remote_status=lambda _deadline: {
                "status": "failed",
                "last_error": "DevKit ICE startup failed; device diagnostics: " + device_log,
            },
        )

    assert device_log in str(raised.value)
    assert str(log) in str(raised.value)


def test_connect_installs_develop_forwarder_when_missing(tmp_path, monkeypatch):
    from sima_cli.cloudex import installer

    store = client.SessionStore(tmp_path / "cloudex")
    forwarder = tmp_path / "kerrigan-p2p-forwarder"
    api = Mock(
        url=allocation_profile()["api_url"],
        requester=allocation_profile()["requester"],
        secret=allocation_profile()["allocation_secret"],
        allocation_id=allocation_profile()["allocation_id"],
    )
    api.call.side_effect = client.APIError(403, "rejected")
    monkeypatch.setattr(installer, "find_forwarder", Mock(side_effect=client.CloudExError("missing")))
    install = Mock(return_value={"path": forwarder})
    monkeypatch.setattr(installer, "install_forwarder", install)
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))

    with pytest.raises(client.APIError, match="rejected"):
        client.connect(allocation_profile(), store, Mock(), attempts=1, transport="p2p")

    assert install.call_args.args[0] == "develop"
    assert install.call_args.kwargs["progress"] is not None


def test_connect_reuses_live_tunnel_for_same_allocation(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    session["ice_path"] = "unknown"
    store.write(session)
    monkeypatch.setattr(installer, "ensure_forwarder", Mock(return_value=tmp_path / "forwarder"))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(
        client,
        "inspect_session",
        Mock(return_value={"connected": True, "status": "connected", "path": "direct"}),
    )
    api = Mock()
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))

    result = client.connect(allocation_profile(), store, Mock(), attempts=1, transport="p2p")

    assert result["session_id"] == session["session_id"]
    assert result["ice_path"] == "direct"
    api.call.assert_not_called()


def test_remote_session_id_migrates_from_forwarder_report(tmp_path):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    _runtime, _config, report, _log = store.runtime_paths(session["session_id"])
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "session_id": "e" * 32,
        "status": "connected",
        "path": "direct",
    }))

    assert client._remote_session_id(session, store) == "e" * 32


@pytest.mark.parametrize("expired,running,local_status,remote_status,reason", [
    (True, False, "failed", "not found", client.LEASE_EXPIRED),
    (True, True, "connected", "connected_relay", client.LEASE_EXPIRED),
    (False, False, "failed", "not found", "tunnel stopped"),
    (False, True, "failed", "not found", "tunnel failed"),
    (False, True, "connected", "not found", "closed on server"),
    (False, True, "connected", "closed", "closed on server"),
    (False, True, "connected", "unavailable", "CloudEx unreachable"),
    (False, True, "connected", "waiting_for_device", "server: waiting_for_device"),
    (False, True, "connected", "connected_relay", None),
])
def test_disconnect_reason_names_the_first_failed_check(expired, running, local_status, remote_status, reason):
    assert client._disconnect_reason(
        expired=expired, running=running, local_status=local_status, remote_status=remote_status,
    ) == reason


@pytest.mark.parametrize("response,expected", [
    ({"status": "connected_direct"}, "connected_direct"),
    ({}, "unknown"),
    (client.APIError(404, "not found"), "not found"),
    (client.APIError(409, '{"error":"session closed or expired"}'), "closed"),
    (client.APIError(409, '{"error":"conflict"}'), "unavailable"),
    (client.APIError(500, "internal error"), "unavailable"),
    (client.CloudExError("network unreachable"), "unavailable"),
])
def test_remote_status_separates_closed_sessions_from_unreachable_service(tmp_path, monkeypatch, response, expected):
    api = Mock()
    if isinstance(response, Exception):
        api.call.side_effect = response
    else:
        api.call.return_value = response
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))

    assert client._remote_status(session_record(), client.SessionStore(tmp_path / "cloudex")) == expected


def _inspect(tmp_path, monkeypatch, session, *, running=True, report=None, remote="connected_direct"):
    store = client.SessionStore(tmp_path / "cloudex")
    if report is not None:
        report_path = store.runtime_paths(session["session_id"])[2]
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report))
    monkeypatch.setattr(client, "_forwarder_running", lambda _pid: running)
    monkeypatch.setattr(client, "_remote_status", lambda _session, _store: remote)
    return client.inspect_session(session, store)


def test_inspect_session_gives_no_reason_for_a_connected_session(tmp_path, monkeypatch):
    status = _inspect(tmp_path, monkeypatch, session_record(), report={"status": "connected", "path": "direct"})

    assert status["connected"] is True
    assert status["reason"] is None


def test_inspect_session_reports_an_expired_lease_before_other_failures(tmp_path, monkeypatch):
    session = dict(session_record(), expires_at=1)

    status = _inspect(tmp_path, monkeypatch, session, running=False, report={"status": "failed"}, remote="not found")

    assert (status["status"], status["reason"]) == ("disconnected", client.LEASE_EXPIRED)


def test_inspect_session_flags_an_expired_lease_whose_tunnel_still_works(tmp_path, monkeypatch):
    # A local clock running ahead of the service, or a teardown that hasn't happened yet.
    session = dict(session_record(), expires_at=1)

    status = _inspect(tmp_path, monkeypatch, session, report={"status": "connected", "path": "direct"})

    assert (status["connected"], status["status"], status["reason"]) == (True, "connected", client.LEASE_EXPIRED)


def test_inspect_session_keeps_an_unverified_tunnel_disconnected_with_a_reason(tmp_path, monkeypatch):
    status = _inspect(tmp_path, monkeypatch, session_record(), report={"status": "connected"}, remote="unavailable")

    assert status["connected"] is False
    assert (status["status"], status["reason"]) == ("disconnected", "CloudEx unreachable")


def test_inspect_session_explains_a_cleanup_pending_record(tmp_path, monkeypatch):
    session = dict(session_record(), status="cleanup-pending")

    status = _inspect(tmp_path, monkeypatch, session, running=False, remote="closed")

    assert (status["status"], status["reason"]) == ("cleanup pending", "tunnel stopped")


def test_rejected_create_does_not_disconnect_existing_owner(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    api = Mock(
        url=allocation_profile()["api_url"],
        requester=allocation_profile()["requester"],
        secret=allocation_profile()["allocation_secret"],
        allocation_id=allocation_profile()["allocation_id"],
    )
    api.call.side_effect = client.APIError(
        409, '{"error":"an ICE session is already active for this allocation"}'
    )
    monkeypatch.setattr(installer, "ensure_forwarder", Mock(return_value=tmp_path / "forwarder"))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))
    cleanup = Mock()
    monkeypatch.setattr(client, "disconnect_session", cleanup)

    with pytest.raises(client.APIError):
        client.connect(allocation_profile(), store, Mock(), attempts=1, transport="p2p")

    cleanup.assert_not_called()
    assert store.list() == []


def test_disconnect_stale_id_closes_allocation_active_session(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    store.write(session)
    active_id = "c" * 32
    api = Mock()
    api.call.side_effect = [
        client.APIError(404, "not found"),
        {"session_id": active_id, "status": "connected_direct"},
        {"status": "closing"},
        {"status": "closed", "cleanup_confirmed": True},
    ]
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client, "_stop_forwarder", Mock())

    client.disconnect_session(session, store, Mock())

    assert [call.args[:2] for call in api.call.call_args_list] == [
        ("DELETE", "/v1/p2p/ice-sessions/" + session["session_id"]),
        ("GET", "/v1/p2p/ice-sessions"),
        ("DELETE", "/v1/p2p/ice-sessions/" + active_id),
        ("GET", "/v1/p2p/ice-sessions/" + active_id),
    ]
    assert store.list() == []


def test_disconnect_expired_record_deletes_locally_without_api_request(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    session["expires_at"] = 1
    store.write(session)
    api = Mock()
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    authorize = Mock()
    monkeypatch.setattr(client, "authorize_admin", authorize)
    monkeypatch.setattr(client, "_forwarder_running", Mock(return_value=False))
    monkeypatch.setattr(client, "_stop_forwarder", Mock())

    result = client.disconnect_session(session, store, Mock())

    assert result == "expired"
    api.call.assert_not_called()
    authorize.assert_not_called()
    assert store.list() == []


def test_disconnect_unexpired_record_without_forwarder_skips_admin_authorization(
    tmp_path, monkeypatch
):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    store.write(session)
    api = Mock()
    api.call.side_effect = [
        {"status": "closing"},
        {"status": "closed", "cleanup_confirmed": True},
    ]
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    authorize = Mock()
    monkeypatch.setattr(client, "authorize_admin", authorize)
    monkeypatch.setattr(client, "_forwarder_running", Mock(return_value=False))
    monkeypatch.setattr(client, "_stop_forwarder", Mock())

    assert client.disconnect_session(session, store, Mock()) == "disconnected"

    authorize.assert_not_called()
    assert store.list() == []


def test_disconnect_unexpired_record_with_forwarder_authorizes_before_stop(
    tmp_path, monkeypatch
):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    store.write(session)
    api = Mock()
    api.call.side_effect = [
        {"status": "closing"},
        {"status": "closed", "cleanup_confirmed": True},
    ]
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    events = []
    monkeypatch.setattr(client, "_forwarder_running", Mock(return_value=True))
    monkeypatch.setattr(client, "authorize_admin", lambda: events.append("authorize"))
    monkeypatch.setattr(client, "_stop_forwarder", lambda _session: events.append("stop"))

    assert client.disconnect_session(session, store, Mock()) == "disconnected"

    assert events == ["authorize", "stop"]
    assert store.list() == []


def test_disconnect_recognizes_legacy_numeric_string_expiration(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    session["expires_at"] = "1"
    store.write(session)
    api = Mock()
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    monkeypatch.setattr(client, "_forwarder_running", Mock(return_value=False))

    assert client.disconnect_session(session, store, Mock()) == "expired"

    api.call.assert_not_called()
    assert store.list() == []


def test_disconnect_closed_session_waits_for_allocation_cleanup(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    store.write(session)
    api = Mock()
    api.call.side_effect = [
        client.APIError(409, '{"error":"session closed or expired"}'),
        client.APIError(409, '{"error":"session closed or expired"}'),
        client.APIError(404, "not found"),
    ]
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client, "_stop_forwarder", Mock())
    monkeypatch.setattr(client.time, "sleep", Mock())

    client.disconnect_session(session, store, Mock())

    assert [call.args[:2] for call in api.call.call_args_list] == [
        ("DELETE", "/v1/p2p/ice-sessions/" + session["session_id"]),
        ("GET", "/v1/p2p/ice-sessions"),
        ("GET", "/v1/p2p/ice-sessions"),
    ]
    assert store.list() == []


def test_disconnect_stale_record_preserves_newer_recorded_session(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    stale = session_record("b" * 32)
    current = session_record("c" * 32)
    store.write(stale)
    store.write(current)
    api = Mock()
    api.call.side_effect = [
        client.APIError(404, "not found"),
        {"session_id": current["session_id"], "status": "connected_direct"},
    ]
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client, "_stop_forwarder", Mock())

    result = client.disconnect_session(stale, store, Mock())

    assert result == "stale"
    assert [item["session_id"] for item in store.list()] == [current["session_id"]]
    assert [call.args[:2] for call in api.call.call_args_list] == [
        ("DELETE", "/v1/p2p/ice-sessions/" + stale["session_id"]),
        ("GET", "/v1/p2p/ice-sessions"),
    ]


def test_disconnect_failure_preserves_retry_state(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path / "cloudex")
    session = session_record()
    store.write(session)
    api = Mock()
    api.call.side_effect = client.CloudExError("offline")
    monkeypatch.setattr(client.CloudExAPI, "from_session", Mock(return_value=api))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client, "_stop_forwarder", Mock())

    with pytest.raises(client.CloudExError, match="offline"):
        client.disconnect_session(session, store, Mock())

    assert store.list()[0]["session_id"] == session["session_id"]


def test_stop_does_not_signal_reused_non_forwarder_pid(monkeypatch):
    monkeypatch.setattr(client, "_process_running", Mock(return_value=True))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="/usr/bin/python service.py\n"))
    monkeypatch.setattr(client.subprocess, "run", run)

    client._stop_forwarder({"forwarder_pid": 123})

    assert run.call_count == 1
    assert run.call_args.args[0][0] == "ps"


def test_benchmark_requires_bidirectional_metrics(monkeypatch):
    report = {
        "parallel_streams": 8,
        "latency_ms": 8.5,
        "host_to_device_throughput_mbps": 51.2,
        "host_to_device_transferred_bytes": 6400000,
        "device_to_host_throughput_mbps": 47.3,
        "device_to_host_transferred_bytes": 5900000,
    }
    monkeypatch.setattr(
        client.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(report))),
    )

    assert client.run_benchmark("forwarder", "10.252.1.2", 10) == report

    del report["device_to_host_throughput_mbps"]
    client.subprocess.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(report))
    with pytest.raises(client.CloudExError, match="bidirectional"):
        client.run_benchmark("forwarder", "10.252.1.2", 10)


@pytest.mark.parametrize("brew_bin", ["/opt/homebrew/bin", "/usr/local/bin"])
def test_macos_forwarder_finds_homebrew_with_restricted_path(monkeypatch, brew_bin):
    client.sys.platform = "darwin"
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    which = Mock(side_effect=lambda name, path: brew_bin + "/" + name
                 if brew_bin in path.split(":") else None)
    monkeypatch.setattr(client.shutil, "which", which)

    assert client._forwarder_sudo_prefix() == [
        "sudo", "-n", "--", "/usr/bin/env", "PATH=" + client.MACOS_TUNNEL_PATH,
    ]
    assert [call.args[0] for call in which.call_args_list] == ["wg", "wireguard-go"]
    assert all(call.kwargs["path"] == client.MACOS_TUNNEL_PATH for call in which.call_args_list)


@pytest.mark.parametrize("brew_bin", ["/opt/homebrew/bin", "/usr/local/bin"])
@pytest.mark.parametrize("missing", [("wg",), ("wireguard-go",), ("wg", "wireguard-go")])
def test_macos_installs_missing_dependencies_as_user(monkeypatch, brew_bin, missing):
    client.sys.platform = "darwin"
    installed = set()

    def which(name, path):
        assert path == client.MACOS_TUNNEL_PATH
        return None if name in missing and name not in installed else brew_bin + "/" + name

    def install(command, **kwargs):
        installed.update(missing)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(client.shutil, "which", which)
    run = Mock(side_effect=install)
    monkeypatch.setattr(client.subprocess, "run", run)
    prefix = client._forwarder_sudo_prefix(progress=Mock())
    assert prefix == ["sudo", "-n", "--", "/usr/bin/env", "PATH=" + client.MACOS_TUNNEL_PATH]
    run.assert_called_once()
    assert run.call_args.args[0] == [brew_bin + "/brew", "install", *[
        "wireguard-tools" if name == "wg" else name for name in missing]]
    assert run.call_args.kwargs["env"]["PATH"] == client.MACOS_TUNNEL_PATH
    assert run.call_args.kwargs["stdin"] is client.subprocess.DEVNULL
    assert run.call_args.kwargs["timeout"] == 600


@pytest.mark.parametrize("failure", ["no-brew", "exit", "timeout", "oserror", "still-missing"])
def test_macos_dependency_failure_stops_before_session_request(monkeypatch, tmp_path, failure):
    client.sys.platform = "darwin"
    monkeypatch.setattr(client.shutil, "which", lambda name, path:
                        "/opt/homebrew/bin/brew" if name == "brew" and failure != "no-brew" else None)
    run = Mock(return_value=SimpleNamespace(returncode=0, stderr="", stdout=""))
    if failure == "exit":
        run.return_value = SimpleNamespace(returncode=1, stderr="brew install failed", stdout="")
    elif failure == "timeout":
        run.side_effect = client.subprocess.TimeoutExpired("brew", 600)
    elif failure == "oserror":
        run.side_effect = OSError("brew unavailable")
    monkeypatch.setattr(client.subprocess, "run", run)
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client, "_reconcile_existing_connection", Mock(return_value=None))
    api = Mock()
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))
    install = Mock()
    monkeypatch.setattr(installer, "ensure_forwarder", install)

    with pytest.raises(client.CloudExError) as error:
        client.connect(allocation_profile(), client.SessionStore(tmp_path), Mock())

    expected = {"no-brew": "Install Homebrew", "exit": "brew install failed",
                "timeout": "Could not install", "oserror": "Could not install",
                "still-missing": "still cannot find"}
    assert expected[failure] in str(error.value)
    api.call.assert_not_called()
    install.assert_not_called()
    if failure == "no-brew":
        run.assert_not_called()


@pytest.mark.parametrize("detail", ["WireGuard is unavailable", "start wireguard-go"])
def test_macos_forwarder_failure_includes_install_instruction(tmp_path, detail):
    client.sys.platform = "darwin"
    log = tmp_path / "forwarder.log"
    log.write_text("kerrigan-p2p-forwarder: starting WireGuard tunnel\n" + detail)

    message = client._forwarder_failure(log)

    assert "brew install wireguard-tools" in message
    assert message.endswith("; last stage: starting WireGuard tunnel")


def test_cloudex_diagnostics_posts_only_safe_stages_for_the_remote_session(tmp_path):
    store = client.SessionStore(tmp_path)
    local_id, sid = "a" * 32, "b" * 32
    _, _, _, log = store.runtime_paths(local_id)
    log.parent.mkdir(parents=True)
    log.write_text("kerrigan-p2p-forwarder: ICE connection state Failed\n"
                   "kerrigan-p2p-forwarder: ICE diagnostics phase=selected elapsed_ms=1148 pairs=6\n"
                   "kerrigan-p2p-forwarder: signaling token=fixture-secret\n")
    calls = []

    class API:
        def call(self, method, route, data, **kwargs):
            calls.append((method, route, data))
            raise client.CloudExError("diagnostics temporarily unavailable")

    session = {"session_id": local_id, "remote_session_id": sid}
    client._submit_diagnostics(API(), session, store, "failed")
    assert calls == []
    session["diagnostics"] = True
    client._submit_diagnostics(API(), session, store, "failed")
    assert calls == [("PUT", f"/v1/p2p/ice-sessions/{sid}/diagnostics",
                      {"reason": "failed", "stages": ["ICE connection state Failed"]})]
    client._submit_diagnostics(API(), session, store, "direct", setup_ms=1520)
    assert len(calls) == 1
    session["diagnostics_version"] = 2
    client._submit_diagnostics(API(), session, store, "direct", setup_ms=1520)
    assert calls[-1] == ("PUT", f"/v1/p2p/ice-sessions/{sid}/diagnostics", {
        "reason": "direct", "stages": ["ICE connection state Failed"],
        "timing": {"setup_ms": 1520, "ice_selection_ms": 1148},
    })


def test_failed_connection_reports_after_attempting_cleanup(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path)
    profile = allocation_profile()
    sid = "d" * 32
    api = Mock(url=profile["api_url"], requester=profile["requester"],
               secret=profile["allocation_secret"], allocation_id=profile["allocation_id"])
    api.call.return_value = {"session_id": sid, "diagnostics": True, "expires_at": 9999999999}
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client, "_forwarder_sudo_prefix", Mock(return_value=[]))
    monkeypatch.setattr(installer, "ensure_forwarder", Mock(return_value=tmp_path / "forwarder"))
    monkeypatch.setattr(client.subprocess, "Popen", Mock(return_value=Mock(pid=123)))
    operations = []

    def fail(_sid, _process, _report_path, log_path, *_args, **_kwargs):
        log_path.write_text("kerrigan-p2p-forwarder: ICE connection state Failed\n")
        raise client.CloudExError("Original tunnel failure")

    def cleanup(*_args, **_kwargs):
        operations.append("cleanup")

    def report(allocation_id, session_id, reason, stages):
        operations.append((allocation_id, session_id, reason, stages))
        raise client.CloudExError("Diagnostics unavailable")

    monkeypatch.setattr(client, "_wait_forwarder", fail)
    monkeypatch.setattr(client, "disconnect_session", cleanup)
    with pytest.raises(client.CloudExError, match="Original tunnel failure"):
        client.connect(profile, store, Mock(), attempts=1, transport="p2p", report_diagnostics=report)
    assert operations == ["cleanup", (profile["allocation_id"], sid, "failed", ["ICE connection state Failed"])]


def test_auto_connect_moves_to_relay_after_one_direct_preferred_attempt(tmp_path, monkeypatch):
    store = client.SessionStore(tmp_path)
    profile = allocation_profile()
    api = Mock(url=profile["api_url"], requester=profile["requester"],
               secret=profile["allocation_secret"], allocation_id=profile["allocation_id"])
    api.call.return_value = {"session_id": "d" * 32, "expires_at": 9999999999,
                             "wireguard": {"remote_address": "10.252.1.2/32"}}
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client, "_forwarder_sudo_prefix", Mock(return_value=[]))
    monkeypatch.setattr(installer, "ensure_forwarder", Mock(return_value=tmp_path / "forwarder"))
    monkeypatch.setattr(client, "disconnect_session", Mock())
    transports = []

    def start(command, **_kwargs):
        with open(command[command.index("--config") + 1], encoding="utf-8") as stream:
            configuration = json.load(stream)
        transports.append(configuration["transport"])
        return Mock(pid=123)

    monkeypatch.setattr(client.subprocess, "Popen", start)
    clock = Mock(return_value=1000)
    monkeypatch.setattr(client.time, "time", clock)

    def wait_for_attempt(_sid, _process, _report, _log, deadline, _progress, **_kwargs):
        if len(transports) == 1:
            assert deadline == 1020
            clock.return_value = deadline
            raise client.DirectHandshakeError("No usable path")
        if len(transports) == 2:
            assert deadline == 1055
            clock.return_value = deadline
            raise client.DirectHandshakeError("WireGuard handshake stalled")
        assert deadline == 1060
        return {"status": "connected", "path": "relay"}

    wait = Mock(side_effect=wait_for_attempt)
    monkeypatch.setattr(client, "_wait_forwarder", wait)

    result = client.connect(profile, store, Mock())

    assert transports == ["p2p", "routed", "routed"]
    assert result["ice_path"] == "relay"
    assert client.disconnect_session.call_count == 2
