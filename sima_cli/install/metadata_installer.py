import os
import re
import tempfile
import click
import json
import sys
import shutil
import tarfile
import zipfile
import stat
import shlex
import platform
import hashlib
from urllib.parse import urlparse, quote, urljoin, unquote
from typing import Dict, List
from tqdm import tqdm
from pathlib import Path
import subprocess
import requests

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from typing import Tuple

from huggingface_hub import snapshot_download

from sima_cli.utils.disk import check_disk_space
from sima_cli.utils.env import get_environment_type, get_exact_devkit_type, get_sima_build_version
from sima_cli.download.downloader import download_file_from_url
from sima_cli.install.metadata_validator import validate_metadata, MetadataValidationError
from sima_cli.install.compatibility import current_host_arch, normalize_host_arch, version_matches
from sima_cli.install.metadata_info import print_metadata_summary, parse_size_string_to_bytes
from sima_cli.utils.container_registries import install_from_cr
from sima_cli.install.registry import PackageRegistry

console = Console()
registry = PackageRegistry()

class InstallationPreflightError(click.ClickException):
    def show(self, file=None) -> None:
        console.print(
            Panel(
                Text(str(self.message), style="yellow"),
                title="Installation Failed",
                border_style="yellow",
                expand=False,
            )
        )


def _ensure_install_dir_writable(install_dir: str, command_name: str = "sima-cli install") -> None:
    target = Path(install_dir or ".").expanduser()
    display_path = Path.cwd() if str(target) == "." else target

    check_dir = target
    if not check_dir.exists():
        check_dir = target.parent
        while not check_dir.exists() and check_dir != check_dir.parent:
            check_dir = check_dir.parent

    if not check_dir.is_dir():
        raise click.ClickException(f"Install path '{display_path}' is not a directory.")

    try:
        with tempfile.NamedTemporaryFile(prefix=".sima-cli-write-test-", dir=str(check_dir)):
            pass
    except OSError as exc:
        if str(target) == ".":
            raise InstallationPreflightError(
                f"Current directory '{Path.cwd()}' is not writable.\n\n"
                "This install downloads package assets into the current directory before installation.\n\n"
                "Run the command again from a writable work directory, for example:\n"
                "  mkdir -p ~/sima-install && cd ~/sima-install\n"
                f"  {command_name} ...\n\n"
                "Or choose a destination explicitly:\n"
                f"  {command_name} ... --install-dir <writable-directory>"
            ) from exc
        raise InstallationPreflightError(
            f"Install directory '{display_path}' is not writable.\n\n"
            "This install downloads package assets into the install directory before installation.\n\n"
            "Choose a writable destination and rerun the command:\n"
            f"  {command_name} ... --install-dir <writable-directory>"
        ) from exc

def _copy_dir(src: Path, dest: Path, label: str):
    """
    Copy files from src → dest, merging with existing files (no deletion).
    Does NOT overwrite files if they already exist.
    Ensures that all parent directories for dest are created.
    """
    if not src.exists():
        raise FileNotFoundError(f"SDK {label} not found: {src}")

    dest.mkdir(parents=True, exist_ok=True)

    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            _copy_dir(item, target, label)
        else:
            if not target.exists():
                shutil.copy2(item, target)
    
    click.echo(f"✅ Copied {label} into {dest}")

def _prepare_pipeline_project(repo_dir: Path):
    """
    Prepare a pipeline project by copying required SDK sources into the repo.

    Steps:
      1. Copy core sources into the project folder
      2. Parse .project/pluginsInfo
      3. Copy required plugin sources from the SDK plugin zoo
    """
    plugins_info_file = repo_dir / ".project" / "pluginsInfo.json"
    if not plugins_info_file.exists():
        return 

    click.echo("📦 Preparing pipeline project...")

    try:
        data = json.loads(plugins_info_file.read_text())
        plugins = data.get("pluginsInfo", [])
    except Exception as e:
        raise RuntimeError(f"Failed to read {plugins_info_file}: {e}")

    # Step a: copy core
    # Define what to copy
    copy_map = [
        (
            Path("/usr/local/simaai/plugin_zoo/gst-simaai-plugins-base/core"),
            repo_dir / "core",
            "core"
        ),
        (
            Path("/usr/local/simaai/utils/gst_app"),
            repo_dir / "dependencies" / "gst_app",
            "dependencies/gst_app"
        ),
        (
            Path("/usr/local/simaai/plugin_zoo/gst-simaai-plugins-base/gst/templates"),
            repo_dir / "plugins" / "templates",
            "plugins/templates"
        ),
    ]

    # Execute
    for src, dest, label in copy_map:
        _copy_dir(src, dest, label)

    # Step b/c: scan plugin paths and copy SDK plugins
    sdk_plugins_base = Path("/usr/local/simaai/plugin_zoo/gst-simaai-plugins-base/gst")
    sdk_alt_base = sdk_plugins_base / "PyGast-plugins"

    dest_plugins_dir = repo_dir / "plugins"
    dest_plugins_dir.mkdir(exist_ok=True)

    for plugin in plugins:
        try:
            path = plugin.get("path", "")
            if not path:
                continue
            parts = path.split("/")
            if len(parts) < 2:
                continue

            plugin_name = parts[1]

            # Look first in gst/, then fallback to gst/PyGast-plugins/
            sdk_plugin_path = sdk_plugins_base / plugin_name
            if not sdk_plugin_path.exists():
                sdk_plugin_path = sdk_alt_base / plugin_name

            if not sdk_plugin_path.exists():
                click.echo(
                    f"⚠️ Missing plugin source: {plugin_name} in the SDK, skipping. "
                    "It is likely a custom plugin already in the repo so it's safe to ignore this warning."
                )
                continue

            dest_plugin_path = dest_plugins_dir / plugin_name
            dest_plugin_path.mkdir(parents=True, exist_ok=True)

            # Walk the SDK plugin dir and copy only missing files
            for src_file in sdk_plugin_path.rglob("*"):
                if src_file.is_file():
                    rel_path = src_file.relative_to(sdk_plugin_path)
                    dest_file = dest_plugin_path / rel_path
                    if dest_file.exists():
                        click.echo(f"↩️  Skipped existing file in the repo: {dest_file}")
                        continue
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)

            click.echo(f"✅ Copied plugin {plugin_name} into {dest_plugin_path} (safe copy)")

        except Exception as e:
            click.echo(f"❌ Error copying plugin {plugin}: {e}")

    click.echo("🎉 Pipeline project prepared.")

