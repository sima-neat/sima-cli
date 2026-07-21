#!/usr/bin/env python3

import os
import ipaddress
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import click
from rich.console import Console
from rich.panel import Panel

from sima_cli.sdk.preinstall import (
    ensure_colima_resources_for_neat_sdk,
    syscheck,
    warn_if_colima_devkit_network_may_need_bridged,
)
from sima_cli.sdk.config import IMAGE_CONFIG
from sima_cli.sdk.linux_shared_network import (
    configure_linux_shared_devkit_network,
    maybe_install_nm_shared_dispatcher_repair,
)
from sima_cli.sdk.network_doctor import ensure_existing_neat_container_startable
from sima_cli.utils.net import get_local_ip_candidates

from sima_cli.sdk.utils import (
    create_config_json,
    find_available_ports,
    get_container_status,
    get_all_containers,
    get_workspace,
    get_local_sima_images,
    prompt_image_selection,
    filter_images_by_selector,
    confirm_to_remove_exiting_container,
    sanitize_container_name,
    ensure_simasdkbridge_network,
    start_docker_container,
    bootstrap_devkit_container,
    configure_container_user,
    print_section,
    extract_short_name,
    is_neat_sdk_image,
    is_snap_docker_cli,
    check_os,
    container_user_mapping_unavailable,
    detect_current_user,
    select_containers,
)

LINUX_NEAT_EXPORTS_PATH = Path("/etc/exports.d/neat-sdk.exports")

# ─────────────────────────────────────────────
# Entrypoint for setup/start
# ─────────────────────────────────────────────
def is_container_running(name: str) -> bool:
    """Return True if the container is running."""
    try:
        status = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            text=True
        ).strip()
        return status == "true"
    except subprocess.CalledProcessError:
        return False

def _has_cmd(name: str) -> bool:
    return (
        subprocess.run(
            ["bash", "-lc", "command -v {} >/dev/null 2>&1".format(shlex.quote(name))],
            check=False,
        ).returncode
        == 0
    )


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


def _linux_nfs_install_hint() -> str:
    distro_ids = set()
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            for line in os_release.read_text().splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key not in {"ID", "ID_LIKE"}:
                    continue
                distro_ids.update(part.strip().strip('"').lower() for part in value.split())
        except OSError:
            pass

    if {"ubuntu", "debian"} & distro_ids:
        return "Install it with: sudo apt-get install -y nfs-kernel-server"
    if {"fedora", "rhel", "centos", "rocky", "almalinux"} & distro_ids:
        return "Install it with: sudo dnf install -y nfs-utils"
    if "arch" in distro_ids:
        return "Install it with: sudo pacman -S nfs-utils"
    return "Install your host NFS server package and ensure `exportfs` is on the root PATH."


def _install_linux_nfs_server() -> None:
    commands = []
    if _has_cmd("apt-get"):
        commands = [
            ["sudo", "apt-get", "update"],
            ["sudo", "apt-get", "install", "-y", "nfs-kernel-server"],
        ]
    elif _has_cmd("dnf"):
        commands = [["sudo", "dnf", "install", "-y", "nfs-utils"]]
    elif _has_cmd("yum"):
        commands = [["sudo", "yum", "install", "-y", "nfs-utils"]]
    elif _has_cmd("zypper"):
        commands = [["sudo", "zypper", "install", "-y", "nfs-kernel-server"]]
    elif _has_cmd("pacman"):
        commands = [["sudo", "pacman", "-S", "--noconfirm", "nfs-utils"]]
    else:
        raise RuntimeError(
            "Linux NFS server tooling is not installed and no supported package manager was detected. "
            f"{_linux_nfs_install_hint()}"
        )

    print("ℹ️  `exportfs` was not found. Installing host NFS server tooling...")
    for command in commands:
        subprocess.run(command, check=True)


def _is_linux_virtual_iface(iface: str) -> bool:
    prefixes = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "wg", "zt", "tailscale", "vmnet", "vboxnet")
    return iface.startswith(prefixes)


def _is_usable_host_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(
        addr.version == 4
        and not addr.is_loopback
        and not addr.is_multicast
        and not addr.is_unspecified
    )


def _detect_physical_ipv4s_macos() -> List[Tuple[str, str]]:
    try:
        out = subprocess.check_output(["ifconfig"], text=True)
    except Exception:
        return []

    found = []  # type: List[Tuple[str, str]]
    iface = ""
    status = ""
    inet = None  # type: Optional[str]
    is_physical = False

    def flush_current() -> None:
        if iface and is_physical and status == "active" and inet and _is_usable_host_ipv4(inet):
            found.append((iface, inet))

    for line in out.splitlines():
        m = re.match(r"^([a-zA-Z0-9]+): flags=", line)
        if m:
            flush_current()
            iface = m.group(1)
            status = ""
            inet = None
            is_physical = iface.startswith("en")
            continue
        if not iface:
            continue
        s = line.strip()
        if s.startswith("status:"):
            status = s.split(":", 1)[1].strip()
        elif s.startswith("inet "):
            parts = s.split()
            if len(parts) >= 2:
                inet = parts[1]

    flush_current()
    return found


