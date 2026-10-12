"""`sima-cli peripherals list|describe`: output contract, DevKit gate, exit codes."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from sima_cli.peripherals import commands
from sima_cli.peripherals.sentinel import SentinelError

FIXTURES = Path(__file__).parent / "fixtures" / "sentinel"


def runner():
    """stdout and stderr apart: click 8.2 always separates them; older releases need mix_stderr=False."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class FakeClient:
    def __init__(self, catalog, error=None):
        self._catalog = catalog
        self._error = error
        self.refreshed = False
        self.calls = []

    def catalog(self, refresh=False):
        self.calls.append("catalog")
        if self._error:
            raise self._error
        self.refreshed = refresh
        return self._catalog

    def health(self):
        self.calls.append("health")
        return {"version": "0.6.0"}


@pytest.fixture
def on_devkit(monkeypatch):
    monkeypatch.setattr(commands, "is_sima_board", lambda: True)
    monkeypatch.setattr(commands, "get_sima_build_version", lambda: ("3.0.0", "3.0.0_B1859"))


@pytest.fixture
def catalog():
    return json.loads((FIXTURES / "devkit-3.0.json").read_text())


def use_client(monkeypatch, client):
    monkeypatch.setattr(commands, "SentinelClient", lambda socket_path: client)
    return client


def test_list_prints_the_table_and_reasons(on_devkit, catalog, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["ID", "TYPE", "CONNECTION", "DEVICE", "STATUS", "SUPPORT"]
    assert "camera:imx477 5-001a" in lines[1] and lines[1].endswith("Available")
    assert "HD Pro Webcam C920" in lines[2] and lines[2].endswith("Unsupported")
    assert any("USB cameras have no camera overlay on this board." in line for line in lines)


def test_list_json_is_the_versioned_envelope_on_stdout_only(on_devkit, catalog, monkeypatch):
    client = use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["list", "--json", "--refresh"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["schema_version"] == "1.0"
    assert document["command"] == "peripherals.list"
    assert document["devkit"] == {"build_version": "3.0.0_B1859", "sentinel_version": "0.6.0",
                                  "sima_cli_version": document["devkit"]["sima_cli_version"]}
    assert document["result"] == "ok"
    assert [p["support"] for p in document["peripherals"]] == ["available", "unsupported", None]
    assert client.refreshed is True
    assert result.stderr == ""


def test_list_filters_by_type_including_types_this_cli_does_not_know(on_devkit, catalog, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["list", "--type", "microphone", "--json"])
    document = json.loads(result.stdout)
    assert [p["type"] for p in document["peripherals"]] == ["microphone"]
    # A type a newer Sentinel adds is a filter, not an error: automation keeps working when providers appear.
    result = runner().invoke(commands.peripherals, ["list", "--type", "lidar"])
    assert result.exit_code == 0
    assert "No lidars detected." in result.stdout


def test_a_camera_without_verdicts_warns_to_update_sentinel(on_devkit, catalog, monkeypatch):
    older = json.loads(json.dumps(catalog))
    for device in older["devices"]:
        for mode in device.get("modes") or []:
            mode.pop("available", None)
            mode.pop("reason", None)
    use_client(monkeypatch, FakeClient(older))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0
    assert "no availability verdict" in result.stderr and "camera:imx477 5-001a" in result.stderr
    assert result.stdout.splitlines()[1].endswith("-")


def test_describe_by_id_shows_facts_and_every_mode(on_devkit, catalog, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["describe", "camera:imx477 5-001a"])
    assert result.exit_code == 0, result.output
    assert "MIPI CSI-2" in result.output
    assert "Platform" in result.output and "3.0.0" in result.output
    assert "sizes set at run time" in result.output
    assert "1920x1080" in result.output and "Available" in result.output
    assert "Neat's CameraInput outputs NV12 only." in result.output


def test_describe_json_carries_the_device_record(on_devkit, catalog, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["describe", "imx477", "--json"])
    document = json.loads(result.stdout)
    assert document["command"] == "peripherals.describe"
    assert document["summary"]["support"] == "available"
    assert document["device"]["id"] == "camera:imx477 5-001a"
    assert document["board"]["firmware"] == "3.0.0"
    assert any(mode["available"] is True for mode in document["modes"])


def test_an_unknown_device_is_invalid_input_with_the_known_ids(on_devkit, catalog, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["describe", "camera:none"])
    assert result.exit_code == 2
    assert "camera:imx477 5-001a" in result.stderr
    as_json = runner().invoke(commands.peripherals, ["describe", "camera:none", "--json"])
    assert as_json.exit_code == 2
    assert json.loads(as_json.stdout)["error"]["code"] == "unknown_device"


def test_off_the_devkit_nothing_is_probed(monkeypatch):
    monkeypatch.setattr(commands, "is_sima_board", lambda: False)
    monkeypatch.setattr(commands, "get_sima_build_version", lambda: (None, None))
    client = FakeClient({})
    constructed = []
    monkeypatch.setattr(commands, "SentinelClient", lambda socket_path: constructed.append(socket_path) or client)
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 3
    assert "runs on the DevKit" in result.stderr
    as_json = runner().invoke(commands.peripherals, ["list", "--json"])
    assert as_json.exit_code == 3
    document = json.loads(as_json.stdout)
    assert document["error"]["code"] == "not_devkit"
    assert document["devkit"]["sentinel_version"] is None
    # Neither path constructs a client or opens the socket, even for the version in the error document.
    assert constructed == [] and client.calls == []


@pytest.mark.parametrize("code,exit_code", [("sentinel_unavailable", 4), ("permission_denied", 7), ("timeout", 8), ("busy", 6)])
def test_sentinel_failures_map_to_exit_codes(on_devkit, monkeypatch, code, exit_code):
    use_client(monkeypatch, FakeClient({}, error=SentinelError(code, "it failed", "do this")))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == exit_code
    assert "it failed" in result.stderr and "do this" in result.stderr
    as_json = runner().invoke(commands.peripherals, ["list", "--json"])
    document = json.loads(as_json.stdout)
    assert document["result"] == "error" and document["error"]["code"] == code


def test_old_firmware_is_a_warning_and_the_usb_devices_still_list(on_devkit, catalog, monkeypatch):
    old = dict(catalog, devices=[d for d in catalog["devices"] if d.get("backend") != "mipi"],
               errors=[{"provider": "board.firmware", "code": "platform.unsupported",
                        "reason": "Firmware 2.0.0 is older than 2.1.2, the oldest release Sentinel supports for MIPI cameras."}])
    use_client(monkeypatch, FakeClient(old))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0
    assert "HD Pro Webcam C920" in result.stdout
    assert "Firmware 2.0.0 is older than 2.1.2" in result.stderr
    document = json.loads(runner().invoke(commands.peripherals, ["list", "--json"]).stdout)
    assert document["warnings"][0].startswith("Firmware 2.0.0")
