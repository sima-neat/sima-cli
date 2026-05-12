import ipaddress
import json
import os
import platform
import random
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import click

from sima_cli.utils.net import get_local_ip_candidates


NEAT_PORT_MAP_SCHEMA = "sima.neat.port-map.v1"
NEAT_PORT_SEARCH_START = 18000
NEAT_PORT_SEARCH_END = 30000
NEAT_DOCKER_RETRY_LIMIT = 3
NEAT_MEDIAMTX_RTSP_TRANSPORTS = "tcp"
NEAT_WEBRTC_UDP_SEARCH_START = 40000
NEAT_WEBRTC_UDP_SEARCH_END = 65535
NEAT_WEBRTC_UDP_PORT_COUNT = 200


@dataclass
class NeatRunConfig:
    port_map: Dict
    port_args: List[str]
    config_host_dir: str
    cert_host_dir: str
    port_map_host_path: str
    cert_file_host_path: str
    key_file_host_path: str
    webrtc_host_ip: str = ""


def _can_bind_tcp(port: int) -> bool:
    if not _can_bind_socket(socket.AF_INET, socket.SOCK_STREAM, "0.0.0.0", port):
        return False
    if socket.has_ipv6 and not _can_bind_socket(socket.AF_INET6, socket.SOCK_STREAM, "::", port):
        return False
    return True


def _can_bind_udp(port: int) -> bool:
    if not _can_bind_socket(socket.AF_INET, socket.SOCK_DGRAM, "0.0.0.0", port):
        return False
    if socket.has_ipv6 and not _can_bind_socket(socket.AF_INET6, socket.SOCK_DGRAM, "::", port):
        return False
    return True


def _can_bind_socket(family: socket.AddressFamily, sock_type: socket.SocketKind, host: str, port: int) -> bool:
    with socket.socket(family, sock_type) as sock:
        if family == socket.AF_INET6 and hasattr(socket, "IPV6_V6ONLY"):
            # Check IPv6 separately. A dual-stack bind can fail on some systems
            # after an IPv4 probe even when the port is actually free.
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        if sock_type == socket.SOCK_STREAM:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _is_port_available(port: int, protocol: str) -> bool:
    if protocol == "tcp":
        return _can_bind_tcp(port)
    if protocol == "udp":
        return _can_bind_udp(port)
    raise ValueError(f"Unsupported protocol: {protocol}")


def _allocate_single_port(preferred: int, protocol: str, reserved: set) -> int:
    preferred_key = (protocol, preferred)
    if preferred_key not in reserved and _is_port_available(preferred, protocol):
        reserved.add(preferred_key)
        return preferred

    for _ in range(2000):
        candidate = random.randint(NEAT_PORT_SEARCH_START, NEAT_PORT_SEARCH_END)
        candidate_key = (protocol, candidate)
        if candidate_key in reserved:
            continue
        if _is_port_available(candidate, protocol):
            reserved.add(candidate_key)
            return candidate

    raise RuntimeError(f"Could not allocate a free {protocol.upper()} host port")


def _is_range_available(start: int, end: int, protocol: str, reserved: set) -> bool:
    return all((protocol, port) not in reserved and _is_port_available(port, protocol) for port in range(start, end + 1))


def _reserve_range(start: int, end: int, protocol: str, reserved: set) -> None:
    reserved.update((protocol, port) for port in range(start, end + 1))


def _allocate_port_range(preferred_start: int, preferred_end: int, protocol: str, reserved: set) -> Tuple[int, int]:
    size = preferred_end - preferred_start + 1
    if _is_range_available(preferred_start, preferred_end, protocol, reserved):
        _reserve_range(preferred_start, preferred_end, protocol, reserved)
        return preferred_start, preferred_end

    max_start = NEAT_PORT_SEARCH_END - size + 1
    candidates = list(range(NEAT_PORT_SEARCH_START, max_start + 1))
    random.shuffle(candidates)
    for start in candidates:
        end = start + size - 1
        if _is_range_available(start, end, protocol, reserved):
            _reserve_range(start, end, protocol, reserved)
            return start, end

    raise RuntimeError(f"Could not allocate a contiguous {size}-port {protocol.upper()} host range")


def _allocate_first_available_port_range(
    search_start: int,
    search_end: int,
    size: int,
    protocol: str,
    reserved: set,
    label: str,
) -> Tuple[int, int]:
    run_start = None
    run_length = 0

    for port in range(search_start, search_end + 1):
        available = (protocol, port) not in reserved and _is_port_available(port, protocol)
        if available:
            if run_start is None:
                run_start = port
            run_length += 1
            if run_length == size:
                end = port
                _reserve_range(run_start, end, protocol, reserved)
                return run_start, end
            continue

        run_start = None
        run_length = 0

    raise RuntimeError(
        f"Could not allocate a contiguous {size}-port {protocol.upper()} range for {label} "
        f"between {search_start} and {search_end}."
    )


