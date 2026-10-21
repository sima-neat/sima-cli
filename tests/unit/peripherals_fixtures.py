"""Small, purpose-built Sentinel records for the peripherals unit tests.

Each builder returns the fewest fields a test needs, shaped as SiMa Sentinel documents them
(sentinel repo: docs/api.md, docs/peripherals/camera-mipi.md, camera.md, microphone.md, board.md).
Nothing here defines what a board supports; Sentinel's documentation does. Recorded catalogs live in
tests/unit/snapshots/sentinel/ and are used only by the contract tests.
"""

NV12_ONLY = "Neat's CameraInput outputs NV12 only."
NO_USB_OVERLAY = "USB cameras have no camera overlay on this board."
IMX477_OVERLAY = "modalix-som-waveshare-ARDU-IMX477-1CAM.dtbo"


def mode(fmt, width, height, available=None, reason=None, **extra):
    """A camera mode as Sentinel lists it; `available`/`reason` only when the verdict is wanted."""
    record = {"format": fmt, "width": width, "height": height, **extra}
    if available is not None:
        record["available"] = available
    if reason is not None:
        record["reason"] = reason
    return record


def mipi_camera(sizing="fixed", modes=None, **overrides):
    """An IMX477 on the DevKit's MIPI port. `sizing="fixed"` is Platform 2.1.x (the ISP's size table,
    `isp_output` modes); `sizing="runtime"` is Platform 3.0 (sensor sizes, `sensor_mode` modes)."""
    source = {"isp_output": True} if sizing == "fixed" else {"sensor_mode": True}
    if modes is None:
        modes = [
            mode("NV12", 1920, 1080, available=True, **source),
            mode("NV12", 1280, 720, available=True, **source),
            mode("AR24", 1920, 1080, available=False, reason=NV12_ONLY, **source),
        ]
    record = {
        "id": "camera:imx477 5-001a",
        "type": "camera",
        "backend": "mipi",
        "camera_name": "imx477 5-001a",
        "model": "imx477",
        "media_device": "/dev/media0",
        "bus_info": "platform:csi2video@1",
        "csi_receiver": "csidev-40c3000.csi",
        "availability": {"state": "unknown"},
        "isp": {"state": "available", "sizing": sizing},
        "sensor_timing": {"width": 1920, "height": 1080, "pixel_rate": 840000000, "hblank_min": 9332, "vblank_min": 48},
        "max_fps": 66.18,
        "modes": modes,
    }
    record.update(overrides)
    return record


def usb_camera(modes=None, **overrides):
    """A UVC webcam. Sentinel nests its frame intervals per probed size; a bare interval list is also read."""
    if modes is None:
        modes = [
            mode("MJPG", 1920, 1080, available=False, reason=NO_USB_OVERLAY,
                 frame_intervals=[{"width": 1920, "height": 1080, "intervals": [
                     {"type": "discrete", "numerator": 1, "denominator": 30},
                     {"type": "discrete", "numerator": 1, "denominator": 15}]}]),
            mode("YUYV", 640, 480, available=False, reason=NO_USB_OVERLAY,
                 frame_intervals=[{"type": "discrete", "numerator": 1, "denominator": 30}]),
        ]
    record = {
        "id": "camera:v4l2:421bbe426738013b",
        "type": "camera",
        "backend": "v4l2",
        "model": "HD Pro Webcam C920",
        "device_path": "/dev/video1",
        "by_id_path": "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_BE998CAF-video-index0",
        "identity": {"product": "HD Pro Webcam C920", "vendor_id": "046d", "product_id": "08e5"},
        "availability": {"state": "unknown"},
        "modes": modes,
    }
    record.update(overrides)
    return record


def microphone(modes=None, **overrides):
    """An ALSA capture device (a webcam's microphone). Modes carry `rates_hz`, not frame intervals."""
    if modes is None:
        modes = [{"format": "S16_LE", "channels": 2, "sample_bits": 16, "rates_hz": [rate]}
                 for rate in (16000, 24000, 32000)]
    record = {
        "id": "microphone:alsa:329f324bddfdde7b",
        "type": "microphone",
        "backend": "alsa",
        "connection": "usb",
        "name": "HD Pro Webcam C920",
        "availability": {"state": "available", "subdevices": 1, "subdevices_available": 1},
        "identity": {"pcm_node": "/dev/snd/pcmC0D0c", "card_driver": "USB-Audio",
                     "by_id": "/dev/snd/by-id/usb-046d_HD_Pro_Webcam_C920_BE998CAF-02"},
        "capture_target": {"card_id": "C920", "device": 0, "selector": "plughw:CARD=C920,DEV=0"},
        "modes": modes,
    }
    record.update(overrides)
    return record


def board(firmware="2.1.3", overlays=(IMX477_OVERLAY,), **overrides):
    record = {
        "model": "SiMa.ai Modalix SoM 16Gig Board",
        "firmware": firmware,
        "overlays": list(overlays),
        "supported_sensors": [{"compatible": "sony,imx477", "overlays": list(overlays)}],
        "configured_cameras": [{"camera_id": "camera:imx477 5-001a", "compatible": "sony,imx477"}],
    }
    record.update(overrides)
    return record


def catalog(*devices, board_block=None, errors=(), revision=1791177145107, observed_at="2026-10-05T05:12:25Z"):
    """A catalog as GET /v1/peripherals returns it, holding exactly the devices given (none: an empty board)."""
    record = {"revision": revision, "observed_at": observed_at, "devices": list(devices), "errors": list(errors)}
    record["board"] = board() if board_block is None else board_block
    return record


def devkit(**catalog_fields):
    """The usual DevKit: the IMX477 on MIPI, the C920 on USB and the C920's microphone."""
    return catalog(mipi_camera(), usb_camera(), microphone(), **catalog_fields)


def without_verdicts(record):
    """The same catalog or device from a Sentinel older than the per-mode verdict (sentinel#43)."""
    devices = record["devices"] if "devices" in record else [record]
    for device in devices:
        for item in device.get("modes") or []:
            item.pop("available", None)
            item.pop("reason", None)
    return record
