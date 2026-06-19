"""
Cross-platform PCIe enumeration utility for Linux and Windows.

Features:
- Linux: sysfs (/sys/bus/pci/devices), no subprocess, no root
- Windows: WMI with lazy import
- Optional auto-install of Windows-only deps (wmi, pywin32)
- Maps vendor/device IDs to human-readable names
- Safe to import on any platform
"""

from pathlib import Path
import platform
import re
import subprocess
import sys
import os
import shutil
import json
from typing import List, Dict, Optional, Tuple

import click

# =============================================================================
# 1. SiMa PCI ID table (single source of truth)
# =============================================================================

SIMA_VENDOR_ID = "0x1f06"

SIMA_DEVICE_MAP = {
    "0xabcd": "Davinci Default",
    "0x0001": "Modalix Default",
    "0x1031": "Davinci 10L DM.2",
    "0x0031": "Davinci 08L DM.2",
    "0x0041": "Davinci HHHL",
    "0x1011": "Davinci DVT 933",
    "0x0011": "Davinci DVT",
    "0x0101": "Modalix DVT",
    "0x0121": "Modalix HHHL",
    "0x1121": "Modalix HHHL x16",
    "0x0123": "Modalix HHHL v2",
    "0x2123": "Modalix HHHL v2 1R",
    "0x1fe5": "Modalix Zebu PCIe",
}

# =============================================================================
# 2. Linux PCIe enumeration (sysfs)
# =============================================================================

def _list_pci_devices_linux() -> List[Dict]:
    devices = []
    base = Path("/sys/bus/pci/devices")

    if not base.exists():
        return devices

    for dev in base.iterdir():
        try:
            vendor = (dev / "vendor").read_text().strip().lower()
            device = (dev / "device").read_text().strip().lower()
        except Exception:
            continue

        devices.append({
            "os": "linux",
            "bdf": dev.name,              # domain:bus:device.func
            "vendor_id": vendor,
            "device_id": device,
        })

    return devices

# =============================================================================
# 3. Windows helpers (lazy deps + optional auto-install)
# =============================================================================

_PCI_ID_RE = re.compile(r"VEN_([0-9A-Fa-f]{4}).*DEV_([0-9A-Fa-f]{4})")

def _windows_deps_available() -> bool:
    try:
        import wmi  # noqa
        import win32com  # noqa (from pywin32)
        return True
    except Exception:
        return False


def _install_windows_deps():
    """
    Installs Windows-only PCIe deps using pip.
    Must be explicitly allowed by caller.
    """
    print("Installing Windows PCIe dependencies: wmi, pywin32")

    cmd = [
        sys.executable,
        "-m", "pip",
        "install",
        "--upgrade",
        "wmi",
        "pywin32",
    ]

    subprocess.check_call(cmd)


def _list_pci_devices_windows(auto_install: bool = False) -> List[Dict]:
    if not _windows_deps_available():
        if not auto_install:
            raise RuntimeError(
                "Missing Windows PCIe dependencies.\n"
                "Install manually with:\n"
                "  pip install wmi pywin32\n"
                "Or rerun with auto_install=True"
            )

        _install_windows_deps()

        if not _windows_deps_available():
            raise RuntimeError("Please run the command again, if the problem persists, contact support@sima.ai")

    # Safe to import now
    import wmi

    devices = []
    c = wmi.WMI()

    for dev in c.Win32_PnPEntity():
        if not dev.PNPDeviceID or not dev.PNPDeviceID.startswith("PCI"):
            continue

        m = _PCI_ID_RE.search(dev.PNPDeviceID)
        if not m:
            continue

        devices.append({
            "os": "windows",
            "name": dev.Name,
            "vendor_id": f"0x{m.group(1).lower()}",
            "device_id": f"0x{m.group(2).lower()}",
        })

    return devices

# =============================================================================
# 4. Cross-platform dispatcher
# =============================================================================

def list_pci_devices(auto_install_windows_deps: bool = False) -> List[Dict]:
    system = platform.system()

    if system == "Linux":
        return _list_pci_devices_linux()
    elif system == "Windows":
        return _list_pci_devices_windows(auto_install=auto_install_windows_deps)
    else:
        return []

