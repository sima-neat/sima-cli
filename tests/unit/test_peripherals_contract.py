"""Contract tests: the CLI over real Sentinel catalogs, as recorded, end to end.

The unit tests build the smallest records they need; these run the whole model and both commands
over Sentinel's documented example catalog and over snapshots recorded from a DevKit (provenance in
tests/unit/snapshots/sentinel/README.md). They prove the CLI reads what Sentinel actually sends;
they do not define what a board supports.
"""

import json
from pathlib import Path

import pytest

from sima_cli.peripherals import commands, model
from tests.unit.test_peripherals_commands import FakeClient, runner, use_client

SNAPSHOTS = Path(__file__).parent / "snapshots" / "sentinel"
CATALOGS = sorted(p.name for p in SNAPSHOTS.glob("*.json"))


def load(name):
    return json.loads((SNAPSHOTS / name).read_text())


@pytest.fixture(params=CATALOGS)
def snapshot(request):
    return request.param, load(request.param)


@pytest.fixture
def on_devkit(monkeypatch):
    monkeypatch.setattr(commands, "is_sima_board", lambda: True)
    monkeypatch.setattr(commands, "get_sima_build_version", lambda: ("x", "x"))


def test_every_recorded_device_summarizes_and_describes(snapshot):
    name, catalog = snapshot
    devices = catalog["devices"]
    assert devices, name
    for device in devices:
        summary = model.summarize(device)
        assert summary["id"] == device["id"] and summary["type"] == device["type"]
        assert summary["support"] in ("available", "unsupported", None)
        description = model.describe(device, catalog.get("board"))
        assert set(description) == {"summary", "facts", "modes", "board"}
        assert len(description["modes"]) == len(device.get("modes") or [])
        for row in description["modes"]:
            assert row["available"] in (True, False, None)
            assert (row["reason"] != "") == (row["available"] is False) or row["available"] is not False


# The USB C920's summary per recording: Sentinel's example judges it against Neat's USB camera input
# (one MJPG mode Supported); the 3.0 recording is from a pre-USB-verdict Sentinel, so the CLI asks for an update.
USB_SUMMARY = {
    "catalog-example.json": ("available", "", 1),
    "devkit-3.0-B1859.json": ("unsupported", model.USB_UPDATE_REASON, 0),
}


def test_the_recorded_devkit_trio_reads_as_documented(snapshot):
    """Each recording holds the same three devices: a MIPI IMX477 (Supported, NV12 sizes only), a USB
    C920 (as USB_SUMMARY) and the C920's microphone (no verdict)."""
    name, catalog = snapshot
    by_backend = {d["backend"]: model.summarize(d) for d in catalog["devices"]}
    assert set(by_backend) == {"mipi", "v4l2", "alsa"}, name
    mipi, usb, alsa = by_backend["mipi"], by_backend["v4l2"], by_backend["alsa"]
    assert (mipi["connection"], mipi["support"]) == ("MIPI CSI-2", "available")
    assert 0 < mipi["available_modes"] < mipi["modes"]
    nv12_only = {row["format"] for row in model.mode_rows(model.find_device(catalog["devices"], mipi["id"])) if row["available"]}
    assert nv12_only == {"NV12"}, name
    assert usb["connection"] == "USB"
    assert (usb["support"], usb["support_reason"], usb["available_modes"]) == USB_SUMMARY[name]
    assert (alsa["type_label"], alsa["support"]) == ("Microphone", None)
    assert [row["sample_rates_hz"] for row in model.mode_rows(model.find_device(catalog["devices"], alsa["id"]))] == \
        [[16000], [24000], [32000]]


def test_the_two_platforms_differ_only_where_sentinel_documents_it():
    """Platform 2.1.x publishes the ISP's size table (`isp_output`); 3.0 sets sizes at run time and
    publishes sensor sizes (`sensor_mode`). The CLI reads both without a platform check."""
    fixed = next(d for d in load("catalog-example.json")["devices"] if d["backend"] == "mipi")
    runtime = next(d for d in load("devkit-3.0-B1859.json")["devices"] if d["backend"] == "mipi")
    assert fixed["isp"]["sizing"] == "fixed" and runtime["isp"]["sizing"] == "runtime"
    assert {row["source"] for row in model.mode_rows(fixed)} == {"isp_output"}
    assert {row["source"] for row in model.mode_rows(runtime)} == {"sensor_mode"}
    assert model.describe(fixed, None)["facts"]["sensor_max_fps"]["fps"] == 66.18
    assert model.describe(runtime, None)["facts"]["sensor_max_fps"]["fps"] == 125.54


def test_list_and_describe_run_over_every_recording(on_devkit, monkeypatch, snapshot):
    name, catalog = snapshot
    use_client(monkeypatch, FakeClient(catalog))
    listed = runner().invoke(commands.peripherals, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    document = json.loads(listed.stdout)
    assert [p["id"] for p in document["peripherals"]] == [d["id"] for d in catalog["devices"]]
    assert document["sentinel"]["revision"] == catalog["revision"] and document["warnings"] == []
    for device in catalog["devices"]:
        human = runner().invoke(commands.peripherals, ["describe", device["id"]])
        assert human.exit_code == 0, (name, device["id"], human.output)
        assert human.output.startswith("ID ") and device["id"] in human.output
        as_json = runner().invoke(commands.peripherals, ["describe", device["id"], "--json"])
        described = json.loads(as_json.stdout)
        assert as_json.exit_code == 0 and set(described) >= {"summary", "facts", "modes", "board"}
        assert "device" not in described, "Sentinel's record never passes through"
