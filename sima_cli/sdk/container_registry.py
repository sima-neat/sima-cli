import ipaddress
import json
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


REGISTRY_CONTAINER_NAME = "sima-sdk-registry"
REGISTRY_IMAGE = "registry:2"
REGISTRY_MANAGED_LABEL = "com.sima.sdk.service"
REGISTRY_MANAGED_VALUE = "container-registry"
REGISTRY_PORT_LABEL = "com.sima.sdk.registry.port"
REGISTRY_BIND_IP_LABEL = "com.sima.sdk.registry.bind-ip"
REGISTRY_VOLUME_NAME = "sima-sdk-registry-data"
DEFAULT_REGISTRY_PORT = 5050
REGISTRY_READY_ATTEMPTS = 20
REGISTRY_READY_DELAY_SECONDS = 0.25


@dataclass(frozen=True)
class ContainerRegistryConfig:
    port: int
    sdk_address: str
    devkit_address: str


def _inspect_registry(ignore_unmanaged: bool = False) -> Optional[dict]:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{index .Config.Labels \"com.sima.sdk.service\"}}|"
            "{{index .Config.Labels \"com.sima.sdk.registry.port\"}}|"
            "{{index .Config.Labels \"com.sima.sdk.registry.bind-ip\"}}|"
            "{{.State.Running}}|"
            "{{json .NetworkSettings.Ports}}",
            REGISTRY_CONTAINER_NAME,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    parts = result.stdout.strip().split("|", 4)
    if len(parts) != 5:
        raise RuntimeError(
            "The existing local registry has invalid setup information. "
            "Remove it or choose a different container name."
        )
    managed_value, port_text, bind_ip, running_text, ports_json = parts
    if managed_value != REGISTRY_MANAGED_VALUE:
        if ignore_unmanaged:
            return None
        raise RuntimeError(
            f"A Docker container named '{REGISTRY_CONTAINER_NAME}' already exists, but it was not "
            "created by sima-cli. Rename or remove that container, then run SDK setup again."
        )
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError(
            "The existing local registry does not record a valid port. "
            "Remove it and run SDK setup again."
        ) from exc
    registry_bindings = (json.loads(ports_json or "null") or {}).get("5000/tcp") or []
    return {
        "port": port,
        "bind_ip": bind_ip if bind_ip not in ("", "<no value>") else None,
        "running": running_text.lower() == "true",
        "published": {f"{item['HostIp']}:{item['HostPort']}" for item in registry_bindings},
    }


def _publishes_expected_ports(existing: dict, port: int, host_ip: str) -> bool:
    # Docker publishes ports only when the container starts. A registry that
    # started before Colima added the host address runs without any ports.
    expected = {f"127.0.0.1:{port}", f"{host_ip}:{port}"}
    return existing["running"] and expected <= existing["published"]


def _registry_bind_ip_is_available(host_ip: str) -> bool:
    # A bridged Colima address belongs to the VM rather than macOS, so recognize
    # the active VM address before checking addresses assigned to the host.
    if platform.system() == "Darwin":
        from sima_cli.sdk.preinstall import (
            _colima_network_config,
            _detect_colima_profile,
            _is_docker_using_colima,
        )

        if _is_docker_using_colima():
            network = _colima_network_config(_detect_colima_profile())
            if host_ip == str(network.get("ip_address") or "").strip():
                return True

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host_ip, 0))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def repair_existing_container_registry() -> None:
    existing = _inspect_registry(ignore_unmanaged=True)
    if not existing or not existing["bind_ip"]:
        return
    if not _publishes_expected_ports(existing, existing["port"], existing["bind_ip"]):
        if not _registry_bind_ip_is_available(existing["bind_ip"]):
            print(
                f"⚠️ Local container registry repair deferred because its recorded "
                f"DevKit-facing address {existing['bind_ip']} is not currently available. "
                "Reconnect that interface and rerun SDK setup."
            )
            return
        ensure_container_registry(existing["bind_ip"], requested_port=existing["port"])


def existing_container_registry_port() -> Optional[int]:
    existing = _inspect_registry()
    return int(existing["port"]) if existing else None


