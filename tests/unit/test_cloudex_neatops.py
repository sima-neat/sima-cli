import hashlib
import base64
import json
import os
import threading
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from click.testing import CliRunner

from sima_cli.cloudex import neatops, commands
from sima_cli.cloudex.client import CloudExError


@pytest.fixture
def api(tmp_path):
    client = neatops.NeatOps("staging", root=tmp_path)
    client.config = {"apiBase": client.dashboard + "/api", "auth": {
        "enabled": True, "domain": "https://login.example.com", "clientId": "sso-client",
        "redirectUri": neatops.REDIRECT_URI, "identityProvider": "Workspace"}}
    return client


def tokens(**kwargs):
    return {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 3600, **kwargs}


def test_browser_login_uses_pkce_and_rejects_wrong_state(api, monkeypatch):
    callbacks, urls, workers = [], [], []
    def browser(url):
        urls.append(url)
        params = parse_qs(urlsplit(url).query)
        def respond():
            for state in ("wrong-state", params["state"][0]):
                callbacks.append(requests.get(neatops.REDIRECT_URI,
                    params={"state": state, "code": "private-code"}, timeout=5).status_code)
        worker = threading.Thread(target=respond)
        workers.append(worker)
        worker.start()
    monkeypatch.setattr(neatops.webbrowser, "open", browser)
    exchange = Mock(return_value=tokens())
    monkeypatch.setattr(api, "_exchange", exchange)
    announced = []
    api.login(announced.append)
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert callbacks == [400, 200]
    params = parse_qs(urlsplit(urls[0]).query)
    assert params["identity_provider"] == ["Workspace"]
    assert params["redirect_uri"] == [neatops.REDIRECT_URI]
    arguments = exchange.call_args.args[0]
    digest = hashlib.sha256(arguments["code_verifier"].encode()).digest()
    assert params["code_challenge"] == [base64.urlsafe_b64encode(digest).decode().rstrip("=")]
    assert arguments["code"] == "private-code"
    assert "private" not in announced[0]
    assert api.access_token() == "private-access"
    if os.name != "nt":
        assert api.path.stat().st_mode & 0o777 == 0o600


def test_login_timeout_saves_no_credentials(api, monkeypatch):
    monkeypatch.setattr(neatops.webbrowser, "open", lambda _: False)
    with pytest.raises(CloudExError, match="timed out"):
        api.login(lambda _: None, timeout=0)
    assert not api.path.exists()


def test_expired_access_refresh_preserves_refresh_token(api, monkeypatch):
    api._save(tokens(expires_in=1))
    exchange = Mock(return_value={"access_token": "new-access", "expires_in": 3600})
    monkeypatch.setattr(api, "_exchange", exchange)
    assert api.access_token() == "new-access"
    assert json.loads(api.path.read_text())["refresh_token"] == "private-refresh"
    exchange.assert_called_once_with({"grant_type": "refresh_token", "refresh_token": "private-refresh"})


def test_client_policy_change_requires_new_login(api, monkeypatch):
    api._save(tokens())
    api.config["auth"]["clientId"] = "new-sso-client"
    with pytest.raises(CloudExError, match="policy changed"):
        api.access_token()


def test_environment_credentials_are_separate(api):
    api._save(tokens())
    production = neatops.NeatOps(root=api.path.parent)
    assert api.path != production.path
    with pytest.raises(CloudExError, match="Sign in first"):
        production._read_session()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_shared_or_symlink_credentials_rejected(api):
    api._save(tokens())
    api.path.chmod(0o644)
    with pytest.raises(CloudExError, match="0600"):
        api.access_token()
    target = api.path.with_name("target.json")
    api.path.rename(target)
    api.path.symlink_to(target)
    with pytest.raises(CloudExError, match="Sign in first"):
        api.access_token()


def test_signed_api_request_does_not_follow_redirects(api, monkeypatch):
    api._save(tokens())
    response = Mock(status_code=200)
    response.json.return_value = {"devices": []}
    request = Mock(return_value=response)
    monkeypatch.setattr(neatops.requests, "request", request)
    assert api.call("GET", "/devkits") == {"devices": []}
    assert request.call_args.kwargs["allow_redirects"] is False
    assert request.call_args.kwargs["headers"] == {"Authorization": "Bearer private-access"}


