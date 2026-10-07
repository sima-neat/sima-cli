import os
import re
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

import plotext as plt


LEGACY_MEMORY_PATH = "/dev/simaai-mem"
MLA_DMA_HEAP_PATH = "/dev/dma_heap/simaai,dms"
DMA_BUFINFO_PATH = "/sys/kernel/debug/dma_buf/bufinfo"

_LEGACY_TOTAL_RE = re.compile(
    r"Total allocated size:\s+0x([0-9a-fA-F]+)", re.IGNORECASE
)
_DMA_BUF_HEADER_RE = re.compile(r"\bsize\b.*\bexp_name\b.*\bino\b")
_DMA_BUF_OBJECT_RE = re.compile(
    r"^\s*(\d+)\s+"  # size (decimal, zero padded)
    r"[0-9a-fA-F]+\s+"  # flags
    r"[0-9a-fA-F]+\s+"  # mode
    r"\d+\s+"  # reference count
    r"(\S+)\s+"  # exporter name
    r"(\d+)"  # unique DMA-BUF inode
    r"(?:\s+.*)?$"  # optional object name
)
_MLA_DMA_BUF_EXPORTERS = frozenset(("mla", "simaai,dms"))


class MemoryTelemetryError(RuntimeError):
    """Raised when the selected platform telemetry cannot be read or parsed."""


@dataclass(frozen=True)
class DmaBufObject:
    size_bytes: int
    exporter: str
    inode: int
    attached_devices: Tuple[str, ...]


class MemorySource(NamedTuple):
    name: str
    path: str
    parser: Callable[[str], int]


def parse_legacy_allocated_bytes(output: str) -> int:
    """Parse the total allocated bytes reported by /dev/simaai-mem."""
    match = _LEGACY_TOTAL_RE.search(output)
    if not match:
        raise ValueError("missing legacy total allocated size")
    return int(match.group(1), 16)


def parse_dma_buf_objects(output: str) -> List[DmaBufObject]:
    """Parse the kernel DMA-BUF debugfs snapshot.

    The inode printed by the kernel is the stable identity for one object in a
    snapshot. Attached-device lines belong to the immediately preceding
    object.
    """
    lines = output.splitlines()
    if not any(_DMA_BUF_HEADER_RE.search(line) for line in lines):
        raise ValueError("missing DMA-BUF object header")

    objects = []  # type: List[DmaBufObject]
    current_size = None  # type: Optional[int]
    current_exporter = None  # type: Optional[str]
    current_inode = None  # type: Optional[int]
    current_devices = []  # type: List[str]
    reading_devices = False

    def finish_current() -> None:
        nonlocal current_size, current_exporter, current_inode, current_devices
        if (
            current_size is not None
            and current_exporter is not None
            and current_inode is not None
        ):
            objects.append(
                DmaBufObject(
                    size_bytes=current_size,
                    exporter=current_exporter,
                    inode=current_inode,
                    attached_devices=tuple(current_devices),
                )
            )
        current_size = None
        current_exporter = None
        current_inode = None
        current_devices = []

    for line in lines:
        object_match = _DMA_BUF_OBJECT_RE.match(line)
        if object_match:
            finish_current()
            current_size = int(object_match.group(1), 10)
            current_exporter = object_match.group(2)
            current_inode = int(object_match.group(3), 10)
            reading_devices = False
            continue

        stripped = line.strip()
        if stripped == "Attached Devices:":
            reading_devices = current_inode is not None
            continue
        if reading_devices and stripped.startswith("Total "):
            reading_devices = False
            continue
        if reading_devices and stripped:
            current_devices.append(stripped)

    finish_current()
    return objects


def _is_mla_device(device_name: str) -> bool:
    normalized = device_name.strip().lower()
    return normalized == "mla" or normalized.endswith(".mla")


