"""
Linux Link Local Helper : Ubuntu doesn't default to APIPA so it will not work out of the box unless user do:

1. Set the interface to LinkLocal only or Shared Network
2. Run device discover which will scan interfaces that has no network configuration, it will prompt user to turn on link local configuration

This is only for Linux host (Ubuntu), as on mac and Windows the fallback to APIPA is default behavior.
"""
import os
import re
import time
import shlex
import subprocess
from sima_cli.utils.env import get_environment_type
from typing import Optional, Dict

def _run(cmd: str, check=False, capture=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        shlex.split(cmd),
        check=check,
        text=True,
        capture_output=capture,
        env={**os.environ, "LC_ALL":"C", "LANG":"C"}
    )

def _has_nmcli() -> bool:
    return _run("which nmcli").returncode == 0


def _iface_has_ipv4(iface: str) -> bool:
    cp = _run(f"ip -4 addr show {iface}")
    return " inet " in cp.stdout

def _get_mac(iface: str) -> str:
    try:
        with open(f"/sys/class/net/{iface}/address", "r") as f:
            return f.read().strip().lower()
    except Exception:
        return ""

def _is_usb_ethernet(iface: str) -> bool:
    """Return True if this interface is a USB Ethernet device."""
    dev_path = f"/sys/class/net/{iface}/device"
    if not os.path.exists(dev_path):
        return False

    cp = _run(f"udevadm info -q property -p {dev_path}", capture=True)
    out = cp.stdout.strip().splitlines()

    for line in out:
        if any(key in line for key in ["ID_BUS=usb", "SUBSYSTEM=usb"]):
            return True
        if "DEVPATH=" in line and "/usb" in line:
            return True
        if "DRIVER=" in line and any(d in line for d in ["cdc_", "r815", "ax88179", "asix"]):
            return True
    return False


def _usb_attrs(iface: str) -> Dict[str, str]:
    """
    Try to extract USB vendor/model info from udev if this NIC is USB-based.
    Returns possibly empty dict.
    """
    dev_path = f"/sys/class/net/{iface}/device"
    if not os.path.exists(dev_path):
        return {}

    cp = _run(f"udevadm info -q property -p {dev_path}", capture=True)
    props = {}
    for line in cp.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

    # Confirm it's a USB interface
    if not (
        props.get("ID_BUS") == "usb"
        or props.get("SUBSYSTEM") == "usb"
        or "/usb" in props.get("DEVPATH", "")
    ):
        return {}

    # Build human-readable vendor/model info
    vendor = props.get("ID_VENDOR_FROM_DATABASE") or props.get("ID_VENDOR") or ""
    model = props.get("ID_MODEL_FROM_DATABASE") or props.get("ID_MODEL") or ""
    serial = props.get("ID_SERIAL_SHORT") or props.get("ID_SERIAL") or ""

    # Fallback for Realtek/etc. if vendor/model missing
    if not vendor and "Realtek" in props.get("ID_VENDOR_FROM_DATABASE", ""):
        vendor = "Realtek"
    if not model and "DRIVER" in props:
        model = props["DRIVER"]

    # Optional PRODUCT hint like "bda/8157/3000"
    if not model and "PRODUCT" in props:
        model = props["PRODUCT"].replace("/", ":")

    return {"vendor": vendor, "model": model, "serial": serial}


def _driver_info(iface: str) -> Dict[str, str]:
    cp = _run(f"ethtool -i {iface}", capture=True)
    info = {}
    for line in cp.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip().lower()] = v.strip()
    return {
        "driver": info.get("driver", ""),
        "bus-info": info.get("bus-info", ""),
        "firmware": info.get("firmware-version", ""),
    }

def _describe_iface(iface: str) -> str:
    mac = _get_mac(iface)
    drv = _driver_info(iface)
    usb = _usb_attrs(iface)
    bits = [f"iface={iface}", f"mac={mac or '-'}"]
    if drv["driver"]:
        bits.append(f"driver={drv['driver']}")
    if drv["bus-info"]:
        bits.append(f"bus={drv['bus-info']}")
    if usb:
        vendor = usb.get("vendor") or "USB-Vendor"
        model  = usb.get("model") or "USB-Model"
        bits.append(f"usb={vendor} {model}")
    return ", ".join(bits)

def _nm_find_connection_for_iface(iface: str) -> Optional[str]:
    # Format: NAME:UUID:TYPE:DEVICE
    cp = _run("nmcli -t -f NAME,UUID,TYPE,DEVICE connection show", capture=True)
    for line in cp.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[3] == iface:
            return parts[0]
    # If none is active on the iface, try MAC-bound profiles
    mac = _get_mac(iface)
    if mac:
        cp2 = _run("nmcli -t -f NAME connection show", capture=True)
        # Later we’ll try to set mac binding; for now return None and we’ll create one.
    return None

