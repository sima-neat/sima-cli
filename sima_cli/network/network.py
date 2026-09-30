import subprocess
import os
import re
import time
import ipaddress
import tempfile
from sima_cli.utils.env import is_sima_board
from sima_cli.utils.env import get_sima_board_type, is_devkit_running_elxr, get_sima_build_version

IP_CMD = "/sbin/ip"
NETWORKD_DIR = "/etc/systemd/network"
NETWORKD_RUNTIME_DIR = "/run/systemd/network"


def parse_static_address(value: str, default_prefix: int = 24) -> str:
    """Validate an IPv4 host address using the supplied default prefix."""
    value = value.strip()
    address = ipaddress.IPv4Interface(value if "/" in value else f"{value}/{default_prefix}")
    ip = address.ip
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved:
        raise ValueError("Enter a unicast IPv4 address for the interface.")
    if address.network.prefixlen < 31 and ip in (
        address.network.network_address, address.network.broadcast_address
    ):
        raise ValueError("Enter a host address, not the subnet or broadcast address.")
    return str(address)


def _custom_network_file(iface: str, persistent: bool = False) -> str:
    directory = NETWORKD_DIR if persistent else NETWORKD_RUNTIME_DIR
    return os.path.join(directory, f"01-{iface}-sima-custom-static.network")


def _remove_custom_network_file(iface: str, persistent: bool = False):
    path = _custom_network_file(iface, persistent)
    if os.path.exists(path):
        subprocess.run(["sudo", "rm", "--", path], check=True)