# =============================================================================
# 5. High-level API: SiMa PCIe devices with names
# =============================================================================

def get_sima_pcie_devices(auto_install_windows_deps: bool = False) -> List[Dict]:
    """
    Returns detected SiMa PCIe devices with human-readable names.
    Supports multiple devices.
    """
    results = []

    for dev in list_pci_devices(auto_install_windows_deps):
        if dev.get("vendor_id") != SIMA_VENDOR_ID:
            continue

        device_name = SIMA_DEVICE_MAP.get(
            dev.get("device_id"),
            f"Unknown SiMa device ({dev.get('device_id')})"
        )

        entry = {
            "vendor_id": dev["vendor_id"],
            "device_id": dev["device_id"],
            "device_name": device_name,
        }

        # Preserve OS-specific fields
        for k, v in dev.items():
            if k not in entry:
                entry[k] = v

        results.append(entry)

    return results


def has_sima_pcie_device(auto_install_windows_deps: bool = False) -> bool:
    return len(get_sima_pcie_devices(auto_install_windows_deps)) > 0


def get_sima_pcie_device_names(auto_install_windows_deps: bool = False) -> List[str]:
    return [d["device_name"] for d in get_sima_pcie_devices(auto_install_windows_deps)]

# =============================================================================
# 6. PCIe virtual ethernet throughput test
# =============================================================================

def _get_virtual_pcie_interfaces() -> List[Tuple[str, str]]:
    """
    Return list of (iface, ipv4) for veth-simaai* interfaces.
    """
    if platform.system() != "Linux":
        return []

    try:
        output = subprocess.check_output(["ip", "-o", "-4", "addr", "show"], text=True)
    except Exception:
        return []

    interfaces = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        ip_cidr = parts[3]
        if not iface.startswith("veth-simaai"):
            continue
        ip = ip_cidr.split("/")[0]
        interfaces.append((iface, ip))

    return interfaces


def _derive_remote_ip(local_ip: str) -> Optional[str]:
    try:
        octets = local_ip.split(".")
        if len(octets) != 4:
            return None
        if octets[0] != "10":
            return None
        octets[3] = "2"
        return ".".join(octets)
    except Exception:
        return None


def _ensure_iperf3_installed() -> bool:
    if shutil.which("iperf3"):
        return True

    if not click.confirm("iperf3 is not installed. Install it now?", default=True):
        return False

    installers = [
        (["apt-get"], [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "iperf3"]]),
        (["dnf"], [["sudo", "dnf", "install", "-y", "iperf3"]]),
        (["yum"], [["sudo", "yum", "install", "-y", "iperf3"]]),
        (["pacman"], [["sudo", "pacman", "-S", "--noconfirm", "iperf3"]]),
        (["zypper"], [["sudo", "zypper", "install", "-y", "iperf3"]]),
    ]

    for check_cmd, install_cmds in installers:
        if shutil.which(check_cmd[0]):
            try:
                for cmd in install_cmds:
                    subprocess.check_call(cmd)
                return bool(shutil.which("iperf3"))
            except Exception:
                break

    click.echo("❌ Failed to install iperf3. Please install it manually.")
    return False


def _ssh_run_command(host: str, username: str, password: str, command: str) -> Tuple[int, str, str]:
    try:
        from sima_cli.utils.ssh import create_devkit_ssh_client
    except Exception as e:
        return 1, "", f"paramiko not available: {e}"

    client = create_devkit_ssh_client()
    try:
        client.connect(hostname=host, username=username, password=password, timeout=10)
        stdin, stdout, stderr = client.exec_command(command)
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
        return 0, out, err
    except Exception as e:
        return 1, "", str(e)
    finally:
        client.close()


