#!/usr/bin/env python3
"""
sima-cli Self-Update
====================

Manual self-update mechanism for sima-cli.

Supports:
  • Default → latest release from PyPI
  • -v <version> → specific version from PyPI
  • -m <url>     → manual wheel URL
Respects the global `--internal` flag for authenticated internal updates.
"""
import os
import sys
import tempfile
import subprocess
import glob
import zipfile
import hashlib
import re
import click
from rich.console import Console

from sima_cli.download.downloader import download_file_from_url
from sima_cli.install.metadata_installer import _resolve_resource_url_candidates
from sima_cli.vulcan.artifacts import (
    ArtifactClient,
    join_url,
    load_branch_choices,
    read_latest_tag,
    ref_key,
    select_from_menu,
)

console = Console()
SELFUPDATE_REPOSITORY = "sima-cli"
SELFUPDATE_ENV_BASE_URLS = {
    "dev": "https://artifacts.neat.paconsultings.com",
    "staging": "https://artifacts.stg.neat.sima.ai",
    "production": "https://artifacts.neat.sima.ai",
}
SELFUPDATE_ENV_LABELS = {
    "dev": "dev",
    "staging": "staging",
    "production": "production",
}
PUBLIC_PYPI_JSON_URL = "https://pypi.org/pypi/sima-cli/json"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _print_windows_manual_update_cmd(python_exec: str, wheel_path: str) -> None:
    wheel_abs = os.path.abspath(wheel_path)
    safe_python = python_exec.replace('"', "")
    safe_wheel = wheel_abs.replace('"', "")
    cmd = f"{safe_python} -m pip install --force-reinstall {safe_wheel}"

    console.print(
        "[yellow]⚠️  Automatic self-update is not supported on Windows while sima-cli is running.[/yellow]"
    )
    console.print("[cyan]Run this command to update:[/cyan]")
    console.print(f"[green]{cmd}[/green]")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_sort_key(version: str):
    numeric = []
    for part in re.split(r"[._+-]", version):
        if part.isdigit():
            numeric.append(int(part))
        else:
            break
    return tuple(numeric), version


def _is_pypi_release_ref(ref: str) -> bool:
    return re.fullmatch(r"v\d+\.\d+\.\d+(?:[a-zA-Z0-9_.-]+)?", ref or "") is not None


def _version_from_release_ref(ref: str) -> str:
    if not _is_pypi_release_ref(ref):
        raise RuntimeError(f"PyPI release refs must look like v2.1.8, got: {ref}")
    return ref[1:]


def _fetch_recent_pypi_releases(client: ArtifactClient, limit: int = 5):
    payload = client.read_json(PUBLIC_PYPI_JSON_URL)
    releases = payload.get("releases", {})
    if not isinstance(releases, dict):
        return []
    versions = [version for version, files in releases.items() if files]
    return [
        f"v{version}"
        for version in sorted(versions, key=_version_sort_key)[-limit:]
    ]


def _resolve_vulcan_ref(client: ArtifactClient, base_url: str, branch: str = None):
    if branch:
        value = branch.strip()
        if not value:
            raise RuntimeError("Branch or release name is empty.")
        if _is_pypi_release_ref(value):
            return "release", value, ""
        return "branch", value, ref_key(value)

    branches = load_branch_choices(client, base_url, SELFUPDATE_REPOSITORY)
    try:
        releases = _fetch_recent_pypi_releases(client, limit=5)
    except Exception as exc:
        releases = []
        console.print(f"[yellow]⚠️  Could not fetch recent PyPI releases: {exc}[/yellow]")

    choices = [item["name"] for item in branches] + releases
    selected = select_from_menu("sima-cli branches or releases", choices)
    if _is_pypi_release_ref(selected):
        return "release", selected, ""
    for item in branches:
        if item["name"] == selected:
            return "branch", item["name"], item["key"]
    raise RuntimeError(f"Selected branch or release was not found: {selected}")


def _find_vulcan_package_resource(metadata: dict) -> str:
    resources = metadata.get("resources")
    if not isinstance(resources, list):
        raise RuntimeError("Vulcan metadata does not contain a resources list.")
    packages = sorted(
        str(resource).strip()
        for resource in resources
        if str(resource).strip().startswith("sima-cli-package-") and str(resource).strip().endswith(".zip")
    )
    if not packages:
        raise RuntimeError("Vulcan metadata does not list a sima-cli package zip.")
    return packages[-1]


def _download_vulcan_resource(client: ArtifactClient, urls, destination: str, expected_sha: str = "") -> None:
    errors = []
    for url in urls:
        try:
            with open(destination, "wb") as stream:
                stream.write(client.read_bytes(url))
            if expected_sha:
                actual_sha = _sha256_file(destination)
                if actual_sha != expected_sha:
                    os.unlink(destination)
                    raise RuntimeError(
                        f"SHA256 mismatch for {os.path.basename(destination)}: expected {expected_sha}, got {actual_sha}"
                    )
            return
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            if os.path.exists(destination):
                os.unlink(destination)
    raise RuntimeError("Failed to download Vulcan resource:\n" + "\n".join(errors))


