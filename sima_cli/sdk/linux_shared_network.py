import ipaddress
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


NM_SHARED_DISPATCHER_PATH = "/etc/NetworkManager/dispatcher.d/90-sima-sdk-shared-network"


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


def _nft_forward_rule_present(chain_output: str, src_iface: str, dst_iface: str, src_subnet: str = "", dst_subnet: str = "", established_only: bool = False) -> bool:
    normalized = (chain_output or "").replace('"', "")
    fragments = [
        f"iifname {src_iface}",
        f"oifname {dst_iface}",
        "accept",
    ]
    if src_subnet:
        fragments.append(f"ip saddr {src_subnet}")
    if dst_subnet:
        fragments.append(f"ip daddr {dst_subnet}")
    if established_only:
        fragments.append("established")
    return all(fragment in normalized for fragment in fragments)


def _nft_nat_rule_present(chain_output: str, docker_subnet: str, devkit_iface: str) -> bool:
    normalized = (chain_output or "").replace('"', "")
    return all(
        fragment in normalized
        for fragment in (
            f"ip saddr {docker_subnet}",
            f"oifname {devkit_iface}",
            "masquerade",
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


def _nm_shared_nat_chain(devkit_iface: str, family: str = "ip") -> Tuple[str, str, str]:
    nft_cmd = _find_executable("nft")
    if not nft_cmd:
        return "", "", ""

    table = f"nm-shared-{devkit_iface}"
    list_cmd = ["sudo", nft_cmd, "list", "chain", family, table, "nat_postrouting"]
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

    if not _nm_shared_chain_blocks_iface(chain_output, devkit_iface):
        return False

    inserted = False
    if not _nft_forward_rule_present(chain_output, bridge_iface, devkit_iface, docker_subnet, devkit_subnet):
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
        inserted = True

    if not _nft_forward_rule_present(chain_output, devkit_iface, bridge_iface, dst_subnet=docker_subnet, established_only=True):
        reverse_cmd = [
            "sudo",
            nft_cmd,
            "insert",
            "rule",
            "ip",
            table,
            "filter_forward",
            "iifname",
            devkit_iface,
            "oifname",
            bridge_iface,
            "ip",
            "daddr",
            docker_subnet,
            "ct",
            "state",
            "related,established",
            "accept",
        ]
        reverse_result = _run_captured(reverse_cmd)
        if reverse_result.returncode != 0:
            raise RuntimeError(
                "Failed to allow return traffic from the DevKit to the SDK Docker bridge.\n"
                "Command: {}\n"
                "Error: {}".format(
                    " ".join(shlex.quote(part) for part in reverse_cmd),
                    (reverse_result.stderr or reverse_result.stdout or "").strip(),
                )
            )
        inserted = True

    nat_configured = _configure_sdk_bridge_devkit_nat(bridge_iface, docker_subnet, devkit_iface)
    inserted = inserted or nat_configured

    print(
        "✅ NetworkManager shared connection {}: SDK bridge {} ({}) <-> {} ({}) for DevKit {}.".format(
            "updated" if inserted else "already allows traffic",
            bridge_iface,
            docker_subnet,
            devkit_iface,
            devkit_subnet,
            devkit_ip,
        )
    )
    return True


def _iptables_nat_rule_exists(iptables_cmd: str, check_args: List[str]) -> bool:
    return _run_captured(["sudo", iptables_cmd, *check_args]).returncode == 0


def _docker_default_masquerade_present(iptables_cmd: str, docker_subnet: str, bridge_iface: str) -> bool:
    result = _run_captured(["sudo", iptables_cmd, "-t", "nat", "-S", "POSTROUTING"])
    if result.returncode != 0:
        return False

    normalized = result.stdout or ""
    return (
        f"-s {docker_subnet}" in normalized
        and f"! -o {bridge_iface}" in normalized
        and "-j MASQUERADE" in normalized
    )


def _configure_sdk_bridge_devkit_nat(bridge_iface: str, docker_subnet: str, devkit_iface: str) -> bool:
    iptables_cmd = _find_executable("iptables")
    if iptables_cmd and _docker_default_masquerade_present(iptables_cmd, docker_subnet, bridge_iface):
        return False

    nft_cmd, table, nat_output = _nm_shared_nat_chain(devkit_iface)
    if nft_cmd and nat_output:
        if _nft_nat_rule_present(nat_output, docker_subnet, devkit_iface):
            return False
        insert_cmd = [
            "sudo",
            nft_cmd,
            "insert",
            "rule",
            "ip",
            table,
            "nat_postrouting",
            "ip",
            "saddr",
            docker_subnet,
            "oifname",
            devkit_iface,
            "masquerade",
        ]
        insert_result = _run_captured(insert_cmd)
        if insert_result.returncode != 0:
            raise RuntimeError(
                "Failed to configure SDK bridge NAT through NetworkManager shared networking.\n"
                "Command: {}\n"
                "Error: {}".format(
                    " ".join(shlex.quote(part) for part in insert_cmd),
                    (insert_result.stderr or insert_result.stdout or "").strip(),
                )
            )
        return True

    if not iptables_cmd:
        return False

    check_args = ["-t", "nat", "-C", "POSTROUTING", "-s", docker_subnet, "-o", devkit_iface, "-j", "MASQUERADE"]
    if _iptables_nat_rule_exists(iptables_cmd, check_args):
        return False

    insert_cmd = ["sudo", iptables_cmd, "-t", "nat", "-I", "POSTROUTING", "1", "-s", docker_subnet, "-o", devkit_iface, "-j", "MASQUERADE"]
    insert_result = _run_captured(insert_cmd)
    if insert_result.returncode != 0:
        raise RuntimeError(
            "Failed to configure SDK bridge NAT for DevKit shared networking.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in insert_cmd),
                (insert_result.stderr or insert_result.stdout or "").strip(),
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


def _nm_connection_ipv4_method(connection_name: str) -> str:
    nmcli_cmd = _find_executable("nmcli")
    if not (nmcli_cmd and connection_name):
        return ""

    result = _run_captured([nmcli_cmd, "-g", "ipv4.method", "connection", "show", connection_name])
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip().splitlines()[0].strip().lower() if (result.stdout or "").strip() else ""


def _iface_uses_nm_shared_ipv4(iface: str) -> bool:
    connection_name = _nm_connection_name_for_iface(iface)
    return bool(connection_name and _nm_connection_ipv4_method(connection_name) == "shared")


def _sudo_command(command: List[str], allow_prompt: bool = True) -> List[str]:
    return ["sudo", *command] if allow_prompt else ["sudo", "-n", *command]


def _iptables_chain_output(iptables_cmd: str, chain: str, allow_sudo_prompt: bool = True) -> str:
    result = _run_captured(_sudo_command([iptables_cmd, "-S", chain], allow_prompt=allow_sudo_prompt))
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _iptables_rule_tokens(line: str) -> List[str]:
    try:
        return shlex.split(line or "")
    except ValueError:
        return []


def _iptables_rule_jumps_to(tokens: List[str], target: str) -> bool:
    for index, token in enumerate(tokens[:-1]):
        if token == "-j" and tokens[index + 1] == target:
            return True
    return False


def _iptables_forward_jumps_nm_before_docker_user(forward_output: str, chain: str) -> bool:
    nm_index = -1
    docker_user_index = -1
    for index, line in enumerate((forward_output or "").splitlines()):
        tokens = _iptables_rule_tokens(line)
        if len(tokens) < 3 or tokens[0] != "-A" or tokens[1] != "FORWARD":
            continue
        if _iptables_rule_jumps_to(tokens, chain) and nm_index < 0:
            nm_index = index
        if _iptables_rule_jumps_to(tokens, "DOCKER-USER") and docker_user_index < 0:
            docker_user_index = index
    return nm_index >= 0 and docker_user_index >= 0 and nm_index < docker_user_index


def _iptables_nm_shared_chain_blocks_iface(chain_output: str, devkit_iface: str) -> bool:
    for line in (chain_output or "").splitlines():
        tokens = _iptables_rule_tokens(line)
        if len(tokens) < 3 or tokens[0] != "-A":
            continue
        has_output_iface = any(token == "-o" and tokens[index + 1] == devkit_iface for index, token in enumerate(tokens[:-1]))
        if has_output_iface and _iptables_rule_jumps_to(tokens, "REJECT"):
            return True
    return False


def _iptables_nm_shared_allow_rule_args(
    chain: str,
    bridge_iface: str,
    docker_subnet: str,
    devkit_iface: str,
    devkit_subnet: str,
) -> List[str]:
    args = ["-s", docker_subnet, "-d", devkit_subnet, "-o", devkit_iface, "-j", "ACCEPT"]
    if bridge_iface:
        args = ["-i", bridge_iface, *args]
    return [chain, *args]


def _iptables_nm_shared_allow_rule_present(
    iptables_cmd: str,
    chain: str,
    bridge_iface: str,
    docker_subnet: str,
    devkit_iface: str,
    devkit_subnet: str,
    allow_sudo_prompt: bool = True,
) -> bool:
    rule_args = _iptables_nm_shared_allow_rule_args(chain, bridge_iface, docker_subnet, devkit_iface, devkit_subnet)
    return _run_captured(_sudo_command([iptables_cmd, "-C", *rule_args], allow_prompt=allow_sudo_prompt)).returncode == 0


def _insert_iptables_nm_shared_allow_rule(
    iptables_cmd: str,
    chain: str,
    bridge_iface: str,
    docker_subnet: str,
    devkit_iface: str,
    devkit_subnet: str,
) -> bool:
    if _iptables_nm_shared_allow_rule_present(iptables_cmd, chain, bridge_iface, docker_subnet, devkit_iface, devkit_subnet):
        return False

    rule_args = _iptables_nm_shared_allow_rule_args(chain, bridge_iface, docker_subnet, devkit_iface, devkit_subnet)
    insert_cmd = ["sudo", iptables_cmd, "-I", rule_args[0], "1", *rule_args[1:]]
    insert_result = _run_captured(insert_cmd)
    if insert_result.returncode != 0:
        raise RuntimeError(
            "Failed to allow the SDK Docker bridge through NetworkManager shared-mode iptables forwarding.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in insert_cmd),
                (insert_result.stderr or insert_result.stdout or "").strip(),
            )
        )
    return True


def nm_shared_iptables_repair_status(
    devkit_ip: str,
    docker_network: str = "simasdkbridge",
    allow_sudo_prompt: bool = True,
) -> Dict[str, object]:
    status: Dict[str, object] = {
        "applicable": False,
        "rule_present": False,
        "dispatcher_installed": False,
        "devkit_iface": "",
        "devkit_subnet": "",
        "docker_subnet": "",
        "bridge_iface": "",
        "chain": "",
        "reason": "",
    }

    if platform.system().lower() != "linux" or not devkit_ip or _is_wsl():
        status["reason"] = "unsupported-host"
        return status

    devkit_iface, _route_src = _route_iface_and_source_for_target(devkit_ip)
    if not devkit_iface:
        status["reason"] = "route-unresolved"
        return status
    status["devkit_iface"] = devkit_iface

    if not _iface_uses_nm_shared_ipv4(devkit_iface):
        status["reason"] = "not-nm-shared"
        return status

    bridge_iface, docker_subnet = _docker_bridge_network_details(docker_network)
    devkit_subnet = _iface_ipv4_network_for_target(devkit_iface, devkit_ip)
    iptables_cmd = _find_executable("iptables")
    chain = f"nm-sh-fw-{devkit_iface}"
    status.update({
        "bridge_iface": bridge_iface,
        "docker_subnet": docker_subnet,
        "devkit_subnet": devkit_subnet,
        "chain": chain,
        "dispatcher_installed": Path(NM_SHARED_DISPATCHER_PATH).exists(),
    })
    if not (docker_subnet and devkit_subnet and iptables_cmd):
        status["reason"] = "missing-prerequisites"
        return status

    forward_output = _iptables_chain_output(iptables_cmd, "FORWARD", allow_sudo_prompt=allow_sudo_prompt)
    chain_output = _iptables_chain_output(iptables_cmd, chain, allow_sudo_prompt=allow_sudo_prompt)
    if not _iptables_forward_jumps_nm_before_docker_user(forward_output, chain):
        status["reason"] = "forward-order-not-applicable"
        return status
    if not _iptables_nm_shared_chain_blocks_iface(chain_output, devkit_iface):
        status["reason"] = "chain-not-blocking"
        return status

    status["applicable"] = True
    status["rule_present"] = _iptables_nm_shared_allow_rule_present(
        iptables_cmd,
        chain,
        bridge_iface,
        docker_subnet,
        devkit_iface,
        devkit_subnet,
        allow_sudo_prompt=allow_sudo_prompt,
    )
    return status


def _configure_nm_shared_devkit_iptables_forwarding(
    devkit_ip: str,
    docker_network: str = "simasdkbridge",
    status: Optional[Dict[str, object]] = None,
) -> bool:
    status = status or nm_shared_iptables_repair_status(devkit_ip, docker_network=docker_network)
    if not status.get("applicable"):
        return False

    iptables_cmd = _find_executable("iptables")
    if not iptables_cmd:
        return False

    inserted = _insert_iptables_nm_shared_allow_rule(
        iptables_cmd,
        str(status["chain"]),
        str(status["bridge_iface"]),
        str(status["docker_subnet"]),
        str(status["devkit_iface"]),
        str(status["devkit_subnet"]),
    )
    print(
        "✅ NetworkManager shared-mode iptables connection {}: SDK bridge {} ({}) -> {} ({}) for DevKit {}.".format(
            "updated" if inserted else "already allows traffic",
            status["bridge_iface"] or "<any>",
            status["docker_subnet"],
            status["devkit_iface"],
            status["devkit_subnet"],
            devkit_ip,
        )
    )
    return True


def _nm_shared_dispatcher_script(devkit_iface: str, devkit_subnet: str, docker_network: str = "simasdkbridge") -> str:
    return """#!/bin/sh
set -eu

IFACE="$1"
ACTION="$2"

DEVKIT_IFACE=__DEVKIT_IFACE__
DEVKIT_SUBNET=__DEVKIT_SUBNET__
SDK_NETWORK=__DOCKER_NETWORK__
CHAIN="nm-sh-fw-$DEVKIT_IFACE"

case "$ACTION" in
  up|connectivity-change|dhcp4-change|reapply)
    ;;
  *)
    exit 0
    ;;
esac

[ "$IFACE" = "$DEVKIT_IFACE" ] || exit 0

NMCLI="$(command -v nmcli || true)"
DOCKER="$(command -v docker || true)"
IPTABLES="$(command -v iptables || true)"
[ -n "$NMCLI" ] && [ -n "$DOCKER" ] && [ -n "$IPTABLES" ] || exit 0

CONNECTION="$("$NMCLI" -g GENERAL.CONNECTION device show "$DEVKIT_IFACE" 2>/dev/null | sed -n '1p' || true)"
[ -n "$CONNECTION" ] && [ "$CONNECTION" != "--" ] || exit 0
METHOD="$("$NMCLI" -g ipv4.method connection show "$CONNECTION" 2>/dev/null | sed -n '1p' || true)"
[ "$METHOD" = "shared" ] || exit 0

SDK_SUBNET="$("$DOCKER" network inspect "$SDK_NETWORK" --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' 2>/dev/null | sed -n '/^[0-9][0-9.]*\\/[0-9][0-9]*$/p' | sed -n '1p' || true)"
[ -n "$SDK_SUBNET" ] || exit 0

SDK_BRIDGE="$("$DOCKER" network inspect "$SDK_NETWORK" --format '{{index .Options "com.docker.network.bridge.name"}}' 2>/dev/null || true)"
if [ -z "$SDK_BRIDGE" ] || [ "$SDK_BRIDGE" = "<no value>" ]; then
  SDK_ID="$("$DOCKER" network inspect "$SDK_NETWORK" --format '{{.Id}}' 2>/dev/null | cut -c1-12 || true)"
  [ -n "$SDK_ID" ] && SDK_BRIDGE="br-$SDK_ID"
fi

"$IPTABLES" -S "$CHAIN" >/dev/null 2>&1 || exit 0

FORWARD_OUTPUT="$("$IPTABLES" -S FORWARD 2>/dev/null || true)"
CHAIN_OUTPUT="$("$IPTABLES" -S "$CHAIN" 2>/dev/null || true)"
FORWARD_ORDER_OK="$(printf '%s\n' "$FORWARD_OUTPUT" | awk -v chain="$CHAIN" '
  $1 == "-A" && $2 == "FORWARD" {
    for (i = 1; i < NF; i++) {
      if ($i == "-j" && $(i + 1) == chain && nm == 0) nm = NR
      if ($i == "-j" && $(i + 1) == "DOCKER-USER" && docker == 0) docker = NR
    }
  }
  END { if (nm > 0 && docker > 0 && nm < docker) print "yes" }
')"
[ "$FORWARD_ORDER_OK" = "yes" ] || exit 0

CHAIN_BLOCKS_IFACE="$(printf '%s\n' "$CHAIN_OUTPUT" | awk -v iface="$DEVKIT_IFACE" '
  $1 == "-A" {
    has_oif = 0
    rejects = 0
    for (i = 1; i < NF; i++) {
      if ($i == "-o" && $(i + 1) == iface) has_oif = 1
      if ($i == "-j" && $(i + 1) == "REJECT") rejects = 1
    }
    if (has_oif && rejects) found = 1
  }
  END { if (found == 1) print "yes" }
')"
[ "$CHAIN_BLOCKS_IFACE" = "yes" ] || exit 0

if [ -n "$SDK_BRIDGE" ]; then
  "$IPTABLES" -C "$CHAIN" -i "$SDK_BRIDGE" -o "$DEVKIT_IFACE" -s "$SDK_SUBNET" -d "$DEVKIT_SUBNET" -j ACCEPT 2>/dev/null ||
  "$IPTABLES" -I "$CHAIN" 1 -i "$SDK_BRIDGE" -o "$DEVKIT_IFACE" -s "$SDK_SUBNET" -d "$DEVKIT_SUBNET" -j ACCEPT
else
  "$IPTABLES" -C "$CHAIN" -o "$DEVKIT_IFACE" -s "$SDK_SUBNET" -d "$DEVKIT_SUBNET" -j ACCEPT 2>/dev/null ||
  "$IPTABLES" -I "$CHAIN" 1 -o "$DEVKIT_IFACE" -s "$SDK_SUBNET" -d "$DEVKIT_SUBNET" -j ACCEPT
fi
""".replace(
        "__DEVKIT_IFACE__", shlex.quote(devkit_iface)
    ).replace(
        "__DEVKIT_SUBNET__", shlex.quote(devkit_subnet)
    ).replace(
        "__DOCKER_NETWORK__", shlex.quote(docker_network)
    )


def install_nm_shared_dispatcher_repair(
    devkit_ip: str,
    docker_network: str = "simasdkbridge",
    status: Optional[Dict[str, object]] = None,
) -> bool:
    status = status or nm_shared_iptables_repair_status(devkit_ip, docker_network=docker_network)
    if not status.get("applicable"):
        raise RuntimeError(
            "Cannot install NetworkManager dispatcher repair hook because the Ubuntu shared-mode "
            f"iptables path is not applicable: {status.get('reason') or 'unknown'}."
        )

    script = _nm_shared_dispatcher_script(
        str(status["devkit_iface"]),
        str(status["devkit_subnet"]),
        docker_network=docker_network,
    )
    install_cmd = [
        "sudo",
        "sh",
        "-c",
        "set -eu; install -d -m 755 /etc/NetworkManager/dispatcher.d; "
        "tmp=$(mktemp /etc/NetworkManager/dispatcher.d/.90-sima-sdk-shared-network.XXXXXX); "
        "trap 'rm -f \"$tmp\"' EXIT; "
        "cat > \"$tmp\"; "
        "chown root:root \"$tmp\"; "
        "chmod 755 \"$tmp\"; "
        f"mv \"$tmp\" {shlex.quote(NM_SHARED_DISPATCHER_PATH)}; "
        "trap - EXIT",
    ]
    install_result = subprocess.run(
        install_cmd,
        input=script,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if install_result.returncode != 0:
        raise RuntimeError(
            "Failed to install NetworkManager dispatcher repair hook.\n"
            "Command: {}\n"
            "Error: {}".format(
                " ".join(shlex.quote(part) for part in install_cmd),
                (install_result.stderr or install_result.stdout or "").strip(),
            )
        )
    print(f"✅ NetworkManager dispatcher repair hook installed: {NM_SHARED_DISPATCHER_PATH}")
    return True


def maybe_install_nm_shared_dispatcher_repair(
    devkit_ip: str,
    docker_network: str = "simasdkbridge",
    noninteractive: bool = False,
    persistent_network_profile: bool = False,
) -> bool:
    status = nm_shared_iptables_repair_status(
        devkit_ip,
        docker_network=docker_network,
        allow_sudo_prompt=not noninteractive or persistent_network_profile,
    )
    if not status.get("applicable") or status.get("dispatcher_installed"):
        return False

    if persistent_network_profile:
        print(
            "ℹ️  Installing persistent NetworkManager dispatcher hook for SDK bridge forwarding "
            "because --persistent-network-profile was provided and the Ubuntu shared-mode iptables repair is applicable."
        )
        return install_nm_shared_dispatcher_repair(devkit_ip, docker_network=docker_network, status=status)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "⚠️  SDK bridge forwarding was repaired for this session, but persistence requires confirmation. "
            f"Run: sima-cli sdk network repair --devkit {devkit_ip} --persist, or rerun setup with --persistent-network-profile."
        )
        return False

    print(
        "⚠️  NetworkManager may recreate its shared-mode firewall chain after reconnect or reboot. "
        f"sima-cli can install a persistent dispatcher hook at {NM_SHARED_DISPATCHER_PATH}."
    )
    response = input("Install persistent NetworkManager SDK bridge repair now? [Y/n]: ").strip().lower()
    if response in {"", "y", "yes"}:
        return install_nm_shared_dispatcher_repair(devkit_ip, docker_network=docker_network, status=status)

    print(f"ℹ️  Persistent NetworkManager repair skipped. You can install it later with: sima-cli sdk network repair --devkit {devkit_ip} --persist")
    return False


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


def configure_linux_shared_devkit_network(devkit_ip: str, persist: bool = False) -> None:
    _configure_nm_shared_devkit_forwarding(devkit_ip)
    iptables_status = nm_shared_iptables_repair_status(devkit_ip) if persist else None
    _configure_nm_shared_devkit_iptables_forwarding(devkit_ip, status=iptables_status)
    if persist:
        if iptables_status and iptables_status.get("applicable"):
            install_nm_shared_dispatcher_repair(devkit_ip, status=iptables_status)
        else:
            print(
                "ℹ️  NetworkManager dispatcher repair hook not installed because the "
                f"Ubuntu shared-mode iptables path is not applicable ({(iptables_status or {}).get('reason') or 'unknown'})."
            )
    _configure_nm_shared_devkit_internet(devkit_ip)
    if not _configure_nm_shared_devkit_ipv6_internet(devkit_ip):
        _disable_nm_shared_devkit_ipv6(devkit_ip)