def _download_requirements_wheels(repo_dir: Path):
    """
    Look for resources/dependencies/requirements.txt under the repo,
    parse each line, and download wheels into the same folder.
    Supports optional pip download flags in parentheses.

    Example line formats:
        jax==0.6.2
        jaxlib==0.6.2 (--platform manylinux2014_aarch64 --python-version 310 --abi cp310)
    """
    deps_dir = repo_dir / "resources" / "dependencies"
    req_file = deps_dir / "requirements.txt"

    if not req_file.exists():
        click.echo("⚠️  No requirements.txt found under resources/dependencies in the repo, skipping wheel download, safe to ignore this message")
        return

    deps_dir.mkdir(parents=True, exist_ok=True)

    with req_file.open("r") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not lines:
        click.echo("⚠️ requirements.txt is empty, nothing to download.")
        return

    for line in lines:
        # Split package and extra params if present
        if "(" in line and ")" in line:
            pkg_part, extra = line.split("(", 1)
            package = pkg_part.strip()
            extra_args = shlex.split(extra.strip(") "))
        else:
            package = line.strip()
            extra_args = []

        click.echo(f"⬇️  Downloading {package} {extra_args if extra_args else ''}")

        try:
            cmd = [
                "pip3", "download", "--no-deps",
                "--only-binary=:all:",
                "-d", str(deps_dir),
                package,
            ] + extra_args

            rc = os.system(" ".join(shlex.quote(c) for c in cmd))
            if rc != 0:
                click.echo(f"❌ pip download failed for {package}")
            else:
                click.echo(f"✅ Downloaded {package} into {deps_dir}")
        except Exception as e:
            click.echo(f"❌ Error downloading {package}: {e}")

def _download_github_repo(owner: str, repo: str, ref: str, dest_folder: str, token: str = None) -> str:
    """
    Download and extract a GitHub repo tarball via the REST API (no git required).

    Args:
        owner (str): GitHub org/user
        repo (str): Repo name
        ref (str): Branch, tag, or commit (default = default branch)
        dest_folder (str): Where to extract
        token (str): Optional GitHub token for private repos

    Returns:
        str: Path to the extracted repo
    """
    # Encode ref for API, but sanitize separately for filesystem usage
    if ref:
        ref_encoded = quote(ref, safe="")  # safe for URL
        ref_safe = ref.replace("/", "_")   # safe for filesystem
        url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{ref_encoded}"
    else:
        ref_encoded = ref_safe = None
        url = f"https://api.github.com/repos/{owner}/{repo}/tarball"

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    click.echo(f"🐙 Downloading GitHub repo: {owner}/{repo}" + (f"@{ref}" if ref else ""))

    with requests.get(url, headers=headers, stream=True) as r:
        if r.status_code in (401, 403):
            raise PermissionError("Authentication required for GitHub repo")
        r.raise_for_status()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp_file:
            for chunk in r.iter_content(chunk_size=8192):
                tmp_file.write(chunk)
            tmp_path = Path(tmp_file.name)

    # Use sanitized ref in folder name (if provided)
    repo_dir = Path(dest_folder) / repo
    repo_dir.mkdir(parents=True, exist_ok=True)

    _extract_tar_strip_top_level(tmp_path, repo_dir)
    tmp_path.unlink(missing_ok=True)

    click.echo(f"✅ Downloaded GitHub repo to folder: {repo_dir}")
    _download_requirements_wheels(repo_dir=repo_dir)

    try:
        _prepare_pipeline_project(repo_dir)
    except Exception as e:
        click.echo(f"⚠️  Pipeline preparation skipped: {e}")

    return str(repo_dir)

def _validate_and_normalize_sha256(value, field_name: str):
    if value is None:
        return None
    if not isinstance(value, str):
        raise click.ClickException(f"❌ '{field_name}' must be a string.")
    checksum = value.strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise click.ClickException(f"❌ '{field_name}' must be a valid 64-character SHA-256 hex string.")
    return checksum

def _compute_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            if chunk:
                hasher.update(chunk)
    return hasher.hexdigest()

def _resolve_resource_url(base_url: str, resource: str) -> str:
    """
    Resolve a metadata resource to a downloadable URL.

    Metadata resources are file names or relative paths. Encode each path segment
    so URL-reserved characters that are valid in artifact names, such as '+', do
    not get interpreted by object storage/CDNs as a different key.
    """
    parsed_resource = urlparse(resource)
    if parsed_resource.scheme or parsed_resource.netloc:
        return resource

    encoded_resource = "/".join(
        quote(segment, safe="")
        for segment in resource.split("/")
    )
    return urljoin(base_url, encoded_resource)

def _resolve_resource_url_candidates(base_url: str, resource: str) -> List[str]:
    primary_url = _resolve_resource_url(base_url, resource)

    parsed_resource = urlparse(resource)
    if parsed_resource.scheme or parsed_resource.netloc or "%" not in resource:
        return [primary_url]

    percent_preserving_resource = "/".join(
        quote(segment, safe="%")
        for segment in resource.split("/")
    )
    fallback_url = urljoin(base_url, percent_preserving_resource)
    if fallback_url == primary_url:
        return [primary_url]
    return [primary_url, fallback_url]

def _metadata_resource_path(dest_folder: str, resource: str, resource_url: str) -> Path:
    parsed_resource = urlparse(resource)
    if parsed_resource.scheme or parsed_resource.netloc:
        file_name = os.path.basename(urlparse(resource_url).path)
    else:
        file_name = os.path.basename(resource)

    if not file_name:
        raise click.ClickException(f"❌ Cannot determine file name for resource '{resource}'.")

    return Path(dest_folder) / file_name

def _normalize_downloaded_metadata_resource(local_path: str, expected_path: Path) -> str:
    downloaded_path = Path(local_path)
    if downloaded_path == expected_path:
        return local_path

    if expected_path.exists():
        expected_path.unlink()
    downloaded_path.rename(expected_path)
    return str(expected_path)

def _download_metadata_file_resource(
    resource: str,
    resource_urls: List[str],
    dest_folder: str,
    dest_path: Path,
    internal: bool,
) -> str:
    errors: List[str] = []
    for index, resource_url in enumerate(resource_urls):
        try:
            local_path = download_file_from_url(
                url=resource_url,
                dest_folder=dest_folder,
                internal=internal
            )
            return _normalize_downloaded_metadata_resource(local_path, dest_path)
        except Exception as e:
            errors.append(f"{resource_url}: {e}")
            if index < len(resource_urls) - 1:
                click.echo(
                    f"⚠️  Download failed for encoded resource URL; retrying percent-preserving URL for '{resource}'."
                )

    raise click.ClickException("; ".join(errors))


