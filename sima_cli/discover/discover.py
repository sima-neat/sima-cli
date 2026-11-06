#!/usr/bin/env python3
import socket
import ipaddress
import select
import threading
import platform
import subprocess
import json
import time
import re
import shutil
import psutil
from typing import List, Dict
from rich.console import Console
from rich.table import Table
from sima_cli.utils.net import get_local_ip_candidates
from sima_cli.discover.identity import valid_mac
from sima_cli.update.remote import get_remote_board_info
from sima_cli.discover.linuxll import suggest_and_switch_to_linklocal
from sima_cli.utils.pcie import get_sima_pcie_devices, maybe_run_pcie_throughput_test
# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
MCAST_GRP = "239.255.42.1"
MCAST_PORT = 50000
SRC_PORT   = 60000
TIMEOUT    = 1.0
DISCOVERY_MSG = b"DISCOVER"
SIMA_OUI = "68:e1:54"

console = Console()

# ─────────────────────────────────────────────────────────────
# Helper: MAC normalization
# ─────────────────────────────────────────────────────────────
def normalize_mac(mac: str) -> str:
    parts = re.split(r"[:-]", mac.lower())
    parts = [p.zfill(2) for p in parts if p]
    return ":".join(parts)

# ─────────────────────────────────────────────────────────────
# ARP Table Scanner
# ─────────────────────────────────────────────────────────────
def get_sima_devices_from_arp():
    """Cross-platform ARP table parser to find SiMa.ai devices."""
    system = platform.system().lower()
    entries = []

    try:
        if "darwin" in system:
            output = subprocess.check_output(["arp", "-n", "-a"], text=True)
            pattern = re.compile(
                r"\((?P<ip>\d+\.\d+\.\d+\.\d+)\)\s+at\s+(?P<mac>(?:[0-9a-f]{1,2}[:-]){5}[0-9a-f]{1,2})",
                re.IGNORECASE,
            )
        elif "linux" in system:
            try:
                output = subprocess.check_output(["ip", "neigh", "show"], text=True)
            except FileNotFoundError:
                if not shutil.which("arp"):
                    console.print("[yellow]ARP lookup unavailable: install iproute2 (ip) or net-tools (arp); continuing with multicast discovery.[/yellow]")
                    return []
                output = subprocess.check_output(["arp", "-n"], text=True)
            pattern = re.compile(
                r"(?P<ip>\d+\.\d+\.\d+\.\d+).*?(?P<mac>(?:[0-9a-f]{1,2}:){5}[0-9a-f]{1,2})",
                re.IGNORECASE,
            )
        elif "windows" in system:
            output = subprocess.check_output(["arp", "-a"], text=True, encoding="utf-8", errors="ignore")
            output = output.replace("-", ":")
            pattern = re.compile(
                r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mac>(?:[0-9a-f]{1,2}:){5}[0-9a-f]{1,2})",
                re.IGNORECASE,
            )
        else:
            console.print(f"[yellow]⚠️ Unsupported OS: {system}[/yellow]")
            return []

        for match in pattern.finditer(output):
            ip = match.group("ip")
            mac = normalize_mac(match.group("mac"))
            if mac.startswith(SIMA_OUI):
                entries.append({"ip": ip, "mac": mac})

        if entries:
            console.print(f"[green]✅ Found {len(entries)} SiMa device(s) in ARP cache[/green]")
        else:
            console.print(f"[yellow]⚠️  No SiMa devices found in local ARP table.[/yellow]")

        return entries

    except Exception as e:
        console.print(f"[red]❌ ARP lookup failed: {e}[/red]")
        return []


# ─────────────────────────────────────────────────────────────
# Multicast Discovery
# ─────────────────────────────────────────────────────────────
def query_multicast(*, iface=None, timeout=TIMEOUT, cancel_event=None, require_mac=True):
    """Query local interfaces within one deadline, without SSH or UI effects.

    Keep source port 60000 for compatibility with existing discovery responders.
    The datagram source is authoritative for the candidate IP, not JSON's IP.
    """
    cancel = cancel_event if cancel_event is not None else threading.Event()
    deadline = time.monotonic() + timeout
    sockets = {}
    records = {}
    try:
        for name, address in get_local_ip_candidates(include_link_local=True):
            if cancel.is_set() or time.monotonic() >= deadline:
                break
            if iface and name != iface:
                continue
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((address, SRC_PORT))
                except OSError:
                    # Preserve the existing discovery fallback for hosts that
                    # cannot bind the reply port to a particular interface IP.
                    sock.bind(('0.0.0.0', SRC_PORT))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
                sock.setblocking(False)
                sock.sendto(DISCOVERY_MSG, (MCAST_GRP, MCAST_PORT))
                sockets[sock] = name
            except OSError:
                if sock is not None:
                    sock.close()
        while sockets and not cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(list(sockets), [], [], min(0.1, remaining))
            for sock in ready:
                try:
                    data, source = sock.recvfrom(2048)
                    message = json.loads(data.decode('utf-8'))
                    if not isinstance(message, dict):
                        continue
                    mac = valid_mac(message.get('mac'))
                    ip = str(ipaddress.IPv4Address(source[0]))
                    if mac or not require_mac:
                        record = dict(message, ip=ip, mac=mac, iface=sockets[sock])
                        records[(ip, mac, sockets[sock])] = record
                except (OSError, ValueError, UnicodeError):
                    continue
    finally:
        for sock in sockets:
            sock.close()
    return list(records.values())


