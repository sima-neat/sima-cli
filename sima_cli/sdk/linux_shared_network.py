import ipaddress
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


def _find_executable(name: str) -> Optional[str]:
    candidates = [
        shutil.which(name),
        f"/usr/sbin/{name}",
        f"/sbin/{name}",
        f"/usr/bin/{name}",
        f"/bin/{name}",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_captured(command: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _is_wsl() -> bool:
    if os.getenv("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False


def _parse_route_iface_and_source(route_output: str) -> Tuple[str, str]:
    dev_match = re.search(r"(?:^|\s)dev\s+(\S+)", route_output or "")
    src_match = re.search(r"(?:^|\s)src\s+(\S+)", route_output or "")
    return (
        dev_match.group(1) if dev_match else "",
        src_match.group(1) if src_match else "",
    )


def _route_iface_and_source_for_target(target_ip: str) -> Tuple[str, str]:
    ip_cmd = _find_executable("ip")
    if not ip_cmd:
        return "", ""

    result = _run_captured([ip_cmd, "-o", "-4", "route", "get", target_ip])
    if result.returncode != 0:
        return "", ""
    return _parse_route_iface_and_source(result.stdout)


def _default_route_iface_and_source(family: str) -> Tuple[str, str]:
    ip_cmd = _find_executable("ip")
    if not ip_cmd:
        return "", ""

    result = _run_captured([ip_cmd, "-o", f"-{family}", "route", "show", "default"])
    if result.returncode != 0:
        return "", ""

    for line in result.stdout.splitlines():
        iface, src = _parse_route_iface_and_source(line)
        if iface:
            return iface, src
    return "", ""


def _iface_ipv4_network_for_target(iface: str, target_ip: str) -> str:
    ip_cmd = _find_executable("ip")
    if not ip_cmd:
        return ""

    try:
        target_addr = ipaddress.ip_address(target_ip)
    except ValueError:
        return ""

    result = _run_captured([ip_cmd, "-o", "-4", "addr", "show", "dev", iface])
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
            if not match:
                continue
            try:
                network = ipaddress.ip_interface(match.group(1)).network
            except ValueError:
                continue
            if target_addr in network:
                return str(network)

    try:
        return str(ipaddress.ip_network(f"{target_ip}/24", strict=False))
    except ValueError:
        return ""


def _iface_ipv6_networks(iface: str) -> List[str]:
    ip_cmd = _find_executable("ip")
    if not ip_cmd:
        return []

    result = _run_captured([ip_cmd, "-o", "-6", "addr", "show", "dev", iface, "scope", "global"])
    if result.returncode != 0:
        return []

    networks = []
    seen = set()
    for line in result.stdout.splitlines():
        match = re.search(r"\binet6\s+([0-9a-fA-F:]+/\d+)", line)
        if not match:
            continue
        try:
            interface = ipaddress.ip_interface(match.group(1))
        except ValueError:
            continue
        if interface.version != 6 or interface.ip.is_link_local or interface.ip.is_multicast:
            continue
        network = str(interface.network)
        if network not in seen:
            seen.add(network)
            networks.append(network)
    return networks


def _docker_bridge_name_from_network(network: dict) -> str:
    options = network.get("Options") or {}
    explicit_bridge = options.get("com.docker.network.bridge.name")
    if explicit_bridge:
        return explicit_bridge

    network_id = network.get("Id", "")
    return f"br-{network_id[:12]}" if len(network_id) >= 12 else ""


def _docker_bridge_network_details(network_name: str = "simasdkbridge") -> Tuple[str, str]:
    docker_cmd = _find_executable("docker") or "docker"
    result = _run_captured([docker_cmd, "network", "inspect", network_name])
    if result.returncode != 0:
        return "", ""

    try:
        networks = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "", ""
    if not networks:
        return "", ""

    network = networks[0]
    subnet = ""
    for config in (network.get("IPAM") or {}).get("Config") or []:
        candidate = config.get("Subnet", "")
        try:
            parsed = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            continue
        if parsed.version == 4:
            subnet = str(parsed)
            break

    return _docker_bridge_name_from_network(network), subnet


def _nft_rule_present(chain_output: str, bridge_iface: str, devkit_iface: str, docker_subnet: str, devkit_subnet: str) -> bool:
    normalized = (chain_output or "").replace('"', "")
    return all(
        fragment in normalized
        for fragment in (
            f"iifname {bridge_iface}",
            f"oifname {devkit_iface}",
            f"ip saddr {docker_subnet}",
            f"ip daddr {devkit_subnet}",
            "accept",
        )
    )


def _nm_shared_chain_blocks_iface(chain_output: str, devkit_iface: str) -> bool:
    normalized = (chain_output or "").replace('"', "")
    return f"oifname {devkit_iface}" in normalized and "reject" in normalized


def _nm_shared_forward_chain(devkit_iface: str, family: str = "ip") -> Tuple[str, str, str]:
    nft_cmd = _find_executable("nft")
    if not nft_cmd:
        return "", "", ""

    table = f"nm-shared-{devkit_iface}"
    list_cmd = ["sudo", nft_cmd, "list", "chain", family, table, "filter_forward"]
    chain_result = _run_captured(list_cmd)
    if chain_result.returncode != 0:
        return nft_cmd, table, ""

    return nft_cmd, table, chain_result.stdout or ""


def _is_nm_shared_devkit_connection(devkit_ip: str) -> bool:
    if platform.system().lower() != "linux" or not devkit_ip or _is_wsl():
        return False

    devkit_iface, _route_src = _route_iface_and_source_for_target(devkit_ip)
    if not devkit_iface:
        return False

    _nft_cmd, _table, chain_output = _nm_shared_forward_chain(devkit_iface)
    return _nm_shared_chain_blocks_iface(chain_output, devkit_iface)


def _configure_nm_shared_devkit_forwarding(devkit_ip: str, docker_network: str = "simasdkbridge") -> bool:
    if platform.system().lower() != "linux" or not devkit_ip or _is_wsl():
        return False

    devkit_iface, _route_src = _route_iface_and_source_for_target(devkit_ip)
    if not devkit_iface:
        return False

    bridge_iface, docker_subnet = _docker_bridge_network_details(docker_network)
    devkit_subnet = _iface_ipv4_network_for_target(devkit_iface, devkit_ip)
    nft_cmd, table, chain_output = _nm_shared_forward_chain(devkit_iface)
    if not (bridge_iface and docker_subnet and devkit_subnet and nft_cmd and chain_output):
        return False

    if _nft_rule_present(chain_output, bridge_iface, devkit_iface, docker_subnet, devkit_subnet):
        print(f"✅ NetworkManager shared connection already allows SDK bridge {bridge_iface} -> {devkit_iface}.")
        return True

    if not _nm_shared_chain_blocks_iface(chain_output, devkit_iface):
        return False

    insert_cmd = [
        "sudo",
        nft_cmd,
        "insert",
        "rule",
        "ip",
        table,
        "filter_forward",
        "iifname",
        bridge_iface,
        "oifname",
        devkit_iface,
        "ip",
        "saddr",
        docker_subnet,
        "ip",
        "daddr",
        devkit_subnet,
        "accept",
    ]
    insert_result = _run_captured(insert_cmd)
    if insert_result.returncode != 0:
        raise RuntimeError(
            "Failed to allow the SDK Docker bridge through NetworkManager shared networking.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in insert_cmd),
                (insert_result.stderr or insert_result.stdout or "").strip(),
            )
        )

    print(
        "✅ NetworkManager shared connection updated: allowed {} ({}) -> {} ({}) for DevKit {}.".format(
            bridge_iface,
            docker_subnet,
            devkit_iface,
            devkit_subnet,
            devkit_ip,
        )
    )
    return True


def _ensure_iptables_rule(iptables_cmd: str, check_args: List[str], insert_args: List[str]) -> bool:
    check_result = _run_captured(["sudo", iptables_cmd, *check_args])
    if check_result.returncode == 0:
        return False

    insert_cmd = ["sudo", iptables_cmd, *insert_args]
    insert_result = _run_captured(insert_cmd)
    if insert_result.returncode != 0:
        raise RuntimeError(
            "Failed to configure DevKit internet forwarding.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in insert_cmd),
                (insert_result.stderr or insert_result.stdout or "").strip(),
            )
        )
    return True


def _configure_nm_shared_devkit_internet(devkit_ip: str) -> bool:
    if platform.system().lower() != "linux" or not devkit_ip or _is_wsl():
        return False

    devkit_iface, _route_src = _route_iface_and_source_for_target(devkit_ip)
    if not devkit_iface:
        return False

    _nft_cmd, _table, chain_output = _nm_shared_forward_chain(devkit_iface)
    if not _nm_shared_chain_blocks_iface(chain_output, devkit_iface):
        return False

    internet_iface, _internet_src = _route_iface_and_source_for_target("8.8.8.8")
    if not internet_iface:
        internet_iface, _internet_src = _route_iface_and_source_for_target("1.1.1.1")
    devkit_subnet = _iface_ipv4_network_for_target(devkit_iface, devkit_ip)
    iptables_cmd = _find_executable("iptables")
    if not (internet_iface and devkit_subnet and iptables_cmd):
        return False

    if internet_iface == devkit_iface:
        print(f"ℹ️  Skipping DevKit internet forwarding: DevKit and default IPv4 route both use {devkit_iface}.")
        return False

    sysctl_cmd = _find_executable("sysctl") or "sysctl"
    sysctl_result = _run_captured(["sudo", sysctl_cmd, "-w", "net.ipv4.ip_forward=1"])
    if sysctl_result.returncode != 0:
        raise RuntimeError(
            "Failed to enable IPv4 forwarding for DevKit shared networking.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in ["sudo", sysctl_cmd, "-w", "net.ipv4.ip_forward=1"]),
                (sysctl_result.stderr or sysctl_result.stdout or "").strip(),
            )
        )

    inserted = False
    inserted |= _ensure_iptables_rule(
        iptables_cmd,
        ["-C", "FORWARD", "-i", devkit_iface, "-o", internet_iface, "-s", devkit_subnet, "-j", "ACCEPT"],
        ["-I", "FORWARD", "1", "-i", devkit_iface, "-o", internet_iface, "-s", devkit_subnet, "-j", "ACCEPT"],
    )
    inserted |= _ensure_iptables_rule(
        iptables_cmd,
        [
            "-C", "FORWARD", "-i", internet_iface, "-o", devkit_iface, "-d", devkit_subnet,
            "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT",
        ],
        [
            "-I", "FORWARD", "2", "-i", internet_iface, "-o", devkit_iface, "-d", devkit_subnet,
            "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT",
        ],
    )
    inserted |= _ensure_iptables_rule(
        iptables_cmd,
        ["-t", "nat", "-C", "POSTROUTING", "-s", devkit_subnet, "-o", internet_iface, "-j", "MASQUERADE"],
        ["-t", "nat", "-I", "POSTROUTING", "1", "-s", devkit_subnet, "-o", internet_iface, "-j", "MASQUERADE"],
    )

    status = "configured" if inserted else "already configured"
    print(f"✅ DevKit internet forwarding {status}: {devkit_iface} ({devkit_subnet}) -> {internet_iface}.")
    return True


