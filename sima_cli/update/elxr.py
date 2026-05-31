import subprocess
import click
from typing import Optional, List, Tuple, Dict
import re
import os
import shutil
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from sima_cli.utils.env import is_devkit_running_elxr

APT_SOURCE_FILE = "/etc/apt/sources.list.d/0000mirror.list"
APT_MAIN_SOURCE_FILE = "/etc/apt/sources.list"
APT_SOURCE_DIR = "/etc/apt/sources.list.d"
BUILDINFO_FILES = ["/etc/build", "/etc/buildinfo"]
EXTERNAL_REPO_URL = "https://repo.sima.ai/elxr/deb/release"
INTERNAL_REPO_URL = "http://sw-web.eng.sima.ai/deb/pre-release"
INTERNAL_REPO_PREFIX = "http://sw-web.eng.sima.ai/"
DEFAULT_REPO_SUITE = "bookworm"
REPO_COMPONENT = "non-free"
SIMAAI_OTA_FALLBACK = "/usr/bin/simaai-ota"
ELXR_UPDATE_DOC_URL = "https://docs.sima.ai/pages/tech-notes/elxr-conversion.html"


def _repo_line(repo_url: str, suite: str) -> str:
    return f"deb {repo_url} {suite} {REPO_COMPONENT}"


def _resolve_simaai_ota() -> str:
    return (
        shutil.which("simaai-ota")
        or (
            SIMAAI_OTA_FALLBACK
            if os.path.isfile(SIMAAI_OTA_FALLBACK) and os.access(SIMAAI_OTA_FALLBACK, os.X_OK)
            else "simaai-ota"
        )
    )


def _normalize_apt_source_line(line: str) -> str:
    value = line.strip()
    if value.startswith("#"):
        value = value[1:].strip()
    return value


def _parse_deb_source_parts(line: str) -> Optional[Tuple[str, str, str]]:
    parts = line.split()

    if len(parts) < 4 or parts[0] != "deb":
        return None

    idx = 1
    if parts[idx].startswith("["):
        while idx < len(parts) and not parts[idx].endswith("]"):
            idx += 1
        idx += 1

    if len(parts) <= idx + 2:
        return None

    return parts[idx], parts[idx + 1], parts[idx + 2]


def _parse_elxr_repo_line(line: str) -> Optional[Tuple[str, str, bool]]:
    stripped = line.strip()
    active = not stripped.startswith("#")
    normalized = _normalize_apt_source_line(line)
    parsed = _parse_deb_source_parts(normalized)
    if not parsed:
        return None

    repo_url, suite, component = parsed
    if component != REPO_COMPONENT:
        return None

    if repo_url != EXTERNAL_REPO_URL and not repo_url.startswith(INTERNAL_REPO_PREFIX):
        return None

    return repo_url, suite, active


def _detect_repo_suite(lines: List[str]) -> str:
    parsed_lines = [
        parsed
        for parsed in (_parse_elxr_repo_line(line) for line in lines)
        if parsed
    ]
    for _repo_url, suite, active in parsed_lines:
        if active:
            return suite
    for _repo_url, suite, _active in parsed_lines:
        return suite
    return DEFAULT_REPO_SUITE


def _is_managed_elxr_repo(repo_url: str) -> bool:
    return repo_url == EXTERNAL_REPO_URL or repo_url.startswith(INTERNAL_REPO_PREFIX)


def _is_target_elxr_repo(repo_url: str, internal: bool) -> bool:
    target_url = INTERNAL_REPO_URL if internal else EXTERNAL_REPO_URL
    return repo_url == target_url


def _list_apt_source_files() -> List[str]:
    files: List[str] = []
    if os.path.isfile(APT_MAIN_SOURCE_FILE):
        files.append(APT_MAIN_SOURCE_FILE)
    try:
        for name in sorted(os.listdir(APT_SOURCE_DIR)):
            path = os.path.join(APT_SOURCE_DIR, name)
            if name.endswith(".list") and os.path.isfile(path):
                files.append(path)
    except OSError:
        pass
    if APT_SOURCE_FILE not in files:
        files.append(APT_SOURCE_FILE)
    return files