def _detect_physical_ipv4s_linux() -> List[Tuple[str, str]]:
    if not _has_cmd("ip"):
        return []
    try:
        out = subprocess.check_output(["ip", "-o", "-4", "addr", "show", "up"], text=True)
    except Exception:
        return []

    found = []  # type: List[Tuple[str, str]]
    for line in out.splitlines():
        m = re.match(r"^\d+:\s+([^\s]+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/\d+", line)
        if not m:
            continue
        iface, ip = m.group(1), m.group(2)
        if _is_linux_virtual_iface(iface) or not _is_usable_host_ipv4(ip):
            continue
        if iface.startswith(("en", "eth")):
            found.append((iface, ip))
    return found


def _detect_local_ip_candidates() -> List[Tuple[str, str]]:
    candidates = []  # type: List[Tuple[str, str]]
    try:
        candidates.extend([
            (iface, ip)
            for iface, ip in get_local_ip_candidates()
            if _is_usable_host_ipv4(ip)
        ])
    except Exception:
        pass

    if sys.platform == "darwin":
        candidates.extend(_detect_physical_ipv4s_macos())
    elif sys.platform.startswith("linux"):
        candidates.extend(_detect_physical_ipv4s_linux())

    seen = set()
    deduped = []
    for iface, ip in candidates:
        key = (iface, ip)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((iface, ip))
    return deduped


def _routed_ipv4_for_target(target_ip: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, 80))
        routed_ip = s.getsockname()[0]
        return routed_ip if _is_usable_host_ipv4(routed_ip) else ""
    except OSError:
        return ""
    finally:
        s.close()


def _detect_host_ip(devkit_ip: Optional[str]) -> Tuple[str, str, List[Tuple[str, str]]]:
    candidates = _detect_local_ip_candidates()

    # Prefer the source IP selected by the kernel for the actual DevKit route.
    # Accept it only when it belongs to a non-VPN local interface candidate;
    # otherwise a tunnel route such as utun/tun/wg could leak into NFS setup.
    if devkit_ip:
        routed_ip = _routed_ipv4_for_target(devkit_ip)
        if routed_ip:
            for iface, ip in candidates:
                if ip == routed_ip:
                    return ip, iface, candidates

            candidate_text = ", ".join("{}:{}".format(iface, ip) for iface, ip in candidates) or "none"
            print(
                "⚠️  DevKit {} is routed via host IP {}, but that IP is not on a supported "
                "non-VPN interface. Ignoring it for DevKit sync. Local candidates: {}".format(
                    devkit_ip,
                    routed_ip,
                    candidate_text,
                )
            )
            if candidates:
                iface, ip = candidates[0]
                return ip, iface, candidates

            raise RuntimeError(
                "DevKit {} is routed via host IP {}, but no supported non-VPN host interface "
                "was found for DevKit sync.".format(devkit_ip, routed_ip)
            )

        if candidates:
            iface, ip = candidates[0]
            return ip, iface, candidates

        raise RuntimeError("Could not determine a host IP for DevKit sync.")

    if candidates:
        iface, ip = candidates[0]
        return ip, iface, candidates

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((devkit_ip or "8.8.8.8", 80))
        return s.getsockname()[0], "auto", []
    finally:
        s.close()

def _detect_routed_host_ip(devkit_ip: Optional[str]) -> Tuple[str, str, List[Tuple[str, str]]]:
    """Return the host source IP the kernel actually routes toward the DevKit.

    Unlike _detect_host_ip, this trusts the kernel's routing decision directly and
    does not require the routed IP to belong to a supported non-VPN interface. The
    routed IP is matched against the local candidates to recover an interface name
    when possible; otherwise the interface is reported as "routed".
    """
    candidates = _detect_local_ip_candidates()

    if devkit_ip:
        routed_ip = _routed_ipv4_for_target(devkit_ip)
        if routed_ip:
            for iface, ip in candidates:
                if ip == routed_ip:
                    return ip, iface, candidates
            return routed_ip, "routed", candidates

    if candidates:
        iface, ip = candidates[0]
        return ip, iface, candidates

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((devkit_ip or "8.8.8.8", 80))
        return s.getsockname()[0], "auto", []
    finally:
        s.close()

def _print_devkit_nfs_banner(workspace: str, devkit_ip: str, host_os: str) -> None:
    console = Console()
    platform_label = "macOS" if host_os == "darwin" else "Linux"
    console.print(
        Panel(
            "\n".join(
                [
                    "DevKit workspace sync is being enabled.",
                    f"The host workspace '{workspace}' will be shared over NFS so DevKit {devkit_ip} can access it.",
                    f"You may be prompted for your {platform_label} host administrator password to install or configure the host NFS service.",
                ]
            ),
            title="DevKit NFS Setup",
            border_style="yellow",
            expand=False,
        )
    )


@dataclass(frozen=True)
class ExistingNfsExport:
    server: str
    export_path: str
    local_export_path: str
    client: str
    client_allowed: bool
    managed_by_sima: bool = False


@dataclass(frozen=True)
class ParsedNfsExport:
    path: Path
    client: str
    options: Tuple[str, ...]
    source: Optional[Path] = None


