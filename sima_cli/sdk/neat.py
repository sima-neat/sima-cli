import ipaddress
import json
import os
import platform
import random
import secrets
import shutil
import socket
import subprocess
import time
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
OPENVSCODE_TOKEN_BYTES = 32
OPENVSCODE_TOKEN_CACHE_FILE = "sdk-code-ui-tokens.json"


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
    code_ui_token: str = ""
    code_ui_supported: bool = True


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


def allocate_neat_ports(
    no_insight: bool = False,
    reserved_ports: Optional[set] = None,
) -> Tuple[Dict, List[str]]:
    reserved = set(reserved_ports or set())

    port_map = {
        "schema": NEAT_PORT_MAP_SCHEMA,
    }

    port_args = []

    if not no_insight:
        port_map["cert"] = {
            "mount": "/sdk-cert",
            "certFile": "/sdk-cert/neat-sdk.pem",
            "keyFile": "/sdk-cert/neat-sdk-key.pem",
        }
        web_ssh = _allocate_single_port(8022, "tcp", reserved)
        rtsp_tcp = _allocate_single_port(8554, "tcp", reserved)
        main_ui = _allocate_single_port(9900, "tcp", reserved)
        code_ui = _allocate_single_port(9999, "tcp", reserved)
        code_ui_https = _allocate_single_port(10000, "tcp", reserved)
        video_ui = _allocate_single_port(8081, "tcp", reserved)
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

        port_map.update(
            {
                "mainUI": {"protocol": "tcp", "host": main_ui, "container": 9900},
                "codeUI": {"protocol": "tcp", "host": code_ui, "container": 9999},
                "codeUIHttps": {"protocol": "tcp", "host": code_ui_https, "container": 10000, "scheme": "https"},
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
            }
        )
        port_args.extend(
            [
                f"{web_ssh}:8022/tcp",
                f"{main_ui}:9900/tcp",
                f"{code_ui}:9999/tcp",
                f"{code_ui_https}:10000/tcp",
                f"{video_ui}:8081/tcp",
                f"{rtsp_tcp}:8554/tcp",
                f"{video_udp_start}-{video_udp_end}:9000-9079/udp",
                f"{metadata_udp_start}-{metadata_udp_end}:9100-9179/udp",
                f"{webrtc_udp_start}-{webrtc_udp_end}:{webrtc_udp_start}-{webrtc_udp_end}/udp",
            ]
        )
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


def _collect_all_ipv4s_for_certs() -> List[str]:
    """Every usable IPv4 on the host, INCLUDING VPN/tunnel interfaces.

    Unlike the advertised-URL selection (which deliberately prefers a physical
    interface), a certificate SAN should be inclusive: list every address the
    browser might connect to so HTTPS is trusted whether or not a VPN is up.
    Without this, a cert minted while a VPN was connected covers only the VPN IP
    (and vice versa), so toggling the VPN flips between "secure" and "not secure".
    """
    hosts = []
    try:
        import psutil
    except Exception:
        return hosts

    for _iface, addrs in psutil.net_if_addrs().items():
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
    # Include VPN/tunnel IPv4s too so the cert stays valid across VPN on/off.
    hosts.extend(_collect_all_ipv4s_for_certs())

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


def _os_release_value(name: str) -> str:
    path = Path("/etc/os-release")
    if not path.exists():
        return ""
    try:
        for line in path.read_text().splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key == name:
                return value.strip().strip('"')
    except OSError:
        # Best-effort OS detection: if /etc/os-release is unreadable, fall back to empty value.
        pass
    return ""


def _manual_mkcert_instructions() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Install mkcert with: brew install mkcert"
    if system == "Windows":
        return "Install mkcert with: winget install FiloSottile.mkcert"
    if system == "Linux":
        return (
            "Install mkcert with: sudo apt-get update && sudo apt-get install -y mkcert libnss3-tools. "
            "On Ubuntu 20.04, enable universe first with: sudo add-apt-repository universe"
        )
    return "Install mkcert from https://github.com/FiloSottile/mkcert"


