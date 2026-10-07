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
REGISTRY_VOLUME_NAME = "sima-sdk-registry-data"
DEFAULT_REGISTRY_PORT = 5050
REGISTRY_READY_ATTEMPTS = 20
REGISTRY_READY_DELAY_SECONDS = 0.25


@dataclass(frozen=True)
class ContainerRegistryConfig:
    port: int
    sdk_address: str
    devkit_address: str


def _inspect_registry() -> Optional[dict]:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{index .Config.Labels \"com.sima.sdk.service\"}}|"
            "{{index .Config.Labels \"com.sima.sdk.registry.port\"}}|"
            "{{.State.Running}}",
            REGISTRY_CONTAINER_NAME,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    parts = result.stdout.strip().split("|", 2)
    if len(parts) != 3:
        raise RuntimeError(
            "The existing local registry has invalid setup information. "
            "Remove it or choose a different container name."
        )
    managed_value, port_text, running_text = parts
    if managed_value != REGISTRY_MANAGED_VALUE:
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
    return {"port": port, "running": running_text.lower() == "true"}


def existing_container_registry_port() -> Optional[int]:
    existing = _inspect_registry()
    return int(existing["port"]) if existing else None


def _require_available_host_port(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} is already in use on this host. Run SDK setup again with "
            f"--container-registry-port <free-port>."
        ) from exc
    finally:
        probe.close()


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

    existing = _inspect_registry()
    port = requested_port or (int(existing["port"]) if existing else DEFAULT_REGISTRY_PORT)
    if not 1 <= port <= 65535:
        raise RuntimeError("The container registry port must be between 1 and 65535.")

    if existing and int(existing["port"]) != port:
        _require_available_host_port(port)
        print(
            f"ℹ️  Changing the local container registry port from {existing['port']} to {port}. "
            "Stored images will be kept."
        )
        subprocess.run(
            ["docker", "rm", "-f", REGISTRY_CONTAINER_NAME],
            text=True,
            capture_output=True,
            check=True,
        )
        existing = None

    if not existing:
        _require_available_host_port(port)

    if existing:
        if not existing["running"]:
            print("ℹ️  Starting the existing local container registry.")
            subprocess.run(
                ["docker", "start", REGISTRY_CONTAINER_NAME],
                text=True,
                capture_output=True,
                check=True,
            )
        else:
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
                "-p",
                f"{port}:5000",
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
    print(
        "ℹ️  Inside the SDK, add --push and tag the image as "
        f"{sdk_address}/<image>:<tag>."
    )
    return ContainerRegistryConfig(
        port=port,
        sdk_address=sdk_address,
        devkit_address=devkit_address,
    )