def allocate_neat_ports() -> Tuple[Dict, List[str]]:
    reserved = set()
    main_ui = _allocate_single_port(9900, "tcp", reserved)
    video_ui = _allocate_single_port(8081, "tcp", reserved)
    web_ssh = _allocate_single_port(8022, "tcp", reserved)
    rtsp_tcp = _allocate_single_port(8554, "tcp", reserved)
    video_udp_start, video_udp_end = _allocate_port_range(9000, 9079, "udp", reserved)
    metadata_udp_start, metadata_udp_end = _allocate_port_range(9100, 9179, "udp", reserved)
    webrtc_udp_start, webrtc_udp_end = _allocate_first_available_port_range(
        NEAT_WEBRTC_UDP_SEARCH_START,
        NEAT_WEBRTC_UDP_SEARCH_END,
        NEAT_WEBRTC_UDP_PORT_COUNT,
        "udp",
        reserved,
        "WebRTC",
    )

    port_map = {
        "schema": NEAT_PORT_MAP_SCHEMA,
        "mainUI": {"protocol": "tcp", "host": main_ui, "container": 9900},
        "videoUI": {"protocol": "tcp", "host": video_ui, "container": 8081},
        "webSSH": {"protocol": "tcp", "host": web_ssh, "container": 8022},
        "rtsp": {
            "tcp": {"host": rtsp_tcp, "container": 8554},
        },
        "videoUDP": {
            "protocol": "udp",
            "containerStart": 9000,
            "containerEnd": 9079,
            "hostStart": video_udp_start,
            "hostEnd": video_udp_end,
        },
        "metadataUDP": {
            "protocol": "udp",
            "containerStart": 9100,
            "containerEnd": 9179,
            "hostStart": metadata_udp_start,
            "hostEnd": metadata_udp_end,
        },
        "webRTC": {
            "protocol": "udp",
            "containerStart": webrtc_udp_start,
            "containerEnd": webrtc_udp_end,
            "hostStart": webrtc_udp_start,
            "hostEnd": webrtc_udp_end,
        },
        "cert": {
            "mount": "/sdk-cert",
            "certFile": "/sdk-cert/neat-sdk.pem",
            "keyFile": "/sdk-cert/neat-sdk-key.pem",
        },
    }

    port_args = [
        f"{main_ui}:9900/tcp",
        f"{video_ui}:8081/tcp",
        f"{web_ssh}:8022/tcp",
        f"{rtsp_tcp}:8554/tcp",
        f"{video_udp_start}-{video_udp_end}:9000-9079/udp",
        f"{metadata_udp_start}-{metadata_udp_end}:9100-9179/udp",
        f"{webrtc_udp_start}-{webrtc_udp_end}:{webrtc_udp_start}-{webrtc_udp_end}/udp",
    ]
    return port_map, port_args


def _is_usable_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(addr.version == 4 and not addr.is_loopback and not addr.is_multicast and not addr.is_unspecified)


def _collect_physical_ipv4s_for_certs() -> List[str]:
    hosts = []
    try:
        import psutil
    except Exception:
        return hosts

    blocked_prefixes = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "utun", "wg", "tailscale", "zt", "vmnet", "vboxnet")
    for iface, addrs in psutil.net_if_addrs().items():
        name = iface.lower()
        if name.startswith(blocked_prefixes):
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and _is_usable_ipv4(addr.address):
                hosts.append(addr.address)
    return hosts


def _collect_cert_hosts(devkit_env: Optional[dict]) -> List[str]:
    hosts = ["localhost", "127.0.0.1"]
    if devkit_env and devkit_env.get("host_ip"):
        hosts.append(devkit_env["host_ip"])

    try:
        for _iface, ip in get_local_ip_candidates():
            if _is_usable_ipv4(ip):
                hosts.append(ip)
    except Exception:
        pass
    hosts.extend(_collect_physical_ipv4s_for_certs())

    seen = set()
    return [host for host in hosts if host and not (host in seen or seen.add(host))]


def _detect_webrtc_host_ip(devkit_env: Optional[dict]) -> str:
    if devkit_env and devkit_env.get("host_ip"):
        return devkit_env["host_ip"]

    try:
        for _iface, ip in get_local_ip_candidates():
            if _is_usable_ipv4(ip):
                return ip
    except Exception:
        pass

    physical_ips = _collect_physical_ipv4s_for_certs()
    return physical_ips[0] if physical_ips else ""