def _parse_export_line(line: str) -> List[ParsedNfsExport]:
    try:
        parts = shlex.split(line, comments=True)
    except ValueError:
        return []
    if len(parts) < 2:
        return []

    export_path = Path(parts[0])
    exports = []
    for client_spec in parts[1:]:
        client, _, option_text = client_spec.partition("(")
        if not client or client.startswith("-"):
            continue
        options = tuple(
            option.strip()
            for option in option_text.rstrip(")").split(",")
            if option.strip()
        )
        exports.append(ParsedNfsExport(path=export_path, client=client, options=options))
    return exports


def _read_linux_exports() -> List[ParsedNfsExport]:
    exports: List[ParsedNfsExport] = []
    paths = [Path("/etc/exports")]
    exports_d = Path("/etc/exports.d")
    if exports_d.is_dir():
        paths.extend(sorted(exports_d.glob("*")))

    for path in paths:
        if not path.is_file():
            continue
        try:
            logical_line = ""
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.rstrip()
                if line.endswith("\\"):
                    logical_line += line[:-1] + " "
                    continue
                logical_line += line
                exports.extend(
                    ParsedNfsExport(export.path, export.client, export.options, source=path)
                    for export in _parse_export_line(logical_line)
                )
                logical_line = ""
            if logical_line:
                exports.extend(
                    ParsedNfsExport(export.path, export.client, export.options, source=path)
                    for export in _parse_export_line(logical_line)
                )
        except OSError:
            continue
    return exports


def _export_allows_client(client: str, devkit_ip: str) -> bool:
    if client in {"*", "<world>"}:
        return True
    try:
        devkit_addr = ipaddress.ip_address(devkit_ip)
    except ValueError:
        return client == devkit_ip

    try:
        if "/" in client:
            return devkit_addr in ipaddress.ip_network(client, strict=False)
        return devkit_addr == ipaddress.ip_address(client)
    except ValueError:
        # Hostnames, netgroups, and wildcard domains are hard to prove locally.
        # Treat them as usable exports; a later mount will be authoritative.
        return True


def _relative_path(parent: Path, child: Path) -> Optional[Path]:
    try:
        return child.resolve().relative_to(parent.resolve())
    except ValueError:
        return None


def _join_nfs_path(base: str, relative: Path) -> str:
    if str(relative) in {"", "."}:
        return base or "/"
    relative_text = relative.as_posix()
    if base == "/":
        return f"/{relative_text}"
    return f"{base.rstrip('/')}/{relative_text}"


def _is_sima_managed_linux_export(export: ParsedNfsExport) -> bool:
    if export.source is None:
        return False
    return export.source == LINUX_NEAT_EXPORTS_PATH


def _resolve_client_visible_export_path(workspace: Path, matching_export: ParsedNfsExport, exports: List[ParsedNfsExport]) -> str:
    workspace_path = workspace.resolve()

    fsid0_exports = [
        export
        for export in exports
        if any(option.lower() == "fsid=0" for option in export.options)
        and _relative_path(export.path, workspace_path) is not None
    ]
    if fsid0_exports:
        root_export = max(fsid0_exports, key=lambda export: len(str(export.path.resolve())))
        relative_to_root = _relative_path(root_export.path, workspace_path)
        if relative_to_root is not None:
            return _join_nfs_path("/", relative_to_root)

    relative_to_export = _relative_path(matching_export.path, workspace_path)
    if relative_to_export is None:
        return str(workspace_path)
    return _join_nfs_path(str(matching_export.path.resolve()), relative_to_export)


def _detect_existing_linux_nfs_export(workspace: Path, devkit_ip: str, host_ip: str) -> Optional[ExistingNfsExport]:
    if platform.system().lower() != "linux":
        return None

    workspace_path = workspace.resolve()
    exports = [
        export
        for export in _read_linux_exports()
        if _relative_path(export.path, workspace_path) is not None
    ]
    if not exports:
        return None

    allowed_exports = [export for export in exports if _export_allows_client(export.client, devkit_ip)]
    matching_export = max(allowed_exports or exports, key=lambda export: len(str(export.path.resolve())))
    managed_by_sima = not allowed_exports and all(_is_sima_managed_linux_export(export) for export in exports)
    return ExistingNfsExport(
        server=host_ip,
        export_path=_resolve_client_visible_export_path(workspace_path, matching_export, exports),
        local_export_path=str(matching_export.path.resolve()),
        client=matching_export.client,
        client_allowed=bool(allowed_exports),
        managed_by_sima=managed_by_sima,
    )