@pytest.mark.parametrize("status", [302, 401, 403, 409, 500])
def test_api_errors_never_expose_response_secrets(api, monkeypatch, status):
    api._save(tokens())
    monkeypatch.setattr(neatops, "_json_request", lambda *a, **k: (status, {"message": "private-secret"}))
    with pytest.raises(CloudExError) as exc:
        api.call("GET", "/devkits")
    assert "private-secret" not in str(exc.value)


def test_config_rejects_cross_origin_api(api, monkeypatch):
    config = {**api.config, "apiBase": "https://attacker.example/api"}
    api.config = None
    monkeypatch.setattr(neatops, "_json_request", lambda *a, **k: (200, config))
    with pytest.raises(CloudExError, match="configuration"):
        api.configuration()


def test_logout_revokes_refresh_and_removes_credentials(api, monkeypatch):
    api._save(tokens())
    request = Mock(return_value=(200, {}))
    monkeypatch.setattr(neatops, "_json_request", request)
    api.logout()
    assert not api.path.exists()
    assert request.call_args.args[1].endswith("/oauth2/revoke")
    assert request.call_args.kwargs["data"]["token"] == "private-refresh"


def test_failed_revocation_retains_credentials_for_retry(api, monkeypatch):
    api._save(tokens())
    monkeypatch.setattr(neatops, "_json_request", lambda *a, **k: (500, {}))
    with pytest.raises(CloudExError, match="revocation"):
        api.logout()
    assert api.path.exists()


DEVICE = {"name": "ll2", "id": "device-id", "cluster_id": "appliance-id", "available": True}
AID = "a" * 32
PROFILE = {"environment": "staging", "api_url": "https://p2p.example.com", "requester": "user",
           "allocation_id": AID, "allocation_secret": "s" * 48}


@pytest.fixture
def command_api(monkeypatch):
    api = Mock()
    def call(method, path, data=None):
        if path == "/users/me/entitlement":
            return {"entitlement": {"max_active_leases": 1, "max_lease_duration_seconds": 14400}}
        if path == "/devkits":
            return {"devices": [DEVICE], "allocations": []}
        return {"session_id": AID}
    api.call.side_effect = call
    monkeypatch.setattr(commands, "NeatOps", Mock(return_value=api))
    monkeypatch.setattr(commands, "_authorize_supported_host", Mock())
    return api


def test_request_selects_device_allocates_then_connects(command_api, monkeypatch):
    monkeypatch.setattr(commands.uuid, "uuid4", lambda: Mock(hex=AID))
    profile = Mock(return_value=PROFILE)
    connect = Mock()
    monkeypatch.setattr(commands, "_allocation_profile", profile)
    monkeypatch.setattr(commands, "_connect_profile", connect)
    result = CliRunner().invoke(commands.cloudex_group, ["request", "--duration", "4h", "--environment", "staging"], input="1\n")
    assert result.exit_code == 0, result.output
    command_api.call.assert_any_call("POST", "/devkits/allocations", {"request_id": AID,
        "cluster_id": "appliance-id", "target_id": "device-id", "duration_seconds": 14400})
    profile.assert_called_once_with(command_api, AID)
    connect.assert_called_once_with(PROFILE, verbose=False)
    assert "allocation_secret" not in result.output


def test_request_rejects_duration_before_allocating(command_api):
    result = CliRunner().invoke(commands.cloudex_group, ["request", "--duration", "7d"])
    assert result.exit_code != 0
    assert "lease limit" in result.output
    assert command_api.call.call_count == 1


def test_failed_connection_displays_resume_and_release(command_api, monkeypatch):
    monkeypatch.setattr(commands.uuid, "uuid4", lambda: Mock(hex=AID))
    monkeypatch.setattr(commands, "_allocation_profile", lambda *a: PROFILE)
    monkeypatch.setattr(commands, "_connect_profile", Mock(side_effect=CloudExError("Tunnel failed")))
    result = CliRunner().invoke(commands.cloudex_group, ["request", "--device", "device-id", "--environment", "staging"])
    assert result.exit_code != 0
    assert "cloudex connect --allocation " + AID in result.output
    assert "cloudex release " + AID in result.output
    assert "--environment staging" in result.output
    assert not any(c.args[0] == "DELETE" for c in command_api.call.call_args_list)