def _run_install_command(command: List[str]) -> None:
    subprocess.run(command, check=True)


def _install_mkcert_linux() -> None:
    ids = _os_release_ids()
    if not ({"ubuntu", "debian"} & ids):
        raise RuntimeError(f"Automatic mkcert install is only supported on Ubuntu/Debian Linux. {_manual_mkcert_instructions()}")

    if "ubuntu" in ids and _os_release_value("VERSION_ID") == "20.04":
        if not shutil.which("add-apt-repository"):
            _run_install_command(["sudo", "apt-get", "update"])
            _run_install_command(["sudo", "apt-get", "install", "-y", "software-properties-common"])
        _run_install_command(["sudo", "add-apt-repository", "-y", "universe"])

    _run_install_command(["sudo", "apt-get", "update"])
    _run_install_command(["sudo", "apt-get", "install", "-y", "mkcert", "libnss3-tools"])


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
        _install_mkcert_linux()
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


def _cert_san_hosts(cert_file: Path) -> Optional[List[str]]:
    """Return the SAN entries of ``cert_file`` (both ``DNS:`` names and
    ``IP Address:`` addresses), or ``None`` when the cert can't be read (no
    openssl / parse error) so callers treat it as "unknown -> re-issue".
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert_file)],
            capture_output=True, text=True, check=False,
        )
        text = result.stdout or ""
        if result.returncode != 0 or ("IP Address" not in text and "DNS" not in text):
            result = subprocess.run(
                ["openssl", "x509", "-noout", "-text", "-in", str(cert_file)],
                capture_output=True, text=True, check=False,
            )
            text = result.stdout or ""
    except OSError:
        return None
    # Parse the comma/newline-separated SAN entries as exact tokens (not
    # substrings) so 192.168.1.5 is not treated as covered by
    # IP Address:192.168.1.50.
    hosts = []
    for token in text.replace("\n", ",").split(","):
        token = token.strip()
        if token.startswith("IP Address:"):
            hosts.append(token[len("IP Address:"):].strip())
        elif token.startswith("DNS:"):
            hosts.append(token[len("DNS:"):].strip())
    return hosts


def _cert_covers_host(cert_file: Path, host_ip: str) -> bool:
    """Return True if the cert's SAN lists ``host_ip``. False on any failure
    (no openssl / parse error) so the caller re-issues to be safe."""
    if not host_ip:
        return False
    san = _cert_san_hosts(cert_file)
    return san is not None and host_ip in san


def _cert_covers_hosts(cert_file: Path, hosts: List[str]) -> bool:
    """Return True only if the cert's SAN covers **every** host in ``hosts``.

    On a re-point the set of host addresses can grow (e.g. a VPN/tunnel IP
    appears) while the primary host IP is unchanged. Checking the full expected
    list -- not just the primary IP -- ensures an older cert missing a newly
    discovered address is re-issued rather than skipped. Returns False on any
    failure so the caller re-issues to be safe; an empty ``hosts`` is treated as
    "nothing to verify -> covered" (the caller's no-host-IP guard already handles
    the do-nothing case).
    """
    wanted = [host for host in hosts if host]
    if not wanted:
        return True
    san = _cert_san_hosts(cert_file)
    if san is None:
        return False
    san_set = set(san)
    return all(host in san_set for host in wanted)


def _container_sdk_cert_dir(container_name: str) -> Optional[Path]:
    """Return the host directory bind-mounted to ``/sdk-cert`` in the running
    container, or ``None`` if it can't be determined.

    The ``/sdk-cert`` mount is fixed at ``docker run`` time to the workspace used
    *then*. We must re-issue into that exact directory, not one reconstructed
    from the current invocation's workspace -- otherwise a user re-running setup
    from a different workspace would write the new cert into an unmounted dir
    while the container keeps serving the old mounted one.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f",
             '{{range .Mounts}}{{if eq .Destination "/sdk-cert"}}{{.Source}}{{end}}{{end}}',
             container_name],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    source = (result.stdout or "").strip()
    return Path(source) if source else None


