"""The pure model over Sentinel catalogs captured from a Modalix DevKit on Platform 2.1.3 and 3.0."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sima_cli.peripherals import model

FIXTURES = Path(__file__).parent / "fixtures" / "sentinel"


def catalog(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def device(cat, backend):
    return next(d for d in cat["devices"] if d.get("backend") == backend)


@pytest.mark.parametrize("name", ["devkit-2.1.3", "devkit-3.0"])
def test_mipi_camera_is_available_and_usb_camera_is_not_on_both_platforms(name):
    cat = catalog(name)
    mipi = model.summarize(device(cat, "mipi"))
    assert mipi["type_label"] == "Camera"
    assert mipi["connection"] == "MIPI CSI-2"
    assert mipi["name"] == "imx477 5-001a"
    assert mipi["status"] == "Connected"
    assert mipi["support"] == "available"
    assert mipi["support_reason"] == ""
    assert 0 < mipi["available_modes"] < mipi["modes"], "NV12 sizes are available; other formats are not"

    usb = model.summarize(device(cat, "v4l2"))
    assert usb["connection"] == "USB"
    assert usb["name"] == "HD Pro Webcam C920"
    assert usb["support"] == "unsupported"
    assert usb["support_reason"] == "USB cameras have no camera overlay on this board."
    assert usb["available_modes"] == 0


def test_microphone_modes_keep_their_sample_rates_and_channels():
    cat = catalog("devkit-3.0")
    microphone = next(d for d in cat["devices"] if d["type"] == "microphone")
    rows = model.mode_rows(microphone)
    assert [r["sample_rates_hz"] for r in rows] == [[16000], [24000], [32000]]
    assert {r["channels"] for r in rows} == {2} and {r["sample_bits"] for r in rows} == {16}
    assert all(r["available"] is None and "rates" not in r for r in rows)
    # Sentinel's contract: a driver may publish a range instead of a list.
    ranged = model.mode_rows({"type": "microphone", "modes": [{"format": "S24_3LE", "rate_range_hz": {"min": 8000, "max": 96000}}]})
    assert ranged[0]["sample_rates_hz"] == [] and ranged[0]["sample_rate_range_hz"] == {"min": 8000, "max": 96000}
    assert ranged[0]["channels"] is None


def test_a_type_the_cli_does_not_know_goes_through_the_generic_adapter():
    """shanguangzai on #290: a provider a newer Sentinel adds (a speaker, a LiDAR) must not be read as a
    camera. The generic adapter lists it, passes its mode properties through and claims no verdict."""
    lidar = {"id": "lidar:usb:1", "type": "lidar", "connection": "usb", "name": "Velo", "availability": {"state": "unknown"},
             "modes": [{"format": "XYZI", "points_per_second": 300000, "channels": 16, "extra": {"nested": 1}}]}
    adapter = model.adapter_for(lidar)
    assert type(adapter) is model.TypeAdapter and adapter.label == "Lidar" and adapter.type == "lidar"
    summary = model.summarize(lidar)
    assert (summary["type_label"], summary["connection"], summary["support"], summary["modes"]) == ("Lidar", "USB", None, 1)
    rows = model.mode_rows(lidar)
    assert rows == [{"format": "XYZI", "properties": {"points_per_second": 300000, "channels": 16}, "available": None, "reason": ""}]
    assert adapter.mode_cells(rows[0]) == ["XYZI", "points_per_second=300000, channels=16", "-", ""]
    assert model.describe(lidar, None)["facts"] == {}
    # A verdict Sentinel writes on such a mode is honoured, with the camera-only clauses left out.
    judged = dict(lidar, modes=[{"format": "XYZI", "available": False, "reason": "No Neat input reads point clouds."}])
    assert model.support(judged) == ("unsupported", "No Neat input reads point clouds.")
    assert model.support(dict(lidar, modes=[])) == (None, "")
    assert [a.type for a in model.ADAPTERS.values()] == ["camera", "microphone"]


def test_microphones_are_listed_without_an_application_verdict():
    mic = model.summarize(device(catalog("devkit-2.1.3"), "alsa"))
    assert mic["type_label"] == "Microphone"
    assert mic["connection"] == "USB"
    assert mic["name"] == "HD Pro Webcam C920"
    assert mic["support"] is None
    assert model.support_label(mic) == "-"


def test_in_use_is_reported_only_when_sentinel_sees_it():
    mic = device(catalog("devkit-2.1.3"), "alsa")
    assert model.status(mic) == "Connected"
    busy = dict(mic, availability={"state": "in_use", "subdevices": 1, "subdevices_free": 0})
    assert model.status(busy) == "In use"


def test_mode_rows_carry_sentinels_verdict_and_source():
    rows = model.mode_rows(device(catalog("devkit-3.0"), "mipi"))
    assert rows and all(row["source"] == "sensor_mode" for row in rows), "Platform 3.0 lists sensor sizes"
    nv12 = [row for row in rows if row["format"] == "NV12"]
    assert any(row["available"] is True and row["reason"] == "" for row in nv12)
    other = [row for row in rows if row["format"] != "NV12"]
    assert all(row["available"] is False and row["reason"] for row in other)
    rows_213 = model.mode_rows(device(catalog("devkit-2.1.3"), "mipi"))
    assert all(row["source"] == "isp_output" for row in rows_213), "Platform 2.1.x lists the ISP's table"


def test_usb_mode_rates_come_from_frame_intervals():
    rows = model.mode_rows(device(catalog("devkit-2.1.3"), "v4l2"))
    assert rows and any(30.0 in row["rates"] for row in rows)


def test_support_falls_back_to_the_isp_reason_and_to_no_modes():
    assert model.support({"type": "camera", "modes": [], "isp": {"state": "unavailable", "reason": "ISP node missing"}}) == \
        ("unsupported", "ISP node missing")
    assert model.support({"type": "camera", "modes": []}) == ("unsupported", "The camera reports no modes.")
    assert model.support({"type": "microphone"}) == (None, "")


def test_find_device_by_id_or_unique_name():
    devices = catalog("devkit-2.1.3")["devices"]
    assert model.find_device(devices, "camera:imx477 5-001a")["backend"] == "mipi"
    assert model.find_device(devices, "imx477")["backend"] == "mipi"
    # Two devices share the C920 name (camera and microphone): a name must be unique to match.
    assert model.find_device(devices, "HD Pro Webcam C920") is None
    assert model.find_device(devices, "camera:none") is None


def test_list_rows():
    cat = catalog("devkit-2.1.3")
    rows = model.list_rows([model.summarize(d) for d in cat["devices"]])
    assert rows[0] == ["camera:imx477 5-001a", "Camera", "MIPI CSI-2", "imx477 5-001a", "Connected", "Supported"]
    assert rows[1][5] == "Unsupported" and rows[2][5] == "-"


def test_catalog_warnings_put_the_firmware_verdict_first_verbatim():
    cat = {"errors": [
        {"provider": "camera.v4l2", "code": "io.permission_denied", "reason": "/dev/video1 cannot be opened"},
        {"provider": "board.firmware", "code": "platform.unsupported",
         "reason": "Firmware 2.0.0 is older than 2.1.2, the oldest release Sentinel supports for MIPI cameras."},
    ]}
    warnings = model.catalog_warnings(cat)
    assert warnings[0].startswith("Firmware 2.0.0 is older than 2.1.2")
    assert warnings[1] == "Provider camera.v4l2 failed: /dev/video1 cannot be opened"
    assert model.catalog_warnings({"errors": []}) == []


def test_envelope_is_versioned_and_carries_the_catalog_identity():
    cat = catalog("devkit-3.0")
    now = datetime(2026, 10, 12, 14, 0, 0, tzinfo=timezone.utc)
    document = model.envelope("peripherals.list", {"build_version": "3.0.0_B1859"}, cat, {"peripherals": []}, now=now)
    assert document["schema_version"] == model.SCHEMA_VERSION == "1.0"
    assert document["command"] == "peripherals.list"
    assert document["timestamp"] == "2026-10-12T14:00:00Z"
    assert document["devkit"] == {"build_version": "3.0.0_B1859"}
    assert document["result"] == "ok" and document["warnings"] == []
    assert document["sentinel"] == {"revision": cat["revision"], "observed_at": cat["observed_at"], "errors": []}
    assert document["peripherals"] == []


def test_exit_codes_are_stable():
    assert model.exit_code_for("sentinel_unavailable") == model.ExitCode.SENTINEL_UNAVAILABLE == 4
    assert model.exit_code_for("permission_denied") == 7
    assert model.exit_code_for("timeout") == 8
    assert model.exit_code_for("busy") == 6
    assert model.exit_code_for("platform.unsupported") == 5
    assert model.exit_code_for("something-new") == 4