def test_waits_for_ready_before_fetching_profile(monkeypatch):
    api = Mock()
    api.call.side_effect = [
        {"allocations": [{"session_id": AID, "status": "reserving"}]},
        {"allocations": [{"session_id": AID, "status": "ready"}]}, PROFILE]
    monkeypatch.setattr(commands.time, "sleep", lambda _: None)
    assert commands._allocation_profile(api, AID) == PROFILE
    assert api.call.call_args.args == ("POST", "/devkits/allocations/" + AID + "/profile", {})


def test_preparation_timeout_retains_allocation():
    api = Mock()
    api.call.return_value = {"allocations": [{"session_id": AID, "status": "reserving"}]}
    with pytest.raises(CloudExError, match="retained"):
        commands._allocation_profile(api, AID, timeout=0)
    api.call.assert_called_once_with("GET", "/devkits")


def test_connect_allocation_skips_manual_key_prompt(command_api, monkeypatch):
    monkeypatch.setattr(commands, "_allocation_profile", Mock(return_value=PROFILE))
    connect = Mock()
    monkeypatch.setattr(commands, "_connect_profile", connect)
    result = CliRunner().invoke(commands.cloudex_group, ["connect", "--allocation", AID])
    assert result.exit_code == 0, result.output
    connect.assert_called_once_with(PROFILE, verbose=False, attempts=3, transport="auto")


def test_release_disconnects_only_matching_local_allocation(command_api, monkeypatch):
    sessions = [{"allocation_id": AID}, {"allocation_id": "b" * 32}]
    store = Mock()
    store.list.return_value = sessions
    monkeypatch.setattr(commands, "_store", lambda: store)
    disconnect = Mock()
    monkeypatch.setattr(commands, "disconnect_session", disconnect)
    result = CliRunner().invoke(commands.cloudex_group, ["release", AID])
    assert result.exit_code == 0, result.output
    assert disconnect.call_count == 1
    assert disconnect.call_args.args[0] == sessions[0]
    command_api.call.assert_called_once_with("DELETE", "/devkits/allocations/" + AID)


def test_cancelled_browser_login_keeps_previous_credentials(api, monkeypatch):
    api._save(tokens())
    before = api.path.read_bytes()
    workers = []
    def browser(url):
        state = parse_qs(urlsplit(url).query)["state"][0]
        worker = threading.Thread(target=lambda: requests.get(neatops.REDIRECT_URI,
            params={"state": state, "error": "access_denied", "error_description": "private-detail"}, timeout=5))
        workers.append(worker)
        worker.start()
    monkeypatch.setattr(neatops.webbrowser, "open", browser)
    with pytest.raises(CloudExError, match="cancelled or rejected"):
        api.login(lambda _: None)
    for worker in workers:
        worker.join(5)
    assert api.path.read_bytes() == before


def test_no_available_devices_never_allocates(command_api):
    command_api.call.side_effect = [
        {"entitlement": {"max_active_leases": 1, "max_lease_duration_seconds": 3600}},
        {"devices": [{**DEVICE, "available": False}], "allocations": []}]
    result = CliRunner().invoke(commands.cloudex_group, ["request", "--device", "device-id"])
    assert result.exit_code != 0
    assert "No matching devices" in result.output
    assert command_api.call.call_count == 2


def test_ambiguous_request_failure_is_not_retried(command_api, monkeypatch):
    call = command_api.call.side_effect
    def fail_request(method, path, data=None):
        if method == "POST":
            raise CloudExError("NeatOps is unavailable")
        return call(method, path, data)
    command_api.call.side_effect = fail_request
    monkeypatch.setattr(commands.uuid, "uuid4", lambda: Mock(hex=AID))
    result = CliRunner().invoke(commands.cloudex_group, ["request", "--device", "device-id"])
    assert result.exit_code != 0
    assert AID in result.output
    assert sum(c.args[0] == "POST" for c in command_api.call.call_args_list) == 1


