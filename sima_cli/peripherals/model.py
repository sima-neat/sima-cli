"""Pure functions over SiMa Sentinel's peripheral catalog: rows, descriptions, the JSON envelope.

Nothing here touches the board. The verdict on each camera mode (`available` and `reason`) is
Sentinel's; this module only summarizes it per device.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"


class ExitCode:
    """Documented process exit statuses, shared by every `sima-cli peripherals` command."""

    OK = 0
    INVALID_INPUT = 2
    NOT_DEVKIT = 3
    SENTINEL_UNAVAILABLE = 4
    UNSUPPORTED = 5
    BUSY = 6
    PERMISSION = 7
    TIMEOUT = 8
    TEST_FAILED = 9
    CLEANUP_FAILED = 10


# A Sentinel error code, as raised by the client, to the exit status it means.
EXIT_FOR_ERROR = {
    "sentinel_unavailable": ExitCode.SENTINEL_UNAVAILABLE,
    "protocol": ExitCode.SENTINEL_UNAVAILABLE,
    "permission_denied": ExitCode.PERMISSION,
    "timeout": ExitCode.TIMEOUT,
    "busy": ExitCode.BUSY,
    "platform.unsupported": ExitCode.UNSUPPORTED,
}

TYPE_LABELS = {"camera": "Camera", "microphone": "Microphone"}


def exit_code_for(error_code: str) -> int:
    return EXIT_FOR_ERROR.get(error_code, ExitCode.SENTINEL_UNAVAILABLE)


def type_label(device: Dict[str, Any]) -> str:
    kind = str(device.get("type") or "")
    return TYPE_LABELS.get(kind, kind.capitalize() or "Device")


def connection_label(device: Dict[str, Any]) -> str:
    backend = device.get("backend")
    if device.get("type") == "camera":
        return "MIPI CSI-2" if backend == "mipi" else "USB" if backend == "v4l2" else str(backend or "Unknown")
    connection = device.get("connection")
    return {"usb": "USB", "platform": "On-board"}.get(str(connection), "Unknown")


def device_name(device: Dict[str, Any]) -> str:
    """The readable name: the sensor's camera name, the USB product, the ALSA card name."""
    for key in ("camera_name", "model", "name"):
        value = device.get(key)
        if isinstance(value, str) and value:
            return value
    identity = device.get("identity") or {}
    product = identity.get("product") if isinstance(identity, dict) else None
    return str(product or device.get("id") or "")


def _discrete_rates(intervals: Any) -> List[float]:
    rates: List[float] = []
    for interval in intervals or []:
        if not isinstance(interval, dict) or interval.get("type") not in (None, "discrete"):
            continue
        numerator, denominator = interval.get("numerator"), interval.get("denominator")
        if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and numerator:
            rates.append(round(denominator / numerator, 2))
    return rates


def mode_rates(mode: Dict[str, Any]) -> List[float]:
    """Frame rates a mode advertises, from its discrete frame intervals (seconds per frame).

    Sentinel lists intervals per probed size (`{"width", "height", "intervals": [...]}`) for the mode's
    own size; a bare interval list is read too."""
    rates: List[float] = []
    for entry in mode.get("frame_intervals") or []:
        if not isinstance(entry, dict):
            continue
        if "intervals" in entry:
            if entry.get("width") in (None, mode.get("width")) and entry.get("height") in (None, mode.get("height")):
                rates.extend(_discrete_rates(entry["intervals"]))
        else:
            rates.extend(_discrete_rates([entry]))
    seen: List[float] = []
    for rate in rates:
        if rate not in seen:
            seen.append(rate)
    return seen


def sample_rates(mode: Dict[str, Any]) -> Tuple[List[int], Optional[Dict[str, int]]]:
    """A microphone mode's sample rates: the discrete `rates_hz` list, or a `rate_range_hz` {min, max}."""
    rates = [int(r) for r in mode.get("rates_hz") or [] if isinstance(r, (int, float)) and r > 0]
    span = mode.get("rate_range_hz")
    if isinstance(span, dict) and isinstance(span.get("min"), (int, float)) and isinstance(span.get("max"), (int, float)):
        return rates, {"min": int(span["min"]), "max": int(span["max"])}
    return rates, None