def _extract_wheel_from_package(package_path: str, output_dir: str) -> str:
    with zipfile.ZipFile(package_path) as archive:
        archive.extractall(output_dir)
    wheels = sorted(glob.glob(os.path.join(output_dir, "*.whl")))
    if not wheels:
        raise RuntimeError("Vulcan sima-cli package did not contain a wheel.")
    return wheels[-1]


def _update_from_vulcan(python_exec: str, environment: str, branch: str = None, client: ArtifactClient = None) -> None:
    client = client or ArtifactClient()
    base_url = SELFUPDATE_ENV_BASE_URLS[environment]
    console.print(f"[cyan]🌋 Resolving sima-cli from Vulcan {SELFUPDATE_ENV_LABELS[environment]}:[/cyan] {base_url}")

    ref_type, ref_name, key = _resolve_vulcan_ref(client, base_url, branch)
    if ref_type == "release":
        version = _version_from_release_ref(ref_name)
        console.print(f"[cyan]Release:[/cyan] {ref_name}")
        _update_from_pypi(python_exec, version)
        return

    latest_tag = read_latest_tag(client, base_url, SELFUPDATE_REPOSITORY, key)
    metadata_url = join_url(base_url, SELFUPDATE_REPOSITORY, key, latest_tag, "metadata.json")
    metadata = client.read_json(metadata_url)
    package_name = _find_vulcan_package_resource(metadata)
    package_urls = _resolve_resource_url_candidates(metadata_url, package_name)
    checksum = str((metadata.get("resources-checksum") or {}).get(package_name, "")).strip()

    console.print(f"[cyan]Branch:[/cyan] {ref_name}")
    console.print(f"[cyan]Version:[/cyan] {latest_tag}")
    console.print(f"[cyan]Metadata:[/cyan] {metadata_url}")

    tmpdir = tempfile.mkdtemp(prefix="sima_vulcan_selfupdate_")
    package_path = os.path.join(tmpdir, package_name)
    package_dir = os.path.join(tmpdir, "package")
    os.makedirs(package_dir, exist_ok=True)

    console.print(f"[cyan]⬇️  Fetching Vulcan package from:[/cyan] {package_urls[0]}")
    _download_vulcan_resource(client, package_urls, package_path, checksum)
    wheel_path = _extract_wheel_from_package(package_path, package_dir)
    console.print(f"[green]✅ Download complete:[/green] {wheel_path}")

    if _is_windows():
        _print_windows_manual_update_cmd(python_exec, wheel_path)
        return

    console.print("[cyan]📦 Installing Vulcan wheel...[/cyan]")
    subprocess.run([python_exec, "-m", "pip", "install", "--force-reinstall", wheel_path], check=True)
    console.print(f"[green]✅ sima-cli successfully updated from Vulcan {environment} in {python_exec}.[/green]")


