import json
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sima_cli.sdk.linux_shared_network import (
    NM_SHARED_DISPATCHER_PATH,
    configure_linux_shared_devkit_network,
    nm_shared_iptables_repair_status,
)


SDK_BRIDGE_NETWORK = "simasdkbridge"
INSIGHT_CONFIG_MOUNT = "/home/docker/.insight-config"
NEAT_PORT_MAP_FILE = "neat-port-map.json"
SECRET_KEY_PATTERN = re.compile(
    r"(password|passwd|token|secret|credential|cookie|authorization|access[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
VPN_INTERFACE_PREFIXES = (
    "tun",
    "tap",
    "wg",
    "tailscale",
    "zt",
    "ppp",
    "utun",
)
VIRTUAL_INTERFACE_PREFIXES = (
    "docker",
    "br-",
    "veth",
    "virbr",
    "vmnet",
    "vboxnet",
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class RouteProbe:
    target_ip: str
    interface: str = ""
    source_ip: str = ""
    raw: str = ""
    classification: str = "unknown"


@dataclass(frozen=True)
class PortSpec:
    name: str
    protocol: str
    host_start: int
    host_end: int
    container_start: int
    container_end: int


@dataclass
class NetworkFinding:
    severity: str
    code: str
    message: str
    detail: str = ""


@dataclass
class NetworkDoctorReport:
    container: str = ""
    devkit_ip: str = ""
    route: Optional[RouteProbe] = None
    port_map_path: str = ""
    findings: List[NetworkFinding] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)

    def add(self, severity: str, code: str, message: str, detail: str = "") -> None:
        self.findings.append(NetworkFinding(severity, code, message, detail))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "container": self.container,
            "devkitIp": self.devkit_ip,
            "route": self.route.__dict__ if self.route else None,
            "portMapPath": self.port_map_path,
            "findings": [finding.__dict__ for finding in self.findings],
        }


def _run_captured(command: List[str]) -> CommandResult:
    proc = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def _find_executable(name: str) -> str:
    return shutil.which(name) or name


def _is_linux_host() -> bool:
    return platform.system().lower() == "linux"


def _is_darwin_host() -> bool:
    return platform.system().lower() == "darwin"


def _redact_text(text: str) -> str:
    redacted_lines = []
    for line in (text or "").splitlines():
        if SECRET_KEY_PATTERN.search(line):
            if "=" in line:
                key = line.split("=", 1)[0]
                redacted_lines.append(f"{key}=<redacted>")
            elif ":" in line:
                key = line.split(":", 1)[0]
                redacted_lines.append(f"{key}: <redacted>")
            else:
                redacted_lines.append("<redacted>")
        else:
            redacted_lines.append(line)
    return "\n".join(redacted_lines) + ("\n" if text.endswith("\n") else "")


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, child in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                sanitized[key] = "<redacted>"
            else:
                sanitized[key] = _sanitize_json(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str) and SECRET_KEY_PATTERN.search(value):
        if "=" in value:
            return f"{value.split('=', 1)[0]}=<redacted>"
        return "<redacted>"
    return value


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_redact_text(content), encoding="utf-8")