def refresh_neat_certificates(
    workspace: str,
    container_name: str,
    devkit_env: Optional[dict],
    *,
    yes_to_all: bool = False,
    noninteractive: bool = False,
) -> bool:
    """Re-issue the mounted ``/sdk-cert`` certificate to cover the current host IP.

    On a DevKit re-point (or a host network change) the routed host IP changes,
    but the HTTPS cert mounted into the Neat container was signed once at
    container creation with the *old* IP in its SAN -- so the browser shows
    "Your connection is not private" on the new IP. The host owns the trusted
    mkcert CA, so the cert must be reissued *here* (not inside the container) to
    stay trusted; neat-insight is restarted separately (devkit.sh's refresh hook)
    to pick up the rewritten file from the bind-mounted cert dir.

    (Re)generates when the cert is **missing** (e.g. the user deleted it) or its
    SAN doesn't cover **every** currently expected host -- the primary host IP
    *and* all alternate addresses (e.g. VPN/tunnel IPs) -- so an older cert that
    covers only the primary IP is still refreshed. No-op (returns False) only when
    there is no usable host IP, or a valid cert already covers the full host list.
    Returns True when a cert was generated.
    """
    host_ip = (devkit_env or {}).get("host_ip", "")
    if not host_ip:
        return False
    # Use the directory actually bind-mounted to /sdk-cert in the container, so a
    # re-issue lands where neat-insight reads it even if the current invocation's
    # workspace differs from the one the container was created with. Fall back to
    # the workspace-derived path (e.g. container not running / no mount).
    cert_dir = _container_sdk_cert_dir(container_name) \
        or (Path(workspace) / f".{container_name}" / "sdk-cert")
    cert_file = cert_dir / "neat-sdk.pem"
    # Re-issue unless the existing cert already covers the full set of hosts we
    # would issue for now (primary host IP + all alternate IPv4s incl. VPN/tunnel),
    # not just the primary host_ip -- otherwise a newly discovered address is
    # silently left out on a re-run.
    expected_hosts = _collect_cert_hosts(devkit_env)
    if cert_file.exists() and _cert_covers_hosts(cert_file, expected_hosts):
        return False
    action = "Re-issuing" if cert_file.exists() else "Generating"
    print(f"🔐 {action} Neat SDK certificate for host IP {host_ip} ...")
    _ensure_certificates(
        cert_dir, devkit_env, yes_to_all=yes_to_all, noninteractive=noninteractive
    )
    return True


