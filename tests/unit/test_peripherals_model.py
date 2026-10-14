"""The pure model over Sentinel records, each test built from only the fields it needs.

The record shapes follow Sentinel's documentation (sentinel repo, docs/peripherals/*.md); the
builders are in peripherals_fixtures.py. Recorded catalogs are exercised by test_peripherals_contract.py.
"""

from datetime import datetime, timezone

from sima_cli.peripherals import model
from tests.unit.peripherals_fixtures import (
    NO_USB_OVERLAY,
    NV12_ONLY,
    board,
    catalog,
    devkit,
    microphone,
    mipi_camera,
    mode,
    usb_camera,
    without_verdicts,
)


def test_a_mipi_camera_is_supported_when_any_mode_is_and_a_usb_camera_is_not():
    mipi = model.summarize(mipi_camera())
    assert (mipi["type_label"], mipi["connection"], mipi["name"], mipi["status"]) == \
        ("Camera", "MIPI CSI-2", "imx477 5-001a", "Connected")
    assert (mipi["support"], mipi["support_reason"]) == ("available", "")
    assert (mipi["modes"], mipi["available_modes"]) == (3, 2), "NV12 sizes are available; other formats are not"

    usb = model.summarize(usb_camera())
    assert (usb["connection"], usb["name"]) == ("USB", "HD Pro Webcam C920")
    assert (usb["support"], usb["support_reason"], usb["available_modes"]) == ("unsupported", NO_USB_OVERLAY, 0)


def test_the_verdict_reason_is_the_one_most_modes_give():
    camera = mipi_camera(modes=[
        mode("AR24", 1920, 1080, available=False, reason=NV12_ONLY),
        mode("RGB3", 1920, 1080, available=False, reason=NV12_ONLY),
        mode("NV12", 4056, 3040, available=False, reason="The ISP cannot output this size."),
    ])
    assert model.support(camera) == ("unsupported", NV12_ONLY)


def test_support_falls_back_to_the_isp_reason_and_to_no_modes():
    assert model.support(mipi_camera(modes=[], isp={"state": "unavailable", "reason": "ISP node missing"})) == \
        ("unsupported", "ISP node missing")
    assert model.support(mipi_camera(modes=[])) == ("unsupported", "The camera reports no modes.")
    assert model.support(without_verdicts(mipi_camera())) == (None, ""), "a Sentinel without the verdict judges nothing"
    # Codex on #290: `available: false` is the verdict; a reason is optional explanation.
    assert model.support(mipi_camera(modes=[mode("NV12", 1920, 1080, available=False)])) == ("unsupported", "")
    assert model.support(mipi_camera(modes=[mode("NV12", 1920, 1080, available=False, reason="")])) == ("unsupported", "")
    assert model.support(microphone()) == (None, "")


def test_mode_rows_carry_sentinels_verdict_and_source():
    rows = model.mode_rows(mipi_camera(sizing="runtime"))
    assert rows and all(row["source"] == "sensor_mode" for row in rows), "Platform 3.0 lists sensor sizes"
    assert [(row["format"], row["available"], row["reason"]) for row in rows] == \
        [("NV12", True, ""), ("NV12", True, ""), ("AR24", False, NV12_ONLY)]
    assert all(row["source"] == "isp_output" for row in model.mode_rows(mipi_camera())), "Platform 2.1.x lists the ISP's table"
    assert all(row["available"] is None and row["reason"] == "" for row in model.mode_rows(without_verdicts(mipi_camera())))


def test_usb_mode_rates_come_from_frame_intervals_nested_per_size_or_bare():
    rows = model.mode_rows(usb_camera())
    assert [row["rates"] for row in rows] == [[30.0, 15.0], [30.0]]
    # Intervals for another size do not leak into this mode; non-discrete or malformed entries are skipped.
    other_size = mode("MJPG", 1280, 720, frame_intervals=[
        {"width": 1920, "height": 1080, "intervals": [{"type": "discrete", "numerator": 1, "denominator": 30}]},
        {"type": "stepwise", "numerator": 1, "denominator": 60},
        {"numerator": 0, "denominator": 30},
        "junk",
    ])
    assert model.mode_rows(usb_camera(modes=[other_size]))[0]["rates"] == []
    assert model.mode_rows(mipi_camera())[0]["rates"] == [], "the DevKit's ISP reports no per-mode rate"


def test_microphone_modes_keep_their_sample_rates_and_channels():
    rows = model.mode_rows(microphone())
    assert [r["sample_rates_hz"] for r in rows] == [[16000], [24000], [32000]]
    assert {r["channels"] for r in rows} == {2} and {r["sample_bits"] for r in rows} == {16}
    assert all(r["available"] is None and "rates" not in r for r in rows)
    # Sentinel's contract: a driver may publish a range instead of a list.
    ranged = model.mode_rows(microphone(modes=[{"format": "S24_3LE", "rate_range_hz": {"min": 8000, "max": 96000}}]))
    assert ranged[0]["sample_rates_hz"] == [] and ranged[0]["sample_rate_range_hz"] == {"min": 8000, "max": 96000}
    assert ranged[0]["channels"] is None


def test_microphones_are_listed_without_an_application_verdict():
    mic = model.summarize(microphone())
    assert (mic["type_label"], mic["connection"], mic["name"], mic["support"]) == ("Microphone", "USB", "HD Pro Webcam C920", None)
    assert model.support_label(mic) == "-"


