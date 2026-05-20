#!/usr/bin/env python3
"""
sima-cli Registry
=================

This module implements a local package registry for sima-cli installations.

📦 Purpose
-----------
Whenever `sima-cli install <package>` is executed, this registry stores:
- package name and version
- installation state (`installing`, `installed`, etc.)
- timestamp of last update
- installation and metadata file paths
- **full metadata.json content**
"""

import json
import click
import datetime
from pathlib import Path
from sima_cli.install.package_builder import build_metadata, write_metadata
from rich.table import Table
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel


# Default registry path (cross-platform)
REGISTRY_PATH = Path.home() / ".sima-cli" / "registry.json"

# ------------------------------------------------------------------------------------------------------------
# Register the "packages" group to the main CLI entrypoint
# ------------------------------------------------------------------------------------------------------------
@click.group(help="Manage sima-cli package registry (list, inspect, clean, etc.)")
def packages():
    pass


@packages.command("list", help="List all packages in the local registry.")
def list_packages():
    reg = PackageRegistry()
    reg.list_packages()


@packages.command(
    "show",
    help="Show metadata or post-install instructions for a package.",
    epilog=(
        "PACKAGE supports partial and case-insensitive matching.\n"
        "If multiple matches are found, a summary table of all matching packages will be shown."
    ),
)
@click.argument(
    "name",
    metavar="PACKAGE",
    required=True,
)
@click.option(
    "--version",
    "-v",
    help="Specify a version when multiple matches exist. If omitted, the latest match is shown."
)
def show_metadata(name, version):
    """
    Display metadata or post-installation help for a package.

    Examples:
      sima-cli packages show multi
      sima-cli packages show palette-sdk -v 1.2.0
    """
    reg = PackageRegistry()
    try:
        reg.show_metadata(name, version)
    except ValueError as e:
        Console().print(f"[red]Error:[/red] {e}")


@packages.command("build", help="Build package metadata.json from an artifacts folder.")
@click.argument(
    "artifacts_folder",
    metavar="ARTIFACTS_FOLDER",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
)
@click.option("--name", help="Package name. Defaults to gh:<org>/<repo> for GitHub repos.")
@click.option("--version", help="Package version. Defaults to exact git tag or short commit hash.")
@click.option("--description", help="Package description. Defaults to the GitHub repo description when available.")
@click.option(
    "--install-script",
    required=True,
    help="Install script file inside ARTIFACTS_FOLDER, or a single-line shell command.",
)
@click.option(
    "--selectables",
    help="Optional resources in 'name1:file1;name2:file2' format.",
)
def build_package_metadata(artifacts_folder, name, version, description, install_script, selectables):
    """
    Generate ARTIFACTS_FOLDER/metadata.json for sima-cli package installation.
    """
    try:
        metadata = build_metadata(
            artifacts_folder=artifacts_folder,
            name=name,
            version=version,
            description=description,
            install_script=install_script,
            selectables=selectables,
        )
        output_path = write_metadata(artifacts_folder, metadata)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    click.echo(f"✅ Package metadata written to: {output_path}")

# ------------------------------------------------------------------------------------------------------------
# Register the group to main CLI entrypoint, skip this group if it's running inside the SDK container already
# ------------------------------------------------------------------------------------------------------------
def register_packages_commands(main):
    """Attach the 'packages' command group to the main Click CLI."""
    main.add_command(packages)


