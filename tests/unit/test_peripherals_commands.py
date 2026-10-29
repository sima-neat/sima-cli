"""`sima-cli peripherals list|describe`: output contract, DevKit gate, exit codes.

Catalogs are built from peripherals_fixtures.py with only the fields each test needs; the recorded
catalogs in tests/unit/snapshots are exercised by test_peripherals_contract.py.
"""

import json

import pytest
from click.testing import CliRunner

from sima_cli.peripherals import commands
from sima_cli.peripherals.sentinel import SentinelError
from tests.unit.peripherals_fixtures import (
    MJPEG_ONLY, NV12_ONLY, USB_CAMERA_NAME, board, catalog, devkit, microphone, mipi_camera, mode, usb_camera, without_verdicts,
)


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


def use_client(monkeypatch, client):
    monkeypatch.setattr(commands, "SentinelClient", lambda socket_path: client)
    return client


def test_list_prints_only_the_table(on_devkit, monkeypatch):
    use_client(monkeypatch, FakeClient(devkit()))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["ID", "TYPE", "CONNECTION", "DEVICE", "STATUS", "SUPPORT"]
    assert "camera:imx477 5-001a" in lines[1] and lines[1].endswith("Supported")
    assert "HD Pro Webcam C920" in lines[2] and lines[2].endswith("Unsupported")
    assert lines[3].startswith("microphone:") and lines[3].endswith("-")
    # Reasons belong to `describe`; the list stays one line per device.
    assert len(lines) == 4 and "overlay" not in result.output


def test_list_with_nothing_attached(on_devkit, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog()))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0 and result.output.strip() == "No cameras or microphones detected."
    document = json.loads(runner().invoke(commands.peripherals, ["list", "--json"]).stdout)
    assert document["peripherals"] == [] and document["result"] == "ok"


def test_list_json_is_the_versioned_envelope_on_stdout_only(on_devkit, monkeypatch):
    client = use_client(monkeypatch, FakeClient(devkit()))
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


def test_a_camera_without_verdicts_warns_to_update_sentinel(on_devkit, monkeypatch):
    use_client(monkeypatch, FakeClient(without_verdicts(devkit())))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0
    assert "no availability verdict" in result.stderr and "camera:imx477 5-001a" in result.stderr
    assert result.stdout.splitlines()[1].endswith("-")


def test_describe_by_id_shows_facts_and_every_mode(on_devkit, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog(mipi_camera(sizing="runtime", max_fps=125.54), board_block=board(firmware="3.0.0"))))
    result = runner().invoke(commands.peripherals, ["describe", "camera:imx477 5-001a"])
    assert result.exit_code == 0, result.output
    facts, table = result.output.split("\n\n", 1)
    assert "MIPI CSI-2" in facts
    assert "Platform" in facts and "3.0.0" in facts and "modalix-som-waveshare-ARDU-IMX477-1CAM.dtbo" in facts
    assert "ISP" in facts and "sizes set at run time" in facts
    # Codex on #290: the DevKit's ISP reports no per-mode rate, so the sensor's limit is the frame-rate fact.
    assert "Sensor max rate" in facts and "125.54 fps at 1920x1080" in facts
    rows = [line.split(maxsplit=4) for line in table.splitlines()]
    assert rows[0][:4] == ["FORMAT", "SIZE", "FPS", "SUPPORT"]
    assert rows[1] == ["NV12", "1920x1080", "-", "Supported"]
    assert rows[3] == ["AR24", "1920x1080", "-", "Unsupported", NV12_ONLY]


def test_describe_a_fixed_isp_camera_does_not_claim_run_time_sizing(on_devkit, monkeypatch):
    use_client(monkeypatch, FakeClient(catalog(mipi_camera())))
    result = runner().invoke(commands.peripherals, ["describe", "imx477"])
    assert result.exit_code == 0 and "run time" not in result.output
    assert "66.18 fps at 1920x1080" in result.output and "Platform" in result.output and "2.1.3" in result.output