def parse_dma_buf_mla_bytes(output: str) -> int:
    """Return the union of DMA-BUF objects exported for or attached to MLA."""
    matched_by_inode = {}
    for obj in parse_dma_buf_objects(output):
        is_mla_exporter = obj.exporter.lower() in _MLA_DMA_BUF_EXPORTERS
        is_mla_attachment = any(_is_mla_device(device) for device in obj.attached_devices)
        if not is_mla_exporter and not is_mla_attachment:
            continue

        previous_size = matched_by_inode.get(obj.inode)
        if previous_size is not None and previous_size != obj.size_bytes:
            raise ValueError(
                "conflicting sizes for DMA-BUF inode {}".format(obj.inode)
            )
        matched_by_inode[obj.inode] = obj.size_bytes
    return sum(matched_by_inode.values())


def detect_memory_source(
    path_exists: Callable[[str], bool] = os.path.exists,
) -> MemorySource:
    """Select 3.0 DMA-BUF telemetry or the pre-3.0 legacy allocator."""
    if path_exists(MLA_DMA_HEAP_PATH):
        return MemorySource(
            name="DMA-BUF (simaai,dms and MLA attachments)",
            path=DMA_BUFINFO_PATH,
            parser=parse_dma_buf_mla_bytes,
        )
    if path_exists(LEGACY_MEMORY_PATH):
        return MemorySource(
            name="legacy simaai-memory",
            path=LEGACY_MEMORY_PATH,
            parser=parse_legacy_allocated_bytes,
        )
    raise MemoryTelemetryError(
        "No supported MLA memory telemetry was found. Expected {} on Platform "
        "3.0 or {} on earlier releases.".format(
            MLA_DMA_HEAP_PATH, LEGACY_MEMORY_PATH
        )
    )


def _read_telemetry_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            return stream.read()
    except PermissionError:
        pass
    except OSError as exc:
        if not os.path.exists(path):
            raise MemoryTelemetryError(
                "Telemetry path is unavailable: {}".format(path)
            ) from exc

    result = subprocess.run(
        ["sudo", "cat", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "exit status {}".format(result.returncode)
        raise MemoryTelemetryError("Unable to read {}: {}".format(path, detail))
    return result.stdout


def read_mla_memory_bytes(source: Optional[MemorySource] = None) -> int:
    selected = source or detect_memory_source()
    try:
        return selected.parser(_read_telemetry_text(selected.path))
    except MemoryTelemetryError:
        raise
    except ValueError as exc:
        raise MemoryTelemetryError(
            "Unable to parse {} telemetry from {}: {}".format(
                selected.name, selected.path, exc
            )
        ) from exc


def chart_limits_mib(values: Sequence[float]) -> Tuple[float, float]:
    """Return a nonzero, nonnegative Y range for a memory sample series."""
    if not values:
        raise ValueError("at least one memory sample is required")
    low = min(values)
    high = max(values)
    if low == high:
        padding = max(abs(high) * 0.05, 1.0)
    else:
        padding = max((high - low) * 0.05, 0.01)
    lower = max(0.0, low - padding)
    upper = high + padding
    if upper <= lower:
        upper = lower + 1.0
    return lower, upper


def monitor_simaai_mem_chart(sample_interval_sec=5, max_samples=100):
    sizes = []
    try:
        source = detect_memory_source()
    except MemoryTelemetryError as exc:
        print("Error: {}".format(exc))
        return False

    print("MLA memory telemetry source: {}".format(source.name))
    print("Monitoring MLA memory usage... (Press 'q' or Ctrl+C to quit)")

    try:
        while True:
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                key = sys.stdin.read(1)
                if key.lower() == "q":
                    print("\nExiting memory monitor...")
                    break

            try:
                size_bytes = read_mla_memory_bytes(source)
            except MemoryTelemetryError as exc:
                print("Error: {}".format(exc))
                return False

            sizes.append(size_bytes / (1024 * 1024))
            sizes = sizes[-max_samples:]

            plt.clear_data()
            plt.clc()
            plt.title("SiMa MLA Memory Usage (MiB)")
            plt.xlabel("Samples")
            plt.ylabel("Memory (MiB)")
            plt.plot(sizes)
            plt.ylim(*chart_limits_mib(sizes))
            plt.show()
            time.sleep(sample_interval_sec)
    except KeyboardInterrupt:
        print("\nExiting memory monitor...")

    return True
