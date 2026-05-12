#!/usr/bin/env python3
"""
stop.py — interactive utility to stop one or more running SiMa SDK containers.

Usage:
    python stop.py ctx keyword
"""

import subprocess
import sys
import json
from rich.console import Console
from sima_cli.sdk.utils import select_containers, get_all_containers, container_matches_sdk_keyword

console = Console()

# ─────────────────────────────────────────────
# Core stop logic (with version filter)
# ─────────────────────────────────────────────
def stop_containers(ctx, keyword=None, yes_to_all=False):
    """Stop containers matching keyword (and optional version filter from ctx)."""
    version_filter = None

    if ctx and getattr(ctx, "obj", None):
        version_filter = ctx.obj.get("version_filter")

    containers = get_all_containers(running_containers_only=True)
    if not containers:
        console.print("[yellow]⚠️  No running containers found.[/yellow]")
        return

    # 🔹 Filter by keyword (mpk, model, etc.)
    if keyword:
        containers = [
            c for c in containers
            if container_matches_sdk_keyword(c, keyword)
        ]

    # 🔹 Apply version filter if provided
    if version_filter:
        containers = [
            c for c in containers
            if version_filter.lower() in c["Names"].lower() or version_filter.lower() in c["Image"].lower()
        ]
        console.print(f"🔍 Version filter applied: [bold cyan]{version_filter}[/bold cyan]")

    if not containers:
        console.print(
            f"[red]❌ No running containers found matching '{keyword or '*'}'"
            + (f" with version '{version_filter}'" if version_filter else "")
            + ".[/red]"
        )
        return
    
    selected = select_containers(containers, yes_to_all=yes_to_all)
    if not selected:
        console.print("[yellow]No containers selected. Exiting.[/yellow]")
        return
    if isinstance(selected, str):
        selected = [selected]

    for name in selected:
        console.print(f"[cyan]🛑 Stopping:[/cyan] [bold]{name}[/bold]")
        subprocess.run(["docker", "stop", name])

    console.print("[green]✅ Done stopping selected containers.[/green]")
