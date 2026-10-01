import hashlib
import json
import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional
from urllib.parse import urlparse

import click
import requests

from sima_cli.download import download_file_from_url


HOST_PACKAGE_BASE_URL = "https://artifacts.neat.sima.ai/daily-platform-images"
HOST_PACKAGE_INDEX_URL = f"{HOST_PACKAGE_BASE_URL}/index.json"
HOST_PACKAGE_NAME = "sima_pcie_host_pkg.sh"
HOST_PACKAGE_PATH = f"artifacts/host_files/{HOST_PACKAGE_NAME}"
_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def resolve_host_package_url(version_or_url: str) -> str:
    """Resolve an internal daily build ID to its public CDN host package."""
    if not version_or_url:
        raise click.UsageError(
            "A daily platform build is required for a host update "
            "(for example, -v 3.0.0_daily_develop_B1774)."
        )

    if version_or_url.startswith(("http://", "https://")):
        parsed = urlparse(version_or_url)
        path_parts = parsed.path.strip("/").split("/")
        valid_path = (
            len(path_parts) == 5
            and path_parts[0] == "daily-platform-images"
            and _BUILD_ID_RE.fullmatch(path_parts[1]) is not None
            and path_parts[2:] == ["artifacts", "host_files", HOST_PACKAGE_NAME]
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname != "artifacts.neat.sima.ai"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or not valid_path
        ):
            raise click.UsageError(
                "Host updates only accept sima_pcie_host_pkg.sh URLs under "
                "https://artifacts.neat.sima.ai/daily-platform-images/."
            )
        return version_or_url

    if _BUILD_ID_RE.fullmatch(version_or_url) is None:
        raise click.UsageError(f"Invalid daily platform build ID: {version_or_url}")

    return f"{HOST_PACKAGE_BASE_URL}/{version_or_url}/{HOST_PACKAGE_PATH}"


def list_host_packages() -> List[Dict]:
    """Return indexed daily builds that contain the Linux host package."""
    try:
        response = requests.get(HOST_PACKAGE_INDEX_URL, timeout=15)
        response.raise_for_status()
        index = response.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        raise click.ClickException(f"Unable to load the host package catalog: {exc}") from exc

    if not isinstance(index, dict) or index.get("schema_version") != 1:
        raise click.ClickException("The host package catalog has an unsupported format.")

    builds = index.get("builds")
    if not isinstance(builds, list):
        raise click.ClickException("The host package catalog has an unsupported format.")

    packages = []
    for build in builds:
        if not isinstance(build, dict):
            continue
        name = build.get("name")
        if not isinstance(name, str) or _BUILD_ID_RE.fullmatch(name) is None:
            continue
        build_number = build.get("build_number")
        if not isinstance(build_number, int):
            continue
        files = build.get("files")
        if not isinstance(files, list):
            continue
        host_file = next(
            (
                item
                for item in files
                if isinstance(item, dict) and item.get("path") == HOST_PACKAGE_PATH
            ),
            None,
        )
        if host_file is None:
            continue
        sha256 = host_file.get("sha256")
        if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
            continue
        packages.append(
            {
                "name": name,
                "build_number": build_number,
                "size": host_file.get("size", 0),
                "sha256": sha256,
            }
        )

    packages.sort(key=lambda package: package["build_number"], reverse=True)
    if not packages:
        raise click.ClickException(
            "No Linux PCIe host packages are available in the daily build catalog."
        )
    return packages


def select_host_package(version_or_url: Optional[str], *, newest: bool = False) -> Dict:
    """Select an indexed host package, prompting when no version was supplied."""
    packages = list_host_packages()

    if version_or_url:
        package_url = resolve_host_package_url(version_or_url)
        requested_name = package_url.split("/")[-4]
        package = next(
            (candidate for candidate in packages if candidate["name"] == requested_name),
            None,
        )
        if package is None:
            raise click.UsageError(
                f"Daily platform build '{requested_name}' does not contain an indexed Linux host package."
            )
        return package

    if newest:
        return packages[0]

    from InquirerPy import inquirer

    choices = []
    for package in packages:
        size = package["size"]
        size_mib = size / (1024 * 1024) if isinstance(size, int) else 0
        choices.append(
            {
                "name": f"{package['name']:<40} {size_mib:>6.1f} MiB",
                "value": package["name"],
            }
        )

    click.echo("Available Linux PCIe host packages:")
    selected_name = inquirer.fuzzy(
        message="Select a daily platform version:",
        choices=choices,
        max_height="70%",
        instruction="(Use ↑↓ to navigate, / to search, Enter to select)",
    ).execute()
    return next(package for package in packages if package["name"] == selected_name)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as package_file:
        for chunk in iter(lambda: package_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_host_package(version_or_url: Optional[str], *, auto_confirm: bool = False) -> None:
    """Download and execute the Linux PCIe host package installer."""
    package = select_host_package(version_or_url, newest=auto_confirm)
    package_url = resolve_host_package_url(package["name"])
    click.echo(f"📦 Linux PCIe host package: {package_url}")

    with tempfile.TemporaryDirectory(prefix="sima-cli-host-update-") as temp_dir:
        try:
            script_path = download_file_from_url(
                package_url,
                dest_folder=temp_dir,
                internal=False,
            )
        except RuntimeError as exc:
            raise click.ClickException(
                f"Unable to download the Linux PCIe host package: {exc}"
            ) from exc
        if (
            os.path.basename(script_path) != HOST_PACKAGE_NAME
            or not os.path.isfile(script_path)
        ):
            raise click.ClickException(
                f"Downloaded host package is missing or has an unexpected name: {script_path}"
            )
        actual_sha256 = _sha256(script_path)
        if actual_sha256 != package["sha256"]:
            raise click.ClickException(
                "Downloaded host package failed SHA-256 verification; it will not be executed."
            )

        if not auto_confirm:
            click.confirm(
                "⚠️  Install the downloaded package with sudo?",
                abort=True,
            )

        click.echo(f"🚀 Running Linux PCIe host installer: {script_path}")
        result = subprocess.run(
            ["sudo", "sh", script_path],
            cwd=temp_dir,
            check=False,
        )
        if result.returncode != 0:
            raise click.ClickException(
                f"Host driver installer exited with code {result.returncode}."
            )

    click.echo(
        "✅ Linux PCIe host package installed successfully. "
        "Reboot the host before testing the driver."
    )