def _resource_basename(resource: str) -> str:
    parsed = urlparse(resource)
    path = parsed.path if parsed.scheme or parsed.netloc else resource
    return unquote(os.path.basename(path)).lower()


def _is_wheel_resource(resource: str) -> bool:
    return _resource_basename(resource).endswith(".whl")


def _wheel_platform_tag(resource: str) -> str:
    name = _resource_basename(resource)
    if not name.endswith(".whl"):
        return ""
    parts = name[:-4].split("-")
    if len(parts) < 5:
        return ""
    return parts[-1].lower()


def _current_wheel_platform() -> tuple:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_family = "mac"
    elif system == "windows":
        os_family = "windows"
    elif system == "linux":
        os_family = "linux"
    else:
        os_family = system

    if machine in {"x86_64", "amd64"}:
        arch_tokens = {"x86_64", "amd64"}
    elif machine in {"aarch64", "arm64"}:
        arch_tokens = {"aarch64", "arm64"}
    elif machine in {"i386", "i686", "x86"}:
        arch_tokens = {"i386", "i686", "x86", "win32"}
    else:
        arch_tokens = {machine} if machine else set()

    return os_family, arch_tokens


def _wheel_arch_matches(platform_tag: str, arch_tokens: set) -> bool:
    known_arch_tokens = {"x86_64", "amd64", "aarch64", "arm64", "i386", "i686", "x86", "win32"}
    present_tokens = {token for token in known_arch_tokens if token in platform_tag}
    return not present_tokens or bool(present_tokens & arch_tokens)


def _is_compatible_wheel_resource(resource: str) -> bool:
    platform_tag = _wheel_platform_tag(resource)
    if not platform_tag:
        return True
    if platform_tag in {"any", "none"}:
        return True

    os_family, arch_tokens = _current_wheel_platform()
    if os_family == "mac":
        os_matches = platform_tag.startswith("macosx") or "macos" in platform_tag
    elif os_family == "windows":
        os_matches = platform_tag.startswith("win") or platform_tag in {"win32", "win_amd64", "win_arm64"}
    elif os_family == "linux":
        os_matches = "linux" in platform_tag
    else:
        os_matches = False

    return os_matches and _wheel_arch_matches(platform_tag, arch_tokens)


def _filter_download_compatible_resources(resources: list) -> list:
    filtered = []
    for resource in resources:
        if _is_wheel_resource(resource) and not _is_compatible_wheel_resource(resource):
            click.echo(f"⏭️  Skipping incompatible wheel for this platform: {resource}")
            continue
        filtered.append(resource)
    return filtered


def _download_assets(metadata: dict, base_url: str, dest_folder: str, internal: bool = False, skip_models: bool = False, tag: str = None) -> list:
    """
    Downloads resources defined in metadata to a local destination folder.

    Supports resource types:
        - Regular files or URLs
        - Hugging Face repos (hf:<repo_id>@revision)
        - GitHub repos (gh:<owner>/<repo>@ref)
        - Container registries (cr:<image>[:tag], ghcr:<owner>/<image>[:tag])

    Args:
        metadata (dict): Parsed and validated metadata
        base_url (str): Base URL of the metadata file (used to resolve relative resource paths)
        dest_folder (str): Local path to download resources into
        internal (bool): Whether to use internal routing (e.g., Artifactory Docker registry)
        skip_models (bool): If True, skips downloading any file path starting with 'models/'
        tag (str): metadata.json tag from GitHub passed into resources if applicable

    Returns:
        list: Paths to the downloaded local files or pulled container image identifiers
    """
    resources = metadata.get("resources", [])
    if not resources:
        raise click.ClickException("❌ No 'resources' defined in metadata.")
    resource_checksums = metadata.get("resources-checksum", {})
    if not isinstance(resource_checksums, dict):
        raise click.ClickException("❌ 'resources-checksum' must be an object when provided.")

    os.makedirs(dest_folder, exist_ok=True)
    local_paths = []

    # Filter model files if needed
    filtered_resources = []
    for r in resources:
        if not isinstance(r, str):
            raise click.ClickException("❌ Each entry in 'resources' must be a string.")
        if skip_models and r.strip().lower().startswith("models/"):
            click.echo(f"⏭️  Skipping model file: {r}")
            continue
        filtered_resources.append(r)

    if metadata.get("download-compatible-files-only"):
        filtered_resources = _filter_download_compatible_resources(filtered_resources)

    if not filtered_resources:
        click.echo("ℹ️ No compatible resources to download.")
        return []

    click.echo(f"📥 Downloading {len(filtered_resources)} resource(s) to: {dest_folder}\n")

    for resource in filtered_resources:
        try:
            expected_sha256 = _validate_and_normalize_sha256(
                resource_checksums.get(resource),
                f"resources-checksum.{resource}",
            )

            # Handle Hugging Face snapshot-style URL: "hf:<repo_id>@version"
            if resource.startswith("hf:"):
                # Strip prefix and split by @
                resource_spec = resource[3:]
                if "@" in resource_spec:
                    repo_id, revision = resource_spec.split("@", 1)
                else:
                    repo_id, revision = resource_spec, None

                if "/" not in repo_id:
                    raise click.ClickException(f"❌ Invalid Hugging Face repo spec: {resource}")

                org, name = repo_id.split("/", 1)
                target_dir = os.path.join(dest_folder, name)

                click.echo(f"🤗 Downloading Hugging Face repo: {org}/{repo_id}" + (f"@{revision}" if revision else ""))
                model_path = snapshot_download(
                    repo_id=repo_id,
                    revision=revision,
                    local_dir=target_dir
                )
                local_paths.append(model_path)
                continue

            # 🐙 GitHub repo
            if resource.startswith("gh:"):
                resource_spec = resource[3:]
                if "@" in resource_spec:
                    repo_id, ref = resource_spec.split("@", 1)
                else:
                    repo_id, ref = resource_spec, tag

                if "/" not in repo_id:
                    raise click.ClickException(f"❌ Invalid GitHub repo spec: {resource}")

                owner, name = repo_id.split("/", 1)

                try:
                    token = os.getenv("GITHUB_TOKEN", None)
                    repo_path = _download_github_repo(owner, name, ref, dest_folder, token)
                except Exception as e:
                    raise click.ClickException(
                        f"❌ Failed to download GitHub repo {owner}/{name}@{ref or 'default'}: {e}"
                    )
                local_paths.append(repo_path)
                continue

            # 🐳 Container registry support
            if resource.startswith("cr:") or resource.startswith("ghcr:"):
                install_from_cr(resource, internal=internal)
                continue

            # 🌐 Standard file or URL
            resource_urls = _resolve_resource_url_candidates(base_url, resource)
            dest_path = _metadata_resource_path(dest_folder, resource, resource_urls[0])

            if expected_sha256 and dest_path.exists() and dest_path.is_file():
                existing_sha = _compute_sha256(dest_path)
                if existing_sha != expected_sha256:
                    click.echo(f"♻️  Checksum mismatch for existing file '{dest_path.name}', re-downloading.")
                    dest_path.unlink()

            local_path = _download_metadata_file_resource(
                resource=resource,
                resource_urls=resource_urls,
                dest_folder=dest_folder,
                dest_path=dest_path,
                internal=internal
            )
            if expected_sha256:
                actual_sha = _compute_sha256(Path(local_path))
                if actual_sha != expected_sha256:
                    raise click.ClickException(
                        f"❌ SHA-256 mismatch for '{resource}'. expected={expected_sha256}, actual={actual_sha}"
                    )
            click.echo(f"✅ Downloaded: {resource}")
            local_paths.append(local_path)

        except Exception as e:
            raise click.ClickException(f"❌ Failed to download resource '{resource}': {e}")

    return local_paths