def _configure_nfs_export(host_dir: Path, devkit_ip: Optional[str], host_os: str, host_ip: str) -> None:
    host_path = str(host_dir.resolve())
    if host_os == "darwin":
        uid = os.getuid()
        gid = os.getgid()
        if devkit_ip:
            line = f"{host_path} -alldirs -mapall={uid}:{gid} {devkit_ip}"
        else:
            iface = ipaddress.IPv4Interface(f"{host_ip}/24")
            network = str(iface.network.network_address)
            mask = str(iface.network.netmask)
            line = f"{host_path} -alldirs -mapall={uid}:{gid} -network {network} -mask {mask}"
        script = (
            "set -eu; "
            "touch /etc/exports; "
            f"tmpf=$(mktemp); awk -v p={shlex.quote(host_path)} '"
            "/^[[:space:]]*#/ || NF==0 { print; next } "
            "{ path=$1 } "
            "(path==p) || (index(path, p \"/\")==1) || (index(p, path \"/\")==1) { next } "
            "{ print }' /etc/exports > \"$tmpf\"; "
            f"echo {shlex.quote(line)} >> \"$tmpf\"; "
            "cp \"$tmpf\" /etc/exports; rm -f \"$tmpf\"; "
            "nfsd checkexports; "
            "if ! nfsd restart; then "
            "echo 'Warning: nfsd restart failed; checking whether nfsd is already running.' >&2; "
            "nfsd status | grep -q 'nfsd is running'; "
            "fi"
        )
        subprocess.run(["sudo", "sh", "-c", script], check=True)
        return

    if host_os == "linux":
        exportfs_cmd = _find_executable("exportfs")
        if not exportfs_cmd:
            _install_linux_nfs_server()
            exportfs_cmd = _find_executable("exportfs")
        if not exportfs_cmd:
            raise RuntimeError(
                "Linux NFS server tooling install completed, but `exportfs` is still not available. "
                f"{_linux_nfs_install_hint()}"
            )
        systemctl_cmd = _find_executable("systemctl") or "systemctl"
        client = devkit_ip if devkit_ip else "*"
        line = f"{host_path} {client}(rw,sync,no_subtree_check,no_root_squash,insecure)"
        script = (
            "set -eu; "
            "mkdir -p /etc/exports.d; "
            "clean_exports_file() { "
            "f=\"$1\"; [ -f \"$f\" ] || return 0; tmpf=$(mktemp); "
            f"awk -v p={shlex.quote(host_path)} '"
            "/^[[:space:]]*#/ || NF==0 { print; next } "
            "{ path=$1 } "
            "(path==p) || (index(path, p \"/\")==1) || (index(p, path \"/\")==1) { next } "
            "{ print }' \"$f\" > \"$tmpf\"; "
            "cp \"$tmpf\" \"$f\"; rm -f \"$tmpf\"; "
            "}; "
            "touch /etc/exports; "
            "clean_exports_file /etc/exports; "
            "for f in /etc/exports.d/*; do [ -f \"$f\" ] || continue; clean_exports_file \"$f\"; done; "
            f"echo {shlex.quote(line)} > /etc/exports.d/neat-sdk.exports; "
            f"{shlex.quote(exportfs_cmd)} -ra; "
            f"({shlex.quote(systemctl_cmd)} restart nfs-server || "
            f"{shlex.quote(systemctl_cmd)} restart nfs-kernel-server || true)"
        )
        subprocess.run(["sudo", "sh", "-c", script], check=True)
        return

    raise RuntimeError("Host NFS setup is only implemented for macOS/Linux")


def _configure_devkit_shared_network_for_setup(
    devkit_ip: str,
    noninteractive: bool = False,
    persistent_network_profile: bool = False,
) -> None:
    configure_linux_shared_devkit_network(devkit_ip)
    maybe_install_nm_shared_dispatcher_repair(
        devkit_ip,
        noninteractive=noninteractive,
        persistent_network_profile=persistent_network_profile,
    )


