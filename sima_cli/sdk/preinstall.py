#!/usr/bin/env python3
r"""
Palette SDK Preinstallation Check
───────────────────────────────────────────────────────────────
Performs essential environment checks before SDK installation:
1. Python version
2. Docker version
3. CPU and RAM
4. Colima resources (macOS, when Docker uses Colima)
5. Firewall (Linux/Windows only)
"""

import sys
import json
import os
import subprocess
import platform
import re
import shutil
import tempfile
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from sima_cli.sdk.utils import run_command
import importlib.resources as pkg_resources
from typing import Any
import yaml

console = Console()
NEAT_COLIMA_MIN_CPUS = 4
NEAT_COLIMA_MIN_MEMORY_GB = 8

# ---------------------------------------------------------------------
# Load system requirements from JSON
# ---------------------------------------------------------------------
def load_requirements() -> Any:
    try:
        # Python 3.9+ supports importlib.resources.files()
        if hasattr(pkg_resources, "files"):
            with pkg_resources.files("sima_cli").joinpath("sdk/requirements.json").open("r", encoding="utf-8") as f:
                return json.load(f)
        else:
            # ✅ Fallback for Python 3.8 and older
            with pkg_resources.open_text("sima_cli.sdk", "requirements.json", encoding="utf-8") as f:
                return json.load(f)

    except Exception as e:
        print(f"Encountered error while loading requirements: {e}")
        sys.exit(1)


def version_gte(v1: str, v2: str) -> bool:
    try:
        t1, t2 = tuple(map(int, v1.split(".")[:3])), tuple(map(int, v2.split(".")[:3]))
        return t1 >= t2
    except Exception:
        return False

# ---------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------
def check_python(min_version):
    version = platform.python_version()
    passed = version_gte(version, min_version)
    console.print(
        f"{'✅' if passed else '❌'} Python {version} "
        f"(Required ≥ {min_version})",
        style="green" if passed else "red",
    )
    return not passed, ["Python", f"≥ {min_version}", version, "✅ PASS" if passed else "❌ FAIL"]


def get_docker_version():
    try:
        out = subprocess.check_output(["docker", "--version"], text=True)
        return out.split()[2].replace(",", "")
    except Exception:
        return None


def check_docker(min_version):
    ver = get_docker_version()
    passed = ver is not None and version_gte(ver, min_version)
    console.print(
        f"{'✅' if passed else '❌'} Docker {ver or 'Not Found'} "
        f"(Required ≥ {min_version})",
        style="green" if passed else "red",
    )
    return not passed, ["Docker", f"≥ {min_version}", ver or "N/A", "✅ PASS" if passed else "❌ FAIL"]


def _bytes_to_gb(total_bytes: int) -> float:
    """Convert memory bytes to decimal GB to match vendor/system RAM sizing."""
    return total_bytes / 1_000_000_000


def check_cpu_ram(min_cores, min_ram_gb):
    import psutil
    cores = psutil.cpu_count(logical=False)
    total_memory = psutil.virtual_memory().total
    ram_gb = _bytes_to_gb(total_memory)
    ram_display = f"{ram_gb:.1f} GB"
    passed = cores >= min_cores and ram_gb >= min_ram_gb
    console.print(
        f"{'✅' if passed else '❌'} {cores} cores / {ram_display} RAM "
        f"(Required ≥ {min_cores} cores / {min_ram_gb} GB)",
        style="green" if passed else "red",
    )
    return not passed, ["CPU/RAM", f"≥{min_cores} cores / ≥{min_ram_gb} GB", f"{cores} / {ram_display}", "✅ PASS" if passed else "❌ FAIL"]


