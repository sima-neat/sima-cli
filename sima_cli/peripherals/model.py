"""Pure functions over SiMa Sentinel's peripheral catalog: rows, descriptions, the JSON envelope.

Nothing here touches the board. The verdict on each camera mode (`available` and `reason`) is
Sentinel's; this module only summarizes it per device.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The --json document. Its fields are the CLI's own projection of Sentinel's catalog: nothing from
# Sentinel is passed through unprojected, so a Sentinel change cannot alter this shape silently.
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

def exit_code_for(error_code: str) -> int:
    return EXIT_FOR_ERROR.get(error_code, ExitCode.SENTINEL_UNAVAILABLE)


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
    """Frame rates a camera mode advertises, from its discrete frame intervals (seconds per frame).

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


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or (
        isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) for v in value))


def _verdict(mode: Dict[str, Any]) -> Dict[str, Any]:
    available = mode.get("available")
    return {"available": available if isinstance(available, bool) else None,
            "reason": str(mode.get("reason") or "") if available is False else ""}


class TypeAdapter:
    """What the CLI knows about one peripheral type: how Sentinel's record for it becomes the CLI's
    rows, facts and verdict. The base class is the adapter for a type the CLI has no knowledge of
    (a provider a newer Sentinel adds): it passes each mode's scalar properties through unchanged and
    claims no verdict beyond the one Sentinel wrote on the mode, so the device is listed and
    describable without being misrepresented as a camera."""

    type = ""
    label = "Device"
    mode_headers = ("FORMAT", "PROPERTIES", "SUPPORT", "REASON")

    def connection(self, device: Dict[str, Any]) -> str:
        connection = device.get("connection")
        known = {"usb": "USB", "platform": "On-board"}
        return known.get(str(connection)) or str(connection or device.get("backend") or "Unknown")

    def facts(self, device: Dict[str, Any]) -> Dict[str, Any]:
        """The CLI-owned facts for `describe`, in JSON form. Unknown types have none beyond the summary."""
        return {}

    def fact_lines(self, facts: Dict[str, Any]) -> List[Tuple[str, str]]:
        """The same facts as (label, text) lines for the human description."""
        return [(key.replace("_", " ").capitalize(), str(value)) for key, value in facts.items()]

    def mode_rows(self, device: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        for mode in device.get("modes") or []:
            if isinstance(mode, dict):
                rows.append(self.mode_row(mode))
        return rows

    def mode_row(self, mode: Dict[str, Any]) -> Dict[str, Any]:
        properties = {k: v for k, v in mode.items() if k not in ("format", "available", "reason") and _scalar(v)}
        return {"format": mode.get("format"), "properties": properties, **_verdict(mode)}

    def mode_cells(self, row: Dict[str, Any]) -> List[str]:
        properties = ", ".join(f"{k}={v}" for k, v in row["properties"].items()) or "-"
        return [str(row["format"] or "-"), properties, verdict_label(row["available"]), row["reason"]]

    def support(self, device: Dict[str, Any]) -> Tuple[Optional[str], str]:
        """Sentinel's verdict for the device: ("available"|"unsupported", reason), or (None, "") when
        Sentinel wrote none. Supported when any mode is; otherwise the reason most of its modes give."""
        rows = self.mode_rows(device)
        if any(row["available"] is True for row in rows):
            return "available", ""
        reasons = Counter(row["reason"] for row in rows if row["reason"])
        if reasons:
            return "unsupported", reasons.most_common(1)[0][0]
        return None, ""


class CameraAdapter(TypeAdapter):
    type = "camera"
    label = "Camera"
    mode_headers = ("FORMAT", "SIZE", "FPS", "SUPPORT", "REASON")

    def connection(self, device: Dict[str, Any]) -> str:
        backend = device.get("backend")
        return "MIPI CSI-2" if backend == "mipi" else "USB" if backend == "v4l2" else str(backend or "Unknown")

    def facts(self, device: Dict[str, Any]) -> Dict[str, Any]:
        facts: Dict[str, Any] = {}
        for key in ("camera_name", "media_device", "bus_info", "csi_receiver", "device_path", "by_id_path"):
            if isinstance(device.get(key), str) and device[key]:
                facts[key] = device[key]
        isp = device.get("isp")
        if isinstance(isp, dict):
            facts["isp"] = {"state": isp.get("state"), "sizing": isp.get("sizing")}
            if isp.get("reason"):
                facts["isp"]["reason"] = str(isp["reason"])
        max_fps = device.get("max_fps")
        if isinstance(max_fps, (int, float)) and max_fps > 0:
            timing = device.get("sensor_timing") if isinstance(device.get("sensor_timing"), dict) else {}
            facts["sensor_max_fps"] = {"fps": max_fps, "width": timing.get("width"), "height": timing.get("height")}
        return facts

    def fact_lines(self, facts: Dict[str, Any]) -> List[Tuple[str, str]]:
        lines: List[Tuple[str, str]] = []
        for key, value in facts.items():
            if key == "isp":
                text = str(value.get("state")) + (", sizes set at run time" if value.get("sizing") == "runtime" else "")
                lines.append(("ISP", text + (f" ({value['reason']})" if value.get("reason") else "")))
            elif key == "sensor_max_fps":
                size = f" at {value['width']}x{value['height']}" if value.get("width") and value.get("height") else ""
                lines.append(("Sensor max rate",
                              f"{value['fps']:g} fps{size} (the sensor's limit; the ISP reports no per-mode rate)"))
            else:
                lines.append((key.replace("_", " ").capitalize(), str(value)))
        return lines

    def mode_row(self, mode: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "format": mode.get("format"),
            "width": mode.get("width"),
            "height": mode.get("height"),
            "rates": mode_rates(mode),
            "source": "isp_output" if mode.get("isp_output") else "sensor_mode" if mode.get("sensor_mode") else None,
            **_verdict(mode),
        }

    def mode_cells(self, row: Dict[str, Any]) -> List[str]:
        size = f"{row['width']}x{row['height']}" if row.get("width") else "-"
        rates = ", ".join(f"{r:g}" for r in row["rates"]) or "-"
        return [str(row["format"] or "-"), size, rates, verdict_label(row["available"]), row["reason"]]

    def support(self, device: Dict[str, Any]) -> Tuple[Optional[str], str]:
        """A camera adds two cases to the generic rule: an unavailable ISP gives its own reason, and a
        camera with no modes at all is Unsupported."""
        verdict, reason = super().support(device)
        if verdict is not None:
            return verdict, reason
        isp = device.get("isp")
        if isinstance(isp, dict) and isp.get("state") == "unavailable" and isp.get("reason"):
            return "unsupported", str(isp["reason"])
        if not self.mode_rows(device):
            return "unsupported", "The camera reports no modes."
        return None, ""


class MicrophoneAdapter(TypeAdapter):
    type = "microphone"
    label = "Microphone"
    mode_headers = ("FORMAT", "CHANNELS", "SAMPLE RATE (Hz)")

    def facts(self, device: Dict[str, Any]) -> Dict[str, Any]:
        identity = device.get("identity") if isinstance(device.get("identity"), dict) else {}
        capture = device.get("capture_target") if isinstance(device.get("capture_target"), dict) else {}
        pairs = (("device_node", identity.get("pcm_node")), ("by_id", identity.get("by_id")),
                 ("driver", identity.get("card_driver")), ("alsa_device", capture.get("selector")))
        return {key: str(value) for key, value in pairs if value}

    def fact_lines(self, facts: Dict[str, Any]) -> List[Tuple[str, str]]:
        labels = {"device_node": "Device node", "by_id": "By id", "driver": "Driver", "alsa_device": "ALSA device"}
        return [(labels.get(key, key), str(value)) for key, value in facts.items()]

    def mode_row(self, mode: Dict[str, Any]) -> Dict[str, Any]:
        rates, span = sample_rates(mode)
        return {
            "format": mode.get("format"),
            "channels": mode.get("channels") if isinstance(mode.get("channels"), int) else None,
            "sample_bits": mode.get("sample_bits") if isinstance(mode.get("sample_bits"), int) else None,
            "sample_rates_hz": rates,
            "sample_rate_range_hz": span,
            **_verdict(mode),
        }

    def mode_cells(self, row: Dict[str, Any]) -> List[str]:
        rates = ", ".join(str(r) for r in row["sample_rates_hz"])
        span = row["sample_rate_range_hz"]
        if span:
            rates = ", ".join(filter(None, [rates, f"{span['min']}-{span['max']}"]))
        return [str(row["format"] or "-"), str(row["channels"]) if row["channels"] is not None else "-", rates or "-"]

    def support(self, device: Dict[str, Any]) -> Tuple[Optional[str], str]:
        """Neat has no audio input today, so Sentinel judges no microphone and neither does the CLI."""
        return None, ""


ADAPTERS: Dict[str, TypeAdapter] = {adapter.type: adapter for adapter in (CameraAdapter(), MicrophoneAdapter())}


def adapter_for_type(kind: Any) -> TypeAdapter:
    """The adapter registered for a Sentinel device type, or the generic one for a type the CLI does
    not know (its label is the type's name)."""
    known = ADAPTERS.get(str(kind or ""))
    if known is not None:
        return known
    generic = TypeAdapter()
    generic.type = str(kind or "")
    generic.label = generic.type.capitalize() or "Device"
    return generic


def adapter_for(device: Dict[str, Any]) -> TypeAdapter:
    return adapter_for_type(device.get("type"))


def type_label(device: Dict[str, Any]) -> str:
    return adapter_for(device).label


def connection_label(device: Dict[str, Any]) -> str:
    return adapter_for(device).connection(device)


def device_name(device: Dict[str, Any]) -> str:
    """The readable name: the sensor's camera name, the USB product, the ALSA card name."""
    for key in ("camera_name", "model", "name"):
        value = device.get(key)
        if isinstance(value, str) and value:
            return value
    identity = device.get("identity") or {}
    product = identity.get("product") if isinstance(identity, dict) else None
    return str(product or device.get("id") or "")


def mode_rows(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per mode, in the shape the device's type adapter defines."""
    return adapter_for(device).mode_rows(device)


def support(device: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Sentinel's verdict for a device, read by its type adapter: ("available"|"unsupported", reason),
    or (None, "") when there is none."""
    return adapter_for(device).support(device)


def status(device: Dict[str, Any]) -> str:
    """Connected, or In use when Sentinel can see that (only ALSA capture reports it)."""
    availability = device.get("availability") or {}
    return "In use" if isinstance(availability, dict) and availability.get("state") == "in_use" else "Connected"


def summarize(device: Dict[str, Any]) -> Dict[str, Any]:
    adapter = adapter_for(device)
    verdict, reason = adapter.support(device)
    rows = adapter.mode_rows(device)
    return {
        "id": device.get("id"),
        "type": device.get("type"),
        "type_label": adapter.label,
        "connection": adapter.connection(device),
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


def board_facts(board: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The board facts the CLI schema owns: firmware, model and the enabled overlays."""
    if not isinstance(board, dict):
        return None
    overlays = board.get("overlays")
    return {"firmware": board.get("firmware"), "model": board.get("model"),
            "overlays": [str(o) for o in overlays] if isinstance(overlays, list) else []}


def describe(device: Dict[str, Any], board: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The describe document's payload. Every field is one the CLI schema owns and its type adapter
    projects; Sentinel's record is not passed through, so a Sentinel upgrade cannot change this
    document's shape without a change here (and a schema version bump)."""
    adapter = adapter_for(device)
    description = {"summary": summarize(device), "facts": adapter.facts(device), "modes": adapter.mode_rows(device)}
    facts = board_facts(board)
    if facts is not None:
        description["board"] = facts
    return description


def verdict_label(available: Optional[bool]) -> str:
    return "Supported" if available is True else "Unsupported" if available is False else "-"


PLATFORM_UNSUPPORTED = "platform.unsupported"


def catalog_errors(catalog: Dict[str, Any]) -> List[Dict[str, str]]:
    """Sentinel's provider errors projected to the fields the CLI schema owns: provider, code, reason."""
    return [{key: str(error.get(key) or "") for key in ("provider", "code", "reason")}
            for error in catalog.get("errors") or [] if isinstance(error, dict)]


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
    return verdict_label(True if summary["support"] == "available" else False if summary["support"] == "unsupported" else None)


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
                                "errors": catalog_errors(catalog)}
    document.update(payload)
    return document