def _write_json(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize_json(content), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def classify_interface(name: str) -> str:
    normalized = (name or "").lower()
    if not normalized:
        return "unknown"
    if normalized == "lo":
        return "loopback"
    if normalized.startswith(VPN_INTERFACE_PREFIXES):
        return "vpn"
    if normalized.startswith(VIRTUAL_INTERFACE_PREFIXES):
        return "virtual"
    return "physical"


def parse_ip_route_get(output: str) -> Tuple[str, str]:
    dev_match = re.search(r"(?:^|\s)dev\s+(\S+)", output or "")
    src_match = re.search(r"(?:^|\s)src\s+(\S+)", output or "")
    return (
        dev_match.group(1) if dev_match else "",
        src_match.group(1) if src_match else "",
    )


def probe_route_to_devkit(devkit_ip: str) -> RouteProbe:
    if not _is_linux_host() or not devkit_ip:
        return RouteProbe(target_ip=devkit_ip)

    ip_cmd = _find_executable("ip")
    result = _run_captured([ip_cmd, "-o", "-4", "route", "get", devkit_ip])
    if result.returncode != 0:
        return RouteProbe(target_ip=devkit_ip, raw=(result.stderr or result.stdout).strip())

    iface, source_ip = parse_ip_route_get(result.stdout)
    return RouteProbe(
        target_ip=devkit_ip,
        interface=iface,
        source_ip=source_ip,
        raw=result.stdout.strip(),
        classification=classify_interface(iface),
    )


def _load_json_command(command: List[str]) -> Optional[object]:
    result = _run_captured(command)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def inspect_container(container: str) -> Optional[dict]:
    docker = _find_executable("docker")
    data = _load_json_command([docker, "inspect", container])
    if isinstance(data, list) and data:
        return data[0]
    return None


def _is_neat_sdk_image_name(image: str) -> bool:
    leaf = (image or "").rsplit("/", 1)[-1].lower()
    return leaf.startswith("sdk") or "sima-neat/sdk" in (image or "").lower()


def list_neat_sdk_containers() -> List[str]:
    docker = _find_executable("docker")
    result = _run_captured([docker, "ps", "-a", "--format", "{{.Names}}\t{{.Image}}"])
    if result.returncode != 0:
        return []

    containers = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        name, image = parts
        if _is_neat_sdk_image_name(image):
            containers.append(name)
    return containers


def resolve_neat_sdk_container(container: str = "") -> Tuple[str, Optional[str]]:
    if container:
        return container, None

    containers = list_neat_sdk_containers()
    if not containers:
        return "", "No Neat SDK containers were found."
    if len(containers) > 1:
        return "", "Multiple Neat SDK containers found: {}. Pass --container.".format(", ".join(containers))
    return containers[0], None


def _container_running(inspect: dict) -> bool:
    return ((inspect.get("State") or {}).get("Running")) is True


def _network_mode(inspect: dict) -> str:
    return ((inspect.get("HostConfig") or {}).get("NetworkMode")) or ""


def _attached_networks(inspect: dict) -> Dict[str, dict]:
    return ((inspect.get("NetworkSettings") or {}).get("Networks")) or {}


def container_has_simasdkbridge(inspect: dict) -> bool:
    return SDK_BRIDGE_NETWORK in _attached_networks(inspect)


def _port_map_mount_source(inspect: dict) -> str:
    for mount in inspect.get("Mounts") or []:
        destination = mount.get("Destination") or ""
        if destination.rstrip("/") == INSIGHT_CONFIG_MOUNT:
            source = mount.get("Source") or ""
            if source:
                return source
    return ""


def find_neat_port_map_path(inspect: dict) -> str:
    source = _port_map_mount_source(inspect)
    if not source:
        return ""
    path = Path(source) / NEAT_PORT_MAP_FILE
    return str(path) if path.exists() else str(path)


def load_port_map_from_container_inspect(inspect: dict) -> Tuple[str, Optional[dict]]:
    path = find_neat_port_map_path(inspect)
    if path and Path(path).exists():
        try:
            return path, json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return path, None
    return path, None


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def port_specs_from_port_map(port_map: Optional[dict]) -> List[PortSpec]:
    if not isinstance(port_map, dict):
        return []

    specs: List[PortSpec] = []
    for name in ("mainUI", "videoUI", "webSSH"):
        entry = port_map.get(name)
        if not isinstance(entry, dict):
            continue
        protocol = entry.get("protocol", "tcp")
        host = _as_int(entry.get("host"))
        container = _as_int(entry.get("container"))
        if host and container and protocol in {"tcp", "udp"}:
            specs.append(PortSpec(name, protocol, host, host, container, container))

    rtsp = port_map.get("rtsp")
    if isinstance(rtsp, dict):
        for protocol, entry in rtsp.items():
            if protocol not in {"tcp", "udp"} or not isinstance(entry, dict):
                continue
            host = _as_int(entry.get("host"))
            container = _as_int(entry.get("container"))
            if host and container:
                specs.append(PortSpec(f"rtsp.{protocol}", protocol, host, host, container, container))

    for name in ("videoUDP", "metadataUDP", "webRTC"):
        entry = port_map.get(name)
        if not isinstance(entry, dict):
            continue
        protocol = entry.get("protocol", "udp")
        host_start = _as_int(entry.get("hostStart"))
        host_end = _as_int(entry.get("hostEnd"))
        container_start = _as_int(entry.get("containerStart"))
        container_end = _as_int(entry.get("containerEnd"))
        if protocol in {"tcp", "udp"} and host_start and host_end and container_start and container_end:
            specs.append(PortSpec(name, protocol, host_start, host_end, container_start, container_end))

    return specs


def _published_ports(inspect: dict) -> Dict[Tuple[int, str], List[int]]:
    ports = ((inspect.get("NetworkSettings") or {}).get("Ports")) or {}
    published: Dict[Tuple[int, str], List[int]] = {}
    for key, bindings in ports.items():
        if "/" not in key:
            continue
        container_port_raw, protocol = key.split("/", 1)
        container_port = _as_int(container_port_raw)
        if not container_port:
            continue
        host_ports = []
        for binding in bindings or []:
            host_port = _as_int((binding or {}).get("HostPort"))
            if host_port:
                host_ports.append(host_port)
        published[(container_port, protocol)] = host_ports
    return published


def _missing_or_mismatched_port_publications(inspect: dict, specs: Iterable[PortSpec]) -> List[str]:
    published = _published_ports(inspect)
    mismatches = []
    for spec in specs:
        for offset, container_port in enumerate(range(spec.container_start, spec.container_end + 1)):
            expected_host = spec.host_start + offset
            actual_hosts = published.get((container_port, spec.protocol), [])
            if expected_host not in actual_hosts:
                mismatches.append(
                    f"{spec.name}: expected {expected_host}->{container_port}/{spec.protocol}, "
                    f"Docker has {actual_hosts or 'no publication'}"
                )
                if len(mismatches) >= 10:
                    return mismatches
    return mismatches


def _can_bind(host: str, port: int, protocol: str) -> bool:
    sock_type = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, sock_type) as sock:
        if sock_type == socket.SOCK_STREAM:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _host_config_port_bindings(inspect: dict) -> List[Tuple[int, str, int]]:
    bindings = ((inspect.get("HostConfig") or {}).get("PortBindings")) or {}
    ports = []
    for key, entries in bindings.items():
        if "/" not in key:
            continue
        container_raw, protocol = key.split("/", 1)
        container_port = _as_int(container_raw)
        for entry in entries or []:
            host_port = _as_int((entry or {}).get("HostPort"))
            if container_port and host_port:
                ports.append((container_port, protocol, host_port))
    return ports