def test_describe_a_usb_camera_shows_sentinels_verdicts_and_no_camera_name(on_devkit, monkeypatch):
    """Sentinel judges UVC modes (MJPEG available, other formats not) and names the camera_name Neat's
    USB camera input takes; `describe` shows the verdicts but not that name, which says nothing to a user."""
    modes = [mode("MJPG", 1920, 1080, available=True, frame_intervals=[{"type": "discrete", "numerator": 1, "denominator": 30}]),
             mode("YUYV", 640, 480, available=False, reason=MJPEG_ONLY)]
    identity = {"product": "HD Pro Webcam C920", "driver": "uvcvideo", "camera_name": USB_CAMERA_NAME}
    use_client(monkeypatch, FakeClient(catalog(usb_camera(modes=modes, identity=identity))))
    result = runner().invoke(commands.peripherals, ["describe", "camera:v4l2:421bbe426738013b"])
    assert result.exit_code == 0, result.output
    facts, table = result.output.split("\n\n", 1)
    assert "USB" in facts and "Camera name" not in result.output and USB_CAMERA_NAME not in result.output
    rows = [line.split(maxsplit=4) for line in table.splitlines()]
    assert rows[1] == ["MJPG", "1920x1080", "30", "Supported"]
    assert rows[2] == ["YUYV", "640x480", "-", "Unsupported", MJPEG_ONLY]
    document = json.loads(runner().invoke(commands.peripherals, ["describe", "camera:v4l2:421bbe426738013b", "--json"]).stdout)
    assert "camera_name" not in document["facts"] and document["summary"]["support"] == "available"


def test_describe_a_microphone_shows_its_nodes_and_sample_rates(on_devkit, monkeypatch):
    """Codex on #290: microphone modes carry `rates_hz`, not frame intervals, and the usable node and
    ALSA selector sit under `identity` and `capture_target`."""
    use_client(monkeypatch, FakeClient(devkit()))
    result = runner().invoke(commands.peripherals, ["describe", "microphone:alsa:329f324bddfdde7b"])
    assert result.exit_code == 0, result.output
    facts = {" ".join(line.split()[:2]): line.split()[-1] for line in result.output.split("\n\n", 1)[0].splitlines()}
    assert facts["Device node"] == "/dev/snd/pcmC0D0c"
    assert facts["By id"] == "/dev/snd/by-id/usb-046d_HD_Pro_Webcam_C920_BE998CAF-02"
    assert facts["ALSA device"] == "plughw:CARD=C920,DEV=0"
    assert "SAMPLE RATE (Hz)" in result.output and "SUPPORT" not in result.output.split("\n\n", 1)[1]
    rows = [line.split() for line in result.output.split("\n\n", 1)[1].splitlines()[1:]]
    assert rows == [["S16_LE", "2", "16000"], ["S16_LE", "2", "24000"], ["S16_LE", "2", "32000"]]


def test_describe_an_unknown_type_prints_its_properties_not_a_camera_table(on_devkit, monkeypatch):
    lidar = {"id": "lidar:usb:1", "type": "lidar", "connection": "usb", "name": "Velo",
             "modes": [{"format": "XYZI", "points_per_second": 300000}]}
    use_client(monkeypatch, FakeClient(catalog(mipi_camera(), lidar)))
    listed = runner().invoke(commands.peripherals, ["list"])
    assert listed.exit_code == 0 and "lidar:usb:1" in listed.output and "Lidar" in listed.output
    result = runner().invoke(commands.peripherals, ["describe", "lidar:usb:1"])
    assert result.exit_code == 0, result.output
    assert "Lidar" in result.output and "USB" in result.output
    table = result.output.split("\n\n", 1)[1]
    assert table.split()[:4] == ["FORMAT", "PROPERTIES", "SUPPORT", "REASON"]
    assert "points_per_second=300000" in table and "SIZE" not in table and "FPS" not in table


def test_describe_json_holds_only_projected_fields(on_devkit, monkeypatch):
    """shanguangzai on #290: the versioned document must not pass Sentinel's `device` or `board`
    records through, so a Sentinel change cannot alter the schema and `modes` has one source."""
    use_client(monkeypatch, FakeClient(catalog(mipi_camera(sizing="runtime", max_fps=125.54), microphone(),
                                               board_block=board(firmware="3.0.0"))))
    result = runner().invoke(commands.peripherals, ["describe", "imx477", "--json"])
    document = json.loads(result.stdout)
    assert document["command"] == "peripherals.describe"
    assert document["summary"]["support"] == "available" and document["summary"]["id"] == "camera:imx477 5-001a"
    assert "device" not in document and set(document["board"]) == {"firmware", "model", "overlays"}
    assert document["board"]["firmware"] == "3.0.0" and document["board"]["overlays"] == ["modalix-som-waveshare-ARDU-IMX477-1CAM.dtbo"]
    assert set(document["facts"]) == {"camera_name", "media_device", "bus_info", "csi_receiver", "isp", "sensor_max_fps"}
    assert document["facts"]["isp"] == {"state": "available", "sizing": "runtime"}
    assert document["facts"]["sensor_max_fps"] == {"fps": 125.54, "width": 1920, "height": 1080}
    assert [mode["available"] for mode in document["modes"]] == [True, True, False]
    assert set(document["modes"][0]) == {"format", "width", "height", "rates", "source", "available", "reason"}
    microphone_doc = json.loads(runner().invoke(commands.peripherals, ["describe", "microphone:alsa:329f324bddfdde7b", "--json"]).stdout)
    assert set(microphone_doc["facts"]) == {"device_node", "by_id", "driver", "alsa_device"}
    assert set(microphone_doc["modes"][0]) == {"format", "channels", "sample_bits", "sample_rates_hz", "sample_rate_range_hz", "available", "reason"}