def _setup_devkit_share(
    devkit_ip: str,
    workspace: str,
    selected_images: List[str],
    noninteractive: bool = False,
    yes_to_all: bool = False,
    persistent_network_profile: bool = False,
):
    if not devkit_ip:
        return {}
    if not any(is_neat_sdk_image(image) for image in selected_images):
        print("ℹ️  Ignoring --devkit because selected images do not match a supported Neat SDK pattern: ghcr.io/sima-neat/sdk*, local sdk*, or legacy ghcr.io/sima-neat/elxr*.")
        return {}

    host_os = platform.system().lower()
    host_dir = Path(workspace)
    host_ip, auto_iface, auto_candidates = _detect_routed_host_ip(devkit_ip)
    print("✅ Fetched Routed Host IP details: {} (Interface: {}, Candidates: {})".format(host_ip, auto_iface, auto_candidates))

    existing_export = _detect_existing_linux_nfs_export(host_dir, devkit_ip, host_ip)
    if existing_export:
        print(
            "ℹ️  Workspace is already covered by an existing NFS export: {} -> {}.".format(
                existing_export.local_export_path,
                existing_export.client,
            )
        )
        if not existing_export.client_allowed:
            if existing_export.managed_by_sima:
                print(
                    "ℹ️  Existing sima-cli-managed NFS export allows {}, updating it for DevKit {}.".format(
                        existing_export.client,
                        devkit_ip,
                    )
                )
                _print_devkit_nfs_banner(workspace, devkit_ip, host_os)
                _configure_nfs_export(host_dir, devkit_ip, host_os, host_ip)
                _configure_devkit_shared_network_for_setup(
                    devkit_ip,
                    noninteractive=noninteractive,
                    persistent_network_profile=persistent_network_profile,
                )
                print("✅ Host NFS export configured for workspace {} -> {}".format(workspace, devkit_ip))
                return {
                    "devkit_ip": devkit_ip,
                    "host_ip": host_ip,
                    "workspace": workspace,
                    "host_platform": host_os,
                    "bootstrap_interactive": not noninteractive,
                    "noninteractive": noninteractive,
                }
            raise RuntimeError(
                "Workspace is under an existing unmanaged NFS export, but DevKit {} is not allowed "
                "by the export client '{}'. Ask an admin to add an export entry that covers {} for the "
                "DevKit IP/subnet, then rerun setup. sima-cli will not try to modify this unmanaged "
                "export without permission.".format(
                    devkit_ip,
                    existing_export.client,
                    existing_export.local_export_path,
                )
            )
        print(
            "ℹ️  Reusing existing NFS export for DevKit sync: {}:{}.".format(
                existing_export.server,
                existing_export.export_path,
            )
        )
        _configure_devkit_shared_network_for_setup(
            devkit_ip,
            noninteractive=noninteractive,
            persistent_network_profile=persistent_network_profile,
        )
        return {
            "devkit_ip": devkit_ip,
            "host_ip": existing_export.server,
            "workspace": existing_export.export_path,
            "host_platform": host_os,
            "bootstrap_interactive": not noninteractive,
            "noninteractive": noninteractive,
        }

    _print_devkit_nfs_banner(workspace, devkit_ip, host_os)
    _configure_nfs_export(host_dir, devkit_ip, host_os, host_ip)
    _configure_devkit_shared_network_for_setup(
        devkit_ip,
        noninteractive=noninteractive,
        persistent_network_profile=persistent_network_profile,
    )
    print("✅ Host NFS export configured for workspace {} -> {}".format(workspace, devkit_ip))

    if auto_iface != "auto":
        print("ℹ️  Detected host IP for DevKit sync: {} (interface: {})".format(host_ip, auto_iface))
        if len(auto_candidates) > 1:
            others = ", ".join(["{}:{}".format(i, ip) for i, ip in auto_candidates[1:]])
            print("ℹ️  Multiple physical interfaces detected; using first. Others: {}".format(others))
    else:
        print("ℹ️  Detected host IP for DevKit sync: {} (auto)".format(host_ip))

    return {
        "devkit_ip": devkit_ip,
        "host_ip": host_ip,
        "workspace": workspace,
        "host_platform": host_os,
        "bootstrap_interactive": not noninteractive,
        "noninteractive": noninteractive,
    }


def _is_x86_platform() -> bool:
    machine = platform.machine().lower()
    return machine in {"x86_64", "amd64", "i386", "i686", "x86"}


def _is_arm64_platform() -> bool:
    machine = platform.machine().lower()
    return machine in {"aarch64", "arm64"}


def _version_at_least(version: str, minimum: str) -> bool:
    def parts(value: str) -> List[int]:
        match = re.match(r"^\s*(\d+(?:\.\d+)*)", value or "")
        if not match:
            return []
        return [int(part) for part in match.group(1).split(".")]

    current_parts = parts(version)
    minimum_parts = parts(minimum)
    if not current_parts or not minimum_parts:
        return False

    length = max(len(current_parts), len(minimum_parts))
    current_parts.extend([0] * (length - len(current_parts)))
    minimum_parts.extend([0] * (length - len(minimum_parts)))
    return current_parts >= minimum_parts


def _extract_version_from_image_ref(image: str) -> str:
    tag = (image or "").rsplit(":", 1)[-1]
    match = re.search(r"\d+(?:\.\d+){1,2}", tag)
    return match.group(0) if match else ""


def _supports_model_sdk_extension_mount(selected_images: List[str]) -> bool:
    neat_images = [image for image in selected_images if is_neat_sdk_image(image)]
    if not neat_images:
        return False
    if _is_x86_platform():
        return True
    if not _is_arm64_platform():
        return False

    versions = [_extract_version_from_image_ref(image) for image in neat_images]
    known_versions = [version for version in versions if version]
    if not known_versions:
        return True
    return any(_version_at_least(version, "2.1.1") for version in known_versions)


MODEL_SDK_EXTENSION_REQUIRED_GB = 20


def _format_gb(byte_count: int) -> str:
    return f"{byte_count / (1024 ** 3):.1f} GB"


def _ensure_writable_sdk_extensions_dir(path: Path) -> Path:
    resolved = Path(os.path.realpath(str(path)))
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / ".sima-cli-write-test"
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok\n")
        probe.unlink()
    except OSError as e:
        raise RuntimeError(
            f"SDK extensions directory is not writable: {resolved}. "
            "Choose another directory where your user can create files."
        ) from e
    return resolved


