#!/usr/bin/env python3
"""
SiMa.ai SDK Management Commands
──────────────────────────────────────────────────────────
Manage local SDK containers and tools.

Usage:
    sima-cli sdk setup : setting up SDK & start
    sima-cli sdk start : start one or more SDK containers
    sima-cli sdk stop : stop one or more SDK containers
    sima-cli sdk remove : remove the container and its image to free up storage space
    sima-cli sdk mpk : go to mpk container
    sima-cli sdk model : go to model container
    sima-cli sdk yocto : go to Yocto container
    sima-cli sdk neat : go to Neat SDK container
    sima-cli sdk elxr : go to elxr container
"""

import click
import ipaddress
import subprocess
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from sima_cli.sdk.install import setup_and_start
from sima_cli.sdk.cmdexec import exec_container_cmd
from sima_cli.sdk.uninstall import remove_containers, remove_unused_images
from sima_cli.sdk.stop import stop_containers
from sima_cli.sdk.utils import get_all_containers
from sima_cli.sdk.utils import extract_short_name
from sima_cli.discover.discover import discover_and_probe
from rich.table import Table
from sima_cli.utils.env import get_environment_type
from sima_cli.utils.docker import check_and_start_docker
from sima_cli.utils.deprecation import should_show_post_neat_ga_deprecation_notice
from sima_cli.sdk.config import IMAGE_CONFIG
from sima_cli.sdk.linux_devkit_network import (
    build_network_doctor_report,
    collect_network_doctor_bundle,
    print_network_doctor_report,
    repair_linux_devkit_network,
)
from sima_cli.sdk.linux_shared_network import (
    NM_SHARED_DISPATCHER_PATH,
    rollback_linux_shared_devkit_network,
)

console = Console()
LEGACY_PALETTE_SDK_TOOLS = {"elxr", "model", "yocto", "mpk"}

# ------------------------------------------------------------
# Group Definition
# ------------------------------------------------------------
@click.group(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option(
    "-v", "--version",
    "version_filter",
    help="Filter SDK containers by version tag (e.g. latest_master).",
    required=False
)
@click.pass_context
def sdk(ctx, version_filter):
    """
    Manage and launch SiMa SDK 2.0 container environments (Beta).

    This group provides access to the full SDK 2.0 toolchain, including
    setup, container orchestration, tool-specific shells (MPK, model,
    Yocto, Neat, eLxr), and hybrid `.sima` script execution. These commands are
    intended for SDK 2.0+ users only.

    \\c Host platforms only.

    Typical Use Cases
    
        • Setting up a full SDK toolchain

        • Starting one or more SDK containers

        • Stopping or removing SDK containers and cached images

        • Launching MPK, model, Yocto, Neat, or eLxr shells

    """
    ctx.ensure_object(dict)
    ctx.obj["version_filter"] = version_filter
    check_and_start_docker()

# ------------------------------------------------------------
# Helper functions 
# ------------------------------------------------------------

def _resolve_devkit_ip(devkit: Optional[str]) -> str:
    """
    Resolve --devkit value into a concrete IP.
    Supports:
      - explicit IPv4/IPv6
      - auto (uses device discover flow)
    """
    if devkit is None:
        return ""

    value = devkit.strip()
    if value.lower() != "auto":
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError as e:
            raise click.ClickException(
                f"Invalid --devkit value '{value}'. Provide a valid IPv4/IPv6 address or 'auto'."
            ) from e

    click.echo("🔍 --devkit auto requested. Attempting device auto-discovery...")
    devices = discover_and_probe()
    ips = []
    for d in devices:
        ip = d.get("ip")
        if ip and ip not in ips:
            ips.append(ip)

    if not ips:
        raise click.ClickException(
            "Could not auto-discover devices. Please provide a connectable DevKit IP via --devkit <IP>."
        )

    if len(ips) == 1:
        click.echo(f"✅ Auto-selected DevKit: {ips[0]}")
        return ips[0]

    click.echo("Multiple DevKits discovered:")
    for idx, ip in enumerate(ips, start=1):
        click.echo(f"  {idx}. {ip}")
    choice = click.prompt(
        f"Select a DevKit [1-{len(ips)}]",
        type=click.IntRange(1, len(ips)),
        default=1,
    )
    selected = ips[choice - 1]
    click.echo(f"✅ Selected DevKit: {selected}")
    return selected


def launch_sdk_tool(tool: str, cmd, ctx):
    """
    Launch a selected SDK tool container, optionally executing a command inside it.
    If no command is provided, defaults to an interactive bash login shell.
    """
    if tool in LEGACY_PALETTE_SDK_TOOLS and should_show_post_neat_ga_deprecation_notice():
        console.print(
            Panel(
                "[yellow]Legacy Palette SDK functionality will be deprecated soon.[/yellow]\n\n"
                "For application development, migrate to Palette Neat.\n"
                "Visit https://community.sima.ai for current documentation.",
                title="Legacy Palette SDK",
                border_style="yellow",
                style="yellow",
                expand=False,
            )
        )

    # Normalize click's tuple argument
    if not cmd:
        cmd_str = None
    elif isinstance(cmd, (list, tuple)):
        cmd_str = " ".join(cmd)
    else:
        cmd_str = str(cmd)

    exec_container_cmd(ctx, tool, cmd_str)


# ------------------------------------------------------------
# Subcommands
# ------------------------------------------------------------

@sdk.command(name="setup")
@click.option(
    "--noninteractive", "--non-interactive", "-n",
    is_flag=True,
    help="Run in non-interactive mode (auto-select defaults)."
)
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation before starting the container."
)
@click.option(
    "--devkit",
    type=str,
    default=None,
    help="Configure DevKit integration for setup. Use '--devkit <IP>' or '--devkit auto'.",
)
@click.option(
    "--no-insight",
    is_flag=True,
    help="Start Neat SDK without Insight UI/video/WebRTC port mappings.",
)
@click.option(
    "--no-model-compiler",
    "--no-model-sdk",
    "no_model_sdk",
    is_flag=True,
    help="Skip Model Compiler extension setup. --no-model-sdk is kept for compatibility.",
)
@click.option(
    "--minimal",
    is_flag=True,
    help="Skip optional Neat SDK container extras for CI compilation jobs.",
)
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, dir_okay=True),
    default=None,
    help="Host workspace directory to mount into SDK containers instead of ~/workspace.",
)
@click.option(
    "--persistent-network-profile",
    is_flag=True,
    help="Allow setup to install a persistent NetworkManager shared-network repair profile without prompting.",
)
@click.pass_context
def setup(ctx, yes, noninteractive, devkit, no_insight, no_model_sdk, minimal, workspace, persistent_network_profile):
    """Initialize SDK environment and select components to start."""
    devkit_ip = _resolve_devkit_ip(devkit)
    try:
        setup_and_start(
            noninteractive=noninteractive,
            yes_to_all=yes,
            devkit_ip=devkit_ip,
            no_insight=no_insight,
            no_model_sdk=no_model_sdk,
            minimal=minimal,
            workspace=workspace,
            persistent_network_profile=persistent_network_profile,
        )
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"SDK setup failed while running: {' '.join(e.cmd)}") from e
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