def discover_multicast():
    """Interactive presentation of the shared multicast query results."""
    console.print("[cyan]📡 Discovering nearby SiMa.ai DevKits via multicast...[/cyan]")
    # Keep the public response shape and one result per IP, including replies
    # without a MAC. Background callers can retain individual interface records.
    responses = {}
    for record in query_multicast(require_mac=False):
        responses.setdefault(record['ip'], dict(record, mac=record.get('mac') or '-',
                                               **{'from': record['ip']}))
    return list(responses.values())

# ─────────────────────────────────────────────────────────────
# Reusable Table Renderer
# ─────────────────────────────────────────────────────────────
def render_device_table(devices):
    """Render a clean, deduplicated table of discovered SiMa devices."""
    if not devices:
        console.print("[yellow]⚠️  No SiMa devices to display.[/yellow]")
        return

    # Deduplicate by MAC (preferred) or IP fallback
    seen = set()
    unique_devices = []
    for dev in devices:
        mac = dev.get("mac")
        key = mac if mac not in {None, "", "-", "—"} else dev.get("ip")
        if key and key not in seen:
            seen.add(key)
            unique_devices.append(dev)
        else:
            # Skip duplicate silently
            continue

    if not unique_devices:
        console.print("[yellow]⚠️  No unique SiMa devices found after filtering duplicates.[/yellow]")
        return

    has_connection_source = any(device.get("connection") for device in unique_devices)
    has_cloudex = any(device.get("source") == "CloudEx" for device in unique_devices)
    table = Table(title="Discovered SiMa Devices" if has_connection_source else "SiMa Devices on the local network")
    if has_connection_source:
        table.add_column("Connection", justify="center")
    if has_cloudex:
        table.add_column("Device")
    table.add_column("IP", justify="center")
    table.add_column("MAC", justify="center")
    table.add_column("Board Type", justify="center")
    table.add_column("Build Version", justify="center")
    table.add_column("DevKit Model", justify="center")
    table.add_column("Full Image", justify="center")
    table.add_column("FW Type", justify="center")

    for dev in unique_devices:
        values = [
            dev.get("ip", "-"),
            dev.get("mac", "-"),
            dev.get("board", "-"),
            dev.get("version", "-"),
            dev.get("model", "-"),
            "—" if dev.get("full_image") is None else "✅" if dev.get("full_image") else "❌",
            dev.get("fwtype", "-"),
        ]
        if has_cloudex:
            values.insert(0, dev.get("device", "—"))
        if has_connection_source:
            values.insert(0, dev.get("connection", "local"))
        table.add_row(*values)
    console.print(table)


def discover_cloudex_devices():
    """Return connected CloudEx endpoints enriched through the normal SSH probe."""
    try:
        # CloudEx remains experimental, so keep its state dependency lazy and
        # let ordinary LAN discovery work even when that state is unavailable.
        from sima_cli.cloudex.client import SessionStore, inspect_session

        store = SessionStore()
        sessions = store.list()
    except Exception as exc:
        console.print(f"[yellow]⚠️  CloudEx connection state could not be read: {exc}[/yellow]")
        return []

    sessions_to_probe = []
    for session in sessions:
        try:
            status = inspect_session(session, store)
        except Exception:
            continue
        target = session.get("target")
        if not status.get("connected") or not isinstance(target, str) or not target:
            continue
        path = status.get("path", "unknown")
        path_label = "relayed" if path == "relay" else path
        sessions_to_probe.append({
            "ip": target,
            "mac": "—",
            "device": session.get("device", "DevKit " + str(session.get("allocation_id", ""))[:8]),
            "board": "—",
            "version": "—",
            "model": "—",
            "full_image": None,
            "fwtype": "—",
            "source": "CloudEx",
            "path": path_label,
            "connection": "CloudEx ({})".format(path_label),
        })

    if sessions_to_probe:
        console.print("[cyan]🔍 Probing CloudEx-connected devices via SSH...[/cyan]")
    devices = []
    for device in sessions_to_probe:
        board, version, model, full, fw = get_remote_board_info(device["ip"])
        identified = any((board, version, model, fw))
        devices.append({
            **device,
            "board": board or "—",
            "version": version or "—",
            "model": model or "—",
            "full_image": full if identified else None,
            "fwtype": fw or "—",
        })
    return devices