class PackageRegistry:
    """Handles read/write operations for sima-cli's local package registry."""

    def __init__(self, registry_path: Path = REGISTRY_PATH):
        self.registry_path = registry_path
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    # -------------------------------
    # Internal helpers
    # -------------------------------

    def _load(self):
        """Load registry data from disk or initialize empty list."""
        if self.registry_path.exists():
            try:
                return json.loads(self.registry_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print("⚠️  Warning: Registry file corrupted. Reinitializing...")
        return []

    def _save(self):
        """Save current registry data to disk (pretty-printed JSON)."""
        self.registry_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _find(self, name, version):
        """Find a registry entry by name and version."""
        return next(
            (pkg for pkg in self._data if pkg["package_name"] == name and pkg["version"] == version),
            None,
        )

    # -------------------------------
    # Public API
    # -------------------------------

    def create_entry(self, name, version, metadata, install_path):
        """
        Create or overwrite a registry entry.

        Accepts a parsed metadata object directly (instead of metadata_path).
        Saves it inline for future inspection.
        """
        # Ensure metadata is a dictionary
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be a dict containing package information.")

        entry = {
            "package_name": name,
            "version": version,
            "state": "installing",
            "metadata_path": None,  # kept for backward compatibility
            "install_path": str(Path(install_path).resolve()),
            "metadata": metadata,
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        }

        existing = self._find(name, version)
        if existing:
            self._data.remove(existing)

        self._data.append(entry)
        self._save()


    def update_state(self, name, version, state):
        """Update the install state of an existing package."""
        pkg = self._find(name, version)
        if not pkg:
            raise ValueError(f"Package {name}@{version} not found in registry.")
        pkg["state"] = state
        pkg["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        self._save()

    def list_packages(self):
        """Display all registry entries in a Rich-formatted table."""
        console = Console()
        table = Table(title="📦 sima-cli Package Registry")

        table.add_column("Package", style="bold cyan")
        table.add_column("Version", style="green")
        table.add_column("State", style="yellow")
        table.add_column("Updated At", style="dim")
        table.add_column("Install Path", style="white")

        if not self._data:
            console.print("[bold yellow]⚠️  No package entries found in the registry.[/bold yellow]\n")
            console.print(
                f"[dim]Registry file location:[/dim] [white]{self.registry_path}[/white]\n"
                "[dim]Notes:[/dim]\n"
                "• Packages installed prior to [bold]sima-cli v0.0.45[/bold] will not appear here.\n"
                "• If you recently cleared or deleted the registry file, You’ll need to [bold]reinstall the packages[/bold] for them to reappear in the registry.\n"
            )
            return

        for pkg in sorted(self._data, key=lambda x: x["updated_at"], reverse=True):
            table.add_row(
                pkg["package_name"],
                pkg["version"],
                pkg["state"],
                pkg["updated_at"],
                pkg["install_path"]
            )

        console.print(table)
        console.print()
        console.print(
            f"[dim]Registry file location:[/dim] [white]{self.registry_path}[/white]\n"
            "[dim]Note:[/dim] Packages installed prior to [bold]sima-cli v0.0.45[/bold] will not appear in this registry.\n"
            "If you don’t see your expected packages, the registry file may have been removed — reinstall them to restore entries.\n"
        )

    def show_metadata(self, name, version=None):
        """
        Display package details in a human-friendly format.

        Performs case-insensitive partial matching on the package name.
        If multiple packages match, prints a summary list of them.
        """
        console = Console()
        name_lower = name.lower()

        # Partial (substring) matching, case-insensitive
        matches = [pkg for pkg in self._data if name_lower in pkg["package_name"].lower()]

        if not matches:
            console.print(f"[red]No packages matching '{name}' found in registry.[/red]")
            return

        # If multiple packages match and no explicit version, list them all
        if len(matches) > 1 and version is None:
            console.print(f"[yellow]Multiple packages matching '{name}' found:[/yellow]\n")
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Package", style="bold")
            table.add_column("Version", style="green")
            table.add_column("State", style="yellow")
            table.add_column("Updated At", style="dim")
            table.add_column("Description", style="white")

            for pkg in sorted(matches, key=lambda x: x["updated_at"], reverse=True):
                meta = pkg.get("metadata", {})
                desc = meta.get("description", "") or "(no description)"
                table.add_row(
                    pkg["package_name"],
                    pkg["version"],
                    pkg["state"],
                    pkg["updated_at"],
                    desc.strip().split("\n")[0],
                )

            console.print(table)
            console.print(
                "\n[dim]Tip:[/dim] Run "
                f"[bold]sima-cli packages show <exact_name> -v <version>[/bold] "
                "[dim]to view one package in detail.[/dim]\n"
            )
            return

        # Pick correct package (by version if provided)
        if version:
            pkg = next((p for p in matches if p["version"] == version), None)
            if not pkg:
                console.print(f"[red]No package '{name}' with version '{version}' found.[/red]")
                return
        else:
            pkg = matches[0]

        metadata = pkg.get("metadata", {})
        if not metadata:
            console.print("[red]No metadata stored for this package.[/red]")
            return

        # Extract main info
        name = metadata.get("name", "(unknown)")
        version = metadata.get("version", "(unknown)")
        release = metadata.get("release", "")
        desc = metadata.get("description", "(no description provided)")
        size = metadata.get("size", {})
        resources = metadata.get("resources", [])
        selectable = metadata.get("selectable-resources", [])
        platforms = metadata.get("platforms", [])

        # -----------------------------
        # 1️⃣ Summary Info Section
        # -----------------------------
        console.print(f"[bold cyan]\n📦 Package:[/bold cyan] {name}")
        console.print(f"[bold cyan]🧩 Version:[/bold cyan] {version}")
        if release:
            console.print(f"[bold cyan]🏷️  Release:[/bold cyan] {release}")
        console.print(f"[bold cyan]📝 Description:[/bold cyan] {desc}\n")

        # Platform Compatibility
        if platforms:
            console.print("[bold cyan]💻 Compatible With:[/bold cyan]")
            for p in platforms:
                os_list = ", ".join(p.get("os", []))
                console.print(f"  • [white]{p.get('type', 'unknown')}[/white] ({os_list})")
            console.print()

        # Size Info
        if size:
            d = size.get("download", "?")
            i = size.get("install", "?")
            console.print(f"[bold cyan]📦 Size:[/bold cyan] Download {d}, Install {i}\n")

        # Resources
        if resources or selectable:
            console.print("[bold cyan]📂 Resources:[/bold cyan]")

            # 1️⃣ Print base resources (simple files only)
            for res in resources:
                # skip any container-style identifiers (e.g. cr:xxx, ghcr:xxx)
                if not (str(res).startswith("cr:") or str(res).startswith("ghcr:")):
                    console.print(f"  • {res}")

            # 2️⃣ Print selectable resources with details
            for sel in selectable:
                name_str = sel.get("name", "?")
                res_id = sel.get("resource", "?")
                desc = sel.get("description", "")
                selected_flag = "[green](selected)" if sel.get("selected") else ""

                console.print(f"  • {name_str} {selected_flag}")
                console.print(f"    ↳ resource: [cyan]{res_id}[/cyan]")

                if desc:
                    console.print(f"    ↳ {desc}")

                # Optional: show additional info like size, privilege, ports
                pull_gb = sel.get("pull_space_in_gb")
                dl_gb = sel.get("download_size_in_gb")
                if pull_gb or dl_gb:
                    console.print(f"    ↳ size: {dl_gb or '?'} GB download / {pull_gb or '?'} GB pull space")
                if sel.get("privileged") or sel.get("port_mapping_required"):
                    flags = []
                    if sel.get("privileged"):
                        flags.append("requires privileged")
                    if sel.get("port_mapping_required"):
                        flags.append("needs port mapping")
                    console.print(f"    ↳ {', '.join(flags)}")

                console.print()

        # -----------------------------
        # 2️⃣ Post-Install Help Section
        # -----------------------------
        post_message = metadata.get("installation", {}).get("post-message")
        if post_message:
            console.print(
                f"\n[bold cyan]🎯 Post-Installation Instructions for {pkg['package_name']}@{pkg['version']}[/bold cyan]\n"
            )
            console.print(post_message, markup=True)
        else:
            # Fallback: full JSON metadata
            from rich.syntax import Syntax
            from rich.panel import Panel
            pretty_json = json.dumps(metadata, indent=2, ensure_ascii=False)
            syntax = Syntax(pretty_json, "json", theme="monokai", line_numbers=True)
            panel = Panel(
                syntax,
                title=f"📘 Metadata for {pkg['package_name']}@{pkg['version']}",
                border_style="cyan",
                expand=False,
            )
            console.print(panel)


# Example manual usage
if __name__ == "__main__":
    reg = PackageRegistry()
    reg.list_packages()
