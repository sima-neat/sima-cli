#!/usr/bin/env python3
"""
exec.py — interactive launcher for running commands inside SiMa SDK containers.
"""

import subprocess
import sys
from typing import Optional
from rich.console import Console
from sima_cli.sdk.utils import (
    select_containers,
    get_all_containers,
    check_os,
    detect_current_user,
    container_matches_sdk_keyword,
    container_user_mapping_unavailable,
)

console = Console()


class SdkContainerUnavailable(RuntimeError):
    """Raised when no running SDK container matches a requested tool."""


# ─────────────────────────────────────────────
# Core executer
# ─────────────────────────────────────────────
def exec_container_cmd(ctx, keyword: str, cmd: Optional[str] = None, raise_on_missing: bool = False):
    """
    Find a running container matching the given SDK keyword (e.g. mpk, yocto),
    optionally filtering by version (from ctx.obj['version_filter']),
    and execute the command or open a shell inside it.
    """
    version_filter = None
    if ctx and getattr(ctx, "obj", None):
        version_filter = ctx.obj.get("version_filter")

    containers = get_all_containers(running_containers_only=True)
    if not containers:
        console.print("[yellow]⚠️  No running containers found.[/yellow]")
        if raise_on_missing:
            raise SdkContainerUnavailable("No running containers found.")
        sys.exit(0)

    # ──────────────────────────────────────────────
    # Step 1: Filter by SDK keyword (tool name)
    # ──────────────────────────────────────────────
    matches = []
    for c in containers:
        if container_matches_sdk_keyword(c, keyword):
            matches.append(c)

    # ──────────────────────────────────────────────
    # Step 2: Filter by version if provided
    # ──────────────────────────────────────────────
    if version_filter:
        filtered = []
        for c in matches:
            name = c.get("Names") or c.get("Name") or ""
            image = c.get("Image") or ""
            if version_filter.lower() in name.lower() or version_filter.lower() in image.lower():
                filtered.append(c)
        matches = filtered

        console.print(
            f"[dim]🔍 Version filter applied:[/dim] [bold cyan]{version_filter}[/bold cyan]"
        )

    if not matches:
        message = (
            f"No running containers found for '{keyword}'"
            + (f" with version '{version_filter}'" if version_filter else "")
            + "."
        )
        console.print(
            f"[red]❌ {message}[/red]"
        )
        if raise_on_missing:
            raise SdkContainerUnavailable(message)
        sys.exit(1)

    # ──────────────────────────────────────────────
    # Step 3: Prompt user if multiple matches
    # ──────────────────────────────────────────────
    if len(matches) > 1:
        selected_name = select_containers(matches, single_select=True)
    else:
        selected_name = matches[0].get("Names") or matches[0].get("Name") or matches[0].get("name")

    # Normalize selector output in case a list is returned unexpectedly.
    if isinstance(selected_name, list):
        if not selected_name:
            console.print("[yellow]⚠️ No container selected.[/yellow]")
            sys.exit(0)
        selected_name = selected_name[0]

    exec_user = None
    if check_os() in ["linux", "macos"]:
        exec_user = detect_current_user()[0]

    # ──────────────────────────────────────────────
    # Step 4: Execute command or attach shell
    # ──────────────────────────────────────────────
    if cmd:
        console.print(
            f"[cyan]▶ Executing command in container:[/cyan] [bold]{selected_name}[/bold]"
        )
        exec_cmd = ["docker", "exec", "-it"]
        if exec_user:
            exec_cmd.extend(["-u", exec_user])
        exec_cmd.extend([selected_name, "bash", "-lc", cmd])
    else:
        console.print(
            f"[cyan]▶ Attaching to container:[/cyan] [bold]{selected_name}[/bold]"
        )
        exec_cmd = ["docker", "exec", "-it"]
        if exec_user:
            exec_cmd.extend(["-u", exec_user])
        exec_cmd.extend([selected_name, "bash", "-l"])

    try:
        first = subprocess.run(exec_cmd, check=False)
        if (
            first.returncode != 0
            and exec_user
            and container_user_mapping_unavailable(selected_name, exec_user)
        ):
            fallback_cmd = ["docker", "exec", "-it", selected_name]
            if cmd:
                fallback_cmd.extend(["bash", "-lc", cmd])
            else:
                fallback_cmd.extend(["bash", "-l"])
            console.print(
                "[yellow]⚠️ User mapping unavailable in this container; retrying without -u.[/yellow]"
            )
            fallback = subprocess.run(fallback_cmd, check=False)
            if fallback.returncode != 0:
                sys.exit(fallback.returncode)
            return
        if first.returncode != 0:
            sys.exit(first.returncode)
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ Interrupted by user.[/yellow]")