@sdk.command(name="start")
@click.option(
    "--noninteractive", "--non-interactive", "-n",
    is_flag=True,
    help="Run in non-interactive mode (auto-select defaults)."
)
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation before starting the container."
)
@click.pass_context
def start(ctx, yes, noninteractive):
    """Select and start one or more SDK containers."""
    try:
        setup_and_start(noninteractive=noninteractive,start_only=True, yes_to_all=yes)
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"SDK start failed while running: {' '.join(e.cmd)}") from e
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e


@sdk.group(name="doctor")
def doctor():
    """Run read-only SDK diagnostics."""


@doctor.command(name="network")
@click.option(
    "--devkit",
    type=str,
    default=None,
    help="DevKit IP to use for route and reachability diagnostics.",
)
@click.option(
    "--container",
    type=str,
    default="",
    help="Neat SDK container name. Required when multiple Neat SDK containers exist.",
)
@click.option(
    "--collect",
    is_flag=True,
    help="Create a read-only support bundle with sanitized network, Docker, and Insight diagnostics.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=True, file_okay=True),
    default="",
    help="Output .tar.gz file or directory for --collect. Defaults to ./sima-sdk-network-doctor-<timestamp>.tar.gz.",
)
def doctor_network(devkit, container, collect, output):
    """Diagnose Ubuntu/Linux host networking for Neat SDK Insight ports."""
    devkit_ip = _resolve_devkit_ip(devkit) if devkit else ""
    report = build_network_doctor_report(container=container, devkit_ip=devkit_ip)
    print_network_doctor_report(report)
    if collect:
        bundle_path = collect_network_doctor_bundle(report, output_path=output)
        click.echo(f"📦 Network doctor support bundle written to: {bundle_path}")
    if report.has_errors:
        raise click.ClickException("Network doctor found blocking issues.")


@sdk.group(name="network")
def network():
    """Probe or repair SDK network configuration."""


