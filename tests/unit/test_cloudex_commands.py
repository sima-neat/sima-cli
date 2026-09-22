from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.cloudex import commands


@pytest.fixture(autouse=True)
def _authorize_cloudex(monkeypatch):
    authorize = Mock()
    monkeypatch.setattr(commands, "authorize_admin", authorize)
    return authorize


def _session(session_id, device):
    return {
        "session_id": session_id,
        "allocation_id": "a" * 32,
        "device": device,
        "target": "10.252.1.2",
        "ice_path": "direct",
    }


def invoke_main(arguments, **kwargs):
    with patch("sima_cli.cli._initialize_main_context"):
        return CliRunner().invoke(main, arguments, **kwargs)


def test_cloudex_is_hidden_from_root_help_but_directly_available():
    root = invoke_main(["--help"])
    direct = invoke_main(["cloudex", "--help"])

    assert root.exit_code == 0
    assert "cloudex" not in root.output
    assert direct.exit_code == 0
    assert "connect" in direct.output
    assert "disconnect" in direct.output
    assert "list" in direct.output
    assert "update" in direct.output


def test_connect_prompts_for_backend_key_without_putting_it_in_argv(monkeypatch):
    session = _session("b" * 32, "ll2")
    monkeypatch.setattr(commands, "decode_key", Mock(return_value={"profile": True}))
    connect = Mock(return_value=session)
    monkeypatch.setattr(commands, "connect", connect)

    result = invoke_main(["cloudex", "connect"], input="encoded\n")

    assert result.exit_code == 0, result.output
    assert "encoded" not in result.output
    assert "Connected to ll2" in result.output
    assert "ssh sima@10.252.1.2" in result.output
    assert connect.call_args.kwargs == {"attempts": 3, "transport": "auto"}


def test_connect_verbose_prints_sanitized_connection_stages(monkeypatch):
    session = _session("b" * 32, "ll2")
    monkeypatch.setattr(commands, "decode_key", Mock(return_value={"profile": True}))

    def staged_connect(_profile, _store, progress, **_kwargs):
        progress.update("joined authenticated signaling session")
        progress.update("ICE connectivity established")
        return session

    monkeypatch.setattr(commands, "connect", staged_connect)

    result = invoke_main(["cloudex", "connect", "--verbose"], input="encoded\n")

    assert result.exit_code == 0, result.output
    assert "joined authenticated signaling session" in result.output
    assert "ICE connectivity established" in result.output
    assert "encoded" not in result.output


def test_connect_does_not_accept_allocation_key_in_argv():
    result = invoke_main(["cloudex", "connect", "--key", "encoded"])

    assert result.exit_code == 2
    assert "No such option '--key'" in result.output


def test_connect_reads_protected_key_file(tmp_path, monkeypatch):
    key_file = tmp_path / "cloudex-key"
    key_file.write_text("encoded\n")
    key_file.chmod(0o600)
    session = _session("b" * 32, "ll2")
    decode = Mock(return_value={"profile": True})
    monkeypatch.setattr(commands, "decode_key", decode)
    monkeypatch.setattr(commands, "connect", Mock(return_value=session))

    result = invoke_main(["cloudex", "connect", "--key-file", str(key_file)])

    assert result.exit_code == 0, result.output
    decode.assert_called_once_with("encoded")


def test_connect_rejects_key_file_with_broad_permissions(tmp_path, monkeypatch):
    key_file = tmp_path / "cloudex-key"
    key_file.write_text("encoded\n")
    key_file.chmod(0o644)

    result = invoke_main(["cloudex", "connect", "--key-file", str(key_file)])

    assert result.exit_code == 1
    assert "mode 0600" in result.output


def test_connect_reads_key_from_stdin_for_automation(monkeypatch):
    session = _session("b" * 32, "ll2")
    decode = Mock(return_value={"profile": True})
    monkeypatch.setattr(commands, "decode_key", decode)
    monkeypatch.setattr(commands, "connect", Mock(return_value=session))

    result = invoke_main(["cloudex", "connect", "--key-stdin"], input="encoded\n")

    assert result.exit_code == 0, result.output
    decode.assert_called_once_with("encoded")