def _is_wsl() -> bool:
    if os.getenv("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False


def _os_release_ids() -> set:
    ids = set()
    path = Path("/etc/os-release")
    if not path.exists():
        return ids
    try:
        for line in path.read_text().splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"ID", "ID_LIKE"}:
                ids.update(part.strip().strip('"').lower() for part in value.split())
    except OSError:
        pass
    return ids


def _manual_mkcert_instructions() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Install mkcert with: brew install mkcert"
    if system == "Windows":
        return "Install mkcert with: winget install FiloSottile.mkcert"
    if system == "Linux":
        return "Install mkcert with: sudo apt-get install -y mkcert libnss3-tools"
    return "Install mkcert from https://github.com/FiloSottile/mkcert"


def _run_install_command(command: List[str]) -> None:
    subprocess.run(command, check=True)


def _install_mkcert(yes_to_all: bool, noninteractive: bool) -> str:
    click.secho(
        "⚠️  mkcert is required for Neat SDK local HTTPS certificates. Installing it may install host packages and modify the local trust store.",
        fg="yellow",
    )
    if not yes_to_all:
        if noninteractive:
            click.secho("ℹ️  Non-interactive mode: accepting default mkcert installation.", fg="cyan")
        else:
            response = input("Install mkcert now? [Y/n]: ").strip().lower()
            if response in {"n", "no"}:
                raise RuntimeError(f"mkcert is required. {_manual_mkcert_instructions()}")

    system = platform.system()
    if system == "Darwin":
        if not shutil.which("brew"):
            raise RuntimeError(f"Homebrew is required to install mkcert automatically. {_manual_mkcert_instructions()}")
        _run_install_command(["brew", "install", "mkcert"])
    elif system == "Linux":
        if _is_wsl():
            click.secho("⚠️  WSL detected. mkcert trust installed inside WSL may not trust certificates in Windows browsers.", fg="yellow")
        ids = _os_release_ids()
        if not ({"ubuntu", "debian"} & ids):
            raise RuntimeError(f"Automatic mkcert install is only supported on Ubuntu/Debian Linux. {_manual_mkcert_instructions()}")
        _run_install_command(["sudo", "apt-get", "update"])
        _run_install_command(["sudo", "apt-get", "install", "-y", "mkcert", "libnss3-tools"])
    elif system == "Windows":
        if shutil.which("winget"):
            _run_install_command(["winget", "install", "FiloSottile.mkcert"])
        elif shutil.which("choco"):
            _run_install_command(["choco", "install", "mkcert", "-y"])
        else:
            raise RuntimeError(f"No supported Windows mkcert installer found. {_manual_mkcert_instructions()}")
    else:
        raise RuntimeError(f"Automatic mkcert install is not supported on this platform. {_manual_mkcert_instructions()}")

    mkcert = shutil.which("mkcert")
    if not mkcert:
        raise RuntimeError(f"mkcert installation completed, but mkcert was not found on PATH. {_manual_mkcert_instructions()}")
    return mkcert


def _ensure_mkcert(yes_to_all: bool, noninteractive: bool) -> str:
    mkcert = shutil.which("mkcert")
    if mkcert:
        return mkcert
    return _install_mkcert(yes_to_all=yes_to_all, noninteractive=noninteractive)


def _run_mkcert_with_fallback(command: List[str], fallback_reason: str) -> bool:
    try:
        subprocess.run(command, check=True)
        return True
    except subprocess.CalledProcessError as e:
        click.secho(
            f"⚠️  mkcert could not {fallback_reason}: {e}. Falling back to a self-signed certificate.",
            fg="yellow",
        )
        return False


def _generate_self_signed_cert(cert_file: Path, key_file: Path, hosts: List[str]) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError(
            "mkcert trust-store setup failed and openssl is not available to generate a fallback self-signed certificate."
        )

    cn = hosts[0] if hosts else "localhost"
    san_entries = []
    for host in hosts:
        try:
            ipaddress.ip_address(host)
            san_entries.append(f"IP:{host}")
        except ValueError:
            san_entries.append(f"DNS:{host}")
    san = ",".join(san_entries or ["DNS:localhost"])

    subprocess.run([
        openssl,
        "req",
        "-x509",
        "-nodes",
        "-newkey",
        "rsa:2048",
        "-days",
        "825",
        "-keyout",
        str(key_file),
        "-out",
        str(cert_file),
        "-subj",
        f"/CN={cn}",
        "-addext",
        f"subjectAltName={san}",
    ], check=True)


