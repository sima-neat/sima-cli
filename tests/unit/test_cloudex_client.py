import base64
import json
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sima_cli.cloudex import client


def allocation_profile():
    return {
        "environment": "vulcan-staging",
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


def test_decode_key_accepts_exact_allocation_contract():
    profile = allocation_profile()
    encoded = base64.b64encode(json.dumps(profile).encode()).decode()

    assert client.decode_key(encoded) == profile


def test_decode_key_rejects_non_staging_environment():
    profile = allocation_profile()
    profile["environment"] = "vulcan-production"
    encoded = base64.b64encode(json.dumps(profile).encode()).decode()

    with pytest.raises(client.CloudExError, match="vulcan-staging"):
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


def test_api_signs_allocation_scoped_request(monkeypatch):
    response = Mock(ok=True)
    response.json.return_value = {"status": "connected_direct"}
    request = Mock(return_value=response)
    monkeypatch.setattr(client.requests, "request", request)
    api = client.CloudExAPI.from_profile(allocation_profile())

    assert api.call("POST", "/v1/p2p/ice-sessions", {"request_id": "b" * 32}) == {
        "status": "connected_direct"
    }

    kwargs = request.call_args.kwargs
    assert kwargs["headers"]["x-p2p-allocation-id"] == "a" * 32
    assert len(kwargs["headers"]["x-p2p-signature"]) == 64
    assert "allocation_secret" not in kwargs["data"].decode()


def test_connect_persists_connected_session_and_removes_ephemeral_config(tmp_path, monkeypatch):
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
    api.call.return_value = allocated
    monkeypatch.setattr(client, "find_forwarder", Mock(return_value=forwarder))
    monkeypatch.setattr(client, "authorize_admin", Mock())
    monkeypatch.setattr(client.CloudExAPI, "from_profile", Mock(return_value=api))
    popen = Mock(return_value=process)
    monkeypatch.setattr(client.subprocess, "Popen", popen)
    monkeypatch.setattr(
        client,
        "_wait_forwarder",
        Mock(return_value={"status": "connected", "path": "direct"}),
    )

    result = client.connect(allocation_profile(), store, Mock(), attempts=1, transport="p2p")

    assert result["device"] == "ll2"
    assert result["target"] == "10.252.1.2"
    assert result["ice_path"] == "direct"
    assert store.list()[0]["api_secret"] == allocation_profile()["allocation_secret"]
    assert not store.runtime_paths(result["session_id"])[1].exists()
    command = popen.call_args.args[0]
    assert command[:3] == ["sudo", "-n", "--"]
    assert popen.call_args.kwargs["stdin"] is client.subprocess.DEVNULL
    api.call.assert_called_once_with(
        "POST", "/v1/p2p/ice-sessions", {"request_id": result["session_id"]}
    )
    assert result["remote_session_id"] == "d" * 32


def test_forwarder_failure_explains_sudo_terminal_requirement(tmp_path):
    log = tmp_path / "forwarder.log"
    log.write_text("sudo: a password is required\n")

    assert client._forwarder_failure(log) == (
        "administrator authorization is required; run CloudEx from an interactive terminal"
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
    monkeypatch.setattr(client, "find_forwarder", Mock(side_effect=client.CloudExError("missing")))
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
    monkeypatch.setattr(client, "find_forwarder", Mock(return_value=tmp_path / "forwarder"))
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
    monkeypatch.setattr(client, "find_forwarder", Mock(return_value=tmp_path / "forwarder"))
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