def _ensure_ip6tables_rule(ip6tables_cmd: str, check_args: List[str], insert_args: List[str]) -> bool:
    check_result = _run_captured(["sudo", ip6tables_cmd, *check_args])
    if check_result.returncode == 0:
        return False

    insert_cmd = ["sudo", ip6tables_cmd, *insert_args]
    insert_result = _run_captured(insert_cmd)
    if insert_result.returncode != 0:
        print(
            "⚠️  Could not configure IPv6 DevKit internet forwarding.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in insert_cmd),
                (insert_result.stderr or insert_result.stdout or "").strip(),
            )
        )
        return False
    return True


def _nft6_rule_present(chain_output: str, src_iface: str, dst_iface: str, subnet: str, direction: str) -> bool:
    normalized = (chain_output or "").replace('"', "")
    address_fragment = f"ip6 {direction} {subnet}"
    return all(
        fragment in normalized
        for fragment in (
            f"iifname {src_iface}",
            f"oifname {dst_iface}",
            address_fragment,
            "accept",
        )
    )


def _insert_nm_shared_ip6_allow(
    nft_cmd: str,
    table: str,
    src_iface: str,
    dst_iface: str,
    subnet: str,
    direction: str,
    established_only: bool = False,
) -> bool:
    insert_cmd = [
        "sudo", nft_cmd, "insert", "rule", "ip6", table, "filter_forward",
        "iifname", src_iface, "oifname", dst_iface, "ip6", direction, subnet,
    ]
    if established_only:
        insert_cmd.extend(["ct", "state", "related,established"])
    insert_cmd.append("accept")

    insert_result = _run_captured(insert_cmd)
    if insert_result.returncode != 0:
        print(
            "⚠️  Could not update NetworkManager IPv6 shared forwarding.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in insert_cmd),
                (insert_result.stderr or insert_result.stdout or "").strip(),
            )
        )
        return False
    return True