def _select_elxr_repo_channel(
    content: str,
    internal: bool,
    append_missing: bool = True,
    suite: Optional[str] = None,
) -> Tuple[str, bool, bool]:
    """
    Return updated apt source content, whether it changed, and whether active
    channel switching was detected.
    """
    lines = content.splitlines()
    suite = suite or _detect_repo_suite(lines)
    target_url = INTERNAL_REPO_URL if internal else EXTERNAL_REPO_URL
    other_url = EXTERNAL_REPO_URL if internal else INTERNAL_REPO_URL
    target = _repo_line(target_url, suite)
    other = _repo_line(other_url, suite)

    other_active = any(
        _is_managed_elxr_repo(parsed[0])
        and not _is_target_elxr_repo(parsed[0], internal)
        and parsed[2]
        for parsed in (_parse_elxr_repo_line(line) for line in lines)
        if parsed
    )
    switching = other_active

    updated_lines = []
    target_seen = False
    other_seen = False
    changed = False

    for line in lines:
        parsed = _parse_elxr_repo_line(line)
        if parsed and parsed[0] == target_url:
            target_seen = True
            updated_line = target
        elif parsed and _is_managed_elxr_repo(parsed[0]):
            if parsed[0] == other_url:
                other_seen = True
            updated_line = line if parsed[0] == target_url else f"# {_repo_line(parsed[0], parsed[1])}"
        else:
            updated_line = line

        if updated_line != line:
            changed = True
        updated_lines.append(updated_line)

    if append_missing and not target_seen:
        updated_lines.append(target)
        changed = True

    if append_missing and not other_seen:
        updated_lines.append(f"# {other}")
        changed = True

    newline = "\n" if content.endswith("\n") else ""
    return "\n".join(updated_lines) + newline, changed, switching


def _read_apt_source_files(paths: List[str]) -> Optional[Dict[str, str]]:
    contents: Dict[str, str] = {}
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                contents[path] = f.read()
        except FileNotFoundError:
            if path == APT_SOURCE_FILE:
                contents[path] = ""
                continue
            click.echo(f"❌ Failed to read {path}: file does not exist")
            return None
        except OSError as e:
            click.echo(f"❌ Failed to read {path}: {e}")
            return None
    return contents


def _select_elxr_repo_channel_files(
    contents: Dict[str, str],
    internal: bool,
) -> Tuple[Dict[str, str], bool, bool]:
    all_lines: List[str] = []
    for content in contents.values():
        all_lines.extend(content.splitlines())
    suite = _detect_repo_suite(all_lines)
    target_url = INTERNAL_REPO_URL if internal else EXTERNAL_REPO_URL
    target_seen = any(
        parsed[0] == target_url
        for parsed in (_parse_elxr_repo_line(line) for line in all_lines)
        if parsed
    )

    updated_contents: Dict[str, str] = {}
    changed = False
    switching = False

    for path, content in contents.items():
        updated, file_changed, file_switching = _select_elxr_repo_channel(
            content,
            internal,
            append_missing=(path == APT_SOURCE_FILE and not target_seen),
            suite=suite,
        )
        updated_contents[path] = updated
        changed = changed or file_changed
        switching = switching or file_switching

    return updated_contents, changed, switching


