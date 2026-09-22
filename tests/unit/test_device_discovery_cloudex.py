from unittest.mock import Mock

from sima_cli.discover import discover


def test_cloudex_discovery_includes_only_connected_sessions(monkeypatch):
    connected = {
        "session_id": "a" * 32,
        "allocation_id": "b" * 32,
        "device": "DevKit ll2",
        "target": "10.252.7.2",
    }
    disconnected = {
        "session_id": "c" * 32,
        "allocation_id": "d" * 32,
        "device": "DevKit old",
        "target": "10.252.8.2",
    }
    store = Mock()
    store.list.return_value = [connected, disconnected]
    monkeypatch.setattr("sima_cli.cloudex.client.SessionStore", Mock(return_value=store))
    monkeypatch.setattr(
        "sima_cli.cloudex.client.inspect_session",
        Mock(side_effect=[
            {"connected": True, "path": "relay"},
            {"connected": False, "path": "unknown"},
        ]),
    )
    probe = Mock(return_value=("modalix", "3.0.0_develop_B1454", "modalix-som", True, "elxr"))
    monkeypatch.setattr(discover, "get_remote_board_info", probe)

    assert discover.discover_cloudex_devices() == [{
        "ip": "10.252.7.2",
        "mac": "—",
        "device": "DevKit ll2",
        "board": "modalix",
        "version": "3.0.0_develop_B1454",
        "model": "modalix-som",
        "full_image": True,
        "fwtype": "elxr",
        "source": "CloudEx",
        "path": "relayed",
        "connection": "CloudEx (relayed)",
    }]
    probe.assert_called_once_with("10.252.7.2")


def test_cloudex_discovery_keeps_session_identity_when_probe_fails(monkeypatch):
    session = {
        "session_id": "a" * 32,
        "allocation_id": "b" * 32,
        "device": "DevKit ll2",
        "target": "10.252.7.2",
    }
    store = Mock()
    store.list.return_value = [session]
    monkeypatch.setattr("sima_cli.cloudex.client.SessionStore", Mock(return_value=store))
    monkeypatch.setattr(
        "sima_cli.cloudex.client.inspect_session",
        Mock(return_value={"connected": True, "path": "direct"}),
    )
    monkeypatch.setattr(discover, "get_remote_board_info", Mock(return_value=("", "", "", False, "")))

    assert discover.discover_cloudex_devices() == [{
        "ip": "10.252.7.2",
        "mac": "—",
        "device": "DevKit ll2",
        "board": "—",
        "version": "—",
        "model": "—",
        "full_image": None,
        "fwtype": "—",
        "source": "CloudEx",
        "path": "direct",
        "connection": "CloudEx (direct)",
    }]


def test_cloudex_connection_avoids_unneeded_multicast_prompt(monkeypatch):
    cloudex = [{"ip": "10.252.7.2", "connection": "CloudEx (direct)"}]
    monkeypatch.setattr(discover, "suggest_and_switch_to_linklocal", Mock())
    monkeypatch.setattr(discover, "discover_cloudex_devices", Mock(return_value=cloudex))
    monkeypatch.setattr(discover, "get_sima_devices_from_arp", Mock(return_value=[]))
    render = Mock()
    multicast = Mock()
    monkeypatch.setattr(discover, "render_device_table", render)
    monkeypatch.setattr(discover, "discover_multicast", multicast)

    result = discover.discover_and_probe(include_cloudex=True)

    assert result == cloudex
    render.assert_called_once_with(cloudex)
    multicast.assert_not_called()


def test_device_table_keeps_multiple_cloudex_endpoints(monkeypatch):
    table = Mock()
    console = Mock()
    monkeypatch.setattr(discover, "Table", Mock(return_value=table))
    monkeypatch.setattr(discover, "console", console)

    discover.render_device_table([
        {"ip": "10.252.7.2", "mac": "—", "source": "CloudEx", "path": "direct", "connection": "CloudEx (direct)"},
        {"ip": "10.252.8.2", "mac": "—", "source": "CloudEx", "path": "relayed", "connection": "CloudEx (relayed)"},
    ])

    assert table.add_row.call_count == 2
    columns = [call.args[0] for call in table.add_column.call_args_list]
    assert columns == [
        "Connection", "Device", "IP", "MAC", "Board Type", "Build Version",
        "DevKit Model", "Full Image", "FW Type",
    ]
    console.print.assert_called_once_with(table)