def _nm_ensure_connection_linklocal(iface: str) -> str:
    """
    Ensure a single persistent link-local connection exists for this interface.
    If USB Ethernet, name profile 'sima-linklocal-usbeth'.
    """
    mac = _get_mac(iface)
    usb_eth = _is_usb_ethernet(iface)
    target_name = "sima-link-usb-ethernet" if usb_eth else f"sima-link-{iface}"

    # List all existing connections
    cp = _run("nmcli -t -f NAME,UUID,DEVICE connection show", capture=True)
    existing = []
    for line in cp.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3:
            name, uuid, dev = parts[0], parts[1], parts[2]
            if name == target_name:
                existing.append(uuid)

    # Clean up duplicates
    if len(existing) > 1:
        print(f"🧹 Removing {len(existing)-1} duplicate profiles for {target_name}...")
        for uuid in existing[1:]:
            _run(f"nmcli connection delete uuid {uuid}", capture=True)

    # Create if missing
    if not existing:
        print(f"🆕 Creating new link-local profile {target_name}...")
        _run(f"nmcli connection add type ethernet ifname {iface} con-name {target_name}", check=True)
    else:
        print(f"♻️  Reusing existing profile {target_name}...")

    # Update its properties in-place
    _run(f"nmcli connection modify {target_name} ipv4.method link-local", check=True)
    _run(f"nmcli connection modify {target_name} ipv4.may-fail yes", check=False)
    _run(f"nmcli connection modify {target_name} ipv6.method ignore", check=False)
    _run(f"nmcli connection modify {target_name} connection.autoconnect yes", check=False)
    if mac:
        _run(f"nmcli connection modify {target_name} 802-3-ethernet.mac-address {mac}", check=False)

    return target_name


def make_linklocal_permanent(iface: str) -> bool:
    """
    1) Bring iface down
    2) Configure NM connection (permanent, MAC-bound) to IPv4 link-local
    3) Bring iface up and verify 169.254.x.x
    If NM is absent, fallback to a one-off ip addr add (non-persistent).
    """
    print(f"🔎 Selected device: {_describe_iface(iface)}")

    # Bring device down cleanly first
    if _has_nmcli():
        print("🔧 Bringing device down via NetworkManager...")
        _run(f"nmcli device disconnect {iface}", capture=True)
        _run(f"nmcli device set {iface} managed yes", capture=True)
        conn = _nm_ensure_connection_linklocal(iface)
        print(f"📝 Using NM connection: {conn}")
        print("🔧 Bringing device up...")
        # Try a clean activate
        _run(f"nmcli connection up {shlex.quote(conn)}", capture=True)
    else:
        print("ℹ️ NetworkManager not found. Applying non-persistent fallback.")
        _run(f"ip link set {iface} down", capture=True)
        _run(f"ip addr flush dev {iface}", capture=True)
        _run(f"ip addr add 169.254.10.10/16 dev {iface}", capture=True)
        _run(f"ip link set {iface} up", capture=True)

    # Verify
    for _ in range(6):
        time.sleep(2)
        if _iface_has_ipv4(iface):
            out = _run(f"ip -4 addr show {iface}").stdout
            m = re.search(r"inet\s+(169\.254\.\d+\.\d+)", out)
            if m:
                print(f"✅ {iface} now has link-local IPv4: {m.group(1)}")
                return True
    print("⚠️ Failed to acquire 169.254.x.x; check cable or try replugging the USB dongle.")
    return False

def suggest_and_switch_to_linklocal():
    # Detect UP interfaces with no IPv4
    envtype, env_subtype = get_environment_type()

    if envtype == 'host' and env_subtype == 'linux':
        cp = _run("ip -o link", capture=True)
        ifaces = []
        for line in cp.stdout.splitlines():
            m = re.match(r"\d+:\s+([^:]+):\s+<([^>]+)>", line)
            if not m:
                continue
            iface, flags = m.group(1), m.group(2)
            if iface.startswith(("lo", "docker", "veth", "br-", "virbr", "tun", "tap")):
                continue
            if "UP" in flags and not _iface_has_ipv4(iface):
                ifaces.append(iface)

        if not ifaces:
            print("👍 No unconfigured network interfaces detected, nothing to configure.")
            return

        print("\n⚠️  The following interfaces are UP but have no IPv4 address:")
        for i, iface in enumerate(ifaces, 1):
            usb = _is_usb_ethernet(iface)
            desc = _describe_iface(iface)
            prefix = "🧩 USB Ethernet" if usb else "💻 Onboard / PCIe NIC"
            print(f"  {i}. {prefix}: {desc}")

        ans = input("\nConfigure one of these for **permanent link-local (169.254.x.x)**? [y/N]: ").strip().lower()
        if ans != "y":
            return

        iface = ifaces[0] if len(ifaces) == 1 else None
        if len(ifaces) > 1 and not iface:
            sel = input(f"Choose interface [1-{len(ifaces)}]: ").strip()
            try:
                iface = ifaces[int(sel)-1]
            except Exception:
                print("❌ Invalid selection.")
                return

        usb = _is_usb_ethernet(iface)
        if usb:
            print(f"🔎 Detected USB Ethernet adapter — this is likely your DevKit connection.")
        else:
            print(f"🔎 Detected standard Ethernet interface.")

        ok = make_linklocal_permanent(iface)
        if ok:
            print(f"🎉 {iface} configured for permanent link-local (profile: "
                f"{'sima-link-usb-ethnet' if usb else f'sima-link-{iface}'}).")