def _mark_install_script_executable(metadata: Dict, install_dir: str) -> None:
    script = metadata.get("installation", {}).get("script", "")
    if not isinstance(script, str):
        return

    script = script.strip()
    if not script or any(char in script for char in "\n\r;&|`$<>"):
        return

    script_path = Path(script)
    if not script_path.is_absolute():
        script_path = Path(install_dir) / script_path
    try:
        resolved = script_path.resolve()
        install_root = Path(install_dir).resolve()
        if resolved != install_root and install_root not in resolved.parents:
            return
        if resolved.is_file():
            resolved.chmod(resolved.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        return

def selectable_resource_handler(metadata):
    """
    Allow user to select one or more opt-in resources to download.
    - All selectable items shown as checkboxes
    - 'Skip' option appended at the end
    - Honors `selected: true` and existing `resources` for preselection
    - Removes unselected items from `selectable-resources` field
    """
    selectable = metadata.get("selectable-resources")
    if not selectable:
        return metadata

    # ──────────────────────────────────────────────
    # Build choice list with preselection
    # ──────────────────────────────────────────────
    existing = set(metadata.get("resources", []))
    choices = []

    for item in selectable:
        name = item.get("name", "Unnamed")
        url = item.get("url")
        label = f"{name} ({url})" if url else name
        res = item.get("resource")

        is_selected = bool(item.get("selected")) or (res in existing)

        choices.append({
            "name": label,
            "value": label,
            "enabled": is_selected,  # ✅ visually preselects
        })

    # Append "Skip" option
    choices.append({
        "name": "🚫 Skip",
        "value": "__skip__",
        "enabled": False,
    })

    # ──────────────────────────────────────────────
    # Help banner
    # ──────────────────────────────────────────────
    banner_text = """[bold white]Use ↑ / ↓ to navigate[/bold white]
[bold white]Press [cyan]Space[/cyan] to select or deselect items[/bold white]
[bold white]Press [green]Enter[/green] to confirm selection[/bold white]
[dim]Tip: You can select multiple items before pressing Enter.[/dim]"""

    console.print(
        Panel(
            banner_text,
            title="[bold green] Select one or more opt-in resources to download[/bold green]",
            title_align="left",
            border_style="green",
            padding=(1, 2),
            expand=False,
        )
    )

    # ──────────────────────────────────────────────
    # Prompt user
    # ──────────────────────────────────────────────
    from InquirerPy import inquirer
    selected = inquirer.checkbox(
        message=":",
        choices=choices,
        instruction="Use Space key to toggle selection(s)",
        qmark="📦",
        enabled_symbol="[x]",
        disabled_symbol="[ ]",
        pointer="❯",
        transformer=lambda res: (
            f"[bold green]{len(res)} selected[/bold green]"
            if res else "[dim]None selected[/dim]"
        ),
    ).execute()

    # ──────────────────────────────────────────────
    # Handle Skip
    # ──────────────────────────────────────────────
    if "__skip__" in selected:
        console.print("[green]✅ No selectable resources chosen.[/green]")
        metadata["selectable-resources"] = []  # Remove all if skipped
        return metadata

    # ──────────────────────────────────────────────
    # Update selected resources
    # ──────────────────────────────────────────────
    metadata.setdefault("resources", [])
    metadata.setdefault("resources-checksum", {})
    updated_selectables = []

    for label, entry in zip([c["value"] for c in choices[:-1]], selectable):  # skip 'Skip'
        res = entry.get("resource")

        if label in selected:
            if res and res not in metadata["resources"]:
                metadata["resources"].append(res)
                console.print(f"[bold green]✅ Added:[/bold green] {entry.get('name','(unnamed)')} → {res}")
            checksum = _validate_and_normalize_sha256(
                entry.get("checksum"),
                f"selectable-resources.{entry.get('name', res)}.checksum",
            )
            if checksum and res:
                metadata["resources-checksum"][res] = checksum
            updated_selectables.append(entry)  # ✅ keep only selected
        else:
            console.print(f"[dim]⏭ Skipped:[/dim] {entry.get('name','(unnamed)')}")

    metadata["selectable-resources"] = updated_selectables

    console.print("[bold green]✅ Resource selection complete.[/bold green]")
    return metadata


def _download_and_validate_metadata(metadata_url, internal=False, force=False):
    """
    Downloads (if remote), validates, and parses metadata from a given URL or local file path.

    Args:
        metadata_url (str): URL or local path to a metadata.json file
        internal (bool): Whether to use internal mirrors or logic in downloader
        force (bool): whether to ignore compatibility check result

    Returns:
        tuple: (parsed metadata dict, folder containing the metadata file)
    """
    try:
        parsed = urlparse(metadata_url)

        # Case 1: Local file (e.g., /path/to/file, ./file, or C:\path on Windows).
        # urlparse treats a Windows drive letter as a single-char scheme, so also
        # accept that case and any path that exists on disk.
        is_local = (
            parsed.scheme in ("", "file")
            or len(parsed.scheme) == 1
            or os.path.isfile(metadata_url)
        )
        if is_local:
            metadata_path = metadata_url if parsed.scheme != "file" else parsed.path
            if not os.path.isfile(metadata_path):
                raise FileNotFoundError(f"File not found: {metadata_path}")
            click.echo(f"📄 Using local metadata file: {metadata_path}")

        # Case 2: Remote URL
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                metadata_path = download_file_from_url(
                    url=metadata_url,
                    dest_folder=tmpdir,
                    internal=internal
                )
                click.echo(f"⬇️  Downloaded metadata to: {metadata_path}")
                
                # Must copy to outside tmpdir since tmpdir will be deleted
                # But since we're returning contents only, no need to keep file
                with open(metadata_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                validate_metadata(metadata)
                if _is_platform_compatible(metadata, force) or force:
                    click.echo("✅ Metadata validated successfully.")
                    metadata = selectable_resource_handler(metadata)
                    return metadata, os.path.dirname(metadata_path)
            
                return None, None

        # Common validation logic for local file
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        validate_metadata(metadata)
        if _is_platform_compatible(metadata, force=force) or force:
            metadata = selectable_resource_handler(metadata)
            click.echo("✅ Metadata validated successfully.")
            return metadata, os.path.dirname(os.path.abspath(metadata_path))

    except MetadataValidationError as e:
        click.echo(f"❌ Metadata validation failed: {e}")
        raise click.Abort()

    except Exception as e:
        click.echo(f"❌ Failed to retrieve or parse metadata from {metadata_url}: {e}")
        raise click.Abort()
    
def _check_whether_disk_is_big_enough(metadata: dict, force: bool = False):
    """
    Check whether available disk space is sufficient for:
      - Base install size (metadata['size']['install'])
      - Combined pull space from selected modules (pull_space_in_gb)
    """
    try:
        # ──────────────────────────────────────────────
        # Step 1: Parse base installation size
        # ──────────────────────────────────────────────
        base_install_bytes = 0
        install_size_str = metadata.get("size", {}).get("install")
        if install_size_str:
            base_install_bytes = parse_size_string_to_bytes(install_size_str)

        # ──────────────────────────────────────────────
        # Step 2: Add up selectable module pull space
        # ──────────────────────────────────────────────
        selectable = metadata.get("selectable-resources", [])
        total_pull_gb = sum(float(item.get("pull_space_in_gb", 0)) for item in selectable)
        total_pull_bytes = total_pull_gb * (1024 ** 3)

        # Total required = base + modules
        total_required_bytes = base_install_bytes + total_pull_bytes

        # ──────────────────────────────────────────────
        # Step 3: Check against actual available disk
        # ──────────────────────────────────────────────
        disk = shutil.disk_usage(".")
        available_bytes = disk.free
        available_gb = available_bytes / (1024 ** 3)
        required_gb = total_required_bytes / (1024 ** 3)

        click.echo(f"💾 Available disk space: {click.style(f'{available_gb:.2f} GB', fg='green')}")
        click.echo(f"📦 Required space (base + modules): {click.style(f'{required_gb:.2f} GB', fg='yellow')}")

        if available_bytes < total_required_bytes:
            shortage_gb = required_gb - available_gb
            click.echo()
            click.echo(click.style("❌ Not enough disk space!", fg="red", bold=True))
            click.echo(click.style(f"   Required: {required_gb:.2f} GB", fg="red"))
            click.echo(click.style(f"   Available: {available_gb:.2f} GB", fg="red"))
            click.echo(click.style(f"   Shortfall: {shortage_gb:.2f} GB", fg="red"))

            if not force:
                click.echo(click.style("\n🧹 Please free up space before continuing.", fg="yellow", bold=True))
                raise click.Abort()

        click.echo(click.style("✅ Enough disk space for installation and resources.", fg="green"))
        return True

    except Exception as e:
        click.echo(click.style(f"❌ Failed to validate disk space {e}", fg="red"))
        raise click.Abort()

def _extract_tar_streaming(tar_path: Path, extract_dir: Path):
    """
    Extract tar while preserving full folder structure.
    """
    extracted_files = 0
    with tarfile.open(tar_path, "r:*") as tar:
        with tqdm(desc=f"📦 Extracting {tar_path.name}", unit=" file") as pbar:
            while True:
                member = tar.next()
                if member is None:
                    break

                # Don't strip anything — preserve full path
                if not member.name.strip():
                    print(f"⚠️ Skipping empty member in archive: {member}")
                    continue

                tar.extract(member, path=extract_dir)
                extracted_files += 1
                pbar.update(1)

    print(f"✅ Extracted {extracted_files} files to {extract_dir}/")

def _extract_zip_streaming(zip_path: Path, extract_dir: Path, overwrite: bool = True):
    """
    Extract a .zip file using streaming and flatten one top-level directory if present.
    - Handles directory entries correctly
    - Preserves unix perms when available
    - Zip-slip safe
    """
    def strip_top_level(p: str) -> Path:
        parts = Path(p).parts
        if not parts:
            return Path()
        return Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])

    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        with tqdm(total=len(members), desc=f"📦 Extracting {zip_path.name}", unit="file") as pbar:
            for info in members:
                # Compute flattened path
                stripped = strip_top_level(info.filename)

                # Some zips can have '' or '.' entries; skip them
                if str(stripped).strip() in {"", ".", "./"}:
                    pbar.update(1)
                    continue

                target = (extract_dir / stripped).resolve()

                # Zip-slip guard: ensure target stays under extract_dir
                if not str(target).startswith(str(extract_dir.resolve()) + os.sep):
                    pbar.update(1)
                    continue  # or raise RuntimeError("Unsafe zip path detected")

                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    pbar.update(1)
                    continue

                # Ensure parent exists
                target.parent.mkdir(parents=True, exist_ok=True)

                # Skip if exists and not overwriting
                if target.exists() and not overwrite:
                    pbar.update(1)
                    continue

                # Stream copy the file
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                # Preserve unix permissions if present
                perms = info.external_attr >> 16
                if perms and not stat.S_ISDIR(perms):
                    try:
                        os.chmod(target, perms)
                    except Exception:
                        pass

                pbar.update(1)

    print(f"✅ Extracted {len(members)} entries to {extract_dir}/")