def _ensure_certificates(cert_dir: Path, devkit_env: Optional[dict], yes_to_all: bool, noninteractive: bool) -> Tuple[Path, Path]:
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_file = cert_dir / "neat-sdk.pem"
    key_file = cert_dir / "neat-sdk-key.pem"
    mkcert = _ensure_mkcert(yes_to_all=yes_to_all, noninteractive=noninteractive)
    hosts = _collect_cert_hosts(devkit_env)
    if _run_mkcert_with_fallback([mkcert, "-install"], "install the local CA into the trust store"):
        subprocess.run(
            [mkcert, "-cert-file", str(cert_file), "-key-file", str(key_file), *hosts],
            check=True,
        )
    else:
        _generate_self_signed_cert(cert_file, key_file, hosts)
    return cert_file, key_file


def _write_port_map(config_dir: Path, port_map: Dict) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "neat-port-map.json"
    path.write_text(json.dumps(port_map, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def prepare_neat_container_run(
    workspace: str,
    container_name: str,
    devkit_env: Optional[dict] = None,
    yes_to_all: bool = False,
    noninteractive: bool = False,
) -> NeatRunConfig:
    container_dir = Path(workspace) / f".{container_name}"
    config_dir = container_dir / "insight-config"
    cert_dir = container_dir / "sdk-cert"
    port_map, port_args = allocate_neat_ports()
    webrtc_host_ip = _detect_webrtc_host_ip(devkit_env)
    cert_file, key_file = _ensure_certificates(
        cert_dir,
        devkit_env=devkit_env,
        yes_to_all=yes_to_all,
        noninteractive=noninteractive,
    )
    port_map_path = _write_port_map(config_dir, port_map)
    return NeatRunConfig(
        port_map=port_map,
        port_args=port_args,
        config_host_dir=str(config_dir),
        cert_host_dir=str(cert_dir),
        port_map_host_path=str(port_map_path),
        cert_file_host_path=str(cert_file),
        key_file_host_path=str(key_file),
        webrtc_host_ip=webrtc_host_ip,
    )


def append_neat_docker_args(docker_cmd: List[str], config: NeatRunConfig) -> None:
    docker_cmd.extend(["-e", f"MTX_RTSPTRANSPORTS={NEAT_MEDIAMTX_RTSP_TRANSPORTS}"])
    if config.webrtc_host_ip:
        docker_cmd.extend(["-e", f"CONTAINER_HOST_IP={config.webrtc_host_ip}"])
    for mapping in config.port_args:
        docker_cmd.extend(["-p", mapping])
    docker_cmd.extend(["-v", f"{config.config_host_dir}:/home/docker/.insight-config"])
    docker_cmd.extend(["-v", f"{config.cert_host_dir}:/sdk-cert"])


def print_neat_setup_summary(config: NeatRunConfig) -> None:
    port_map = config.port_map
    print("🔌 Neat SDK port map:")
    print(f"   mainUI:      http://localhost:{port_map['mainUI']['host']}")
    print(f"   videoUI:     http://localhost:{port_map['videoUI']['host']}")
    print(f"   webSSH:      http://localhost:{port_map['webSSH']['host']}")
    print(f"   rtsp:        rtsp://localhost:{port_map['rtsp']['tcp']['host']}")
    print(
        "   videoUDP:    {}-{}/udp -> {}-{}/udp".format(
            port_map["videoUDP"]["hostStart"],
            port_map["videoUDP"]["hostEnd"],
            port_map["videoUDP"]["containerStart"],
            port_map["videoUDP"]["containerEnd"],
        )
    )
    print(
        "   metadataUDP: {}-{}/udp -> {}-{}/udp".format(
            port_map["metadataUDP"]["hostStart"],
            port_map["metadataUDP"]["hostEnd"],
            port_map["metadataUDP"]["containerStart"],
            port_map["metadataUDP"]["containerEnd"],
        )
    )
    print(
        "   webRTC:      {}-{}/udp -> {}-{}/udp".format(
            port_map["webRTC"]["hostStart"],
            port_map["webRTC"]["hostEnd"],
            port_map["webRTC"]["containerStart"],
            port_map["webRTC"]["containerEnd"],
        )
    )
    if config.webrtc_host_ip:
        print(f"   iceHost:     {config.webrtc_host_ip}")
    print(f"   config:      {config.port_map_host_path}")
    print(f"   certs:       {config.cert_host_dir}")


def is_docker_port_collision_error(error_text: str) -> bool:
    normalized = (error_text or "").lower()
    markers = (
        "port is already allocated",
        "bind: address already in use",
        "ports are not available",
        "failed to bind",
        "listen tcp",
        "listen udp",
    )
    return any(marker in normalized for marker in markers)
