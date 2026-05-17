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
from typing import Optional
from rich.console import Console
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
from sima_cli.sdk.config import IMAGE_CONFIG

console = Console()

# ------------------------------------------------------------
# Group Definition
# ------------------------------------------------------------
@click.group(hidden=True, context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
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
    "--no-model-sdk",
    is_flag=True,
    help="Skip Model SDK extension setup. Intended for CI installation tests.",
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
@click.pass_context
def setup(ctx, yes, noninteractive, devkit, no_insight, no_model_sdk, minimal, workspace):
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
    """Access MPK CLI toolset container for managing and building pipelines along with the device manager.
    It also includes the plugins zoo and the Performance Estimator tool.
    """
    launch_sdk_tool("mpk", cmd, ctx)


@sdk.command(
    name="model",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def model(ctx, cmd):
    """Launch the Model SDK tool environment."""
    launch_sdk_tool("model", cmd, ctx)


@sdk.command(
    name="yocto",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def yocto(ctx, cmd):
    """Launch the Yocto SDK tool environment."""
    launch_sdk_tool("yocto", cmd, ctx)


@sdk.command(
    name="neat",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def neat(ctx, cmd):
    """Launch the Neat SDK tool environment."""
    launch_sdk_tool("neat", cmd, ctx)


@sdk.command(
    name="elxr",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def elxr(ctx, cmd):
    """Launch the eLxr SDK tool environment."""
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