def _configure_nm_shared_devkit_ipv6_internet(devkit_ip: str) -> bool:
    if platform.system().lower() != "linux" or not devkit_ip or _is_wsl():
        return False

    devkit_iface, _route_src = _route_iface_and_source_for_target(devkit_ip)
    if not devkit_iface or not _is_nm_shared_devkit_connection(devkit_ip):
        return False

    internet_iface, _internet_src = _default_route_iface_and_source("6")
    devkit_subnets = _iface_ipv6_networks(devkit_iface)
    ip6tables_cmd = _find_executable("ip6tables")
    if not (internet_iface and devkit_subnets and ip6tables_cmd):
        return False

    if internet_iface == devkit_iface:
        return False

    sysctl_cmd = _find_executable("sysctl") or "sysctl"
    sysctl_result = _run_captured(["sudo", sysctl_cmd, "-w", "net.ipv6.conf.all.forwarding=1"])
    if sysctl_result.returncode != 0:
        print(
            "⚠️  Could not enable IPv6 forwarding for DevKit shared networking.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in ["sudo", sysctl_cmd, "-w", "net.ipv6.conf.all.forwarding=1"]),
                (sysctl_result.stderr or sysctl_result.stdout or "").strip(),
            )
        )
        return False

    nft_cmd, table, ip6_chain_output = _nm_shared_forward_chain(devkit_iface, family="ip6")
    inserted = False
    for devkit_subnet in devkit_subnets:
        if ip6_chain_output and _nm_shared_chain_blocks_iface(ip6_chain_output, devkit_iface):
            if not _nft6_rule_present(ip6_chain_output, devkit_iface, internet_iface, devkit_subnet, "saddr"):
                inserted |= _insert_nm_shared_ip6_allow(nft_cmd, table, devkit_iface, internet_iface, devkit_subnet, "saddr")
            if not _nft6_rule_present(ip6_chain_output, internet_iface, devkit_iface, devkit_subnet, "daddr"):
                inserted |= _insert_nm_shared_ip6_allow(
                    nft_cmd,
                    table,
                    internet_iface,
                    devkit_iface,
                    devkit_subnet,
                    "daddr",
                    established_only=True,
                )

        inserted |= _ensure_ip6tables_rule(
            ip6tables_cmd,
            ["-C", "FORWARD", "-i", devkit_iface, "-o", internet_iface, "-s", devkit_subnet, "-j", "ACCEPT"],
            ["-I", "FORWARD", "1", "-i", devkit_iface, "-o", internet_iface, "-s", devkit_subnet, "-j", "ACCEPT"],
        )
        inserted |= _ensure_ip6tables_rule(
            ip6tables_cmd,
            [
                "-C", "FORWARD", "-i", internet_iface, "-o", devkit_iface, "-d", devkit_subnet,
                "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT",
            ],
            [
                "-I", "FORWARD", "2", "-i", internet_iface, "-o", devkit_iface, "-d", devkit_subnet,
                "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT",
            ],
        )
        inserted |= _ensure_ip6tables_rule(
            ip6tables_cmd,
            ["-t", "nat", "-C", "POSTROUTING", "-s", devkit_subnet, "-o", internet_iface, "-j", "MASQUERADE"],
            ["-t", "nat", "-I", "POSTROUTING", "1", "-s", devkit_subnet, "-o", internet_iface, "-j", "MASQUERADE"],
        )

    status = "configured" if inserted else "already configured or not required"
    print(f"✅ DevKit IPv6 forwarding {status}: {devkit_iface} ({', '.join(devkit_subnets)}) -> {internet_iface}.")
    return True