def _extract_tar_strip_top_level(tar_path: Path, extract_dir: Path):
    """Extract a GitHub tarball, stripping the top-level folder."""
    with tarfile.open(tar_path, "r:*") as tar:
        members = tar.getmembers()

        # Detect top-level prefix (first part before '/')
        top_level = None
        if members:
            first_name = members[0].name
            top_level = first_name.split("/", 1)[0]

        for member in members:
            # Strip top-level folder
            if top_level and member.name.startswith(top_level + "/"):
                member.name = member.name[len(top_level) + 1 :]
            if not member.name:
                continue
            tar.extract(member, path=extract_dir)

def _combine_multipart_files(folder: str, local_paths=None):
    """
    Scan a folder for multipart files like name-split-aa, -ab, etc.,
    combine them into a single file, and remove the split parts.
    Then auto-extract .tar files with progress.
    """
    folder = Path(folder)
    parts_by_base = {}

    # Step 1: Group parts by base name
    candidate_files = []
    if local_paths is None:
        candidate_files = [p for p in folder.iterdir() if p.is_file()]
    else:
        folder_resolved = folder.resolve()
        for p in local_paths:
            path = Path(p).resolve()
            if path.exists() and path.is_file() and path.parent == folder_resolved:
                candidate_files.append(path)

    for file in candidate_files:
        if not file.is_file():
            continue

        match = re.match(r"(.+)-split-([a-z]{2})$", file.name)
        if match:
            base, part = match.groups()
            parts_by_base.setdefault(base, []).append((part, file))

    # Step 2: Process each group
    for base, parts in parts_by_base.items():
        parts.sort(key=lambda x: x[0])
        output_file = folder / f"{base}.tar"
        total_size = sum(part_file.stat().st_size for _, part_file in parts)

        print(f"\n🧩 Reassembling: {output_file.name} from {len(parts)} parts")

        if not output_file.exists():
            with open(output_file, "wb") as outfile, tqdm(
                total=total_size,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=f"Combining {output_file.name}",
            ) as pbar:
                for _, part_file in parts:
                    with open(part_file, "rb") as infile:
                        while True:
                            chunk = infile.read(1024 * 1024)  # 1MB
                            if not chunk:
                                break
                            outfile.write(chunk)
                            pbar.update(len(chunk))

        # Step 3: Remove original parts
        # for _, part_file in parts:
        #     part_file.unlink()

        print(f"✅ Created: {output_file.name} ({output_file.stat().st_size / 1e6:.2f} MB)")

        # Step 4: Auto-extract .tar
        extract_dir = folder / base
        print(f"📦 Extracting {output_file.name} to {extract_dir}/")
        _extract_tar_streaming(output_file, extract_dir)

        print(f"✅ Extracted to: {extract_dir}/")