def test_an_unknown_device_is_invalid_input_with_the_known_ids(on_devkit, monkeypatch):
    use_client(monkeypatch, FakeClient(devkit()))
    result = runner().invoke(commands.peripherals, ["describe", "camera:none"])
    assert result.exit_code == 2
    assert "camera:imx477 5-001a" in result.stderr
    as_json = runner().invoke(commands.peripherals, ["describe", "camera:none", "--json"])
    assert as_json.exit_code == 2
    assert json.loads(as_json.stdout)["error"]["code"] == "unknown_device"


def test_usage_errors_are_json_documents_too(on_devkit, monkeypatch):
    """Codex on #290: Click rejects a missing <ID>, an unknown option or an extra argument before the
    callback runs; with --json those must still be the versioned error document on stdout, exit 2."""
    use_client(monkeypatch, FakeClient(devkit()))
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


def test_a_timed_out_catalog_does_not_probe_sentinel_again(on_devkit, monkeypatch):
    """Codex on #290: after a timeout the JSON error must not wait out a second timeout on /v1/health."""
    client = use_client(monkeypatch, FakeClient({}, error=SentinelError("timeout", "no answer")))
    document = json.loads(runner().invoke(commands.peripherals, ["list", "--json"]).stdout)
    assert document["error"]["code"] == "timeout" and document["devkit"]["sentinel_version"] is None
    assert client.calls == ["catalog"]
    # A daemon that answers with an error is still asked for its version.
    client = use_client(monkeypatch, FakeClient({}, error=SentinelError("protocol", "bad body")))
    document = json.loads(runner().invoke(commands.peripherals, ["list", "--json"]).stdout)
    assert document["devkit"]["sentinel_version"] == "0.6.0" and client.calls == ["catalog", "health"]


def test_through_the_root_cli_json_stdout_carries_only_the_document(on_devkit, monkeypatch):
    """The root group prints an environment banner and may run a PyPI update check; neither may reach
    the JSON on stdout, and the update check must not run at all (a DevKit may be offline, and its
    prompt would abort a script)."""
    from sima_cli import cli as root_cli

    use_client(monkeypatch, FakeClient(devkit()))
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


def test_through_the_root_cli_internal_mode_skips_the_artifactory_gates(on_devkit, monkeypatch):
    """Codex on #290: with --internal (or SIMA_CLI_INTERNAL=1) the root group checks the internal
    resource map and Artifactory reachability and exits 0 without running the command when either
    fails. `peripherals` reads only the DevKit, so neither gate may run or stop it."""
    from sima_cli import cli as root_cli

    use_client(monkeypatch, FakeClient(devkit()))
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


def test_old_firmware_is_a_warning_and_the_usb_devices_still_list(on_devkit, monkeypatch):
    old = catalog(usb_camera(), microphone(), board_block=board(firmware="2.0.0"),
                  errors=[{"provider": "board.firmware", "code": "platform.unsupported",
                           "reason": "Firmware 2.0.0 is older than 2.1.2, the oldest release Sentinel supports for MIPI cameras."}])
    use_client(monkeypatch, FakeClient(old))
    result = runner().invoke(commands.peripherals, ["list"])
    assert result.exit_code == 0
    assert "HD Pro Webcam C920" in result.stdout
    assert "Firmware 2.0.0 is older than 2.1.2" in result.stderr
    document = json.loads(runner().invoke(commands.peripherals, ["list", "--json"]).stdout)
    assert document["warnings"][0].startswith("Firmware 2.0.0")
