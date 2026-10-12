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


def test_list_prints_only_the_table(on_devkit, catalog, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["ID", "TYPE", "CONNECTION", "DEVICE", "STATUS", "SUPPORT"]
    assert "camera:imx477 5-001a" in lines[1] and lines[1].endswith("Supported")
    assert "HD Pro Webcam C920" in lines[2] and lines[2].endswith("Unsupported")
    # Reasons belong to `describe`; the list stays one line per device.
    assert len(lines) == 4 and "overlay" not in result.output


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
    assert "1920x1080" in result.output and "Supported" in result.output
    assert "Neat's CameraInput outputs NV12 only." in result.output
    # Codex on #290: the DevKit's ISP reports no per-mode rate, so the sensor's limit is the frame-rate fact.
    assert "Sensor max rate" in result.output and "125.54 fps at 1920x1080" in result.output


def test_describe_a_microphone_shows_its_nodes_and_sample_rates(on_devkit, catalog, monkeypatch):
    """Codex on #290: microphone modes carry `rates_hz`, not frame intervals, and the usable node and
    ALSA selector sit under `identity` and `capture_target`."""
    use_client(monkeypatch, FakeClient(catalog))
    result = runner().invoke(commands.peripherals, ["describe", "microphone:alsa:329f324bddfdde7b"])
    assert result.exit_code == 0, result.output
    facts = {" ".join(line.split()[:2]): line.split()[-1] for line in result.output.split("\n\n", 1)[0].splitlines()}
    assert facts["Device node"] == "/dev/snd/pcmC0D0c"
    assert facts["By id"] == "/dev/snd/by-id/usb-046d_HD_Pro_Webcam_C920_BE998CAF-02"
    assert facts["ALSA device"] == "plughw:CARD=C920,DEV=0"
    assert "SAMPLE RATE (Hz)" in result.output and "SUPPORT" not in result.output.split("\n\n", 1)[1]
    rows = [line.split() for line in result.output.split("\n\n", 1)[1].splitlines()[1:]]
    assert rows == [["S16_LE", "2", "16000"], ["S16_LE", "2", "24000"], ["S16_LE", "2", "32000"]]


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


def test_usage_errors_are_json_documents_too(on_devkit, catalog, monkeypatch):
    """Codex on #290: Click rejects a missing <ID>, an unknown option or an extra argument before the
    callback runs; with --json those must still be the versioned error document on stdout, exit 2."""
    use_client(monkeypatch, FakeClient(catalog))
    for argv in (["describe", "--json"], ["list", "--bogus", "--json"], ["list", "extra", "--json"]):
        result = runner().invoke(commands.peripherals, argv)
        assert result.exit_code == 2, argv
        document = json.loads(result.stdout)
        assert document["result"] == "error" and document["error"]["code"] == "invalid_input", argv
        assert document["command"] == f"peripherals.{argv[0]}" and "--help" in document["error"]["hint"]
    prose = runner().invoke(commands.peripherals, ["describe"])
    assert prose.exit_code == 2 and prose.stdout == "" and "Missing argument" in prose.stderr


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


def test_through_the_root_cli_json_stdout_carries_only_the_document(on_devkit, catalog, monkeypatch):
    """The root group prints an environment banner and may run a PyPI update check; neither may reach
    the JSON on stdout, and the update check must not run at all (a DevKit may be offline, and its
    prompt would abort a script)."""
    from sima_cli import cli as root_cli

    use_client(monkeypatch, FakeClient(catalog))
    monkeypatch.setattr(root_cli, "get_environment_type", lambda: ("board", "modalix"))

    def no_update_check(package_name):
        raise AssertionError("the update check ran before a peripherals command")

    monkeypatch.setattr(root_cli, "check_for_update", no_update_check)
    monkeypatch.setattr(root_cli, "internal_resource_exists", lambda: True)
    monkeypatch.setenv("SIMA_CLI_INTERNAL", "0")
    result = runner().invoke(root_cli.main, ["peripherals", "list", "--json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["command"] == "peripherals.list"
    assert "Environment" not in result.stdout
    assert "Environment: board (modalix)" in result.stderr


def test_through_the_root_cli_internal_mode_skips_the_artifactory_gates(on_devkit, catalog, monkeypatch):
    """Codex on #290: with --internal (or SIMA_CLI_INTERNAL=1) the root group checks the internal
    resource map and Artifactory reachability and exits 0 without running the command when either
    fails. `peripherals` reads only the DevKit, so neither gate may run or stop it."""
    from sima_cli import cli as root_cli

    use_client(monkeypatch, FakeClient(catalog))
    monkeypatch.setattr(root_cli, "get_environment_type", lambda: ("board", "modalix"))
    monkeypatch.setattr(root_cli, "check_for_update", lambda package_name: False)
    monkeypatch.setattr(root_cli, "internal_resource_exists", lambda: False)

    def offline():
        raise AssertionError("Artifactory reachability was checked before a peripherals command")

    monkeypatch.setattr(root_cli, "check_artifactory_reachability", offline)
    for argv in (["--internal", "peripherals", "list", "--json"], ["peripherals", "list", "--json"]):
        monkeypatch.setenv("SIMA_CLI_INTERNAL", "0" if argv[0] == "--internal" else "1")
        result = runner().invoke(root_cli.main, argv)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["command"] == "peripherals.list"
        assert "internal resource map" not in result.output


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
