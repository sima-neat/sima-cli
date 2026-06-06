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
import subprocess
import platform
import re
import shutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from sima_cli.sdk.utils import run_command
import importlib.resources as pkg_resources
from typing import Any

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


def _restart_colima_with_resources(profile: str) -> None:
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
        str(NEAT_COLIMA_MIN_CPUS),
        "--memory",
        str(NEAT_COLIMA_MIN_MEMORY_GB),
    ], check=True)


def ensure_colima_resources_for_neat_sdk(yes_to_all: bool = False, noninteractive: bool = False) -> bool:
    if platform.system() != "Darwin" or not _is_docker_using_colima():
        return False

    profile = _detect_colima_profile()
    status = _colima_status(profile)
    if not status:
        console.print("[yellow]⚠️  Could not inspect Colima resources for Neat SDK setup.[/yellow]")
        return False

    cpus, memory_gb = _parse_colima_status(status)
    if cpus >= NEAT_COLIMA_MIN_CPUS and memory_gb >= NEAT_COLIMA_MIN_MEMORY_GB:
        console.print(
            f"✅ Colima resources OK: {cpus} CPUs / {memory_gb:.1f} GB RAM "
            f"(Required ≥ {NEAT_COLIMA_MIN_CPUS} CPUs / {NEAT_COLIMA_MIN_MEMORY_GB} GB RAM)",
            style="green",
        )
        return False

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

    should_restart = yes_to_all or noninteractive
    if not should_restart:
        choice = input(
            f"Restart Colima with {NEAT_COLIMA_MIN_CPUS} CPUs and "
            f"{NEAT_COLIMA_MIN_MEMORY_GB} GB RAM now? [Y/n]: "
        ).strip().lower()
        should_restart = choice in ("", "y", "yes")

    if not should_restart:
        console.print("[yellow]⚠️  Continuing with current Colima resources. Neat SDK may be unstable or fail to start.[/yellow]")
        return False

    console.print(
        f"[yellow]⚙️  Restarting Colima with {NEAT_COLIMA_MIN_CPUS} CPUs and "
        f"{NEAT_COLIMA_MIN_MEMORY_GB} GB RAM...[/yellow]"
    )
    _restart_colima_with_resources(profile)
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