def resolve_container_registry_bind_ip(host_ip: str, devkit_ip: str = "") -> str:
    """Return the Docker-host address on the DevKit-facing network path."""
    if platform.system() != "Darwin":
        return host_ip

    # Colima's Docker daemon runs in a VM. Direct LAN routes need the VM's
    # bridged address. macOS Internet Sharing and routed tunnels use Colima's
    # host-address forwarding so Docker can bind the Mac's DevKit-facing IP.
    from sima_cli.sdk.preinstall import (
        _boolish,
        _colima_network_config,
        _colima_vm_has_host_address,
        _colima_vm_tunnel_route_ready,
        _detect_colima_profile,
        _is_colima_tunnel_network_suitable,
        _is_docker_using_colima,
        _is_tunnel_route_interface,
        _resolve_safe_colima_bridge_interface,
        _route_interface_for_target,
    )

    if not _is_docker_using_colima():
        return host_ip

    profile = _detect_colima_profile()
    network = _colima_network_config(profile)
    bind_ip = str(network.get("ip_address") or "").strip()
    mode = str(network.get("mode") or "").strip().lower()
    configured_interface = str(network.get("interface") or "en0").strip()
    if devkit_ip:
        route_interface = _route_interface_for_target(devkit_ip)
        if _is_tunnel_route_interface(route_interface):
            if not _is_colima_tunnel_network_suitable(network, host_ip):
                raise RuntimeError(
                    "The local registry needs Colima shared networking with host-address "
                    "forwarding for the Cloudex tunnel route. Rerun SDK setup and allow "
                    "sima-cli to restart Colima."
                )
            if not host_ip:
                raise RuntimeError(
                    "Could not determine the Mac source address for the Cloudex DevKit route."
                )
            if not _colima_vm_has_host_address(profile, host_ip):
                raise RuntimeError(
                    f"Colima has not replicated current Cloudex tunnel address {host_ip}. "
                    "Rerun SDK setup after connecting Cloudex."
                )
            if not _colima_vm_tunnel_route_ready(profile, host_ip, devkit_ip):
                raise RuntimeError(
                    f"Colima is treating Cloudex DevKit {devkit_ip} as a local VM address. "
                    "Rerun SDK setup to repair the tunnel route."
                )
            return host_ip
        expected_interface = _resolve_safe_colima_bridge_interface(route_interface)
        if not expected_interface:
            raise RuntimeError(
                "The local registry could not identify a safe physical interface for the DevKit route."
            )
        if route_interface.strip().lower().startswith("bridge"):
            if (
                _boolish(network.get("address"))
                or mode != "shared"
                or not _boolish(network.get("host_addresses"))
                or host_ip not in network.get("forwarded_host_ips", [])
            ):
                raise RuntimeError(
                    "The local registry needs Colima shared networking with host-address forwarding "
                    "for a DevKit connected through macOS Internet Sharing. Rerun SDK setup and "
                    "allow sima-cli to recreate the profile."
                )
            return host_ip
        if configured_interface != expected_interface:
            raise RuntimeError(
                f"The Colima profile is bridged to {configured_interface}, but the DevKit route uses "
                f"{expected_interface}. Rerun SDK setup and allow sima-cli to recreate the profile."
            )
    if not _boolish(network.get("address")) or mode != "bridged" or not bind_ip:
        raise RuntimeError(
            "The local registry needs a Colima address bridged to the DevKit-facing network. "
            "Rerun SDK setup and allow sima-cli to restart Colima in bridged mode."
        )
    return bind_ip