def discover_and_render_pcie_devices():
    # ------------------------------------------------------------------
    # PCIe Devices Table
    # ------------------------------------------------------------------
    pcie_devices = get_sima_pcie_devices(auto_install_windows_deps=True)

    if not pcie_devices:
        return

    pcie_table = Table(title="SiMa PCIe Devices Detected")

    pcie_table.add_column("OS", justify="center")
    pcie_table.add_column("Device Name", justify="left")
    pcie_table.add_column("Vendor ID", justify="center")
    pcie_table.add_column("Device ID", justify="center")
    pcie_table.add_column("Location", justify="center")

    for d in pcie_devices:
        location = (
            d.get("bdf") if d["os"] == "linux"
            else d.get("name", "-")
        )

        pcie_table.add_row(
            d.get("os", "-"),
            d.get("device_name", "-"),
            d.get("vendor_id", "-"),
            d.get("device_id", "-"),
            location,
        )

    console.print(pcie_table)
    maybe_run_pcie_throughput_test(pcie_devices)

# ─────────────────────────────────────────────────────────────
# Unified Discovery Orchestration
# ─────────────────────────────────────────────────────────────
def discover_and_probe(mdns_only: bool = False, include_cloudex: bool = False) -> List[Dict[str, str]]:
    # Check if there's any interface without IP (Ubuntu quirk), if so, fix it by setting to linklocal
    suggest_and_switch_to_linklocal()
    cloudex_devices = discover_cloudex_devices() if include_cloudex else []

    if not mdns_only:
        arp_devices = get_sima_devices_from_arp()
        enriched = []

        if arp_devices:
            console.print("[cyan]🔍 Probing ARP-discovered devices via SSH...[/cyan]")
            for entry in arp_devices:
                ip, mac = entry["ip"], entry["mac"]
                board, version, model, full, fw = get_remote_board_info(ip)
                enriched.append({
                    "ip": ip,
                    "mac": mac,
                    "board": board,
                    "version": version,
                    "model": model,
                    "full_image": full,
                    "fwtype": fw,
                    "connection": "local" if cloudex_devices else None,
                })
            devices = cloudex_devices + enriched
            render_device_table(devices)
            return devices

        if cloudex_devices:
            console.print("[green]✅ Found {} active CloudEx connection(s)[/green]".format(len(cloudex_devices)))
            render_device_table(cloudex_devices)
            console.print("[dim]Use --ignore-cache to additionally scan local networks with multicast.[/dim]")
            return cloudex_devices

        # 2️⃣ No ARP hits → Ask user for multicast
        console.print(
            "\n[yellow]"
            "Would you like to run a broader multicast scan on the local networks?\n"
            "This sends a DISCOVER packet to multicast group address 239.255.42.1:50000 which is utilized by the DevKit to support zeroconf discovery.\n"
            "This is generally a safe operation but if unsure, contact your IT administrator.[/yellow]\n"
        )
        ans = input("🔔 Proceed with multicast scan? (y/N): ").strip().lower()
        if ans != "y":
            console.print("[red]Aborted by user.[/red]")
            return []
    else:
        console.print("[cyan]📡 mDNS/multicast-only discovery mode enabled. Skipping ARP scan.[/cyan]")

    responses = discover_multicast()
    if not responses:
        if cloudex_devices:
            console.print("[yellow]⚠️  No multicast responses received; showing active CloudEx connections.[/yellow]")
            render_device_table(cloudex_devices)
            return cloudex_devices
        console.print("[yellow]⚠️  No multicast responses received from DevKits, unable to discover devices.[/yellow]")
        console.print("🔍 If you are sure the DevKit is online, try to connect to the serial console using 'sima-cli serial' command, login and type 'ifconfig' to find out its IP address.")
        return []

    devices = []
    for r in responses:
        ip = r.get("ip") or r.get("from")
        mac = r.get("mac", "-")
        board, version, model, full, fw = get_remote_board_info(ip)
        devices.append({
            "ip": ip,
            "mac": mac,
            "board": board,
            "version": version,
            "model": model,
            "full_image": full,
            "fwtype": fw,
            "connection": "local" if cloudex_devices else None,
        })
    devices = cloudex_devices + devices
    render_device_table(devices)
    return devices


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    discover_and_probe()