def _setup_sdk_extensions(
    selected_images: List[str],
    noninteractive: bool = False,
    yes_to_all: bool = False,
    for_model_compiler: bool = True,
) -> str:
    if not _supports_model_sdk_extension_mount(selected_images):
        if any(is_neat_sdk_image(image) for image in selected_images):
            click.secho("⚠️  SDK extensions mount is not available on ARM64 platforms before Neat SDK 2.1.1; skipping /sdk-extensions mount.", fg="yellow")
        return ""

    default_extensions_dir = Path.home() / "sima-sdk-extensions"
    if for_model_compiler:
        home_usage = shutil.disk_usage(Path.home())
        click.echo(
            "ℹ️  Model Compiler extension may require about "
            f"{MODEL_SDK_EXTENSION_REQUIRED_GB} GB of additional disk space. "
            f"Available under {Path.home()}: {_format_gb(home_usage.free)}."
        )
        if home_usage.free < MODEL_SDK_EXTENSION_REQUIRED_GB * 1024 ** 3:
            click.secho(
                "⚠️  The default home filesystem may not have enough free space for the Model Compiler extension.",
                fg="yellow",
            )

    if noninteractive or yes_to_all:
        extensions_dir = default_extensions_dir
        click.echo(f"ℹ️  Using default SDK extensions directory: {extensions_dir}")
        extensions_dir = _ensure_writable_sdk_extensions_dir(extensions_dir)
    else:
        while True:
            response = input(f"Enter SDK extensions directory [{default_extensions_dir}]: ").strip()
            extensions_dir = Path(response).expanduser() if response else default_extensions_dir
            try:
                extensions_dir = _ensure_writable_sdk_extensions_dir(extensions_dir)
                break
            except RuntimeError as e:
                click.secho(f"⚠️  {e}", fg="yellow")
    print(f"✅ SDK extensions directory configured: {extensions_dir}")
    return str(extensions_dir)


def _container_exists(container_name: str) -> bool:
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return container_name in result.stdout.splitlines()


def _get_container_host_port(container_name: str, container_port: int = 8084) -> Optional[int]:
    format_expr = "{{(index (index .NetworkSettings.Ports \"%d/tcp\") 0).HostPort}}" % container_port
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            format_expr,
            container_name,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode != 0:
        return None

    value = (inspect.stdout or "").strip()
    if not value.isdigit():
        return None

    port = int(value)
    if port <= 0:
        return None
    return port


def _refresh_mpk_config_json(
    selected_images: List[str],
    noninteractive: bool = False,
    yes_to_all: bool = False,
) -> None:
    """
    Recreate config.json based on the current SDK selection and copy it into
    the MPK container so MPK always sees fresh Yocto/eLxr mappings.
    """
    if any(is_neat_sdk_image(image) for image in selected_images):
        return

    all_sdk_containers = get_all_containers(running_containers_only=False)

    # Discover MPK containers globally, not only from the current selection.
    existing_mpk_containers = []
    for c in all_sdk_containers:
        name = c.get("Names") or c.get("Name") or c.get("name") or ""
        image = c.get("Image") or c.get("image") or ""
        if "mpk" in name.lower() or "mpk" in image.lower():
            existing_mpk_containers.append(name)

    # Keep fallback compatibility with name sanitization-based detection.
    if not existing_mpk_containers:
        mpk_images = [
            image
            for image in selected_images
            if IMAGE_CONFIG.get(extract_short_name(image), {}).get("port_mapping_required")
        ]
        existing_mpk_containers = [
            sanitize_container_name(image)
            for image in mpk_images
            if _container_exists(sanitize_container_name(image))
        ]

    if not existing_mpk_containers:
        print("ℹ️  No MPK container present; skipping config sync.")
        return

    # Remove duplicates while preserving order.
    seen = set()
    existing_mpk_containers = [
        c for c in existing_mpk_containers if not (c in seen or seen.add(c))
    ]

    if len(existing_mpk_containers) == 1:
        mpk_container = existing_mpk_containers[0]
    elif noninteractive or yes_to_all:
        mpk_container = sorted(existing_mpk_containers)[0]
        print(
            "ℹ️  Multiple MPK containers detected; non-interactive mode selected "
            f"'{mpk_container}' for config sync."
        )
    else:
        selection = select_containers(existing_mpk_containers, single_select=True)
        if not selection:
            print("ℹ️  No MPK container selected; skipping config sync.")
            return
        mpk_container = selection[0] if isinstance(selection, list) else selection

    mpk_port = _get_container_host_port(mpk_container, container_port=8084)
    if mpk_port is None:
        print(f"⚠️  Could not resolve host port for '{mpk_container}'. Skipping MPK config sync.")
        return

    # Build config from selected images plus any existing Yocto/eLxr/Neat SDK images,
    # so setup of one tool still keeps MPK config aware of the other.
    config_image_sources = list(selected_images)
    for c in all_sdk_containers:
        image = c.get("Image") or c.get("image") or ""
        if extract_short_name(image) in {"yocto", "elxr", "neat"}:
            config_image_sources.append(image)

    # Remove duplicates while preserving order.
    seen_sources = set()
    config_image_sources = [
        img for img in config_image_sources if not (img in seen_sources or seen_sources.add(img))
    ]

    config_path = create_config_json(
        file_path="config.json",
        selected_images=config_image_sources,
        port=mpk_port,
    )
    if not config_path:
        print("⚠️  Failed to regenerate config.json for MPK sync.")
        return

    subprocess.run(
        ["docker", "exec", "-u", "root", mpk_container, "mkdir", "-p", "/home/docker/.simaai"],
        check=False,
    )
    subprocess.run(
        ["docker", "cp", config_path, f"{mpk_container}:/home/docker/.simaai/config.json"],
        check=True,
    )
    print(f"✅ MPK config.json refreshed in '{mpk_container}' using host port {mpk_port}.")