def _nm_custom_exists(iface: str) -> bool:
    result = subprocess.run(
        ["nmcli", "connection", "show", f"{iface}-sima-custom-static"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def _default_static_template(iface: str, backend: str):
    if backend == "nm":
        def read_setting(setting):
            return subprocess.check_output(
                ["nmcli", "-g", setting, "connection", "show", f"{iface}-static"],
                text=True,
            ).strip()
        addresses = read_setting("ipv4.addresses")
        first = re.split(r"[,\s]+", addresses)[0]
        prefix = ipaddress.IPv4Interface(first).network.prefixlen
        return {"prefix": prefix, "gateway": read_setting("ipv4.gateway")}

    pattern = re.compile(r"\d+-" + re.escape(iface) + r"-static\.network$")
    paths = sorted(name for name in os.listdir(NETWORKD_DIR) if pattern.fullmatch(name))
    if not paths:
        raise ValueError(f"No default static configuration found for {iface}")
    with open(os.path.join(NETWORKD_DIR, paths[0])) as source:
        content = source.read()
    for match in re.finditer(r"(?m)^\s*Address\s*=\s*([^\s#;]+)", content):
        address = ipaddress.ip_interface(match.group(1))
        if address.version == 4:
            return {"prefix": address.network.prefixlen, "content": content}
    raise ValueError("Default static configuration has no IPv4 address")


def _gateway_for_address(gateway: str, address: str) -> str:
    """Keep a compatible gateway; let the user replace or omit an incompatible one."""
    if not gateway:
        return ""
    interface = ipaddress.IPv4Interface(address)

    def compatible(value):
        try:
            candidate = ipaddress.IPv4Address(value)
            parse_static_address(f"{candidate}/{interface.network.prefixlen}")
            return candidate in interface.network and candidate != interface.ip
        except ValueError:
            return False

    if compatible(gateway):
        return gateway
    from InquirerPy import inquirer
    print(f"ℹ️ Default gateway {gateway} is not usable with {address}.")
    while True:
        value = inquirer.text(
            message="Enter a gateway in the new subnet (blank for no gateway):",
        ).execute()
        if value is None:
            raise KeyboardInterrupt
        value = value.strip()
        if not value or compatible(value):
            return value
        print("❌ Enter a usable gateway in the new subnet, different from the device IP.")


def _custom_networkd_content(content: str, address: str) -> str:
    # Preserve repeated sections and settings (ConfigParser would lose them).
    blocks = re.split(r"(?m)(?=^\s*\[[^]\n]+\]\s*$)", content)
    output = []
    replaced = False
    for block in blocks:
        section_match = re.match(r"\s*\[([^]]+)\]", block)
        section = section_match.group(1) if section_match else ""
        lines = []
        drop_block = False
        for line in block.splitlines(keepends=True):
            setting = re.match(r"\s*([A-Za-z]+)\s*=\s*(.*?)\s*$", line)
            if not setting:
                lines.append(line)
                continue
            key, value = setting.groups()
            if section == "Match" and key == "KernelCommandLine" and value == "!netcfg=dhcp":
                continue
            if section in ("Network", "Address") and key == "Address" and value and ipaddress.ip_interface(value).version == 4:
                if not replaced:
                    lines.append(f"Address={address}\n")
                    replaced = True
                elif section == "Address":
                    drop_block = True
            elif section in ("Network", "Route") and key == "Gateway" and value and ipaddress.ip_address(value).version == 4:
                gateway = _gateway_for_address(value, address)
                if gateway:
                    lines.append(f"Gateway={gateway}\n")
                elif section == "Route":
                    drop_block = True
            else:
                lines.append(line)
        if not drop_block:
            output.extend(lines)
    if not replaced:
        raise ValueError("Default static configuration has no IPv4 address to replace")
    return "".join(output)


def apply_custom_static_ip(iface: str, value: str) -> bool:
    """Clone the default static settings without changing boot configuration."""
    try:
        # Validate syntax before reading profiles. Host/subnet validation follows
        # once the default profile's prefix is known.
        parse_static_address(value, default_prefix=32)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", iface):
            raise ValueError("Invalid interface name")
        backend = _select_network_backend()
        template = _default_static_template(iface, backend)
        address = parse_static_address(value, template["prefix"])
        if backend == "nm":
            gateway = _gateway_for_address(template["gateway"], address)
            name = f"{iface}-sima-custom-static"
            # Clone afresh so repeated use follows the current default settings.
            # The temporary clone cannot replace the boot profile, even if a
            # later command fails before autoconnect has been disabled.
            if _nm_custom_exists(iface):
                subprocess.run(["sudo", "nmcli", "connection", "delete", name], check=True)
            subprocess.run(
                ["sudo", "nmcli", "connection", "clone", "--temporary", f"{iface}-static", name],
                check=True,
            )
            try:
                subprocess.run([
                    "sudo", "nmcli", "connection", "modify", "--temporary", name,
                    "connection.autoconnect", "no", "connection.autoconnect-priority", "0",
                    "ipv4.method", "manual", "ipv4.addresses", address, "ipv4.gateway", gateway,
                ], check=True)
                subprocess.run(["sudo", "nmcli", "connection", "up", name], check=True)
            except (OSError, subprocess.CalledProcessError):
                subprocess.run(["sudo", "nmcli", "connection", "delete", name], check=False)
                raise
        else:
            content = _custom_networkd_content(template["content"], address)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".network") as temp:
                temp.write(content)
                temp.flush()
                subprocess.run(
                    ["sudo", "install", "-D", "-m", "644", temp.name, _custom_network_file(iface)],
                    check=True,
                )
            _remove_custom_network_file(iface, persistent=True)
            subprocess.run(["sudo", "systemctl", "restart", "systemd-networkd"], check=True)
        print(f"✅ Custom static address {address} configured on {iface}.")
        return True
    except (KeyboardInterrupt, EOFError):
        print("Cancelled custom static configuration.")
        return False
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"❌ Unable to configure custom static IP: {exc}")
        return False


def prompt_custom_static_ip(iface: str):
    from InquirerPy import inquirer

    while True:
        try:
            value = inquirer.text(
                message="Enter static IPv4 address (optional /prefix; otherwise use default static prefix; blank to cancel):",
            ).execute()
            if not value or not value.strip():
                return
            parse_static_address(value, default_prefix=32)
        except ValueError as exc:
            print(f"❌ Invalid address: {exc}")
            continue
        except (KeyboardInterrupt, EOFError):
            return
        print("ℹ️ Changing the IP may disconnect SSH. Reconnect using the new address.")
        apply_custom_static_ip(iface, value)
        return

def extract_interface_index(name):
    """Extract numeric index from interface name for sorting (e.g., end0 → 0)."""
    match = re.search(r'(\d+)$', name)
    return int(match.group(1)) if match else float('inf')