@network.command(name="repair")
@click.option(
    "--devkit",
    type=str,
    default=None,
    help="DevKit IP to use for route and shared-network repair.",
)
@click.option(
    "--container",
    type=str,
    default="",
    help="Neat SDK container name. Required when multiple Neat SDK containers exist.",
)
@click.option(
    "--persist",
    is_flag=True,
    help="Install/update a persistent NetworkManager dispatcher hook after applying runtime repair.",
)
def network_repair(devkit, container, persist):
    """Apply scoped Ubuntu/Linux host network repair for Neat SDK Insight paths."""
    devkit_ip = _resolve_devkit_ip(devkit) if devkit else ""
    report = repair_linux_devkit_network(container=container, devkit_ip=devkit_ip, persist=persist)
    print_network_doctor_report(report)
    if report.has_errors:
        raise click.ClickException("Network repair did not resolve all blocking issues.")


@network.command(name="rollback")
@click.option(
    "--devkit",
    type=str,
    default=None,
    help="DevKit IP to use for route and shared-network rollback.",
)
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Apply rollback changes. Without this flag, rollback runs in dry-run mode.",
)
@click.option(
    "--remove-persistent-profile",
    is_flag=True,
    help="Remove the persistent NetworkManager dispatcher hook installed by SDK network repair.",
)
def network_rollback(devkit, apply_changes, remove_persistent_profile):
    """Best-effort rollback for Linux SDK network setup/repair changes."""
    devkit_ip = _resolve_devkit_ip(devkit) if devkit else ""
    if not devkit_ip:
        raise click.ClickException("Provide --devkit <IP> for Linux SDK network rollback.")

    dry_run = not apply_changes
    if apply_changes and not remove_persistent_profile and Path(NM_SHARED_DISPATCHER_PATH).exists():
        click.echo(
            "ℹ️  A persistent SDK network repair profile is installed on this host.\n"
            "   In plain terms, this is a small NetworkManager hook that reapplies the SDK-to-DevKit\n"
            "   network fix after you reconnect the DevKit cable, restart networking, or reboot.\n"
            "   Removing it is safe if you want to fully undo the SDK network repair, but DevKit\n"
            "   connectivity may need to be repaired again later."
        )
        remove_persistent_profile = click.confirm(
            "Remove the persistent SDK network repair profile as part of rollback?",
            default=False,
        )

    actions = rollback_linux_shared_devkit_network(
        devkit_ip,
        dry_run=dry_run,
        remove_persistent_profile=remove_persistent_profile,
    )

    table = Table(title="SDK Network Rollback" + (" (dry run)" if dry_run else ""))
    table.add_column("Status", style="bold")
    table.add_column("Action")
    table.add_column("Detail")
    for action in actions:
        table.add_row(action.get("status", ""), action.get("action", ""), action.get("detail", ""))
    console.print(table)

    if dry_run:
        click.echo("ℹ️  Dry run only. Rerun with --apply to remove the listed SDK-specific network rules.")
    click.echo("ℹ️  Best-effort rollback does not restore previous IPv6 profile values or net.ipv4.ip_forward.")

@sdk.command(
    name="stop",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("sdk", required=False)
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation before stopping SDK containers."
)
@click.pass_context
def stop(ctx, sdk, yes):
    """
    Stop one or more running SDK containers.

    Examples:
        sima-cli sdk stop
        sima-cli sdk stop yocto
        sima-cli sdk -v latest_develop stop mpk -y
    """
    # Confirmation prompt unless -y provided
    if not yes:
        confirm = click.confirm(
            "⚠️  This will stop one or more running SDK containers. Continue?",
            default=False,
        )
        if not confirm:
            console.print("❌ Operation cancelled.", style="bold yellow")
            return

    stop_containers(ctx, sdk, yes_to_all=yes)

@sdk.command(
    name="remove",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("sdk", required=False)
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation before removing SDK containers/images."
)
@click.pass_context
def remove(ctx, sdk, yes):
    """
    Remove SDK containers and images.
    Example:
        sima-cli sdk remove yocto
        sima-cli sdk -v latest_develop remove mpk -y
    """
    # Confirm unless -y is provided
    if not yes:
        confirm = click.confirm(
            "⚠️  This will remove matching SDK containers and cached images. Continue?",
            default=False,
        )
        if not confirm:
            console.print("❌ Operation cancelled.", style="bold yellow")
            return

    # Call version-aware remover
    remove_containers(ctx, sdk, yes_to_all=yes)
    remove_unused_images()