def _reject_if_windows_native_neat_sdk(
    selected_images: List[str],
    console: Console,
) -> None:
    """On native Windows + Neat SDK, abort and direct the user into WSL2.

    Docker Desktop on Windows runs Linux containers in a WSL2 VM. Any path on
    a Windows drive (C:\\, D:\\, /mnt/c) is exposed to that VM via the 9p
    filesystem, which is ~10-30× slower than ext4 for many-small-file
    workloads — SDK install, pip install, model compilation, and container
    I/O all suffer materially. Legacy SDKs (mpk/yocto/elxr/modelsdk) on
    Windows are intentionally left alone here.

    sys.platform == "win32" is only True on native Windows Python; Python
    invoked from inside a WSL2 distro reports "linux" and will not trigger
    this rejection.
    """
    if check_os() != "windows":
        return
    if not any(is_neat_sdk_image(img) for img in selected_images):
        return

    console.print(
        Panel(
            "[red]Neat SDK setup is not supported when sima-cli is run directly on Windows.[/red]\n\n"
            "Docker Desktop on Windows uses a WSL2 backend, and files on Windows drives "
            "(C:\\, D:\\) are accessed through the [bold]9p[/bold] protocol — typically "
            "[bold]10-30× slower[/bold] than native Linux ext4. SDK install, pip install, "
            "model compilation, and container I/O are all unusably slow as a result.\n\n"
            "[green]Required setup:[/green] run sima-cli from inside a WSL2 Ubuntu distro.\n"
            "  1. From PowerShell:  [cyan]wsl --install -d Ubuntu[/cyan]\n"
            "  2. Open Ubuntu, then install sima-cli following the official guide:\n"
            "     [cyan]https://docs.sima.ai/pages/sima_cli/main.html[/cyan]\n"
            "  3. Keep working files under [cyan]~/[/cyan] — do [bold]NOT[/bold] use [cyan]/mnt/c/[/cyan]\n"
            "     ([cyan]/mnt/c[/cyan] is the same 9p mount and just as slow as running on Windows natively).\n"
            "  4. Re-run [cyan]sima-cli sdk setup[/cyan] from inside Ubuntu\n\n"
            "Docker Desktop's WSL integration shares the same engine, so no separate Docker install is needed.",
            title="🛑 Unsupported — Windows Native",
            border_style="red",
            expand=False,
        )
    )
    sys.exit(1)


def _warn_if_snap_docker_neat_sdk(
    selected_images: List[str],
    console: Console,
) -> None:
    if not any(is_neat_sdk_image(img) for img in selected_images):
        return
    if not is_snap_docker_cli():
        return

    console.print(
        Panel(
            "[bold red]Snap Docker detected.[/bold red]\n\n"
            "Neat SDK setup can run with Snap Docker, but Snap confinement may make "
            "container file copies slower and can restrict access to host paths such as "
            "[cyan]/tmp[/cyan] and hidden directories under [cyan]$HOME[/cyan]. This can "
            "limit SDK setup functionality and make container operations noticeably slower.\n\n"
            "[bold]Recommended:[/bold] switch to the official Docker Engine packages from "
            "Docker's apt repository before using Neat SDK setup.",
            title="Snap Docker May Be Slow or Limited",
            border_style="red",
            expand=False,
        )
    )