def test_existing_lease_limit_does_not_request_another_device(command_api):
    command_api.call.side_effect = [
        {"entitlement": {"max_active_leases": 1, "max_lease_duration_seconds": 3600}},
        {"devices": [DEVICE], "allocations": [{"session_id": AID}]}]
    result = CliRunner().invoke(commands.cloudex_group, ["request", "--device", "device-id"])
    assert result.exit_code != 0
    assert "lease limit is reached" in result.output
    assert command_api.call.call_count == 2


def test_root_cloudex_login_bypasses_developer_portal_initialization(monkeypatch):
    import sima_cli.cli as cli
    initialize = Mock(side_effect=AssertionError("Unexpected portal initialization"))
    monkeypatch.setattr(cli, "_initialize_main_context", initialize)
    login = Mock()
    monkeypatch.setattr(commands, "_neatops_login", login)
    result = CliRunner().invoke(cli.main, ["cloudex", "login"], env={"SIMA_CLI_INTERNAL": "1"})
    assert result.exit_code == 0, result.output
    initialize.assert_not_called()
    login.assert_called_once()


@pytest.mark.parametrize("cleanup_error", ["tunnel API unavailable", "remote tunnel cleanup is still pending"])
@pytest.mark.parametrize("lease_release_fails", [False, True])
def test_release_attempts_lease_delete_after_cleanup_failure(
    command_api, monkeypatch, tmp_path, cleanup_error, lease_release_fails
):
    from sima_cli.cloudex.client import SessionStore

    store = SessionStore(tmp_path)
    session = {"session_id": "b" * 32, "allocation_id": AID, "target": "10.252.1.2"}
    store.write(session)
    monkeypatch.setattr(commands, "_store", lambda: store)
    monkeypatch.setattr(commands, "disconnect_session", Mock(side_effect=CloudExError(cleanup_error)))
    if lease_release_fails:
        command_api.call.side_effect = CloudExError("NeatOps unavailable")

    result = CliRunner().invoke(commands.cloudex_group, ["release", AID, "--environment", "staging"])

    assert result.exit_code != 0
    command_api.call.assert_called_once_with("DELETE", "/devkits/allocations/" + AID)
    assert cleanup_error in result.output
    assert "Local retry state retained" in result.output
    assert "cloudex disconnect" in result.output
    assert store.list() == [session]
    if lease_release_fails:
        assert "Lease release failed: NeatOps unavailable" in result.output
        assert "Lease released;" not in result.output
    else:
        assert "Lease released;" in result.output


def test_release_continues_other_tunnel_cleanup_after_one_failure(command_api, monkeypatch):
    sessions = [{"allocation_id": AID, "session_id": "b" * 32},
                {"allocation_id": AID, "session_id": "c" * 32}]
    store = Mock()
    store.list.return_value = sessions
    monkeypatch.setattr(commands, "_store", lambda: store)
    disconnect = Mock(side_effect=[CloudExError("offline"), "disconnected"])
    monkeypatch.setattr(commands, "disconnect_session", disconnect)

    result = CliRunner().invoke(commands.cloudex_group, ["release", AID])

    assert result.exit_code != 0
    assert disconnect.call_count == 2
    assert [call.args[0] for call in disconnect.call_args_list] == sessions
    command_api.call.assert_called_once_with("DELETE", "/devkits/allocations/" + AID)


@pytest.fixture
def auto_login(api):
    callback = Mock(side_effect=lambda client: client._save(tokens(access_token="new-access")))
    api.on_login_required = callback
    return callback