def check_firewall(use_sudo=False):
    """Check firewall state on Linux/Windows. macOS is skipped."""
    results = []
    fw_failed = False
    sysname = platform.system()

    if sysname == "Darwin":
        return fw_failed, results

    # ───────────────────────────────────────────────
    # 🔥 Firewall (Linux/Windows)
    # ───────────────────────────────────────────────
    note, result = "Unknown", "⚠️ WARNING"

    try:
        if sysname == "Windows":
            out = subprocess.check_output(["netsh", "advfirewall", "show", "allprofiles"], text=True)
            note, result = ("Active", "⚠️ WARNING") if "ON" in out else ("Disabled", "✅ PASS")

        elif sysname == "Linux":
            # Try without sudo first
            out = run_command(["ufw", "status"], use_sudo=False).stdout
            if "permission denied" in out.lower() or not out.strip():
                if use_sudo:
                    out = run_command(["ufw", "status"], use_sudo=True).stdout
                else:
                    note, result = "Unverified (sudo required)", "⚠️ WARNING"
                    raise PermissionError
            note, result = ("Active", "⚠️ WARNING") if "active" in out.lower() else ("Disabled", "✅ PASS")

    except PermissionError:
        console.print("[yellow]⚠️ Firewall check skipped — sudo required for accurate status.[/yellow]")
    except Exception:
        note, result = "Unverified", "⚠️ WARNING"

    fw_failed = "⚠️" in result
    results.append(["Firewall", "Disabled", note, result])

    if result == "⚠️ WARNING":
        console.print("⚠️  Firewall may restrict Docker or SDK communication.", style="yellow")
    else:
        console.print("✅ Firewall Disabled or Inactive", style="green")

    return fw_failed, results


def check_rosetta_and_firewall(use_sudo=False):
    """Backward-compatible wrapper. Rosetta is no longer a prerequisite."""
    fw_failed, results = check_firewall(use_sudo=use_sudo)
    return False, fw_failed, results


def _is_docker_using_colima() -> bool:
    if platform.system() != "Darwin":
        return False

    try:
        context_name = subprocess.check_output(["docker", "context", "show"], text=True).strip()
    except Exception:
        context_name = ""

    try:
        inspect = subprocess.check_output(["docker", "context", "inspect"], text=True)
    except Exception:
        inspect = ""

    return "colima" in context_name.lower() or ".colima" in inspect.lower()


def _detect_colima_profile() -> str:
    try:
        inspect = subprocess.check_output(["docker", "context", "inspect"], text=True)
        match = re.search(r"\.colima/([^/]+)/docker\.sock", inspect)
        if match:
            return match.group(1)
    except Exception:
        pass

    try:
        context_name = subprocess.check_output(["docker", "context", "show"], text=True).strip()
        if context_name.startswith("colima-"):
            return context_name[len("colima-"):]
    except Exception:
        pass

    return "default"


def _parse_colima_status(status: dict) -> tuple:
    cpus = int(status.get("cpu") or 0)
    memory_value = float(status.get("memory") or 0)
    # Recent Colima reports memory in bytes. Older versions may report GiB;
    # accept MiB too for defensive parsing.
    if memory_value > 1024 ** 2:
        memory_gb = memory_value / (1024 ** 3)
    elif memory_value > 1024:
        memory_gb = memory_value / 1024
    else:
        memory_gb = memory_value
    return cpus, memory_gb