def _ensure_remote_iperf3(host: str, username: str, password: str) -> bool:
    code, out, err = _ssh_run_command(host, username, password, "command -v iperf3")
    if code == 0 and out.strip():
        return True

    install_cmd = (
        "if command -v apt-get >/dev/null 2>&1; then sudo -S apt-get update && sudo -S apt-get install -y iperf3; "
        "elif command -v dnf >/dev/null 2>&1; then sudo -S dnf install -y iperf3; "
        "elif command -v yum >/dev/null 2>&1; then sudo -S yum install -y iperf3; "
        "elif command -v zypper >/dev/null 2>&1; then sudo -S zypper install -y iperf3; "
        "elif command -v pacman >/dev/null 2>&1; then sudo -S pacman -S --noconfirm iperf3; "
        "else echo 'no package manager'; exit 1; fi"
    )
    code, out, err = _ssh_run_command(
        host, username, password, f"printf '%s\\n' {password!r} | {install_cmd}"
    )
    return code == 0


def _start_remote_iperf3_server(host: str, username: str, password: str, bind_ip: str) -> bool:
    cmd = f"nohup iperf3 -s -1 -B {bind_ip} >/tmp/iperf3_{bind_ip}.log 2>&1 &"
    code, out, err = _ssh_run_command(host, username, password, cmd)
    return code == 0


def _run_iperf3_client(remote_ip: str, local_ip: str) -> Optional[float]:
    cmd = ["iperf3", "-J", "-c", remote_ip, "-B", local_ip, "-t", "5"]
    try:
        proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(proc.stdout)
        end = data.get("end", {})
        summary = end.get("sum_received") or end.get("sum_sent") or {}
        bps = summary.get("bits_per_second")
        if bps is not None:
            return float(bps)
    except Exception:
        return None
    return None


def maybe_run_pcie_throughput_test():
    """
    Check for veth-simaai virtual ethernet and optionally run iperf3 throughput test.
    """
    if platform.system() != "Linux":
        click.echo("ℹ️  PCIe virtual ethernet throughput test is only supported on Linux.")
        return

    interfaces = _get_virtual_pcie_interfaces()
    if not interfaces:
        click.echo("⚠️  Virtual ethernet over PCIe is not available (veth-simaai not found).")
        return

    if not click.confirm("Detected PCIe virtual ethernet. Run throughput test now?", default=False):
        return

    if not _ensure_iperf3_installed():
        return

    username = "sima"
    password = "edgeai"

    for iface, local_ip in interfaces:
        remote_ip = _derive_remote_ip(local_ip)
        if not remote_ip:
            click.echo(f"⚠️  Skipping {iface} ({local_ip}): cannot derive remote IP.")
            continue

        click.echo(f"🔗 Testing {local_ip} -> {remote_ip} ...")

        if not _ensure_remote_iperf3(remote_ip, username, password):
            click.echo(f"❌ iperf3 not available on {remote_ip}. Skipping.")
            continue

        if not _start_remote_iperf3_server(remote_ip, username, password, remote_ip):
            click.echo(f"❌ Failed to start iperf3 server on {remote_ip}.")
            continue

        bps = _run_iperf3_client(remote_ip, local_ip)
        if bps is None:
            click.echo(f"❌ iperf3 test failed for {local_ip} -> {remote_ip}.")
            continue

        gbps = bps / 1e9
        if gbps >= 4.0:
            click.echo(f"✅ Virtual Ethernet Throughput: {gbps:.2f} Gbps (healthy)")
        else:
            click.echo(f"⚠️ Virtual Ethernet Throughput: {gbps:.2f} Gbps (below 4 Gbps)")

# =============================================================================
# 7. CLI / debug usage
# =============================================================================

if __name__ == "__main__":
    # Opt-in auto-install via env var (recommended)
    AUTO_INSTALL = os.getenv("SIMA_AUTO_INSTALL_WINDOWS_DEPS") == "1"

    try:
        devices = get_sima_pcie_devices(auto_install_windows_deps=AUTO_INSTALL)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    if not devices:
        print("❌ No SiMa PCIe devices detected")
        sys.exit(2)

    for d in devices:
        if d["os"] == "linux":
            print(
                f"✅ {d['device_name']} "
                f"({d['vendor_id']}:{d['device_id']}) "
                f"@ {d.get('bdf')}"
            )
        else:
            print(
                f"✅ {d['device_name']} "
                f"({d['vendor_id']}:{d['device_id']}) "
                f"- {d.get('name', '')}"
            )

    maybe_run_pcie_throughput_test()