def _extract_archives_in_folder(folder: str, local_paths):
    """
    Extract .tar, .gz, .tar.gz, and .zip files in the given folder,
    but only if they are listed in local_paths.
    Uses streaming to avoid NFS performance issues.
    """
    folder = Path(folder).resolve()
    for local_path in local_paths:
        file = Path(local_path).resolve()
        if not file.exists() or not file.is_file():
            continue
        if file.parent != folder:
            continue

        # TAR, GZ, TAR.GZ → all handled by _extract_tar_streaming
        if file.suffix in [".tar", ".gz"] or file.name.endswith(".tar.gz"):
            extract_dir = folder / file.stem.replace(".tar", "")
            print(f"📦 Extracting TAR/GZ: {file.name} to {extract_dir}/")
            _extract_tar_streaming(file, extract_dir)

        # ZIP
        elif file.suffix == ".zip":
            extract_dir = folder / file.stem
            print(f"📦 Extracting ZIP: {file.name} to {extract_dir}/")
            _extract_zip_streaming(file, extract_dir)


def _version_to_tuple(v: str):
    """Convert '15.5' -> (15, 5), safely handling missing parts."""
    parts = v.strip().split(".")
    return tuple(int(p) for p in parts if p.isdigit())

def _print_compatible_platforms(platforms):
    """Pretty-print supported platforms using a bordered Rich table."""
    from rich.table import Table
    from rich import box
    table = Table(
        title="Supported Platforms",
        title_style="bold white",
        header_style="bold cyan",
        border_style="cyan",
        box=box.SQUARE,  # ✅ Use visible square borders
        show_lines=True,
        expand=False,
    )

    table.add_column("Platform Type", style="bold cyan", no_wrap=True)
    table.add_column("Details", style="bold yellow")
    table.add_column("Supported Versions / Targets", style="white")
    table.add_column("Arch", style="white")

    for p in platforms:
        ptype = p.get("type", "N/A")

        if ptype == "host":
            arch = ", ".join(p.get("arch") or ["All"])
            versions_by_os = {str(k).lower(): v for k, v in p.get("versions", {}).items()}
            for os_name in p.get("os", []):
                versions = versions_by_os.get(str(os_name).lower(), ["All"])
                table.add_row(ptype, os_name.capitalize(), ", ".join(versions), arch)

        elif ptype == "board":
            compat = p.get("compatible_with", [])
            version = p.get("version", "All")
            compat_text = ", ".join(compat) if compat else "N/A"
            table.add_row(ptype, compat_text, version, "N/A")

        else:
            table.add_row(ptype, "N/A", "N/A", "N/A")

    console.print(table)
    console.print()

def _compare_versions(current: str, condition: str) -> bool:
    """
    Compare current version (e.g. '15.5') against a condition string like:
      '>=12', '<=16', '>20.04', '<23.0', '14'
    """
    cond = condition.strip()
    if re.match(r"^(>=|<=|==|=|>|<)", cond):
        try:
            return version_matches(current, cond)
        except ValueError:
            pass

    cur = _version_to_tuple(current)

    # Detect operator and target
    match = re.match(r"^(>=|<=|>|<|=)?\s*([\d.]+)$", cond)
    if not match:
        return current == condition  # fallback exact match

    op, target_str = match.groups()
    target = _version_to_tuple(target_str)
    op = op or "="

    if op == "=":
        return cur[:len(target)] == target
    elif op == ">":
        return cur > target
    elif op == ">=":
        return cur >= target
    elif op == "<":
        return cur < target
    elif op == "<=":
        return cur <= target
    return False


def _get_palette_sdk_version(release_file: Path = Path("/etc/sdk-release")) -> str:
    try:
        content = release_file.read_text(encoding="utf-8")
    except OSError:
        return ""

    match = re.search(r"^SDK Version\s*=\s*(\S+)", content, flags=re.MULTILINE)
    if not match:
        return ""
    return match.group(1).split("_", 1)[0].strip()