def _nm_connection_name_for_iface(iface: str) -> str:
    nmcli_cmd = _find_executable("nmcli")
    if not nmcli_cmd:
        return ""

    result = _run_captured([nmcli_cmd, "-g", "GENERAL.CONNECTION", "device", "show", iface])
    if result.returncode != 0:
        return ""

    name = (result.stdout or "").strip().splitlines()[0].strip() if (result.stdout or "").strip() else ""
    if not name or name == "--":
        return ""
    return name


def _disable_nm_shared_devkit_ipv6(devkit_ip: str) -> bool:
    if platform.system().lower() != "linux" or not devkit_ip or _is_wsl():
        return False

    devkit_iface, _route_src = _route_iface_and_source_for_target(devkit_ip)
    if not devkit_iface or not _is_nm_shared_devkit_connection(devkit_ip):
        return False

    nmcli_cmd = _find_executable("nmcli")
    connection_name = _nm_connection_name_for_iface(devkit_iface)
    if not (nmcli_cmd and connection_name):
        return False

    modify_result = _run_captured([
        "sudo",
        nmcli_cmd,
        "connection",
        "modify",
        connection_name,
        "ipv6.method",
        "ignore",
    ])
    if modify_result.returncode != 0:
        print(
            "⚠️  Could not disable IPv6 on the NetworkManager shared connection.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in [
                    "sudo",
                    nmcli_cmd,
                    "connection",
                    "modify",
                    connection_name,
                    "ipv6.method",
                    "ignore",
                ]),
                (modify_result.stderr or modify_result.stdout or "").strip(),
            )
        )
        return False

    reapply_result = _run_captured(["sudo", nmcli_cmd, "device", "reapply", devkit_iface])
    if reapply_result.returncode != 0:
        print(
            "⚠️  IPv6 was disabled in the shared connection profile, but NetworkManager could not reapply it automatically. "
            "Reconnect the DevKit cable or reactivate the shared connection if the DevKit still selects IPv6."
        )
    else:
        print(f"✅ Disabled IPv6 on NetworkManager shared connection '{connection_name}' so the DevKit uses the working IPv4 path.")
    return True


def configure_linux_shared_devkit_network(devkit_ip: str) -> None:
    _configure_nm_shared_devkit_forwarding(devkit_ip)
    _configure_nm_shared_devkit_internet(devkit_ip)
    if not _configure_nm_shared_devkit_ipv6_internet(devkit_ip):
        _disable_nm_shared_devkit_ipv6(devkit_ip)