def _write_port_map(config_dir: Path, port_map: Dict) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "neat-port-map.json"
    path.write_text(json.dumps(port_map, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _generate_code_ui_token() -> str:
    return secrets.token_urlsafe(OPENVSCODE_TOKEN_BYTES)


def _sima_cli_home() -> Path:
    return Path(os.environ.get("SIMA_CLI_HOME", str(Path.home() / ".sima-cli"))).expanduser()


def _code_ui_token_cache_path() -> Path:
    return _sima_cli_home() / OPENVSCODE_TOKEN_CACHE_FILE


def _read_code_ui_token_cache() -> Dict:
    path = _code_ui_token_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_code_ui_token_cache(container_name: str, base_url: str, token: str) -> Path:
    cache_dir = _sima_cli_home()
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        cache_dir.chmod(0o700)
    except OSError:
        pass

    cache = _read_code_ui_token_cache()
    entries = cache.setdefault("containers", {})
    if not isinstance(entries, dict):
        entries = {}
        cache["containers"] = entries
    host_port = int(base_url.rsplit(":", 1)[1])
    entries[container_name] = {
        "codeUI": {
            "host": host_port,
            "token": token,
            "url": f"{base_url}/?tkn={token}&folder=/workspace",
        },
        "updated_at": int(time.time()),
    }

    path = _code_ui_token_cache_path()
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def reserved_ports_from_neat_port_map(port_map: Dict) -> set:
    reserved = set()
    for entry in port_map.values():
        if not isinstance(entry, dict):
            continue
        protocol = entry.get("protocol")
        if protocol not in {"tcp", "udp"}:
            continue
        if "host" in entry:
            reserved.add((protocol, int(entry["host"])))
            continue
        if "hostStart" in entry and "hostEnd" in entry:
            reserved.update(
                (protocol, port)
                for port in range(int(entry["hostStart"]), int(entry["hostEnd"]) + 1)
            )
    rtsp = port_map.get("rtsp")
    if isinstance(rtsp, dict):
        for protocol, entry in rtsp.items():
            if protocol in {"tcp", "udp"} and isinstance(entry, dict) and "host" in entry:
                reserved.add((protocol, int(entry["host"])))
    return reserved


def prepare_neat_container_run(
    workspace: str,
    container_name: str,
    devkit_env: Optional[dict] = None,
    yes_to_all: bool = False,
    noninteractive: bool = False,
    no_insight: bool = False,
    minimal: bool = False,
    reserved_ports: Optional[set] = None,
) -> NeatRunConfig:
    no_insight = no_insight or minimal
    container_dir = Path(workspace) / f".{container_name}"
    config_dir = container_dir / "insight-config"
    cert_dir = container_dir / "sdk-cert"
    port_map, port_args = allocate_neat_ports(
        no_insight=no_insight,
        reserved_ports=reserved_ports,
    )
    if no_insight:
        return NeatRunConfig(
            port_map=port_map,
            port_args=port_args,
            config_host_dir="",
            cert_host_dir="",
            port_map_host_path="",
            cert_file_host_path="",
            key_file_host_path="",
            webrtc_host_ip="",
        )
    webrtc_host_ip = _detect_webrtc_host_ip(devkit_env)
    cert_file, key_file = _ensure_certificates(
        cert_dir,
        devkit_env=devkit_env,
        yes_to_all=yes_to_all,
        noninteractive=noninteractive,
    )
    code_ui_token = ""
    code_ui = port_map.get("codeUI")
    if isinstance(code_ui, dict):
        code_ui_token = _generate_code_ui_token()
        code_ui_https = port_map.get("codeUIHttps")
        code_ui_url = f"http://localhost:{int(code_ui['host'])}"
        if isinstance(code_ui_https, dict):
            code_ui_url = f"https://localhost:{int(code_ui_https['host'])}"
        _write_code_ui_token_cache(container_name, code_ui_url, code_ui_token)
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
        code_ui_token=code_ui_token,
    )


def append_neat_docker_args(docker_cmd: List[str], config: NeatRunConfig) -> None:
    if config.port_args or config.config_host_dir:
        docker_cmd.extend(["-e", f"MTX_RTSPTRANSPORTS={NEAT_MEDIAMTX_RTSP_TRANSPORTS}"])
    if config.webrtc_host_ip:
        docker_cmd.extend(["-e", f"CONTAINER_HOST_IP={config.webrtc_host_ip}"])
    if config.code_ui_token:
        docker_cmd.extend(["-e", f"OPENVSCODE_SERVER_TOKEN={config.code_ui_token}"])
    cert_config = config.port_map.get("cert")
    if config.cert_host_dir and isinstance(cert_config, dict):
        docker_cmd.extend(["-e", f"OPENVSCODE_SERVER_CERT={cert_config['certFile']}"])
        docker_cmd.extend(["-e", f"OPENVSCODE_SERVER_CERT_KEY={cert_config['keyFile']}"])
        code_ui_https = config.port_map.get("codeUIHttps")
        if isinstance(code_ui_https, dict):
            docker_cmd.extend(["-e", f"OPENVSCODE_SERVER_HTTPS_PORT={code_ui_https['container']}"])
    for mapping in config.port_args:
        docker_cmd.extend(["-p", mapping])
    if config.config_host_dir:
        docker_cmd.extend(["-v", f"{config.config_host_dir}:/home/docker/.insight-config"])
    if config.cert_host_dir:
        docker_cmd.extend(["-v", f"{config.cert_host_dir}:/sdk-cert"])


def print_neat_setup_summary(config: NeatRunConfig) -> None:
    port_map = config.port_map
    rows = []
    display_host = config.webrtc_host_ip or "localhost"
    web_scheme = "https" if config.cert_host_dir else "http"
    if "mainUI" in port_map:
        rows.append(("mainUI", f"{web_scheme}://{display_host}:{port_map['mainUI']['host']}"))
    if config.code_ui_supported and "codeUI" in port_map:
        display_entry = port_map.get("codeUIHttps") if config.cert_host_dir else None
        if not isinstance(display_entry, dict):
            display_entry = port_map["codeUI"]
        scheme = display_entry.get("scheme", "http")
        code_url = f"{scheme}://{display_host}:{display_entry['host']}"
        token = config.code_ui_token or port_map["codeUI"].get("token")
        if token:
            code_url = f"{code_url}/?tkn={token}&folder=/workspace"
        rows.append(("codeUI", code_url))
        if "codeUIHttps" in port_map:
            rows.append(("codeUIHttp", f"http://{display_host}:{port_map['codeUI']['host']}"))
    if "videoUI" in port_map:
        rows.append(("videoUI", f"{web_scheme}://{display_host}:{port_map['videoUI']['host']}"))
    if "webSSH" in port_map:
        rows.append(("webSSH", f"http://{display_host}:{port_map['webSSH']['host']}"))
    if "rtsp" in port_map:
        rows.append(("rtsp", f"rtsp://{display_host}:{port_map['rtsp']['tcp']['host']}"))
    if "videoUDP" in port_map:
        rows.append(
            ("videoUDP", "{}-{}/udp -> {}-{}/udp".format(
                port_map["videoUDP"]["hostStart"],
                port_map["videoUDP"]["hostEnd"],
                port_map["videoUDP"]["containerStart"],
                port_map["videoUDP"]["containerEnd"],
            ))
        )
    if "metadataUDP" in port_map:
        rows.append(
            ("metadataUDP", "{}-{}/udp -> {}-{}/udp".format(
                port_map["metadataUDP"]["hostStart"],
                port_map["metadataUDP"]["hostEnd"],
                port_map["metadataUDP"]["containerStart"],
                port_map["metadataUDP"]["containerEnd"],
            ))
        )
    if "webRTC" in port_map:
        rows.append(
            ("webRTC", "{}-{}/udp -> {}-{}/udp".format(
                port_map["webRTC"]["hostStart"],
                port_map["webRTC"]["hostEnd"],
                port_map["webRTC"]["containerStart"],
                port_map["webRTC"]["containerEnd"],
            ))
        )
    if config.webrtc_host_ip:
        rows.append(("iceHost", config.webrtc_host_ip))
    if config.port_map_host_path:
        rows.append(("config", config.port_map_host_path))
    if config.cert_host_dir:
        rows.append(("certs", config.cert_host_dir))

    print("🔌 Neat SDK port map:")
    if not rows:
        print("   No Neat SDK ports are mapped.")
        return

    name_width = max(len("Name"), *(len(name) for name, _ in rows))
    print(f"   {'Name'.ljust(name_width)} | Endpoint / Value")
    print(f"   {'-' * name_width}-+-{'-' * len('Endpoint / Value')}")
    for name, value in rows:
        print(f"   {name.ljust(name_width)} | {value}")
    if display_host == "localhost":
        print("   Note: Use localhost for local access, or replace it with this machine's external IP/DNS name for remote access.")
    else:
        print("   Note: Use the shown host IP for remote access, or replace it with localhost/127.0.0.1 for local access.")


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