def _detected_host_platform() -> tuple:
    os_name = platform.system().lower()
    os_version = "Unknown"

    if os_name == "darwin":
        return "mac", platform.mac_ver()[0] or "Unknown", current_host_arch()
    if os_name == "windows":
        return "windows", platform.release() or "Unknown", current_host_arch()
    if os_name != "linux":
        return os_name, os_version, current_host_arch()

    os_release = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                key, separator, value = line.strip().partition("=")
                if separator:
                    os_release[key] = value.strip().strip('"')
    except OSError:
        os_release = {}

    distro_id = (os_release.get("ID") or "").lower()
    version_id = os_release.get("VERSION_ID") or ""
    if distro_id == "ubuntu":
        os_name = "ubuntu"
        os_version = version_id or "Unknown"
    else:
        os_name = "linux"
        os_version = version_id or "Unknown"

    if os_version == "Unknown":
        try:
            out = subprocess.check_output(["lsb_release", "-ds"], text=True).strip().lower()
            if "ubuntu" in out:
                os_name = "ubuntu"
            match = re.search(r"(\d+\.\d+|\d+)", out)
            os_version = match.group(1) if match else "Unknown"
        except Exception:
            pass

    match = re.search(r"(\d+\.\d+|\d+)", os_version)
    if match:
        os_version = match.group(1)
    return os_name, os_version, current_host_arch()


def _host_os_matches(detected_os: str, supported_oses: List[str]) -> bool:
    if detected_os in supported_oses:
        return True
    return detected_os == "ubuntu" and "linux" in supported_oses


def _host_versions_for_os(platform_entry: dict, detected_os: str) -> list:
    versions_dict = {str(k).lower(): v for k, v in platform_entry.get("versions", {}).items()}
    return versions_dict.get(detected_os) or (versions_dict.get("linux") if detected_os == "ubuntu" else []) or []


def _host_arch_matches(platform_entry: dict, detected_arch: str) -> bool:
    supported_arches = []
    for value in platform_entry.get("arch", []):
        try:
            supported_arches.append(normalize_host_arch(value))
        except ValueError:
            supported_arches.append(str(value).lower())
    return not supported_arches or detected_arch in supported_arches


def _is_platform_compatible(metadata: dict, force: bool = False) -> bool:
    """
    Determines if the current environment is compatible with the package metadata.
    Supports OS-level version checks with partial and range syntax.
    """
    env_type, env_subtype = get_environment_type()
    exact_devkit_type = get_exact_devkit_type()
    platforms = metadata.get("platforms", [])
    board_ver, _ = get_sima_build_version()

    if not platforms:
        click.echo("ℹ️  No platform restrictions specified; treating package as compatible with all platforms.")
        return True

    os_name, os_version, host_arch = _detected_host_platform()

    # ──────────────────────────────────────────────
    # Compatibility checks
    # ──────────────────────────────────────────────
    for platform_entry in platforms:
        platform_type = platform_entry.get("type")
        if (platform_type, env_type, env_subtype) == ("palette", "sdk", "palette"):
            compatible_palette_version = platform_entry.get("version", "")
            if not compatible_palette_version:
                return True
            palette_sdk_version = _get_palette_sdk_version()
            if palette_sdk_version and version_matches(palette_sdk_version, compatible_palette_version):
                return True
            click.echo(
                f"❌ Palette SDK version {palette_sdk_version or 'unknown'} is not compatible. "
                f"Required: {compatible_palette_version}"
            )
            continue
        if platform_type != env_type:
            continue

        # 1️⃣ Board/devkit compatibility
        if env_type == "board":
            compat = platform_entry.get("compatible_with", [])
            if env_subtype not in compat and exact_devkit_type not in compat:
                continue
            else:
                compatible_board_version = platform_entry.get("version", "")
                # If version field exists in metadata then check if the board is running compatible version.
                if len(compatible_board_version) > 0:
                    if board_ver and version_matches(board_ver, compatible_board_version):
                        return True
                else:
                    # otherwise return true as it's generally compatible
                    return True

        # 2️⃣ OS match (mac, ubuntu, linux, windows)
        supported_oses = [o.lower() for o in platform_entry.get("os", [])]
        if not _host_os_matches(os_name, supported_oses):
            continue

        # 3️⃣ Architecture match
        if not _host_arch_matches(platform_entry, host_arch):
            click.echo(
                f"❌ Host architecture {host_arch or 'unknown'} is not supported. "
                f"Allowed: {platform_entry.get('arch')}"
            )
            continue

        # 4️⃣ Version match
        supported_versions = _host_versions_for_os(platform_entry, os_name)

        if supported_versions:
            ok = False
            for cond in supported_versions:
                if _compare_versions(os_version, cond):
                    ok = True
                    break

            if not ok:
                click.echo(
                    f"❌ OS version {os_version} for {os_name} not supported. "
                    f"Allowed: {supported_versions}"
                )
                continue
            else:
                click.echo(
                    f"✅ OS version {os_version} for {os_name} is compatible."
                )

        # ✅ All checks passed
        return True

    click.secho(
        f"❌ Current environment [{env_type}:{env_subtype}] "
        f"({os_name} {os_version} {host_arch}) is not compatible with this package.", fg='red'
    )
    _print_compatible_platforms(platforms)
    if not force:
        # Modify the built-in exit to sys.exit, this allows for consistent exit
        # handling which can be implemented inside the click handlers
        sys.exit(1)
    return False

def _print_post_install_message(metadata: Dict):
    """
    Print post-installation instructions from the metadata in a compact box.

    Args:
        metadata (Dict): The package metadata dictionary.
    """
    msg = metadata.get("installation", {}).get("post-message", "").strip()

    if msg:
        panel = Panel.fit(
            msg,
            title="[bold green]Post-Installation Instructions[/bold green]",
            title_align="left",
            border_style="green",
            padding=(1, 2)
        )
        console.print(panel)