def _ensure_elxr_repo_channel(internal: bool) -> bool:
    channel_name = "internal pre-release" if internal else "external release"

    source_files = _list_apt_source_files()
    current_contents = _read_apt_source_files(source_files)
    if current_contents is None:
        return False

    new_contents, changed, switching = _select_elxr_repo_channel_files(current_contents, internal)
    if not changed:
        click.echo(f"✅ ELXR APT channel already set to {channel_name}.")
        return True

    if switching:
        click.secho(
            "⚠️  You are switching ELXR update channels between external release and internal pre-release.\n"
            "   This upgrade path has not been tested. Proceed with caution.",
            fg="yellow",
        )
        if not click.confirm(f"Switch ELXR APT channel to {channel_name}?", default=False):
            click.echo("❌ Update cancelled")
            return False

    if subprocess.call(["sudo", "-n", "true"],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) != 0:
        click.echo("ℹ️  sudo may prompt you for a password...")

    try:
        for path, new_content in new_contents.items():
            if new_content == current_contents[path]:
                continue
            subprocess.run(
                ["sudo", "tee", path],
                input=new_content,
                text=True,
                stdout=subprocess.DEVNULL,
                check=True,
            )
        subprocess.check_call(["sync"])
    except subprocess.CalledProcessError:
        click.echo("❌ Failed to update ELXR APT source files")
        return False

    click.echo(f"✅ ELXR APT channel set to {channel_name}.")
    return True


def _get_installed_palette_version() -> Optional[str]:
    """Return installed simaai-palette-modalix version, or None if unavailable."""
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", "simaai-palette-modalix"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None

    version = result.stdout.strip()
    return version if version else None


def _get_installed_elxr_distro_version() -> Optional[str]:
    """Return ELXR DISTRO_VERSION from /etc/build or /etc/buildinfo, if available."""
    pattern = re.compile(r"^\s*DISTRO_VERSION\s*=\s*(\S+)\s*$")

    for path in BUILDINFO_FILES:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    match = pattern.match(line)
                    if match:
                        return match.group(1)
        except OSError:
            continue

    return None


def _is_current_elxr_version(
    requested_version: str,
    installed_palette_version: Optional[str],
    installed_distro_version: Optional[str],
) -> bool:
    return requested_version in {
        version
        for version in (installed_palette_version, installed_distro_version)
        if version
    }


def _parse_elxr_release_version(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not version:
        return None

    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:$|[~+-])", version)
    if not match:
        return None

    return tuple(int(part) for part in match.groups())