def get_interfaces():
    interfaces = []
    ip_output = subprocess.check_output([IP_CMD, '-o', 'link', 'show']).decode()
    for line in ip_output.splitlines():
        match = re.match(r'\d+: (\w+):', line)
        if match:
            iface = match.group(1)
            if iface.startswith('lo'):
                continue
            try:
                with open(f"/sys/class/net/{iface}/carrier") as f:
                    carrier = f.read().strip() == "1"
            except FileNotFoundError:
                carrier = False

            try:
                ip_addr = subprocess.check_output([IP_CMD, '-4', 'addr', 'show', iface]).decode()
                ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_addr)
                ip = ip_match.group(1) if ip_match else "IP Not Assigned"
            except subprocess.CalledProcessError:
                ip = "IP Not Assigned"

            # Check internet connectivity only if carrier is up
            internet = False
            if carrier:
                try:
                    result = subprocess.run(
                        ["ping", "-I", iface, "-c", "1", "-W", "1", "8.8.8.8"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    internet = result.returncode == 0
                except Exception:
                    internet = False

            interfaces.append({
                "name": iface,
                "carrier": carrier,
                "ip": ip,
                "internet": internet
            })

    interfaces.sort(key=lambda x: extract_interface_index(x["name"]))
    return interfaces

def move_network_file(iface, mode):
    try:
        networkd_dir = NETWORKD_DIR
        files = os.listdir(networkd_dir)

        # Match any static file for this iface
        pattern = re.compile(r"(\d+)-(%s)-static\.network" % re.escape(iface))
        static_file = next((f for f in files if pattern.match(f)), None)
        if not static_file:
            print(f"⚠️ No static .network file found for {iface}")
            return

        src = os.path.join(networkd_dir, static_file)
        desired_prefix = "02" if mode == "static" else "20"
        dst_file = f"{desired_prefix}-{iface}-static.network"
        dst = os.path.join(networkd_dir, dst_file)

        if static_file == dst_file:
            print(f"✅ Using existing {mode.upper()} configuration for {iface}.")
        else:
            print(f"🔧 Changing mode of {iface} to {mode.upper()}...")
            subprocess.run(["sudo", "mv", src, dst], check=True)

        # Modify content only if going to static
        if mode == "static":
            # Read as normal user
            with open(dst, "r") as f:
                lines = f.readlines()
            cleaned = [line for line in lines if "KernelCommandLine=!netcfg=dhcp" not in line]

            # Only write if change is needed
            if len(cleaned) != len(lines):
                temp_path = f"/tmp/{iface}-static.network"
                with open(temp_path, "w") as tmpf:
                    tmpf.writelines(cleaned)
                subprocess.run(["sudo", "cp", temp_path, dst], check=True)
                os.remove(temp_path)
                print(f"✂️ Removed KernelCommandLine override from {dst_file}")
            else:
                print(f"✅ No KernelCommandLine override found — file already clean.")

        # Remove our higher-priority custom profile when returning to a built-in mode.
        _remove_custom_network_file(iface)
        _remove_custom_network_file(iface, persistent=True)
        # Restart networkd
        subprocess.run(["sudo", "systemctl", "restart", "systemd-networkd"], check=True)
        time.sleep(2)
    except Exception as e:
        print(f"❌ Unable to change configuration, error: {e}")


def _parse_semver(version: str):
    match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$", version or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _is_version_at_least(version: str, minimum: str) -> bool:
    left = _parse_semver(version)
    right = _parse_semver(minimum)
    if not left or not right:
        return False
    return left >= right


def _is_service_enabled(service_name: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "enabled"
    except Exception:
        return False


def _nm_connection_up(iface: str, mode: str) -> bool:
    conn_name = f"{iface}-{mode}"
    try:
        subprocess.run(["sudo", "nmcli", "connection", "up", conn_name], check=True)
        if _nm_custom_exists(iface):
            subprocess.run(
                ["sudo", "nmcli", "connection", "delete", f"{iface}-sima-custom-static"],
                check=True,
            )
        print(f"✅ NetworkManager profile brought up: {conn_name}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to activate NetworkManager profile '{conn_name}': {e}")
        return False


def _select_network_backend():
    """
    Returns:
      - "nm": use NetworkManager profiles
      - "networkd": use legacy systemd-networkd file switching
    """
    # Buildinfo-driven compatibility gate:
    # eLxr Modalix 2.1+ transitioned away from networkd to NetworkManager.
    board_type = get_sima_board_type().strip().lower()
    is_modalix_elxr = board_type == "modalix" and is_devkit_running_elxr()
    core_version, _ = get_sima_build_version()
    is_elxr_21_or_above = _is_version_at_least(core_version or "", "2.1.0")

    networkd_enabled = _is_service_enabled("systemd-networkd")
    nm_enabled = _is_service_enabled("NetworkManager")

    if is_modalix_elxr:
        print(
            "ℹ️  Modalix eLxr detected "
            f"(version={core_version or 'unknown'}, systemd-networkd={'enabled' if networkd_enabled else 'disabled'}, "
            f"NetworkManager={'enabled' if nm_enabled else 'disabled'})."
        )

    # Primary path for 2.1+ style images where only NM is enabled.
    if is_modalix_elxr and nm_enabled and not networkd_enabled:
        return "nm"

    # Backward compatibility: pre-2.1 keeps networkd flow when enabled.
    if is_modalix_elxr and networkd_enabled and not is_elxr_21_or_above:
        return "networkd"

    # Fallback for future image transitions where NetworkManager is authoritative.
    if nm_enabled and not networkd_enabled:
        return "nm"

    # Conservative default: preserve legacy behavior unless NM-only state is explicit.
    return "networkd"


def apply_network_mode(iface: str, mode: str):
    backend = _select_network_backend()
    if backend == "nm":
        # NM flow activates pre-defined profiles like end0-dhcp / end0-static.
        ok = _nm_connection_up(iface, mode)
        if ok and mode == "dhcp":
            populate_resolv_conf()
        return

    # Legacy flow for older releases: manipulate networkd config files.
    move_network_file(iface, mode)
    if mode == "dhcp":
        populate_resolv_conf()

def get_gateway_for_interface(ip):
    """Guess the gateway from the IP address, assuming .1 is the router."""
    if ip == "IP Not Assigned":
        return None
    parts = ip.split('.')
    parts[-1] = "1"
    return ".".join(parts)

def populate_resolv_conf(dns_server="8.8.8.8"):
    """
    Use sudo to write a DNS entry into /etc/resolv.conf even if not running as root.
    """
    content = f"nameserver {dns_server}\n"

    try:
        # Write using echo and sudo tee
        cmd = f"echo '{content.strip()}' | sudo tee /etc/resolv.conf > /dev/null"
        result = subprocess.run(cmd, shell=True, check=True)
        print(f"✅ /etc/resolv.conf updated with nameserver {dns_server}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to update /etc/resolv.conf: {e}")

def set_default_route(iface, ip):
    gateway = get_gateway_for_interface(ip)
    if not gateway:
        print(f"❌ Cannot set default route — IP not assigned for {iface}")
        return

    print(f"🔧 Setting default route via {iface} ({gateway})")

    try:
        # Delete all existing default routes
        subprocess.run(["sudo", "/sbin/ip", "route", "del", "default"], check=False)

        # Add new default route for this iface
        subprocess.run(
            ["sudo", "/sbin/ip", "route", "add", "default", "via", gateway, "dev", iface],
            check=True
        )
        print(f"✅ Default route set via {iface} ({gateway})")
        
    except subprocess.CalledProcessError:
        print(f"❌ Failed to set default route via {iface}")

def network_menu():
    if not is_sima_board():
        print("❌ This command only runs on the DevKit")
        return

    from InquirerPy import inquirer

    print("✅ Scanning network configuration, please wait...")
    
    while True:
        interfaces = get_interfaces()
        choices = ["🚪 Quit Menu"]
        iface_map = {}

        for iface in interfaces:
            status_icon = "carrier (✅)" if iface["carrier"] else "carrier (❌)"
            internet_icon = "internet (🌐)" if iface.get("internet") else "internet (🚫)"
            label = f"{iface['name']:<10} {status_icon} {internet_icon}  {iface['ip']:<20}"
            choices.append(label)
            iface_map[label] = iface

        try:
            
            iface_choice = inquirer.fuzzy(
                message="Select Ethernet Interface:",
                choices=choices,
                instruction="(Type or use ↑↓)",
            ).execute()
        except KeyboardInterrupt:
            print("\nExiting.")
            break

        if iface_choice is None or iface_choice == "🚪 Quit Menu":
            print("Exiting.")
            break

        selected_iface = iface_map[iface_choice]

        try:
            second = inquirer.select(
                message=f"Configure {selected_iface['name']}:",
                choices=[
                    "Set to DHCP",
                    "Set to Default Static IP",
                    "Set to Custom Static IP",
                    "Set as Default Route",
                    "Back to Interface Selection"
                ]
            ).execute()
        except KeyboardInterrupt:
            print("\nExiting.")
            break

        if second == "Set to DHCP":
            apply_network_mode(selected_iface["name"], "dhcp")
        elif second == "Set to Default Static IP":
            apply_network_mode(selected_iface["name"], "static")
        elif second == "Set to Custom Static IP":
            prompt_custom_static_ip(selected_iface["name"])
        elif second == "Set as Default Route":
            set_default_route(selected_iface["name"], selected_iface["ip"])            
        else:
            continue 

if __name__ == '__main__':
    network_menu()