def mode_rows(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per mode. A camera row carries size, frame rates and Sentinel's verdict; a microphone row
    carries channels and sample rates (Sentinel gives microphones no verdict)."""
    rows = []
    microphone = device.get("type") == "microphone"
    for mode in device.get("modes") or []:
        if not isinstance(mode, dict):
            continue
        available = mode.get("available")
        row: Dict[str, Any] = {
            "format": mode.get("format"),
            "available": available if isinstance(available, bool) else None,
            "reason": str(mode.get("reason") or "") if available is False else "",
        }
        if microphone:
            rates, span = sample_rates(mode)
            row.update({
                "channels": mode.get("channels") if isinstance(mode.get("channels"), int) else None,
                "sample_bits": mode.get("sample_bits") if isinstance(mode.get("sample_bits"), int) else None,
                "sample_rates_hz": rates,
                "sample_rate_range_hz": span,
            })
        else:
            row.update({
                "width": mode.get("width"),
                "height": mode.get("height"),
                "rates": mode_rates(mode),
                "source": "isp_output" if mode.get("isp_output") else "sensor_mode" if mode.get("sensor_mode") else None,
            })
        rows.append(row)
    return rows


def support(device: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Sentinel's verdict for a device: ("available"|"unsupported", reason), or (None, "") when it makes none.

    A camera is Supported when any mode is. Otherwise its reason is the one most of its modes give, or
    the ISP's reason when the ISP itself is unavailable. Sentinel judges no other device type."""
    if device.get("type") != "camera":
        return None, ""
    rows = mode_rows(device)
    if any(row["available"] is True for row in rows):
        return "available", ""
    reasons = Counter(row["reason"] for row in rows if row["reason"])
    if reasons:
        return "unsupported", reasons.most_common(1)[0][0]
    isp = device.get("isp")
    if isinstance(isp, dict) and isp.get("state") == "unavailable" and isp.get("reason"):
        return "unsupported", str(isp["reason"])
    if not rows:
        return "unsupported", "The camera reports no modes."
    return None, ""


def status(device: Dict[str, Any]) -> str:
    """Connected, or In use when Sentinel can see that (only ALSA capture reports it)."""
    availability = device.get("availability") or {}
    return "In use" if isinstance(availability, dict) and availability.get("state") == "in_use" else "Connected"


def summarize(device: Dict[str, Any]) -> Dict[str, Any]:
    verdict, reason = support(device)
    rows = mode_rows(device)
    return {
        "id": device.get("id"),
        "type": device.get("type"),
        "type_label": type_label(device),
        "connection": connection_label(device),
        "name": device_name(device),
        "status": status(device),
        "support": verdict,
        "support_reason": reason,
        "modes": len(rows),
        "available_modes": sum(1 for row in rows if row["available"] is True),
    }


def find_device(devices: Sequence[Dict[str, Any]], wanted: str) -> Optional[Dict[str, Any]]:
    """The device with this id; a unique name matches too, so `describe imx477` works on a one-camera board."""
    for device in devices:
        if isinstance(device, dict) and device.get("id") == wanted:
            return device
    matches = [device for device in devices if isinstance(device, dict)
               and wanted.lower() in (device_name(device).lower(), str(device.get("model") or "").lower())]
    return matches[0] if len(matches) == 1 else None


def describe(device: Dict[str, Any], board: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    description = {"summary": summarize(device), "modes": mode_rows(device), "device": device}
    if isinstance(board, dict):
        description["board"] = board
    return description


PLATFORM_UNSUPPORTED = "platform.unsupported"


def catalog_warnings(catalog: Dict[str, Any]) -> List[str]:
    """Sentinel's provider errors as sentences: a firmware older than it supports comes first, verbatim."""
    warnings: List[str] = []
    for error in catalog.get("errors") or []:
        if not isinstance(error, dict):
            continue
        reason = str(error.get("reason") or "")
        if error.get("code") == PLATFORM_UNSUPPORTED:
            warnings.insert(0, reason or "This firmware is older than SiMa Sentinel supports for MIPI cameras.")
        else:
            warnings.append(f"Provider {error.get('provider')} failed: {reason}")
    return warnings


def verdict_warnings(devices: Sequence[Dict[str, Any]]) -> List[str]:
    """A camera with modes but no verdict on any of them comes from a Sentinel older than the verdict
    (sentinel#43); say so once, rather than showing `-` without a reason."""
    silent = [str(d.get("id")) for d in devices if isinstance(d, dict) and d.get("type") == "camera"
              and mode_rows(d) and all(row["available"] is None for row in mode_rows(d))]
    if not silent:
        return []
    return [f"SiMa Sentinel reports no availability verdict for {', '.join(silent)}; "
            "update the simaai-sentinel package to one that judges camera modes."]


def support_label(summary: Dict[str, Any]) -> str:
    if summary["support"] == "available":
        return "Supported"
    if summary["support"] == "unsupported":
        return "Unsupported"
    return "-"


def list_rows(summaries: Sequence[Dict[str, Any]]) -> List[List[str]]:
    """The human-readable table: ID, TYPE, CONNECTION, DEVICE, STATUS, SUPPORT."""
    return [[str(s["id"]), s["type_label"], s["connection"], s["name"], s["status"], support_label(s)] for s in summaries]


def envelope(command: str, devkit: Dict[str, Any], catalog: Optional[Dict[str, Any]], payload: Dict[str, Any],
             result: str = "ok", warnings: Optional[List[str]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The versioned JSON document every command prints with --json. New optional fields may be added;
    existing ones keep their meaning within a schema version."""
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "timestamp": stamp.replace("+00:00", "Z"),
        "devkit": devkit,
        "result": result,
        "warnings": list(warnings or []),
    }
    if isinstance(catalog, dict):
        document["sentinel"] = {"revision": catalog.get("revision"), "observed_at": catalog.get("observed_at"),
                                "errors": catalog.get("errors") or []}
    document.update(payload)
    return document