def _download_wheel_from_pypi(python_exec: str, version: str = None) -> str:
    package = "sima-cli"
    target = f"{package}=={version}" if version else package
    tmpdir = tempfile.mkdtemp(prefix="sima_selfupdate_")

    cmd = [python_exec, "-m", "pip", "download", "--no-deps", "--only-binary=:all:", "--dest", tmpdir, target]
    subprocess.run(cmd, check=True)

    wheels = sorted(
        glob.glob(os.path.join(tmpdir, "*.whl")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not wheels:
        raise RuntimeError("No wheel file was downloaded from PyPI.")
    return wheels[0]


@click.command("selfupdate")
@click.option(
    "-v", "--version",
    help="Version to update to (cannot be combined with --manual-url)."
)
@click.option(
    "-m", "--manual-url",
    help="Manual wheel URL (cannot be combined with --version)."
)
@click.option(
    "--dev",
    "vulcan_environment",
    flag_value="dev",
    default=None,
    help="Self-update from the Vulcan dev environment.",
)
@click.option(
    "--stg", "--staging",
    "vulcan_environment",
    flag_value="staging",
    help="Self-update from the Vulcan staging environment.",
)
@click.option(
    "--prd", "--prod", "--neat", "--vulcan",
    "vulcan_environment",
    flag_value="production",
    help="Self-update from the Vulcan production environment.",
)
@click.option(
    "--branch",
    help="Vulcan sima-cli branch to install. If omitted, prompts with branches.json choices.",
)
@click.pass_context
def selfupdate(ctx, version, manual_url, vulcan_environment, branch):
    """
    Update sima-cli manually from PyPI or a direct wheel URL.

    This command downloads and installs a new version of sima-cli.
    You may update to the latest PyPI release, update to a specific
    version, or install from a manually supplied wheel URL.

    \b
    Update modes:
      - No options: update to the latest PyPI release
      - --version: update to the specified PyPI version
      - --manual-url: install from a direct wheel link
      - --dev: install from Vulcan dev artifacts
      - --stg/--staging: install from Vulcan staging artifacts
      - --prd/--prod/--neat/--vulcan: install from Vulcan production artifacts
    
    \b
    Rules:
      - --version and --manual-url cannot be used together
      - Manual URLs must point to a valid .whl file
      - Internal builds may be installed using the global -i flag

    \b
    Examples:

      sima-cli selfupdate

      sima-cli selfupdate --dev

      sima-cli selfupdate --stg --branch main

      sima-cli selfupdate -v 0.0.45

      sima-cli selfupdate -m https://.../sima_cli-0.0.46.whl

    """
    if branch and not vulcan_environment:
        console.print("[red]❌ Error:[/red] --branch can only be used with --dev, --stg, or --prd/--neat/--vulcan.")
        sys.exit(1)

    if vulcan_environment and (version or manual_url):
        console.print("[red]❌ Error:[/red] Cannot use Vulcan self-update modes with -v or -m.")
        sys.exit(1)

    if version and manual_url:
        console.print("[red]❌ Error:[/red] Cannot use -v and -m together.")
        sys.exit(1)

    # Retrieve global --internal flag from the parent CLI
    internal = ctx.obj.get("internal", False)
    python_exec = sys.executable

    try:
        # Case 0: Vulcan artifact installer
        if vulcan_environment:
            _update_from_vulcan(python_exec, vulcan_environment, branch=branch)

        # Case 1: Manual URL (direct .whl)
        elif manual_url:
            _update_from_url(python_exec, manual_url, internal)

        # Case 2: Version + internal → build internal Artifactory URL
        elif version and internal:
            url = (
                f"https://artifacts.eng.sima.ai:443/artifactory/sima-pypi/"
                f"sima-cli/sima_cli-{version}-py3-none-any.whl"
            )
            console.print(
                f"[cyan]📦 Detected internal mode — fetching version {version} from Artifactory[/cyan]"
            )
            _update_from_url(python_exec, url, internal)

        # Case 3: Version only → PyPI
        elif version:
            _update_from_pypi(python_exec, version)

        # Case 4: Default → latest PyPI
        else:
            _update_from_pypi(python_exec)

    except Exception as e:
        console.print(f"[red]❌ Update failed:[/red] {e}")
        sys.exit(1)


def _update_from_pypi(python_exec, version=None):
    """Force reinstall sima-cli from PyPI."""
    package = "sima-cli"
    target = f"{package}=={version}" if version else package

    console.print(f"[cyan]⬇️  Updating {package} from PyPI...[/cyan]")
    if version:
        console.print(f"[dim]Requested version:[/dim] [white]{version}[/white]")

    if _is_windows():
        wheel_path = _download_wheel_from_pypi(python_exec, version=version)
        console.print(f"[green]✅ Download complete:[/green] {wheel_path}")
        _print_windows_manual_update_cmd(python_exec, wheel_path)
        return

    cmd = [python_exec, "-m", "pip", "install", "--upgrade", "--force-reinstall", target]
    subprocess.run(cmd, check=True)
    console.print(f"[green]✅ sima-cli successfully updated from PyPI in {python_exec}.[/green]")

def _update_from_url(python_exec, url, internal=False):
    """
    Download a wheel from a URL (authenticated if internal) and install it.

    If the provided URL is actually a local path to an existing .whl file,
    the download step is skipped and installation proceeds directly.
    """
    # Check if it's a local file path (absolute or relative)
    if os.path.exists(url) and url.endswith(".whl"):
        wheel_path = os.path.abspath(url)
        console.print(f"[green]📦 Local wheel detected:[/green] {wheel_path}")
    else:
        console.print(f"[cyan]⬇️  Fetching wheel from:[/cyan] {url}")
        tmpdir = tempfile.mkdtemp(prefix="sima_selfupdate_")

        # ✅ Use built-in downloader (auth, resume, tqdm)
        wheel_path = download_file_from_url(url, dest_folder=tmpdir, internal=internal)
        console.print(f"[green]✅ Download complete:[/green] {wheel_path}")

    if _is_windows():
        _print_windows_manual_update_cmd(python_exec, wheel_path)
        return

    console.print("[cyan]📦 Installing wheel...[/cyan]")

    # Add --no-deps to avoid breaking shared environments
    cmd = [python_exec, "-m", "pip", "install", "--force-reinstall", wheel_path]
    subprocess.run(cmd, check=True)

    console.print(f"[green]✅ sima-cli successfully updated from wheel in {python_exec}.[/green]")

def register_selfupdate_command(main):
    """Attach the 'selfupdate' command to the main Click CLI."""
    main.add_command(selfupdate)