def setup_and_start(
    noninteractive: bool = False,
    start_only: bool = False,
    yes_to_all: bool = False,
    devkit_ip: str = "",
    no_insight: bool = False,
    no_model_sdk: bool = False,
    minimal: bool = False,
    workspace: Optional[str] = None,
    persistent_network_profile: bool = False,
    image_selectors=(),
):
    """Main entry for SDK setup and container start."""

    console = Console()

    if not start_only:
        console.print(Panel("🔧 SiMa.ai SDK Setup", border_style="cyan", expand=False))
        ensure_simasdkbridge_network()
        syscheck(force_install=yes_to_all, noninteractive=noninteractive)

    images = get_local_sima_images()
    if image_selectors:
        selected_images = filter_images_by_selector(images, image_selectors)
    else:
        selected_images = prompt_image_selection(images, noninteractive)

    if not start_only:
        _reject_if_windows_native_neat_sdk(selected_images, console)
        if any(is_neat_sdk_image(img) for img in selected_images):
            _warn_if_snap_docker_neat_sdk(selected_images, console)
            ensure_colima_resources_for_neat_sdk(
                yes_to_all=yes_to_all,
                noninteractive=noninteractive,
            )
            if devkit_ip:
                warn_if_colima_devkit_network_may_need_bridged(
                    devkit_ip,
                    noninteractive=noninteractive,
                    yes_to_all=yes_to_all,
                )


    # Step 2: Check running containers
    print("\n🔍 Checking for running SDK containers...")
    container_statuses = get_container_status()

    if container_statuses:
        count = len(container_statuses)
        print(f"✅ Found {count} SDK container{'s' if count > 1 else ''}:")
        for cname, status in container_statuses.items():
            print(f"   • {cname:<30} | {status}")
    else:
        print("ℹ️  No Running SDK containers found.")

    # ──────────────────────────────────────────────
    # Start containers
    # ──────────────────────────────────────────────
    workspace = get_workspace(
        yes_to_all,
        noninteractive=noninteractive,
        workspace_override=workspace,
    )
    uid = os.getuid() if hasattr(os, "getuid") else 900
    gid = os.getgid() if hasattr(os, "getgid") else 900
    devkit_env = _setup_devkit_share(
        devkit_ip,
        workspace,
        selected_images,
        noninteractive=noninteractive,
        yes_to_all=yes_to_all,
        persistent_network_profile=persistent_network_profile,
    )
    skip_model_sdk = no_model_sdk or minimal
    skip_insight = no_insight or minimal
    if minimal:
        sdk_extensions_dir = ""
    else:
        sdk_extensions_dir = _setup_sdk_extensions(
            selected_images,
            noninteractive=noninteractive,
            yes_to_all=yes_to_all,
            for_model_compiler=not skip_model_sdk,
        )
    if skip_model_sdk and any(is_neat_sdk_image(img) for img in selected_images):
        reason = "--minimal" if minimal else "--no-model-compiler"
        click.echo(f"ℹ️  Skipping Model Compiler extension setup because {reason} was specified.")
    if minimal and any(is_neat_sdk_image(img) for img in selected_images):
        click.echo("ℹ️  Skipping Insight setup because --minimal was specified.")
    
    for img in selected_images:
        container_name = sanitize_container_name(img)
        print_section(f"🔄 CONTAINER START SEQUENCE for {container_name}")
        existing_container = confirm_to_remove_exiting_container(
            container_name,
            yes_to_all=(yes_to_all or noninteractive),
        )

        if existing_container == None:
            # Get image configuration
            config = IMAGE_CONFIG.get(extract_short_name(img), {"privileged": False, "port_mapping_required": False})

            # Dynamically allocate a free port if required
            port = find_available_ports(1)[0] if config["port_mapping_required"] else 0

            # config.json is relevant to MPK only; use the mapped host port.
            if config["port_mapping_required"]:
                create_config_json(file_path="config.json", selected_images=selected_images, port=port)

            start_docker_container(
                uid=uid,
                gid=gid,
                port=port,
                workspace=workspace,
                image=img,
                privileged=config["privileged"],
                port_mapping_required=config["port_mapping_required"],
                devkit_env=devkit_env,
                sdk_extensions_dir=sdk_extensions_dir,
                noninteractive=noninteractive,
                yes_to_all=yes_to_all,
                no_insight=skip_insight,
                no_model_sdk=skip_model_sdk,
                minimal=minimal,
            )
        else:
            if skip_insight and is_neat_sdk_image(img):
                option = "--minimal" if minimal else "--no-insight"
                raise RuntimeError(
                    f"Cannot apply {option} to an existing Neat SDK container because Docker "
                    "port mappings are immutable. Remove and recreate the container when prompted, "
                    f"or run: docker rm -f {existing_container}"
                )

            if not is_container_running(existing_container):
                if is_neat_sdk_image(img):
                    ensure_existing_neat_container_startable(existing_container)
                subprocess.run(["docker", "start", existing_container], check=True)

            if check_os() in ["linux", "macos"]:
                login_name, user_uid, user_gid = detect_current_user()
                configure_container_user(existing_container, login_name, user_uid, user_gid)

            if devkit_env and is_neat_sdk_image(img):
                if not skip_insight:
                    # Re-issue the mounted HTTPS cert for the (possibly new) host
                    # IP before bootstrap restarts neat-insight, so the browser
                    # doesn't hit a SAN mismatch ("connection is not private")
                    # after a re-point.
                    from sima_cli.sdk.neat import refresh_neat_certificates
                    refresh_neat_certificates(
                        workspace, existing_container, devkit_env,
                        yes_to_all=yes_to_all, noninteractive=noninteractive,
                    )
                bootstrap_devkit_container(existing_container, devkit_env)

            if len(selected_images) == 1:
                exec_cmd = ["docker", "exec", "-it"]
                fallback_cmd = ["docker", "exec", "-it", existing_container, "bash", "-l"]
                if check_os() in ["linux", "macos"]:
                    exec_cmd.extend(["-u", detect_current_user()[0]])
                exec_cmd.extend([existing_container, "bash", "-l"])
                first = subprocess.run(exec_cmd, check=False)
                if (
                    first.returncode != 0
                    and check_os() in ["linux", "macos"]
                    and container_user_mapping_unavailable(existing_container, detect_current_user()[0])
                ):
                    print("⚠️ User mapping unavailable in this container; retrying without -u.")
                    subprocess.run(fallback_cmd, check=False)

    _refresh_mpk_config_json(selected_images, noninteractive=noninteractive, yes_to_all=yes_to_all)
    console.print("\n[bold green]✅ All selected containers started successfully![/bold green]")

if __name__ == "__main__":
    setup_and_start()