def _is_stable_elxr_release(version: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+", version) is not None


def _latest_stable_elxr_release(versions: List[str]) -> Optional[str]:
    stable_versions = [
        (parsed, version)
        for version in versions
        if _is_stable_elxr_release(version)
        for parsed in [_parse_elxr_release_version(version)]
        if parsed
    ]
    if not stable_versions:
        return None

    return max(stable_versions, key=lambda item: item[0])[1]


def _is_supported_stable_elxr_upgrade(
    requested_version: str,
    installed_palette_version: Optional[str],
    installed_distro_version: Optional[str],
    available_versions: List[str],
) -> bool:
    if requested_version not in available_versions:
        return False
    if not _is_stable_elxr_release(requested_version):
        return False
    if requested_version != _latest_stable_elxr_release(available_versions):
        return False

    current_release = (
        _parse_elxr_release_version(installed_distro_version)
        or _parse_elxr_release_version(installed_palette_version)
    )
    requested_release = _parse_elxr_release_version(requested_version)

    return bool(current_release and requested_release and requested_release > current_release)


def _get_available_palette_versions() -> List[str]:
    """Parse apt policy output and return available simaai-palette-modalix versions."""
    try:
        result = subprocess.run(
            ["apt", "policy", "simaai-palette-modalix"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        click.echo("❌ Failed to run: apt policy simaai-palette-modalix")
        return []

    versions: List[str] = []
    capture = False

    # Matches lines like:
    #   "     2.0.0~git202511281205.97d3129-755 950"
    #   "*** 2.0.0~git202511271206.97d3129-751 950"
    pattern = re.compile(r"^\s*(\*{3}\s+)?([0-9A-Za-z.~+-]+)\s+")

    for line in result.stdout.splitlines():
        line = line.rstrip()

        if "Version table:" in line:
            capture = True
            continue

        if not capture:
            continue

        m = pattern.match(line)
        if not m:
            continue

        ver = m.group(2)

        # Skip pure numeric entries (950, 100, etc.)
        if ver.isdigit():
            continue

        versions.append(ver)

    # Remove duplicates, preserve order
    versions = list(dict.fromkeys(versions))

    return versions


def _show_unsupported_specific_elxr_update(
    requested_version: str,
    current_version: Optional[str] = None,
    latest_version: Optional[str] = None,
) -> None:
    details = [
        "Updating ELXR to a specific version is not currently supported by `sima-cli update`.",
        "Downgrades and non-latest upgrade paths are not reliable enough to automate safely.",
    ]
    if current_version:
        details.append(f"Current simaai-palette-modalix version: {current_version}")
    if requested_version:
        details.append(f"Requested simaai-palette-modalix version: {requested_version}")
    if latest_version:
        details.append(f"Latest available simaai-palette-modalix version: {latest_version}")
    details.extend([
        "",
        "Use `sima-cli update` without a version to update to the latest supported build.",
        f"For full-system conversion/update guidance, see: {ELXR_UPDATE_DOC_URL}",
    ])

    Console().print(
        Panel(
            Text("\n".join(details), style="yellow"),
            title="[yellow]Unsupported ELXR Update Path[/yellow]",
            border_style="yellow",
            expand=False,
        )
    )


def print_current_versions():
    p1 = subprocess.Popen(
        ["dpkg", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    p2 = subprocess.Popen(
        ["grep", "simaai"],
        stdin=p1.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    p1.stdout.close()
    out, err = p2.communicate()

    click.secho('Current SiMa component versions:', fg='green')
    click.secho(out)

def update_elxr(version_or_url: Optional[str], internal: bool = False):
    """
    Update packages on an ELXR-based devkit using simaai-ota.
    Enhanced:
    - "Update to a specific version" shows available versions from apt policy.
    - Adds Back and Cancel options.
    """
    if not is_devkit_running_elxr():
        click.echo("ℹ️  Not an ELXR devkit, skipping update")
        return

    print_current_versions()

    if not _ensure_elxr_repo_channel(internal):
        return

    # Check connectivity
    if subprocess.call(["ping", "-c", "1", "deb.debian.org"],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) != 0:
        click.echo("⚠️  ELXR devkit not connected to the network, skipping update")
        return

    click.echo("➡️  Refreshing APT package metadata...")
    if subprocess.call(["sudo", "-n", "true"],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) != 0:
        click.echo("ℹ️  sudo may prompt you for a password...")
    try:
        subprocess.check_call(["sudo", "apt", "update"])
    except subprocess.CalledProcessError:
        click.echo("❌ Failed to run: sudo apt update")
        return

    # -----------------------------
    # Main interaction loop
    # -----------------------------
    simaai_ota = _resolve_simaai_ota()
    if version_or_url is None:
        from InquirerPy import inquirer

    while True:

        # If user did not pass a version, show the update type menu
        if version_or_url is None:
            choice = inquirer.select(
                message="How would you like to update this ELXR devkit?",
                choices=[
                    {"name": "Update all packages to the latest", "value": "latest"},
                    {"name": "Update to a specific sima-palette version", "value": "version"},
                    {"name": "Cancel", "value": "cancel"},
                ],
                default="latest"
            ).execute()

            if choice == "cancel":
                click.echo("❌ Update cancelled")
                return

            if choice == "latest":
                cmd = [simaai_ota, "-f", "-o"]
                desc = "Update all packages to the latest"
                break

            elif choice == "version":
                # -----------------------------
                # Fetch and display version list
                # -----------------------------
                versions = _get_available_palette_versions()
                installed_version = _get_installed_palette_version()
                installed_distro_version = _get_installed_elxr_distro_version()
                latest_version = versions[0] if versions else None

                if not versions:
                    click.echo("❌ No versions found in APT policy, aborting.")
                    return

                # Add back and cancel
                version_choices = (
                    [{"name": "⬅️  Back to previous menu", "value": "back"}] +
                    [{"name": v, "value": v} for v in versions] +
                    [{"name": "❌ Cancel", "value": "cancel"}]
                )

                selected = inquirer.fuzzy(
                    message="Available simaai-palette-modalix versions:",
                    choices=version_choices,
                ).execute()

                if selected == "back":
                    # Return to main menu loop
                    continue

                if selected == "cancel":
                    click.echo("❌ Update cancelled")
                    return

                if _is_current_elxr_version(selected, installed_version, installed_distro_version):
                    same_version_choice = inquirer.select(
                        message=(
                            f"Version {selected} is already running. "
                            "Do you want to force reinstall it?"
                        ),
                        choices=[
                            {"name": "⬅️  Back to previous menu", "value": "back"},
                            {"name": "✅ Confirm force reinstall", "value": "confirm"},
                        ],
                        default="back"
                    ).execute()

                    if same_version_choice == "back":
                        continue

                    cmd = [simaai_ota, "-f", "-o", "-v", selected]
                    desc = f"Force reinstall specific version {selected}"
                    break

                if latest_version and selected == latest_version:
                    cmd = [simaai_ota, "-f", "-o", "-v", selected]
                    desc = f"Update to latest version {selected}"
                    break

                if _is_supported_stable_elxr_upgrade(
                    selected,
                    installed_version,
                    installed_distro_version,
                    versions,
                ):
                    cmd = [simaai_ota, "-f", "-o", "-v", selected]
                    desc = f"Update to stable version {selected}"
                    break

                _show_unsupported_specific_elxr_update(
                    requested_version=selected,
                    current_version=installed_distro_version or installed_version,
                    latest_version=latest_version,
                )
                warning_choice = inquirer.select(
                    message="What would you like to do next?",
                    choices=[
                        {"name": "⬅️  Back to previous menu", "value": "back"},
                        {"name": "❌ Cancel", "value": "cancel"},
                    ],
                    default="back",
                ).execute()

                if warning_choice == "cancel":
                    click.echo("❌ Update cancelled")
                    return

                continue

        else:
            # version_or_url specified by user (non-interactive)
            versions = _get_available_palette_versions()
            installed_version = _get_installed_palette_version()
            installed_distro_version = _get_installed_elxr_distro_version()
            latest_version = versions[0] if versions else None
            if _is_current_elxr_version(version_or_url, installed_version, installed_distro_version):
                cmd = [simaai_ota, "-f", "-o", "-v", version_or_url]
                desc = f"Force reinstall specific version {version_or_url}"
                break

            if latest_version and version_or_url == latest_version:
                cmd = [simaai_ota, "-f", "-o", "-v", version_or_url]
                desc = f"Update to latest version {version_or_url}"
                break

            if _is_supported_stable_elxr_upgrade(
                version_or_url,
                installed_version,
                installed_distro_version,
                versions,
            ):
                cmd = [simaai_ota, "-f", "-o", "-v", version_or_url]
                desc = f"Update to stable version {version_or_url}"
                break

            _show_unsupported_specific_elxr_update(
                requested_version=version_or_url,
                current_version=installed_distro_version or installed_version,
                latest_version=latest_version,
            )
            return

    # -----------------------------
    # Execute update
    # -----------------------------
    click.secho(
        "⚠️  This update will reset the u-boot environment variable to the target version.\n"
        "   For standard setups, this is expected and typically harmless.\n"
        "   If you have custom u-boot environment settings, you will need to re-apply them after the update.",
        fg="yellow",
    )
    if not click.confirm("Proceed with ELXR update?", default=False):
        click.echo("❌ Update cancelled")
        return

    cmd = ["sudo"] + cmd
    click.echo(f"➡️  {desc}\n   " + click.style(f"Running: {' '.join(cmd)}", fg="cyan"))

    if subprocess.call(["sudo", "-n", "true"],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) != 0:
        click.echo("ℹ️  sudo may prompt you for a password...")

    subprocess.check_call(cmd)
    click.echo("✅ ELXR update completed successfully")