def stale_saved_port_binding_conflicts(inspect: dict) -> List[str]:
    if _container_running(inspect):
        return []

    conflicts = []
    for container_port, protocol, host_port in _host_config_port_bindings(inspect):
        if not _can_bind("0.0.0.0", host_port, protocol):
            conflicts.append(f"{host_port}:{container_port}/{protocol}")
    return conflicts


def _docker_exec_success(container: str, command: str) -> bool:
    docker = _find_executable("docker")
    result = _run_captured([docker, "exec", container, "bash", "-lc", command])
    return result.returncode == 0


def _container_has_docker_gateway(inspect: dict) -> bool:
    for network in _attached_networks(inspect).values():
        if (network or {}).get("Gateway"):
            return True
    return False


def _container_default_route_confirmed(container: str, inspect: dict) -> bool:
    # Docker inspect already records the gateway for normal bridge-networked
    # containers. Prefer that over shelling into SDK images that may not carry
    # the iproute2 package.
    if _container_has_docker_gateway(inspect):
        return True

    return _docker_exec_success(
        container,
        "if command -v ip >/dev/null 2>&1; then "
        "ip route show default >/dev/null 2>&1; "
        "elif [ -r /proc/net/route ]; then "
        "awk 'NR>1 && $2==\"00000000\" {found=1} END{exit !found}' /proc/net/route; "
        "else exit 0; fi",
    )