def _host_port_is_available(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def find_available_container_registry_port(start_port: int = DEFAULT_REGISTRY_PORT) -> int:
    for port in range(start_port, 65536):
        if _host_port_is_available(port):
            if port != start_port:
                print(f"ℹ️  Port {start_port} is busy, so the local registry will use port {port}.")
            return port
    raise RuntimeError(
        f"Could not find a free port for the local container registry at or above {start_port}."
    )


def _require_available_host_port(port: int) -> None:
    if not _host_port_is_available(port):
        raise RuntimeError(
            f"Port {port} is already in use on this host. Run SDK setup again with "
            f"--container-registry-port <free-port>."
        )


def _wait_for_registry(port: int) -> None:
    address = f"http://127.0.0.1:{port}/v2/"
    last_error: Optional[Exception] = None
    for _ in range(REGISTRY_READY_ATTEMPTS):
        try:
            with urllib.request.urlopen(address, timeout=2) as response:
                if response.status == 200:
                    return
                last_error = RuntimeError(
                    f"the registry returned HTTP status {response.status}"
                )
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(REGISTRY_READY_DELAY_SECONDS)
    raise RuntimeError(
        f"The local container registry started, but it did not become ready on port {port}. "
        f"Check 'docker logs {REGISTRY_CONTAINER_NAME}' and run SDK setup again. "
        f"Last error: {last_error or 'no response'}"
    )


def ensure_container_registry(host_ip: str, requested_port: Optional[int] = None) -> ContainerRegistryConfig:
    if not host_ip:
        raise RuntimeError("Could not determine the SDK host address for the DevKit registry.")
    try:
        parsed_host_ip = ipaddress.ip_address(host_ip)
    except ValueError as exc:
        raise RuntimeError(f"The DevKit-facing registry address is not a valid IP address: {host_ip}") from exc
    if parsed_host_ip.version != 4 or parsed_host_ip.is_unspecified:
        raise RuntimeError(
            "The DevKit-facing registry address must be a specific IPv4 address."
        )

    existing = _inspect_registry()
    port = requested_port or (
        int(existing["port"]) if existing else find_available_container_registry_port()
    )
    if not 1 <= port <= 65535:
        raise RuntimeError("The container registry port must be between 1 and 65535.")

    port_changed = bool(existing and int(existing["port"]) != port)
    bind_ip_changed = bool(existing and existing.get("bind_ip") != host_ip)
    if existing and (port_changed or bind_ip_changed):
        if port_changed:
            _require_available_host_port(port)
        if port_changed and bind_ip_changed:
            change = f"port to {port} and DevKit-facing address to {host_ip}"
        elif port_changed:
            change = f"port from {existing['port']} to {port}"
        else:
            change = f"DevKit-facing address to {host_ip}"
        recreate_reason = f"Changing the local container registry {change}"
    elif existing and not _publishes_expected_ports(existing, port, host_ip):
        recreate_reason = (
            f"Recreating the local container registry so it publishes port {port} "
            f"on 127.0.0.1 and {host_ip}"
        )
    else:
        recreate_reason = None

    if recreate_reason:
        print(f"ℹ️  {recreate_reason}. Stored images will be kept.")
        subprocess.run(
            ["docker", "rm", "-f", REGISTRY_CONTAINER_NAME],
            text=True,
            capture_output=True,
            check=True,
        )
        existing = None

    # A recreated registry keeps its own port; probing it right after removal
    # can fail on connections the old container left in TIME_WAIT.
    if not existing and not recreate_reason:
        _require_available_host_port(port)

    if existing:
        print("ℹ️  Reusing the existing local container registry.")
    else:
        print("ℹ️  Setting up a local container registry for SDK-built images.")
        subprocess.run(
            ["docker", "volume", "create", REGISTRY_VOLUME_NAME],
            text=True,
            capture_output=True,
            check=True,
        )
        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                REGISTRY_CONTAINER_NAME,
                "--restart",
                "unless-stopped",
                "--label",
                f"{REGISTRY_MANAGED_LABEL}={REGISTRY_MANAGED_VALUE}",
                "--label",
                f"{REGISTRY_PORT_LABEL}={port}",
                "--label",
                f"{REGISTRY_BIND_IP_LABEL}={host_ip}",
                "-p",
                f"127.0.0.1:{port}:5000",
                *([] if host_ip == "127.0.0.1" else ["-p", f"{host_ip}:{port}:5000"]),
                "-v",
                f"{REGISTRY_VOLUME_NAME}:/var/lib/registry",
                REGISTRY_IMAGE,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            subprocess.run(
                ["docker", "rm", "-f", REGISTRY_CONTAINER_NAME],
                text=True,
                capture_output=True,
                check=False,
            )
            detail = (result.stderr or result.stdout or "Docker could not start the registry.").strip()
            raise RuntimeError(
                f"Could not start the local container registry on port {port}. {detail}"
            )

    _wait_for_registry(port)
    sdk_address = f"localhost:{port}"
    devkit_address = f"{host_ip}:{port}"
    print(f"✅ The SDK can push container images to {sdk_address}.")
    print(f"✅ The DevKit can pull the same images from {devkit_address}.")
    print("ℹ️  The registry is available only on this computer and the DevKit-facing network path.")
    print(
        "ℹ️  Inside the SDK, add --push and tag the image as "
        f"{sdk_address}/<image>:<tag>."
    )
    return ContainerRegistryConfig(
        port=port,
        sdk_address=sdk_address,
        devkit_address=devkit_address,
    )