@pytest.mark.parametrize("session_state", ["missing", "expired", "refresh-rejected", "policy-changed", "malformed"])
def test_api_automatically_signs_in_and_continues_original_request(api, auto_login, monkeypatch, session_state):
    if session_state in {"expired", "refresh-rejected"}:
        api._save(tokens(expires_in=1, refresh_token=None if session_state == "expired" else "old-refresh"))
    elif session_state == "policy-changed":
        api._save(tokens())
        api.config["auth"]["clientId"] = "updated-sso-client"
    elif session_state == "malformed":
        api.path.write_text("{broken-json")
        api.path.chmod(0o600)
    def request(method, url, **kwargs):
        if url.endswith("/oauth2/token"):
            return 400, {"error": "invalid_grant"}
        return 200, {"session_id": AID}
    request_mock = Mock(side_effect=request)
    monkeypatch.setattr(neatops, "_json_request", request_mock)
    data = {"request_id": AID, "target_id": "device-id"}

    assert api.call("POST", "/devkits/allocations", data) == {"session_id": AID}

    auto_login.assert_called_once_with(api)
    api_requests = [call for call in request_mock.call_args_list if call.args[1].endswith("/devkits/allocations")]
    assert len(api_requests) == 1
    assert api_requests[0].kwargs["json"] == data
    assert api_requests[0].kwargs["headers"] == {"Authorization": "Bearer new-access"}


def test_valid_session_does_not_start_browser_login(api, auto_login, monkeypatch):
    api._save(tokens())
    monkeypatch.setattr(neatops, "_json_request", Mock(return_value=(200, {"devices": []})))
    assert api.call("GET", "/devkits") == {"devices": []}
    auto_login.assert_not_called()


@pytest.mark.parametrize("status", [403, 409, 500])
def test_permissions_and_service_errors_do_not_start_login_or_retry(api, auto_login, monkeypatch, status):
    api._save(tokens())
    request = Mock(return_value=(status, {}))
    monkeypatch.setattr(neatops, "_json_request", request)
    with pytest.raises(CloudExError):
        api.call("POST", "/devkits/allocations", {"request_id": AID})
    auto_login.assert_not_called()
    assert request.call_count == 1


def test_refresh_service_failure_does_not_start_login(api, auto_login, monkeypatch):
    api._save(tokens(expires_in=1))
    monkeypatch.setattr(neatops, "_json_request", Mock(return_value=(503, {})))
    with pytest.raises(CloudExError):
        api.call("GET", "/devkits")
    auto_login.assert_not_called()


def test_authorizer_401_retries_same_request_once_after_browser_login(api, auto_login, monkeypatch):
    api._save(tokens())
    request = Mock(side_effect=[(401, {}), (200, {"session_id": AID})])
    monkeypatch.setattr(neatops, "_json_request", request)
    data = {"request_id": AID}
    assert api.call("POST", "/devkits/allocations", data) == {"session_id": AID}
    auto_login.assert_called_once_with(api)
    assert request.call_count == 2
    assert [call.kwargs["json"] for call in request.call_args_list] == [data, data]
    assert request.call_args.kwargs["headers"] == {"Authorization": "Bearer new-access"}


def test_rejected_new_login_does_not_loop(api, auto_login, monkeypatch):
    request = Mock(return_value=(401, {}))
    monkeypatch.setattr(neatops, "_json_request", request)
    with pytest.raises(CloudExError, match="access denied"):
        api.call("GET", "/devkits")
    auto_login.assert_called_once_with(api)
    assert request.call_count == 1


def test_cancelled_automatic_login_does_not_send_allocation_request(api, auto_login, monkeypatch):
    auto_login.side_effect = CloudExError("NeatOps sign-in was cancelled or rejected")
    request = Mock()
    monkeypatch.setattr(neatops, "_json_request", request)
    with pytest.raises(CloudExError, match="cancelled"):
        api.call("POST", "/devkits/allocations", {"request_id": AID})
    request.assert_not_called()
    auto_login.assert_called_once_with(api)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_insecure_credentials_do_not_trigger_automatic_login(api, auto_login):
    api._save(tokens())
    api.path.chmod(0o644)
    with pytest.raises(CloudExError, match="0600"):
        api.call("GET", "/devkits")
    auto_login.assert_not_called()


def test_command_api_enables_automatic_browser_login(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(commands, "NeatOps", factory)
    assert commands._neatops_api("staging") == factory.return_value
    factory.assert_called_once_with("staging", on_login_required=commands._neatops_login)