def _add_colima_network_findings(report: NetworkDoctorReport) -> None:
    if not _is_darwin_host():
        return

    try:
        from sima_cli.sdk import preinstall
    except Exception:
        return

    if not preinstall._is_docker_using_colima():
        return

    profile = preinstall._detect_colima_profile()
    network = preinstall._colima_network_config(profile)
    detail = (
        f"profile={profile} "
        f"address={network.get('address')} "
        f"mode={network.get('mode') or 'unset'} "
        f"interface={network.get('interface') or 'unset'} "
        f"ipAddress={network.get('ip_address') or 'unset'}"
    )
    if preinstall._is_colima_network_suitable_for_devkit(profile):
        report.add(
            "info",
            "colima-network-address-enabled",
            "Colima reachable VM addressing is enabled for DevKit-Sync.",
            detail,
        )
        return

    interface = preinstall._route_interface_for_target(report.devkit_ip) or "en0"
    report.add(
        "warning",
        "colima-network-address-disabled",
        "Colima reachable VM addressing is not enabled; SDK containers may be unable to reach the DevKit.",
        (
            f"{detail}\n"
            "Run: colima stop && "
            f"colima start --network-address --network-mode bridged --network-interface {interface} --save-config"
        ),
    )


def build_network_doctor_report(container: str = "", devkit_ip: str = "") -> NetworkDoctorReport:
    report = NetworkDoctorReport(devkit_ip=devkit_ip)

    if not _is_linux_host():
        _add_colima_network_findings(report)
        report.add(
            "warning",
            "unsupported-host",
            "Linux network repair is only supported on Ubuntu/Linux hosts.",
            "macOS and Windows should use diagnostics only; do not run iptables/nft/NetworkManager repair logic.",
        )
        return report

    if devkit_ip:
        route = probe_route_to_devkit(devkit_ip)
        report.route = route
        if route.classification == "vpn":
            report.add(
                "error",
                "vpn-route",
                "Route to the DevKit resolves through a VPN/tunnel interface.",
                f"route={route.raw or 'unavailable'}",
            )
        elif route.classification in {"virtual", "loopback"}:
            report.add(
                "warning",
                "non-physical-route",
                "Route to the DevKit does not resolve through a physical interface.",
                f"route={route.raw or 'unavailable'}",
            )
        elif route.interface and route.source_ip:
            report.add(
                "info",
                "devkit-route",
                f"DevKit route uses {route.interface} ({route.source_ip}).",
                route.raw,
            )
        else:
            report.add("warning", "route-unresolved", "Could not resolve route to DevKit.", route.raw)

        iptables_status = nm_shared_iptables_repair_status(devkit_ip, allow_sudo_prompt=False)
        if iptables_status.get("applicable"):
            chain = str(iptables_status.get("chain") or "")
            report.add(
                "info",
                "nm-shared-iptables-route",
                f"DevKit route uses NetworkManager shared-mode chain {chain}.",
                f"iface={iptables_status.get('devkit_iface')} devkitSubnet={iptables_status.get('devkit_subnet')} sdkSubnet={iptables_status.get('docker_subnet')}",
            )
            if not iptables_status.get("rule_present"):
                report.add(
                    "error",
                    "nm-shared-iptables-blocking",
                    f"NetworkManager shared-mode chain {chain} rejects SDK bridge traffic before DOCKER-USER.",
                    f"Run: sima-cli sdk network repair --devkit {devkit_ip} --persist",
                )
            if iptables_status.get("rule_present") and not iptables_status.get("dispatcher_installed"):
                report.add(
                    "warning",
                    "nm-shared-dispatcher-missing",
                    "Runtime NetworkManager shared-mode SDK forwarding rule is present, but no dispatcher hook is installed for reconnects/reboots.",
                    f"Install persistence with: sima-cli sdk network repair --devkit {devkit_ip} --persist ({NM_SHARED_DISPATCHER_PATH})",
                )

    resolved_container, error = resolve_neat_sdk_container(container)
    if error:
        report.add("warning", "container-unresolved", error)
        return report
    report.container = resolved_container

    inspect = inspect_container(resolved_container)
    if not inspect:
        report.add("error", "container-inspect-failed", f"Could not inspect container '{resolved_container}'.")
        return report

    mode = _network_mode(inspect)
    if mode == "host":
        report.add(
            "error",
            "host-network-mode",
            "Neat SDK container is using host networking; this is not the supported Insight networking model.",
            "Use sima-cli sdk setup to recreate the container with simasdkbridge and generated port mappings.",
        )
    elif mode and mode != SDK_BRIDGE_NETWORK:
        report.add(
            "warning",
            "unexpected-network-mode",
            f"Container network mode is '{mode}', expected '{SDK_BRIDGE_NETWORK}'.",
        )

    if _container_running(inspect) and not container_has_simasdkbridge(inspect):
        report.add(
            "error",
            "missing-simasdkbridge",
            "Container is running but is not attached to simasdkbridge.",
            "Restart or recreate it with sima-cli sdk setup. VS Code Dev Containers direct-start can cause this stale state.",
        )

    stale_conflicts = stale_saved_port_binding_conflicts(inspect)
    if stale_conflicts:
        report.add(
            "error",
            "stale-port-bindings",
            "Stopped container has saved Docker port bindings that are no longer available.",
            ", ".join(stale_conflicts),
        )

    port_map_path, port_map = load_port_map_from_container_inspect(inspect)
    report.port_map_path = port_map_path
    if port_map_path and port_map is None:
        report.add("warning", "port-map-unreadable", f"Could not read generated Insight port map: {port_map_path}")
    elif port_map:
        mismatches = _missing_or_mismatched_port_publications(inspect, port_specs_from_port_map(port_map))
        if mismatches:
            report.add(
                "error",
                "port-map-mismatch",
                "Docker published ports do not match the generated Insight port map.",
                "; ".join(mismatches),
            )
        else:
            report.add("info", "port-map-ok", "Docker published ports match the generated Insight port map.", port_map_path)

    if _container_running(inspect):
        if not _container_default_route_confirmed(resolved_container, inspect):
            report.add("warning", "container-default-route", "Could not confirm a default route inside the SDK container.")
        if devkit_ip and not _docker_exec_success(resolved_container, f"timeout 2 bash -lc '</dev/tcp/{devkit_ip}/22' >/dev/null 2>&1 || ping -c 1 -W 1 {devkit_ip} >/dev/null 2>&1"):
            report.add(
                "warning",
                "container-devkit-reachability",
                "Could not confirm SDK container reachability to the DevKit.",
                "This may require DevKit SSH/firewall access; run doctor from the final host setup for confirmation.",
            )

    return report