@sdk.command(
    name="mpk",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def mpk(ctx, cmd):
    """
    Access MPK CLI toolset container for managing and building pipelines along with the device manager.
    It also includes the plugins zoo and the Performance Estimator tool.

    If CMD is provided, all remaining tokens are executed inside the matching
    running container with bash -lc. If CMD is omitted, sima-cli opens an
    interactive login shell.

    \b
    Examples:
        sima-cli sdk mpk
        sima-cli sdk mpk mpk --help
        sima-cli sdk mpk "mpk compile --help"
    """
    launch_sdk_tool("mpk", cmd, ctx)


@sdk.command(
    name="model",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def model(ctx, cmd):
    """
    Launch the Model SDK tool environment.

    If CMD is provided, all remaining tokens are executed inside the matching
    running container with bash -lc. If CMD is omitted, sima-cli opens an
    interactive login shell.

    \b
    Examples:
        sima-cli sdk model
        sima-cli sdk model python --version
        sima-cli sdk model "python script.py --flag"
    """
    launch_sdk_tool("model", cmd, ctx)


@sdk.command(
    name="yocto",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def yocto(ctx, cmd):
    """
    Launch the Yocto SDK tool environment.

    If CMD is provided, all remaining tokens are executed inside the matching
    running container with bash -lc. If CMD is omitted, sima-cli opens an
    interactive login shell.

    \b
    Examples:
        sima-cli sdk yocto
        sima-cli sdk yocto bitbake --version
        sima-cli sdk yocto "bitbake core-image-minimal"
    """
    launch_sdk_tool("yocto", cmd, ctx)


@sdk.command(
    name="neat",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def neat(ctx, cmd):
    """
    Launch the Neat SDK tool environment.

    If CMD is provided, all remaining tokens are executed inside the matching
    running container with bash -lc. If CMD is omitted, sima-cli opens an
    interactive login shell.

    \b
    Examples:
        sima-cli sdk neat
        sima-cli sdk neat python --version
        sima-cli sdk neat "python app.py --config config.json"
    """
    launch_sdk_tool("neat", cmd, ctx)


@sdk.command(
    name="elxr",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def elxr(ctx, cmd):
    """
    Launch the eLxr SDK tool environment.

    If CMD is provided, all remaining tokens are executed inside the matching
    running container with bash -lc. If CMD is omitted, sima-cli opens an
    interactive login shell.

    \b
    Examples:
        sima-cli sdk elxr
        sima-cli sdk elxr uname -a
        sima-cli sdk elxr "source /opt/bin/simaai-init-build-env modalix && bitbake --version"
    """
    launch_sdk_tool("elxr", cmd, ctx)


@sdk.command(name="run")
@click.argument("script_path", type=click.Path(exists=True))
@click.pass_context
def run(ctx, script_path):
    """Run a .sima hybrid script with local + container commands."""
    from sima_cli.sdk.script import execute_script
    execute_script(ctx, script_path)

@sdk.command(name="ls")
@click.pass_context
def list_sdk(ctx):
    """
    List installed and running SiMa SDK containers.
    Shows SDK name, version, and running status.
    """
    containers = get_all_containers(running_containers_only=False)
    if not containers:
        console.print("[yellow]⚠️ No SDK containers found.[/yellow]")
        return

    table = Table(title="📦 Installed SDK Containers", show_lines=False, header_style="bold cyan")
    table.add_column("SDK", style="white", no_wrap=True)
    table.add_column("Image", style="cyan")
    table.add_column("Version", style="magenta")
    table.add_column("Running", style="green", justify="center")

    for c in containers:
        name = c.get("Names") or c.get("Name") or c.get("name", "")
        image = c.get("Image", "")
        state_field = (c.get("State") or "").lower().strip()
        status_field = (c.get("Status") or "").lower().strip()

        # ──────────────────────────────────────────────
        # 1. Identify SDK name + version
        # ──────────────────────────────────────────────
        sdk_name = extract_short_name(image)
        image_leaf = image.rsplit("/", 1)[-1]
        version = image.rsplit(":", 1)[1] if ":" in image_leaf else "unknown"

        # ──────────────────────────────────────────────
        # 2. Determine running state robustly
        # ──────────────────────────────────────────────
        if any(word in status_field for word in ("up", "running")) or state_field == "running":
            running = "✅"
        else:
            running = "❌"

        # ──────────────────────────────────────────────
        # 3. Skip non-SDK containers
        # ──────────────────────────────────────────────
        if not sdk_name or sdk_name not in IMAGE_CONFIG:
            continue

        table.add_row(sdk_name, image, version, running)

    console.print()
    console.print(table)

# ------------------------------------------------------------------------------------------------------------
# Register the group to main CLI entrypoint, skip this group if it's running inside the SDK container already
# ------------------------------------------------------------------------------------------------------------
def register_sdk_commands(main):
    """Attach the SDK command group to the main Click CLI on the host platforms."""
    env, _ = get_environment_type()

    if env == 'host':
        main.add_command(sdk)