def test_update_defaults_to_develop_and_reports_installed_version(monkeypatch):
    forwarder = "/usr/local/bin/kerrigan-p2p-forwarder"
    update = Mock(return_value={
        "branch": "develop",
        "version": "0.1.0+abc1234",
        "artifact": "abc1234",
        "path": forwarder,
    })
    monkeypatch.setattr(commands, "install_forwarder", update)

    result = invoke_main(["cloudex", "update"])

    assert result.exit_code == 0, result.output
    assert "updated from develop" in result.output
    assert "0.1.0+abc1234" in result.output
    assert forwarder in result.output
    assert update.call_args.args[0] == "develop"


def test_update_accepts_explicit_branch(monkeypatch):
    update = Mock(return_value={
        "branch": "release/test",
        "version": "1.0",
        "artifact": "abc1234",
        "path": "/usr/local/bin/kerrigan-p2p-forwarder",
    })
    monkeypatch.setattr(commands, "install_forwarder", update)

    result = invoke_main(["cloudex", "update", "--branch", "release/test"])

    assert result.exit_code == 0, result.output
    assert update.call_args.args[0] == "release/test"


def test_update_on_native_windows_skips_sudo_and_reports_unsupported_package(
    monkeypatch, _authorize_cloudex
):
    monkeypatch.setattr(commands.os, "name", "nt")
    monkeypatch.setattr(
        commands,
        "install_forwarder",
        Mock(side_effect=commands.CloudExError(
            "CloudEx forwarder packages are unavailable for Windows amd64"
        )),
    )

    result = invoke_main(["cloudex", "update"])

    assert result.exit_code == 1
    assert "packages are unavailable for Windows" in result.output
    _authorize_cloudex.assert_not_called()


def test_disconnect_requires_selection_for_multiple_noninteractive_sessions(monkeypatch):
    store = Mock()
    store.list.return_value = [_session("a" * 32, "one"), _session("b" * 32, "two")]
    monkeypatch.setattr(commands, "_store", Mock(return_value=store))

    result = invoke_main(["cloudex", "disconnect"])

    assert result.exit_code == 1
    assert "use --session ID or --all" in result.output


def test_disconnect_all_handles_each_session(monkeypatch):
    store = Mock()
    sessions = [_session("a" * 32, "one"), _session("b" * 32, "two")]
    store.list.return_value = sessions
    monkeypatch.setattr(commands, "_store", Mock(return_value=store))
    disconnect = Mock(return_value="disconnected")
    monkeypatch.setattr(commands, "disconnect_session", disconnect)

    result = invoke_main(["cloudex", "disconnect", "--all"])

    assert result.exit_code == 0, result.output
    assert disconnect.call_count == 2
    assert "Disconnected one" in result.output
    assert "Disconnected two" in result.output


def test_disconnect_reports_when_stale_record_preserves_newer_session(monkeypatch):
    store = Mock()
    store.list.return_value = [_session("a" * 32, "one")]
    monkeypatch.setattr(commands, "_store", Mock(return_value=store))
    monkeypatch.setattr(commands, "disconnect_session", Mock(return_value="stale"))

    result = invoke_main(["cloudex", "disconnect"])

    assert result.exit_code == 0, result.output
    assert "newer connection retained" in result.output


def test_list_benchmark_reports_both_directions_and_leaves_failed_tunnel_connected(monkeypatch):
    store = Mock()
    sessions = [_session("a" * 32, "one"), _session("b" * 32, "two")]
    store.list.return_value = sessions
    monkeypatch.setattr(commands, "_store", Mock(return_value=store))
    monkeypatch.setattr(commands, "find_forwarder", Mock(return_value="forwarder"))
    monkeypatch.setattr(
        commands,
        "inspect_session",
        Mock(return_value={"connected": True, "status": "connected", "path": "direct"}),
    )
    benchmark = Mock(side_effect=[
        {
            "latency_ms": 8.5,
            "host_to_device_throughput_mbps": 51.2,
            "host_to_device_transferred_bytes": 6400000,
            "device_to_host_throughput_mbps": 47.3,
            "device_to_host_transferred_bytes": 5900000,
        },
        commands.CloudExError("listener unavailable"),
    ])
    monkeypatch.setattr(commands, "run_benchmark", benchmark)

    result = invoke_main(["cloudex", "list", "--benchmark", "--duration", "3"])

    assert result.exit_code == 0, result.output
    assert "Host → DevKit" in result.output
    assert "DevKit → Host" in result.output
    assert "51.2 Mbps" in result.output
    assert "47.3 Mbps" in result.output
    assert "Some benchmarks were unavailable" in result.output