def _colima_status(profile: str) -> dict:
    colima_cmd = shutil.which("colima")
    if not colima_cmd:
        return {}

    try:
        output = subprocess.check_output(
            [colima_cmd, "status", "--json", "--profile", profile],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return json.loads(output)
    except Exception:
        return {}


def _colima_config_path(profile: str) -> Path:
    configured_home = os.environ.get("COLIMA_HOME")
    configured_path = Path(configured_home).expanduser() if configured_home else None
    if configured_path and configured_path.exists():
        colima_home = configured_path
    else:
        legacy_home = Path.home() / ".colima"
        xdg_home = os.environ.get("XDG_CONFIG_HOME")
        default_xdg_home = Path.home() / ".config" / "colima"
        if legacy_home.exists():
            colima_home = legacy_home
        elif xdg_home:
            colima_home = Path(xdg_home).expanduser() / "colima"
        elif default_xdg_home.exists():
            colima_home = default_xdg_home
        else:
            colima_home = legacy_home if platform.system() == "Darwin" else default_xdg_home
    return colima_home / profile / "colima.yaml"


def _colima_config(profile: str) -> dict:
    config_path = _colima_config_path(profile)
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _colima_instance_config(profile: str) -> dict:
    """Read Colima's generated instance config, which reflects immutable VM settings."""
    if profile in ("", "default", "colima"):
        profile_id = "colima"
    else:
        short_name = profile[len("colima-"):] if profile.startswith("colima-") else profile
        profile_id = f"colima-{short_name}"
    configured_lima_home = os.environ.get("LIMA_HOME")
    lima_home = (
        Path(configured_lima_home).expanduser()
        if configured_lima_home
        else _colima_config_path(profile).parent.parent / "_lima"
    )
    path = lima_home / profile_id / "colima.yaml"
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _colima_network_config(profile: str) -> dict:
    status = _colima_status(profile)
    config = _colima_config(profile)
    instance = _colima_instance_config(profile)

    instance_network = (
        instance.get("network") if isinstance(instance.get("network"), dict) else {}
    )
    status_network = status.get("network") if isinstance(status.get("network"), dict) else {}

    return {
        "address": status_network.get("address", instance_network.get("address")),
        "mode": status_network.get("mode", instance_network.get("mode")),
        "interface": status_network.get("interface", instance_network.get("interface")),
        "ip_address": status.get("ip_address") or status.get("address"),
        "cpu": status.get("cpu", config.get("cpu")),
        "memory": status.get("memory", config.get("memory")),
    }


def _colima_port_forwarder(profile: str) -> str:
    """Return the active profile's effective port forwarder when available."""
    status = _colima_status(profile)
    config = _colima_config(profile)
    for source in (status, config):
        value = source.get("portForwarder") or source.get("port_forwarder")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _ensure_colima_udp_forwarding_for_insight(
    yes_to_all: bool = False,
    noninteractive: bool = False,
) -> bool:
    profile = _detect_colima_profile()
    forwarder = _colima_port_forwarder(profile)
    if forwarder == "grpc":
        return False

    profile_args = ["--profile", profile]
    command = (
        f"colima stop --profile {profile} && "
        f"colima start --profile {profile} --port-forwarder grpc --save-config"
    )
    if not forwarder:
        raise RuntimeError(
            "Could not determine Colima's effective port forwarder. Upgrade Colima, then run "
            f"`{command}`, or rerun setup with --no-insight."
        )

    reason = (
        "uses the SSH port forwarder, which supports TCP only"
        if forwarder == "ssh"
        else f"uses portForwarder={forwarder}, which is not UDP-capable"
    )
    message = "\n".join([
        f"Colima profile '{profile}' {reason}.",
        "Insight webcam/WebRTC, UDP video and metadata ingest, and vf WebRTC delivery will not work.",
        "Restarting Colima interrupts every container in this profile.",
        "",
        f"Remediation: {command}",
        "Or rerun setup with --no-insight (or --minimal).",
    ])
    console.print(Panel(
        message,
        title="Colima UDP Forwarding Required",
        border_style="red",
        expand=False,
    ))

    if noninteractive:
        raise RuntimeError(
            "Insight requires UDP forwarding, but Colima profile "
            f"'{profile}' uses portForwarder={forwarder}. Run `{command}` or rerun with --no-insight."
        )

    should_restart = yes_to_all
    if not should_restart:
        choice = input(
            "Restart this Colima profile with the gRPC port forwarder now? [y/N]: "
        ).strip().lower()
        should_restart = choice in ("y", "yes")
    if not should_restart:
        raise RuntimeError(
            "Insight setup stopped because Colima UDP forwarding is unavailable. "
            f"Run `{command}` or rerun with --no-insight."
        )

    colima_cmd = shutil.which("colima")
    if not colima_cmd:
        raise RuntimeError(f"Colima was not found on PATH. Run `{command}` manually.")

    try:
        subprocess.run([colima_cmd, "stop", *profile_args], check=True)
        subprocess.run([
            colima_cmd,
            "start",
            *profile_args,
            "--port-forwarder",
            "grpc",
            "--save-config",
        ], check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Could not restart Colima with UDP forwarding. Run `{command}` manually."
        ) from exc
    console.print("[green]✅ Colima restarted with UDP-capable gRPC port forwarding.[/green]")
    return True


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "enabled")
    return bool(value)


def _is_colima_network_suitable_for_devkit(
    profile: str,
    expected_interface: str = "",
    network: dict = None,
) -> bool:
    network = network if network is not None else _colima_network_config(profile)
    # Colima persists a LAN-reachable VM address as:
    #   network.address: true
    #   network.mode: bridged
    # A shared-mode address is reachable from macOS but not from a DevKit on
    # the physical LAN, so it cannot be advertised as a registry endpoint.
    mode = str(network.get("mode") or "").strip().lower()
    configured_interface = str(network.get("interface") or "en0").strip()
    interface_matches = (
        not expected_interface
        or configured_interface == expected_interface
    )
    return (
        _boolish(network.get("address"))
        and mode == "bridged"
        and bool(network.get("ip_address"))
        and interface_matches
    )


