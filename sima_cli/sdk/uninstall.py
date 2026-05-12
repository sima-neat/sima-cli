#!/usr/bin/env python3
"""
remove.py — interactive utility to stop and remove one or more SiMa SDK containers (and their images).

Usage:
    python remove.py ctx keyword
"""

import subprocess
import sys
import json
from rich.console import Console
from InquirerPy import inquirer
from sima_cli.sdk.utils import (
    get_all_containers,
    FILTER_KEYWORDS,
    container_matches_sdk_keyword,
    extract_short_name,
)

console = Console()


def _select_containers_for_removal(containers, yes_to_all=False):
    names = []
    for c in containers:
        if isinstance(c, dict):
            name = c.get("Names") or c.get("Name") or c.get("name")
            if name:
                names.append(name)
        elif isinstance(c, str):
            names.append(c)

    if not names:
        console.print("[yellow]⚠️  No valid container names found.[/yellow]")
        return []

    if yes_to_all:
        return names

    choices = [{"name": n, "value": n} for n in names]
    return inquirer.checkbox(
        message="Select SDK containers to remove:",
        choices=choices,
        instruction="(Space to select, Enter to confirm)",
        qmark="🧩",
        enabled_symbol="[x]",
        disabled_symbol="[ ]",
        pointer="❯",
        transformer=lambda res: (
            f"[bold red]{len(res)} selected[/bold red]"
            if res else "[dim]None selected[/dim]"
        ),
    ).execute()


# ─────────────────────────────────────────────
# Core removal logic (with version filter)
# ─────────────────────────────────────────────
def remove_containers(ctx, keyword=None, yes_to_all=False):
    """Stop and remove containers matching keyword (and optional version filter)."""
    version_filter = None
    if ctx and getattr(ctx, "obj", None):
        version_filter = ctx.obj.get("version_filter")

    containers = get_all_containers(running_containers_only=False)
    if not containers:
        console.print("[yellow]⚠️  No containers found.[/yellow]")
        return

    # 🔹 Filter by keyword
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
        console.print(f"[dim]🔍 Version filter applied:[/dim] [bold cyan]{version_filter}[/bold cyan]")

    if not containers:
        console.print(
            f"[red]❌ No containers found matching '{keyword or '*'}'"
            + (f" with version '{version_filter}'" if version_filter else "")
            + ".[/red]"
        )
        return

    selected = _select_containers_for_removal(containers, yes_to_all=yes_to_all)
    if not selected:
        console.print("[yellow]No containers selected. Exiting.[/yellow]")
        return
    if isinstance(selected, str):
        selected = [selected]

    for name in selected:
        console.print(f"[cyan]🛑 Stopping (if running):[/cyan] [bold]{name}[/bold]")
        subprocess.run(["docker", "stop", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        console.print(f"[red]🧹 Removing container:[/red] [bold]{name}[/bold]")
        subprocess.run(["docker", "rm", "-f", name])

    console.print("[green]✅ Done removing selected containers.[/green]")

    # Ask user if they want to remove associated images
    # Ask user if they want to remove associated images
    remove_images = yes_to_all or inquirer.confirm(
        message="Also remove associated images?",
        default=False,
        qmark="🧩",
    ).execute()

    if remove_images:
        images = set(c["Image"] for c in containers if c["Names"] in selected)
        for img in images:
            console.print(f"[red]🧨 Removing image:[/red] [bold]{img}[/bold]")
            subprocess.run(["docker", "rmi", "-f", img])
        console.print("[green]✅ Done removing images.[/green]")


def get_unused_images():
    """
    Return list of Docker images not used by any container.
    Equivalent to the shell one-liner:
      docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | while read img id; do
          if ! docker ps -a --format '{{.Image}}' | grep -q "$id"; then
              echo "🧹 Unused: $img ($id)"
          fi
      done
    """
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}"],
        capture_output=True, text=True, check=False
    )
    images = [line.strip().split(" ", 2) for line in result.stdout.strip().splitlines() if line.strip()]

    used = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Image}}"],
        capture_output=True, text=True, check=False
    )
    used_ids = set(used.stdout.strip().splitlines())

    unused_images = []
    for parts in images:
        if len(parts) < 2:
            continue
        img_name, img_id = parts[0], parts[1]
        size = parts[2] if len(parts) == 3 else "unknown"
        if img_id not in used_ids:
            unused_images.append({
                "Image": img_name or "<none>",
                "ImageID": img_id,
                "Size": size,
            })

    return unused_images


def remove_unused_images():
    """Interactively remove unused images matching ELXR/Yocto/MPK/Model keywords."""
    images = get_unused_images()
    if not images:
        console.print("[yellow]⚠️  No unused images found.[/yellow]")
        return

    # 🔹 Apply keyword filters
    images = [
        img for img in images
        if extract_short_name(img["Image"]) in {"mpk_cli_toolset", "yocto", "modelsdk", "elxr", "neat"}
        or any(kw in img["Image"].lower() for kw in FILTER_KEYWORDS)
    ]

    if not images:
        console.print(f"[yellow]⚠️  No unused images matched keywords: {', '.join(FILTER_KEYWORDS)}[/yellow]")
        return

    console.print("\n[bold underline]Filtered Unused Docker Images:[/bold underline]")
    choices = []
    for img in images:
        label = f"{img['Image']}  ({img['ImageID']}, {img['Size']})"
        choices.append({"name": label, "value": img})

    # 🔹 Multi-select prompt
    selected = inquirer.checkbox(
        message="Select unused images to remove:",
        choices=choices,
        instruction="(Use space to select, enter to confirm)",
        qmark="🧩",
        enabled_symbol="[x]",
        disabled_symbol="[ ]",
        pointer="❯",
        transformer=lambda res: (
            f"[bold red]{len(res)} selected[/bold red]"
            if res else "[dim]None selected[/dim]"
        ),
    ).execute()

    if not selected:
        console.print("[yellow]No images selected. Exiting.[/yellow]")
        return

    # 🔹 Confirm removal
    console.print(f"\n[red]🧹 You selected {len(selected)} image(s) for removal.[/red]")
    if not inquirer.confirm(
        message="Proceed with deletion?",
        default=False,
        qmark="⚠️",
    ).execute():
        console.print("[yellow]No images removed. Exiting.[/yellow]")
        return

    # 🔹 Remove selected images
    for img in selected:
        console.print(f"[red]🧨 Removing:[/red] [bold]{img['Image']}[/bold] ({img['ImageID']})")
        subprocess.run(["docker", "rmi", "-f", img["ImageID"]], check=False)

    console.print("[green]✅ Done removing selected unused images.[/green]")
