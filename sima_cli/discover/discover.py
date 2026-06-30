#!/usr/bin/env python3
import socket
import platform
import subprocess
import json
import time
import re
import psutil
from typing import List, Dict
from rich.console import Console
from rich.table import Table
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
def discover_multicast():
    """Cross-platform multicast discovery over all physical interfaces (no netifaces)."""
    console.print("[cyan]📡 Discovering nearby SiMa.ai DevKits via multicast...[/cyan]")

    # 1️⃣ Collect IPv4 interfaces
    iface_candidates = []
    if_stats = psutil.net_if_stats()

    for iface, addrs in psutil.net_if_addrs().items():
        # Skip interfaces that are down or missing stats info
        stats = if_stats.get(iface)
        if not stats or not stats.isup:
            continue

        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                name = iface.lower()
                if name.startswith(("en", "eth", "lan", "ethernet")):
                    iface_candidates.append((iface, addr.address))

    if not iface_candidates:
        console.print("[yellow]⚠️  No active physical interfaces found.[/yellow]")
        return []

    responses, seen_ips = [], set()

    # 2️⃣ Send multicast probe on each candidate interface
    for iface, iface_ip in iface_candidates:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(TIMEOUT)

            try:
                sock.bind((iface_ip, SRC_PORT))
            except OSError:
                # Some systems require binding to 0.0.0.0 for multicast
                sock.bind(("0.0.0.0", SRC_PORT))  # lgtm[py/bind-socket-all-network-interfaces]

            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

            console.print(f"📤 Sending DISCOVER from [bold]{iface}[/bold] ({iface_ip})")
            sock.sendto(DISCOVERY_MSG, (MCAST_GRP, MCAST_PORT))

            start = time.time()
            while time.time() - start < TIMEOUT:
                try:
                    data, addr = sock.recvfrom(2048)
                    msg = json.loads(data.decode(errors="ignore"))
                    ip = addr[0]
                    if ip not in seen_ips:
                        msg["from"] = ip
                        msg["iface"] = iface
                        responses.append(msg)
                        seen_ips.add(ip)
                except socket.timeout:
                    break
                except Exception:
                    break

            sock.close()
        except Exception as e:
            console.print(f"[red]❌ Error on {iface}: {e}[/red]")
            continue

    return responses

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
        key = dev.get("mac") or dev.get("ip")
        if key and key not in seen:
            seen.add(key)
            unique_devices.append(dev)
        else:
            # Skip duplicate silently
            continue

    if not unique_devices:
        console.print("[yellow]⚠️  No unique SiMa devices found after filtering duplicates.[/yellow]")
        return

    table = Table(title="SiMa Devices on the local network")
    table.add_column("IP", justify="center")
    table.add_column("MAC", justify="center")
    table.add_column("Board Type", justify="center")
    table.add_column("Build Version", justify="center")
    table.add_column("DevKit Model", justify="center")
    table.add_column("Full Image", justify="center")
    table.add_column("FW Type", justify="center")

    for dev in unique_devices:
        table.add_row(
            dev.get("ip", "-"),
            dev.get("mac", "-"),
            dev.get("board", "-"),
            dev.get("version", "-"),
            dev.get("model", "-"),
            "✅" if dev.get("full_image") else "❌",
            dev.get("fwtype", "-"),
        )

    console.print(table)

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
    maybe_run_pcie_throughput_test()

# ─────────────────────────────────────────────────────────────
# Unified Discovery Orchestration
# ─────────────────────────────────────────────────────────────
def discover_and_probe(mdns_only: bool = False) -> List[Dict[str, str]]:
    # Check if there's any interface without IP (Ubuntu quirk), if so, fix it by setting to linklocal
    suggest_and_switch_to_linklocal()

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
                })
            render_device_table(enriched)
            return enriched

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
        })
    render_device_table(devices)
    return devices


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    discover_and_probe()