def _run_installation_script(metadata: Dict, extract_path: str = "."):
    """
    Run the installation script specified in the metadata.

    Behavior:
      • macOS / Linux / eLxr:
          - Run via interactive login shell unless nested inside sima-cli
          - Safely source the first available RC file among:
              ~/.zshrc → ~/.bashrc → ~/.bash_profile → ~/.profile
      • Windows:
          - Run via cmd.exe or PowerShell
    """
    script = metadata.get("installation", {}).get("script", "").strip()
    if not script:
        registry.update_state(metadata.get('name'), metadata.get('version'), 'installed-no-script')
        print("⚠️  No installation script provided. Follow package documentation to install the package.")
        return

    print(f"🚀 Running installation script in: {os.path.abspath(extract_path)}")
    print(f"📜 Script: {script}")

    nested = os.environ.get("SIMA_INSTALL_CONTEXT") == "1"
    env = os.environ.copy()
    env["SIMA_INSTALL_CONTEXT"] = "1"

    try:
        if os.name == "nt":
            shell_executable = os.environ.get("COMSPEC", "cmd.exe")

            # Detect PowerShell script
            if any(x in script.lower() for x in ("powershell", ".ps1", "write-host")):
                cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-Command", script]
            else:
                cmd = [shell_executable, "/C", script]

            result = subprocess.run(cmd, cwd=extract_path, env=env)
            exit_code = result.returncode

        else:
            shell = os.environ.get("SHELL", "/bin/bash")

            rc_candidates = [
                os.path.expanduser("~/.zshrc"),
                os.path.expanduser("~/.bashrc"),
                os.path.expanduser("~/.bash_profile"),
                os.path.expanduser("~/.profile"),
            ]
            rc_file = next((rc for rc in rc_candidates if os.path.exists(rc)), None)

            if nested:
                # Nested → non-interactive but source RC file if found
                if rc_file:
                    rc_source = f"source {rc_file} && "
                    print(f"🔁 Nested install: sourcing {rc_file}")
                else:
                    rc_source = ""
                    print("🔁 Nested install: no RC file found, running directly")

                cmd = ["bash", "-c", f"{rc_source}{script}"]

            else:
                # Normal context → interactive login shell
                shell_name = os.path.basename(shell)
                cmd = [shell, "-i", "-l", "-c", script]

            result = subprocess.run(cmd, cwd=extract_path, env=env)
            exit_code = result.returncode


        normalized = exit_code if exit_code != 255 else -1
        if normalized == -1:
            print("⚠️  Installation script exited with -1. Aborted.")
            registry.update_state(metadata.get('name'), metadata.get('version'), 'aborted')
            return

        if normalized != 0:
            print(f"❌ Installation failed with return code: {normalized}")
            registry.update_state(metadata.get('name'), metadata.get('version'), 'failed')
            sys.exit(normalized)

        _print_post_install_message(metadata=metadata)
        registry.update_state(metadata.get('name'), metadata.get('version'), 'installed')
        print("✅ Installation completed successfully.")

    except FileNotFoundError:
        print("❌ Shell executable not found. Ensure Bash, Zsh, or PowerShell is installed.")
        registry.update_state(metadata.get('name'), metadata.get('version'),
                              'failed-no-shell')
        sys.exit(1)



def _resolve_github_metadata_url(gh_ref: str) -> Tuple[str, str]:
    """
    Resolve a GitHub shorthand like gh:org/repo@tag into a local metadata.json file path.
    If tag is omitted, defaults to 'main'.

    Args:
        gh_ref (str): Reference in the form 'gh:org/repo@tag'
    
    Returns:
        tuple[str, str]: (local_path_to_metadata_json, tag_used)
    """
    try:
        _, repo_ref = gh_ref.split(":", 1)  # strip 'gh:'
        if "@" in repo_ref:
            org_repo, tag = repo_ref.split("@", 1)
        else:
            org_repo, tag = repo_ref, "main"

        owner, repo = org_repo.split("/", 1)
        token = os.getenv("GITHUB_TOKEN")

        # Encode the ref safely for GitHub API
        tag_encoded = quote(tag, safe="")

        # GitHub API URL for raw file contents
        api_url = (
            f"https://api.github.com/repos/{owner}/{repo}/contents/metadata.json?ref={tag_encoded}"
        )
        headers = {"Accept": "application/vnd.github.v3.raw"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        r = requests.get(api_url, headers=headers)
        r.raise_for_status()

        # --- Sanitize tag for filesystem use ---
        tag_safe = tag.replace("/", "_")

        # Write metadata.json locally
        local_path = os.path.join(tempfile.gettempdir(), f"{repo}-{tag_safe}-metadata.json")
        with open(local_path, "wb") as f:
            f.write(r.content)

        return local_path, tag
    except Exception as e:
        raise RuntimeError(f"Failed to resolve GitHub metadata URL {gh_ref}: {e}")

def install_from_metadata(
    metadata_url: str,
    internal: bool,
    install_dir: str = '.',
    force: bool = False,
    command_name: str = "sima-cli install",
):
    _ensure_install_dir_writable(install_dir, command_name=command_name)

    try:
        tag = None

        if metadata_url.startswith("gh:"):
            metadata_url, tag = _resolve_github_metadata_url(metadata_url)
            internal = False

        if force:
            click.secho('⚠️  --force option was provided, skipping available space and compatibility check, package may not work properly', fg='yellow')

        metadata, _ = _download_and_validate_metadata(metadata_url, internal, force=force)
        registry.create_entry(metadata.get('name'), metadata.get('version'), metadata, '')

        if metadata:
            print_metadata_summary(metadata=metadata)
                
            if _check_whether_disk_is_big_enough(metadata, force) or force:
                if _is_platform_compatible(metadata, force) or force:
                    local_paths = _download_assets(metadata, metadata_url, install_dir, internal, tag=tag)
                    if len(local_paths) > 0:
                        _mark_install_script_executable(metadata, install_dir)
                        _combine_multipart_files(install_dir, local_paths=local_paths)
                        _extract_archives_in_folder(install_dir, local_paths)
                        _mark_install_script_executable(metadata, install_dir)
                        _run_installation_script(metadata=metadata, extract_path=install_dir)

    except Exception as e:
        click.echo(f"❌ Failed to install from metadata URL {metadata_url}: {e}")
        # sys.exit (not builtin exit()) so we don't close stdin out from under
        # the interactive shell. See note at the platform-compat check above.
        sys.exit(1)

    return False

def metadata_resolver(component: str, version: str = None, tag: str = None) -> str:
    """
    Resolve the metadata.json URL for a given component and version/tag.

    Args:
        component (str): Component name (e.g., "examples.llima" or "assets/ragfps")
        version (str): Optional. If not provided, auto-detect from /etc/build.
        tag (str): Optional tag to use (e.g., "dev")

    Returns:
        str: Fully qualified metadata URL
    """

    if tag:
        metadata_name = f"metadata-{tag}.json"
    else:
        metadata_name = "metadata.json"

    # --- Asset case, assets are SDK version agnostic ---
    if component.startswith("assets/"):
        return f"https://docs.sima.ai/{component}/{metadata_name}"

    # --- Auto-detect SDK version if missing ---
    if not version:
        core_version, _ = get_sima_build_version()
        if core_version:
            version = core_version
        else:
            raise ValueError(
                "Version (-v) is required and could not be auto-detected "
                "from /etc/build or /etc/buildinfo."
            )

    sdk_path = f"SDK{version}"
    return f"https://docs.sima.ai/pkg_downloads/{sdk_path}/{component}/{metadata_name}"