def print_network_doctor_report(report: NetworkDoctorReport) -> None:
    print("🔎 Neat SDK network doctor")
    if report.devkit_ip:
        print(f"   DevKit:    {report.devkit_ip}")
    if report.route:
        route = report.route
        print(f"   Route:     iface={route.interface or 'unknown'} src={route.source_ip or 'unknown'} class={route.classification}")
    if report.container:
        print(f"   Container: {report.container}")
    if report.port_map_path:
        print(f"   Port map:  {report.port_map_path}")

    if not report.findings:
        print("✅ No network issues detected.")
        return

    for finding in report.findings:
        prefix = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}.get(finding.severity, "•")
        print(f"{prefix} [{finding.code}] {finding.message}")
        if finding.detail:
            print(f"   {finding.detail}")


def _command_output_for_bundle(command: List[str]) -> str:
    result = _run_captured(command)
    header = [
        f"$ {' '.join(command)}",
        f"exit_code={result.returncode}",
        "",
    ]
    body = []
    if result.stdout:
        body.extend(["[stdout]", result.stdout.rstrip(), ""])
    if result.stderr:
        body.extend(["[stderr]", result.stderr.rstrip(), ""])
    return "\n".join(header + body).rstrip() + "\n"


def _collect_command(bundle_dir: Path, relative_path: str, command: List[str]) -> None:
    _write_text(bundle_dir / relative_path, _command_output_for_bundle(command))


def _collect_json_command(bundle_dir: Path, relative_path: str, command: List[str]) -> bool:
    result = _run_captured(command)
    if result.returncode != 0:
        _write_text(bundle_dir / relative_path.replace(".json", ".txt"), _command_output_for_bundle(command))
        return False
    try:
        content = json.loads(result.stdout)
    except json.JSONDecodeError:
        _write_text(bundle_dir / relative_path.replace(".json", ".txt"), _command_output_for_bundle(command))
        return False
    _write_json(bundle_dir / relative_path, content)
    return True