def test_in_use_is_reported_only_when_sentinel_sees_it():
    assert model.status(microphone()) == "Connected"
    assert model.status(microphone(availability={"state": "in_use", "subdevices": 1, "subdevices_free": 0})) == "In use"
    assert model.status(mipi_camera()) == "Connected", "cameras report no ownership state"


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
    assert model.support(dict(lidar, modes=[{"format": "XYZI", "available": False}])) == ("unsupported", "")
    assert [a.type for a in model.ADAPTERS.values()] == ["camera", "microphone"]
    # A connection the CLI has no label for is shown as Sentinel wrote it, ahead of the backend.
    assert model.connection_label(dict(lidar, connection="ethernet", backend="udp")) == "ethernet"
    assert model.connection_label(dict(lidar, connection="", backend="udp")) == "udp"
    assert model.connection_label({k: v for k, v in lidar.items() if k != "connection"}) == "Unknown"


def test_describe_projects_the_facts_its_type_owns_and_the_board():
    camera = model.describe(mipi_camera(sizing="runtime", max_fps=125.54), board(firmware="3.0.0"))
    assert set(camera) == {"summary", "facts", "modes", "board"}
    assert camera["facts"] == {
        "camera_name": "imx477 5-001a", "media_device": "/dev/media0", "bus_info": "platform:csi2video@1",
        "csi_receiver": "csidev-40c3000.csi", "isp": {"state": "available", "sizing": "runtime"},
        "sensor_max_fps": {"fps": 125.54, "width": 1920, "height": 1080},
    }
    assert camera["board"] == {"firmware": "3.0.0", "model": "SiMa.ai Modalix SoM 16Gig Board",
                               "overlays": ["modalix-som-waveshare-ARDU-IMX477-1CAM.dtbo"]}
    usb = model.describe(usb_camera(), None)
    assert usb["facts"] == {"device_path": "/dev/video1",
                            "by_id_path": "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_BE998CAF-video-index0"}
    assert "board" not in usb
    assert model.describe(microphone(), board())["facts"] == {
        "device_node": "/dev/snd/pcmC0D0c", "by_id": "/dev/snd/by-id/usb-046d_HD_Pro_Webcam_C920_BE998CAF-02",
        "driver": "USB-Audio", "alsa_device": "plughw:CARD=C920,DEV=0"}
    # Optional Sentinel fields stay out when absent, instead of appearing as null.
    bare = model.describe(mipi_camera(max_fps=None, sensor_timing=None, isp=None), board(overlays=()))
    assert set(bare["facts"]) == {"camera_name", "media_device", "bus_info", "csi_receiver"}
    assert bare["board"]["overlays"] == []


def test_find_device_by_id_or_unique_name():
    devices = devkit()["devices"]
    assert model.find_device(devices, "camera:imx477 5-001a")["backend"] == "mipi"
    assert model.find_device(devices, "imx477")["backend"] == "mipi"
    # Two devices share the C920 name (camera and microphone): a name must be unique to match.
    assert model.find_device(devices, "HD Pro Webcam C920") is None
    assert model.find_device(devices, "camera:none") is None


def test_list_rows():
    rows = model.list_rows([model.summarize(d) for d in devkit()["devices"]])
    assert rows[0] == ["camera:imx477 5-001a", "Camera", "MIPI CSI-2", "imx477 5-001a", "Connected", "Supported"]
    assert rows[1][5] == "Unsupported" and rows[2][5] == "-"


def test_a_camera_without_verdicts_gets_one_warning_naming_the_fix():
    devices = [without_verdicts(mipi_camera()), usb_camera(), microphone()]
    warnings = model.verdict_warnings(devices)
    assert len(warnings) == 1 and "camera:imx477 5-001a" in warnings[0] and "simaai-sentinel" in warnings[0]
    assert model.verdict_warnings([mipi_camera(), mipi_camera(modes=[])]) == []


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


def test_catalog_errors_carry_only_the_cli_owned_fields():
    """Codex on #290: a field Sentinel adds to a provider error must not change the schema-1.0 document."""
    cat = catalog(errors=[
        {"provider": "camera.v4l2", "code": "io.permission_denied", "reason": "/dev/video1 cannot be opened",
         "detail": {"errno": 13}},
        {"provider": "board.firmware", "code": "platform.unsupported"},
        "not a record",
    ])
    document = model.envelope("peripherals.list", {}, cat, {})
    assert document["sentinel"]["errors"] == [
        {"provider": "camera.v4l2", "code": "io.permission_denied", "reason": "/dev/video1 cannot be opened"},
        {"provider": "board.firmware", "code": "platform.unsupported", "reason": ""},
    ]


def test_envelope_is_versioned_and_carries_the_catalog_identity():
    cat = catalog(revision=42, observed_at="2026-10-12T13:59:00Z")
    now = datetime(2026, 10, 12, 14, 0, 0, tzinfo=timezone.utc)
    document = model.envelope("peripherals.list", {"build_version": "3.0.0_B1859"}, cat, {"peripherals": []}, now=now)
    assert document["schema_version"] == model.SCHEMA_VERSION == "1.0"
    assert document["command"] == "peripherals.list"
    assert document["timestamp"] == "2026-10-12T14:00:00Z"
    assert document["devkit"] == {"build_version": "3.0.0_B1859"}
    assert document["result"] == "ok" and document["warnings"] == []
    assert document["sentinel"] == {"revision": 42, "observed_at": "2026-10-12T13:59:00Z", "errors": []}
    assert document["peripherals"] == []
    assert "sentinel" not in model.envelope("peripherals.list", {}, None, {}), "no catalog, no sentinel block"


def test_exit_codes_are_stable():
    assert model.exit_code_for("sentinel_unavailable") == model.ExitCode.SENTINEL_UNAVAILABLE == 4
    assert model.exit_code_for("permission_denied") == 7
    assert model.exit_code_for("timeout") == 8
    assert model.exit_code_for("busy") == 6
    assert model.exit_code_for("platform.unsupported") == 5
    assert model.exit_code_for("something-new") == 4