def _host_colima_resource_limits() -> tuple:
    """Return the host CPU count and whole GiB available to Colima."""
    import psutil

    max_cpus = psutil.cpu_count(logical=True) or os.cpu_count() or 0
    max_memory = int(psutil.virtual_memory().total // (1024 ** 3))
    return max_cpus, max_memory


def _default_colima_resource_targets(cpus: int, memory_gb: float) -> tuple:
    """Use half the host by default without reducing an existing allocation."""
    max_cpus, max_memory = _host_colima_resource_limits()
    if max_cpus < NEAT_COLIMA_MIN_CPUS or max_memory < NEAT_COLIMA_MIN_MEMORY_GB:
        raise RuntimeError(
            "The host does not have enough CPU or memory for the Neat SDK: "
            f"found {max_cpus} CPUs / {max_memory} GB RAM, required at least "
            f"{NEAT_COLIMA_MIN_CPUS} CPUs / {NEAT_COLIMA_MIN_MEMORY_GB} GB RAM."
        )

    recommended_cpus = max(NEAT_COLIMA_MIN_CPUS, max_cpus // 2)
    recommended_memory = max(
        float(NEAT_COLIMA_MIN_MEMORY_GB),
        max_memory / 2,
    )
    return (
        min(max(cpus, recommended_cpus), max_cpus),
        min(max(memory_gb, recommended_memory), float(max_memory)),
    )


def _prompt_colima_resource_targets(cpus: int, memory_gb: float) -> tuple:
    """Prompt for valid Colima resources, using half the host as defaults."""
    max_cpus, max_memory = _host_colima_resource_limits()
    default_cpus, default_memory = _default_colima_resource_targets(cpus, memory_gb)

    while True:
        raw_cpus = input(
            f"Colima CPU count [{default_cpus}] "
            f"({NEAT_COLIMA_MIN_CPUS}-{max_cpus}): "
        ).strip()
        try:
            target_cpus = int(raw_cpus) if raw_cpus else default_cpus
        except ValueError:
            target_cpus = 0
        if NEAT_COLIMA_MIN_CPUS <= target_cpus <= max_cpus:
            break
        console.print(
            f"[yellow]Enter between {NEAT_COLIMA_MIN_CPUS} and {max_cpus} CPUs.[/yellow]"
        )

    while True:
        raw_memory = input(
            f"Colima memory in GB [{default_memory:g}] "
            f"({NEAT_COLIMA_MIN_MEMORY_GB}-{max_memory}): "
        ).strip()
        try:
            target_memory = float(raw_memory) if raw_memory else default_memory
        except ValueError:
            target_memory = 0
        if NEAT_COLIMA_MIN_MEMORY_GB <= target_memory <= max_memory:
            break
        console.print(
            f"[yellow]Enter between {NEAT_COLIMA_MIN_MEMORY_GB} and "
            f"{max_memory} GB of memory.[/yellow]"
        )

    return target_cpus, target_memory


def _route_interface_for_target(target_ip: str) -> str:
    if platform.system() != "Darwin" or not target_ip:
        return ""

    try:
        output = subprocess.check_output(["route", "get", target_ip], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""

    match = re.search(r"^\s*interface:\s*(\S+)\s*$", output, flags=re.MULTILINE)
    return match.group(1) if match else ""


def _is_safe_colima_bridge_interface(interface: str) -> bool:
    normalized = (interface or "").strip().lower()
    if not normalized:
        return False
    if normalized == "lo" or normalized.startswith((
        "utun",
        "tun",
        "tap",
        "wg",
        "tailscale",
        "zt",
        "ppp",
        "ipsec",
        "bridge",
        "vmnet",
        "vboxnet",
        "awdl",
        "llw",
        "stf",
        "gif",
    )):
        return False
    return True


def _colima_start_help() -> str:
    colima_cmd = shutil.which("colima")
    if not colima_cmd:
        return ""

    try:
        return subprocess.check_output([colima_cmd, "start", "--help"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def _colima_supports_network_address_flag() -> bool:
    return "--network-address" in _colima_start_help()


def _colima_supports_bridged_network_flags() -> bool:
    output = _colima_start_help()
    if not output:
        return False

    return "--network-mode" in output and "--network-interface" in output


def _stage_colima_profile_config(profile: str, interface: str) -> tuple:
    """Stage a bridged profile config outside the directory Colima deletes."""
    config_path = _colima_config_path(profile)
    if not config_path.is_file():
        raise RuntimeError(f"Could not find Colima profile configuration at {config_path}.")

    config_mode = config_path.stat().st_mode & 0o777
    config_dir_mode = config_path.parent.stat().st_mode & 0o777
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
    except Exception as exc:
        raise RuntimeError(f"Could not read Colima profile configuration at {config_path}.") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"Colima profile configuration at {config_path} is not a mapping.")

    network = config.get("network")
    if not isinstance(network, dict):
        network = {}
        config["network"] = network
    # Colima accepts subnet and nat66Prefix only in shared mode. Remove them
    # from the staged copy before the old VM is deleted.
    network.pop("subnet", None)
    network.pop("nat66Prefix", None)
    network["address"] = True
    network["mode"] = "bridged"
    network["interface"] = interface

    descriptor, snapshot_name = tempfile.mkstemp(
        prefix=f"sima-cli-colima-{profile}-",
        suffix=".yaml",
    )
    snapshot_path = Path(snapshot_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            yaml.safe_dump(config, stream, sort_keys=False)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        snapshot_path.unlink(missing_ok=True)
        raise
    return config_path, snapshot_path, config_mode, config_dir_mode


def _restore_colima_profile_config(
    config_path: Path,
    snapshot_path: Path,
    config_mode: int,
    config_dir_mode: int,
) -> None:
    config_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(config_path.parent, config_dir_mode)
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination, snapshot_path.open("rb") as source:
            shutil.copyfileobj(source, destination)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    os.chmod(config_path, config_mode)


def _colima_store_path(profile: str) -> Path:
    if profile in ("", "default", "colima"):
        profile_id = "colima"
    else:
        short_name = profile[len("colima-"):] if profile.startswith("colima-") else profile
        profile_id = f"colima-{short_name}"
    return _colima_config_path(profile).parent.parent / "_store" / f"{profile_id}.json"


def _colima_profile_recreation_safety(profile: str) -> tuple:
    """Return whether Colima can recreate this profile without known data loss."""
    config = _colima_config(profile)
    kubernetes = config.get("kubernetes") if isinstance(config.get("kubernetes"), dict) else {}
    if _boolish(kubernetes.get("enabled")):
        return False, "Kubernetes is enabled in the profile configuration. Colima does not preserve its data."

    status = _colima_status(profile)
    if "kubernetes" not in status:
        return False, "The live Colima Kubernetes state could not be verified."
    if _boolish(status.get("kubernetes")):
        return False, "Kubernetes is running in the profile. Colima does not preserve its data."

    store_path = _colima_store_path(profile)
    try:
        with store_path.open("r", encoding="utf-8") as stream:
            store = json.load(stream)
    except Exception:
        return False, "Colima's separate runtime-disk state could not be verified."

    if not isinstance(store, dict) or not _boolish(store.get("disk_formatted")):
        return False, "This profile does not use Colima's separate runtime disk. It may be a legacy profile."
    if str(store.get("disk_runtime") or "").strip().lower() != "docker":
        return False, "The separate runtime disk is not recorded as a Docker data disk."
    return True, ""


def warn_if_colima_devkit_network_may_need_bridged(
    devkit_ip: str,
    noninteractive: bool = False,
    yes_to_all: bool = False,
    require_udp: bool = False,
) -> bool:
    if platform.system() != "Darwin" or not devkit_ip or not _is_docker_using_colima():
        return False

    profile = _detect_colima_profile()
    route_interface = _route_interface_for_target(devkit_ip)
    if not _is_safe_colima_bridge_interface(route_interface):
        console.print(
            f"[yellow]Route to DevKit resolved through '{route_interface or 'unknown'}', "
            "which is not a physical interface that Colima can bridge safely. "
            "The Colima profile was not changed. Connect the DevKit through a physical LAN "
            "interface and rerun SDK setup.[/yellow]"
        )
        raise RuntimeError(
            "Could not identify a safe physical interface for the route to the DevKit."
        )
    interface = route_interface
    network = _colima_network_config(profile)
    if _is_colima_network_suitable_for_devkit(profile, interface, network=network):
        return False
    # Always name the detected profile explicitly. Colima otherwise falls back
    # to COLIMA_PROFILE, which could retarget these destructive commands after
    # the safety checks inspected a different profile.
    profile_args = ["--profile", profile]
    profile_display = f" --profile {profile}"
    supports_network_address = _colima_supports_network_address_flag()
    supports_bridged_flags = _colima_supports_bridged_network_flags()
    start_flags = ["--network-address"]
    start_display_flags = list(start_flags)
    if supports_bridged_flags:
        start_flags.extend(["--network-mode", "bridged", "--network-interface", interface])
        start_display_flags.extend(["--network-mode", "bridged", "--network-interface", interface])
    enable_udp = require_udp and _colima_port_forwarder(profile) != "grpc"
    if enable_udp:
        start_flags.extend(["--port-forwarder", "grpc"])
        start_display_flags.extend(["--port-forwarder", "grpc"])
    current_cpus, current_memory = _parse_colima_status(network)
    resources_known = bool(network.get("cpu") and network.get("memory"))
    resize_resources = resources_known and (
        current_cpus < NEAT_COLIMA_MIN_CPUS
        or current_memory < NEAT_COLIMA_MIN_MEMORY_GB
    )
    target_cpus, target_memory = current_cpus, current_memory
    if resize_resources:
        target_cpus, target_memory = _default_colima_resource_targets(
            current_cpus,
            current_memory,
        )
        if not (yes_to_all or noninteractive):
            target_cpus, target_memory = _prompt_colima_resource_targets(
                current_cpus,
                current_memory,
            )
    if resize_resources:
        resource_flags = [
            "--cpu", str(target_cpus),
            "--memory", f"{target_memory:g}",
        ]
        start_flags.extend(resource_flags)
        start_display_flags.extend(resource_flags)
    start_flags.append("--save-config")
    start_display_flags.append("--save-config")
    target_start_command = f"colima start{profile_display} {' '.join(start_display_flags)}"

    console.print(
        Panel(
            "\n".join([
                "[bold red]Colima is not configured with a bridged/reachable network for DevKit-Sync.[/bold red]",
                "",
                "The macOS host may be able to SSH to the DevKit while the SDK container cannot, because the",
                "container reaches the LAN through the Colima VM network path.",
                "",
                "Colima cannot change network mode after a profile is created. The profile VM must be",
                "recreated. This operation stops all running containers in the profile.",
                "",
                "After confirmation, sima-cli will save the profile configuration, recreate the VM without",
                "the --data option, restore the configuration, and start Colima with:",
                f"[cyan]{target_start_command}[/cyan]",
                "" if not enable_udp else "This also enables the gRPC port forwarder required for Insight UDP traffic.",
                "" if not resize_resources else (
                    f"This also configures {target_cpus} CPUs and {target_memory:g} GB RAM for the Neat SDK."
                ),
                "" if supports_network_address else "",
                "" if supports_network_address else (
                    "[yellow]Your Colima version does not expose the network-address flag. "
                    "Upgrade Colima before running this command.[/yellow]"
                ),
                "" if supports_bridged_flags else (
                    "[yellow]This Colima version does not expose --network-mode/--network-interface; "
                    "upgrade Colima before rerunning SDK setup.[/yellow]"
                ),
            ]),
            title="Colima DevKit-Sync Network Warning",
            border_style="red",
            expand=False,
        )
    )

    if not supports_network_address or not supports_bridged_flags:
        missing_feature = (
            "the network-address flag"
            if not supports_network_address
            else "the bridged network flags"
        )
        console.print(
            "[yellow]⚠️  Not restarting Colima automatically because this Colima version "
            f"does not support {missing_feature}. Upgrade Colima, then rerun SDK setup.[/yellow]"
        )
        return False

    recreation_is_safe, unsafe_reason = _colima_profile_recreation_safety(profile)
    if not recreation_is_safe:
        raise RuntimeError(
            f"sima-cli will not recreate Colima profile '{profile}': {unsafe_reason} "
            "Back up or migrate the profile data, recreate the profile with bridged networking, "
            "and then rerun SDK setup. The profile was not changed."
        )

    if noninteractive and not yes_to_all:
        raise RuntimeError(
            "Colima must recreate its VM profile to use bridged networking. "
            "Rerun interactively or pass --yes to approve this change. "
            "Colima reports a separate Docker data disk, but it does not guarantee against data loss."
        )

    should_restart = yes_to_all
    if not should_restart:
        choice = input(
            "Recreate the Colima VM profile with bridged networking now? "
            "Colima reports a separate Docker data disk, but data loss is still possible. "
            "Confirm that important data is backed up. [y/N]: "
        ).strip().lower()
        should_restart = choice in ("y", "yes")
        if not should_restart:
            console.print("[yellow]⚠️  Continuing with current Colima network. DevKit-Sync may fail from the SDK container.[/yellow]")
            return False

    colima_cmd = shutil.which("colima")
    if not colima_cmd:
        console.print("[yellow]⚠️  Colima executable was not found on PATH. Run the commands above manually.[/yellow]")
        return False

    try:
        config_path, snapshot_path, config_mode, config_dir_mode = _stage_colima_profile_config(
            profile,
            interface,
        )
    except (OSError, RuntimeError) as exc:
        console.print(
            "[yellow]⚠️  Could not safely preserve the Colima profile configuration; "
            f"the profile was not changed: {exc}[/yellow]"
        )
        return False

    config_restored = False
    recreation_started = False
    try:
        subprocess.run([colima_cmd, "stop", *profile_args], check=True)
        recreation_started = True
        subprocess.run([colima_cmd, "delete", *profile_args, "--force"], check=True)
        _restore_colima_profile_config(
            config_path,
            snapshot_path,
            config_mode,
            config_dir_mode,
        )
        config_restored = True
        subprocess.run(
            [colima_cmd, "start", *profile_args, *start_flags],
            check=True,
        )
        updated_network = _colima_network_config(profile)
        if not _is_colima_network_suitable_for_devkit(
            profile,
            interface,
            network=updated_network,
        ):
            raise RuntimeError("Colima did not report a bridged, reachable address after recreation.")
        if enable_udp and _colima_port_forwarder(profile) != "grpc":
            raise RuntimeError("Colima did not report the gRPC port forwarder after recreation.")
        if resize_resources:
            updated_cpus, updated_memory = _parse_colima_status(updated_network)
            if updated_cpus < target_cpus or updated_memory < target_memory:
                raise RuntimeError("Colima did not report the requested CPU and memory after recreation.")
        console.print("[green]✅ Colima recreated with reachable VM networking for DevKit-Sync.[/green]")
        return True
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        console.print(
            "[yellow]⚠️  Could not recreate Colima with bridged networking automatically: "
            f"{exc}\nFix the reported problem, then rerun SDK setup.[/yellow]"
        )
        if recreation_started:
            raise
        return False
    finally:
        if config_restored:
            snapshot_path.unlink(missing_ok=True)
        else:
            console.print(
                f"[yellow]The saved Colima profile configuration remains at {snapshot_path}.[/yellow]"
            )


def check_colima_resources() -> list:
    if platform.system() != "Darwin" or not _is_docker_using_colima():
        return []

    profile = _detect_colima_profile()
    status = _colima_status(profile)
    if not status:
        console.print("[yellow]⚠️  Could not inspect Colima resources for Neat SDK setup.[/yellow]")
        return [["Colima", f"≥{NEAT_COLIMA_MIN_CPUS} CPUs / ≥{NEAT_COLIMA_MIN_MEMORY_GB} GB RAM", "Unknown", "⚠️ WARNING"]]

    cpus, memory_gb = _parse_colima_status(status)
    found = f"{cpus} CPUs / {memory_gb:.1f} GB RAM ({profile})"
    required = f"≥{NEAT_COLIMA_MIN_CPUS} CPUs / ≥{NEAT_COLIMA_MIN_MEMORY_GB} GB RAM"
    if cpus >= NEAT_COLIMA_MIN_CPUS and memory_gb >= NEAT_COLIMA_MIN_MEMORY_GB:
        console.print(f"✅ Colima {found} (Required {required})", style="green")
        return [["Colima", required, found, "✅ PASS"]]

    console.print(
        f"⚠️  Colima {found} may be too small for Neat SDK native builds "
        f"(Required {required})",
        style="yellow",
    )
    return [["Colima", required, found, "⚠️ WARNING"]]


def _restart_colima_with_resources(
    profile: str,
    cpus: int = NEAT_COLIMA_MIN_CPUS,
    memory_gb: float = NEAT_COLIMA_MIN_MEMORY_GB,
) -> None:
    colima_cmd = shutil.which("colima")
    if not colima_cmd:
        raise RuntimeError("Colima is not installed or is not available on PATH.")

    subprocess.run([colima_cmd, "stop", "--profile", profile], check=True)
    subprocess.run([
        colima_cmd,
        "start",
        "--profile",
        profile,
        "--cpu",
        str(cpus),
        "--memory",
        f"{memory_gb:g}",
    ], check=True)


def ensure_colima_resources_for_neat_sdk(
    yes_to_all: bool = False,
    noninteractive: bool = False,
    require_udp: bool = False,
) -> bool:
    if platform.system() != "Darwin" or not _is_docker_using_colima():
        return False

    udp_restarted = (
        _ensure_colima_udp_forwarding_for_insight(
            yes_to_all=yes_to_all,
            noninteractive=noninteractive,
        )
        if require_udp else False
    )

    profile = _detect_colima_profile()
    status = _colima_status(profile)
    if not status:
        console.print("[yellow]⚠️  Could not inspect Colima resources for Neat SDK setup.[/yellow]")
        return udp_restarted

    cpus, memory_gb = _parse_colima_status(status)
    if cpus >= NEAT_COLIMA_MIN_CPUS and memory_gb >= NEAT_COLIMA_MIN_MEMORY_GB:
        console.print(
            f"✅ Colima resources OK: {cpus} CPUs / {memory_gb:.1f} GB RAM "
            f"(Required ≥ {NEAT_COLIMA_MIN_CPUS} CPUs / {NEAT_COLIMA_MIN_MEMORY_GB} GB RAM)",
            style="green",
        )
        return udp_restarted

    console.print(
        Panel(
            "\n".join([
                "Neat SDK requires Colima to have enough CPU and memory allocated.",
                f"Current Colima profile '{profile}': {cpus} CPUs / {memory_gb:.1f} GB RAM",
                f"Required: at least {NEAT_COLIMA_MIN_CPUS} CPUs / {NEAT_COLIMA_MIN_MEMORY_GB} GB RAM",
            ]),
            title="Colima Resources",
            border_style="yellow",
            expand=False,
        )
    )

    target_cpus, target_memory = _default_colima_resource_targets(cpus, memory_gb)
    if not (yes_to_all or noninteractive):
        target_cpus, target_memory = _prompt_colima_resource_targets(cpus, memory_gb)

    should_restart = yes_to_all or noninteractive
    if not should_restart:
        choice = input(
            f"Restart Colima with {target_cpus} CPUs and "
            f"{target_memory:g} GB RAM now? [Y/n]: "
        ).strip().lower()
        should_restart = choice in ("", "y", "yes")

    if not should_restart:
        console.print("[yellow]⚠️  Continuing with current Colima resources. Neat SDK may be unstable or fail to start.[/yellow]")
        return udp_restarted

    console.print(
        f"[yellow]⚙️  Restarting Colima with {target_cpus} CPUs and "
        f"{target_memory:g} GB RAM...[/yellow]"
    )
    _restart_colima_with_resources(profile, target_cpus, target_memory)
    console.print("[green]✅ Colima restarted with sufficient resources for Neat SDK.[/green]")
    return True


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------
def print_system_report(all_data):
    table = Table(
        title="System Requirements Report",
        title_style="bold grey",
        header_style="bold cyan",
        border_style="cyan",
        box=box.SQUARE,
        show_lines=True,
    )
    table.add_column("Component", style="bold cyan")
    table.add_column("Required", style="white")
    table.add_column("Found", style="white")
    table.add_column("Result", justify="left")

    for comp, req, found, res in all_data:
        color = "green" if "✅" in res else "yellow" if "⚠️" in res else "red"
        table.add_row(comp, req, found, f"[{color}]{res}[/{color}]")

    console.print("\n")
    console.print(table)
    console.print()


# ---------------------------------------------------------------------
# syscheck
# ---------------------------------------------------------------------
def syscheck(force_install: bool, noninteractive: bool = False):
    req = load_requirements()
    py_failed, py_info = check_python(req["python"])
    dock_failed, dock_info = check_docker(req["docker"])
    cpu_failed, cpu_info = check_cpu_ram(req["min_cores"], req["min_ram_gb"])
    fw_failed, fw_info = check_firewall(use_sudo=True)
    colima_info = check_colima_resources()
    all_data = [py_info, dock_info, cpu_info] + colima_info + fw_info
    print_system_report(all_data)

    if any([py_failed, dock_failed, cpu_failed, fw_failed]):
        if force_install:
            console.print("[yellow]⚠️  Force install enabled — continuing despite warnings.[/yellow]")
            return 1
        if noninteractive:
            console.print("[red]❌ Some system checks failed. Non-interactive mode accepts the default abort.[/red]")
            sys.exit(-1)
        else:
            console.print("[red]❌ Some system checks failed.[/red]")
            choice = input("Do you want to continue anyway? [y/N]: ").strip().lower()
            if choice in ("y", "yes"):
                console.print("[yellow]⚠️  Proceeding despite warnings.[/yellow]")
                return 0
            else:
                console.print("[cyan]🛑 Installation aborted by user.[/cyan]")
                exit(-1)

    console.print("[bold green]✅ All system requirements met. Ready for installation![/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(syscheck())