def _collect_file(bundle_dir: Path, relative_path: str, source_path: str) -> None:
    if not source_path:
        return
    source = Path(source_path)
    if not source.exists() or not source.is_file():
        _write_text(bundle_dir / relative_path, f"File not found: {source_path}\n")
        return
    try:
        _write_text(bundle_dir / relative_path, source.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        _write_text(bundle_dir / relative_path, f"Could not read {source_path}: {exc}\n")


def _collect_linux_host_state(bundle_dir: Path, devkit_ip: str) -> None:
    _collect_command(bundle_dir, "host/uname.txt", ["uname", "-a"])
    _collect_file(bundle_dir, "host/os-release.txt", "/etc/os-release")

    ip_cmd = _find_executable("ip")
    _collect_command(bundle_dir, "network/ip-route.txt", [ip_cmd, "route"])
    _collect_command(bundle_dir, "network/ip-addr.txt", [ip_cmd, "-o", "addr", "show"])
    _collect_command(bundle_dir, "network/ip-neigh.txt", [ip_cmd, "neigh", "show"])
    if devkit_ip:
        _collect_command(bundle_dir, "network/ip-route-get-devkit.txt", [ip_cmd, "-o", "-4", "route", "get", devkit_ip])

    ss_cmd = shutil.which("ss")
    if ss_cmd:
        _collect_command(bundle_dir, "network/listening-sockets.txt", [ss_cmd, "-lntu"])

    for name, command in (
        ("iptables-filter.txt", ["sudo", "-n", _find_executable("iptables"), "-S"]),
        ("iptables-nat.txt", ["sudo", "-n", _find_executable("iptables"), "-t", "nat", "-S"]),
        ("iptables-docker-user.txt", ["sudo", "-n", _find_executable("iptables"), "-S", "DOCKER-USER"]),
        ("nft-ruleset.txt", ["sudo", "-n", _find_executable("nft"), "list", "ruleset"]),
    ):
        _collect_command(bundle_dir, f"firewall/{name}", command)

    if shutil.which("ufw"):
        _collect_command(bundle_dir, "firewall/ufw-status.txt", ["sudo", "-n", _find_executable("ufw"), "status", "verbose"])
    if shutil.which("firewall-cmd"):
        _collect_command(bundle_dir, "firewall/firewalld-state.txt", [_find_executable("firewall-cmd"), "--state"])
        _collect_command(bundle_dir, "firewall/firewalld-active-zones.txt", [_find_executable("firewall-cmd"), "--get-active-zones"])
    if shutil.which("nmcli"):
        nmcli = _find_executable("nmcli")
        _collect_command(bundle_dir, "network/nmcli-devices.txt", [nmcli, "device", "status"])
        _collect_command(bundle_dir, "network/nmcli-active-connections.txt", [nmcli, "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"])


def _collect_colima_state(bundle_dir: Path) -> None:
    if not _is_darwin_host():
        return

    colima = shutil.which("colima")
    if not colima:
        _write_text(bundle_dir / "colima/not-found.txt", "Colima executable was not found on PATH.\n")
        return

    try:
        from sima_cli.sdk import preinstall
        profile = preinstall._detect_colima_profile()
    except Exception:
        profile = "default"

    _collect_command(bundle_dir, "colima/version.txt", [colima, "version"])
    _collect_command(bundle_dir, "colima/status.json", [colima, "status", "--json", "--profile", profile])

    try:
        from sima_cli.sdk import preinstall
        _collect_file(bundle_dir, "colima/colima.yaml", str(preinstall._colima_config_path(profile)))
    except Exception:
        pass


def _collect_docker_state(bundle_dir: Path, container: str) -> None:
    docker = _find_executable("docker")
    _collect_command(bundle_dir, "docker/version.txt", [docker, "version"])
    _collect_command(bundle_dir, "docker/ps-all.txt", [docker, "ps", "-a", "--no-trunc"])
    _collect_json_command(bundle_dir, "docker/network-simasdkbridge.json", [docker, "network", "inspect", SDK_BRIDGE_NETWORK])

    if not container:
        return

    inspect = inspect_container(container)
    if inspect:
        _write_json(bundle_dir / "docker/container-inspect.json", inspect)
        port_map_path, port_map = load_port_map_from_container_inspect(inspect)
        if port_map:
            _write_json(bundle_dir / "insight/neat-port-map.json", port_map)
        elif port_map_path:
            _collect_file(bundle_dir, "insight/neat-port-map.json", port_map_path)
    else:
        _collect_command(bundle_dir, "docker/container-inspect.txt", [docker, "inspect", container])


def collect_network_doctor_bundle(
    report: NetworkDoctorReport,
    output_path: str = "",
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(output_path or f"sima-sdk-network-doctor-{timestamp}.tar.gz").expanduser()
    if destination.is_dir():
        destination = destination / f"sima-sdk-network-doctor-{timestamp}.tar.gz"
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sima-sdk-network-doctor-") as tmpdir:
        bundle_dir = Path(tmpdir) / "sima-sdk-network-doctor"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        _write_json(
            bundle_dir / "summary.json",
            {
                "generatedAt": timestamp,
                "hostPlatform": platform.platform(),
                "pythonVersion": platform.python_version(),
                "container": report.container,
                "devkitIp": report.devkit_ip,
                "hasErrors": report.has_errors,
                "hasWarnings": report.has_warnings,
                "findings": [finding.__dict__ for finding in report.findings],
            },
        )
        _write_json(bundle_dir / "doctor-report.json", report.to_dict())
        _write_text(
            bundle_dir / "README.txt",
            "\n".join(
                [
                    "SiMa Neat SDK network doctor support bundle",
                    "",
                    "This bundle is generated by sima-cli sdk doctor network --collect.",
                    "It is intended for SiMa support/debugging of SDK, DevKit, Docker, and Insight networking.",
                    "The collector is read-only and redacts common token/password/secret fields.",
                    "",
                    "Do not add ~/.sima-cli, Docker credential files, SSH keys, or browser cookies to this bundle.",
                    "",
                ]
            ),
        )

        if _is_linux_host():
            _collect_linux_host_state(bundle_dir, report.devkit_ip)
        else:
            _write_text(
                bundle_dir / "host/non-linux.txt",
                "Linux host firewall/NetworkManager diagnostics were skipped because this host is not Linux.\n",
            )
            _collect_colima_state(bundle_dir)
        _collect_docker_state(bundle_dir, report.container)

        with tarfile.open(destination, "w:gz") as archive:
            archive.add(bundle_dir, arcname=bundle_dir.name)

    return str(destination)


def ensure_existing_neat_container_startable(container: str) -> None:
    inspect = inspect_container(container)
    if not inspect:
        return
    conflicts = stale_saved_port_binding_conflicts(inspect)
    if conflicts:
        raise RuntimeError(
            "Existing Neat SDK container has stale saved Docker port bindings that are no longer available: "
            + ", ".join(conflicts)
            + ". Recreate the container with sima-cli sdk setup so Insight ports can be regenerated."
        )


def validate_running_neat_container_network(container: str, devkit_ip: str = "") -> None:
    report = build_network_doctor_report(container=container, devkit_ip=devkit_ip)
    blocking = [
        finding
        for finding in report.findings
        if finding.severity == "error"
        and finding.code in {"missing-simasdkbridge", "host-network-mode", "port-map-mismatch"}
    ]
    if blocking:
        print_network_doctor_report(report)
        raise RuntimeError(blocking[0].message)


def repair_linux_devkit_network(container: str = "", devkit_ip: str = "", persist: bool = False) -> NetworkDoctorReport:
    report = build_network_doctor_report(container=container, devkit_ip=devkit_ip)
    if not _is_linux_host():
        report.add(
            "error",
            "repair-unsupported-host",
            "SDK network repair is only supported on Ubuntu/Linux hosts.",
            "Use 'sima-cli sdk doctor network --collect' for diagnostics on macOS or Windows.",
        )
        return report

    if report.route and report.route.classification == "vpn":
        report.add(
            "error",
            "repair-blocked-vpn-route",
            "Repair was not attempted because the DevKit route uses a VPN/tunnel interface.",
            "Pass explicit host/interface overrides after confirming the intended route.",
        )
        return report

    if devkit_ip:
        configure_linux_shared_devkit_network(devkit_ip, persist=persist)
        post_report = build_network_doctor_report(container=container, devkit_ip=devkit_ip)
        post_report.findings.insert(
            0,
            NetworkFinding(
                "info",
                "shared-network-repair",
                "Applied scoped NetworkManager shared-network repair checks where applicable.",
            ),
        )
        return post_report
    else:
        report.add(
            "error",
            "repair-skipped-no-devkit",
            "No DevKit IP was provided; route and shared-network repair were skipped.",
        )
    return report
