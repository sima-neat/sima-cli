import subprocess
import click
from typing import Optional, List, Tuple
import re
import os
import shutil

from sima_cli.utils.env import is_devkit_running_elxr

APT_SOURCE_FILE = "/etc/apt/sources.list.d/0000mirror.list"
EXTERNAL_REPO_URL = "https://repo.sima.ai/elxr/deb/release"
INTERNAL_REPO_URL = "http://sw-web.eng.sima.ai/deb/pre-release"
DEFAULT_REPO_SUITE = "bookworm"
REPO_COMPONENT = "non-free"
SIMAAI_OTA_FALLBACK = "/usr/bin/simaai-ota"


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


def _parse_elxr_repo_line(line: str) -> Optional[Tuple[str, str, bool]]:
    stripped = line.strip()
    active = not stripped.startswith("#")
    normalized = _normalize_apt_source_line(line)
    parts = normalized.split()

    if len(parts) < 4 or parts[0] != "deb":
        return None

    repo_url = parts[1]
    suite = parts[2]
    component = parts[3]
    if component != REPO_COMPONENT:
        return None

    if repo_url not in (EXTERNAL_REPO_URL, INTERNAL_REPO_URL):
        return None

    return repo_url, suite, active


def _detect_repo_suite(lines: List[str]) -> str:
    for line in lines:
        parsed = _parse_elxr_repo_line(line)
        if parsed:
            _repo_url, suite, _active = parsed
            return suite
    return DEFAULT_REPO_SUITE


def _select_elxr_repo_channel(content: str, internal: bool) -> Tuple[str, bool, bool]:
    """
    Return updated apt source content, whether it changed, and whether active
    channel switching was detected.
    """
    lines = content.splitlines()
    suite = _detect_repo_suite(lines)
    target_url = INTERNAL_REPO_URL if internal else EXTERNAL_REPO_URL
    other_url = EXTERNAL_REPO_URL if internal else INTERNAL_REPO_URL
    target = _repo_line(target_url, suite)
    other = _repo_line(other_url, suite)

    other_active = any(
        parsed[0] == other_url and parsed[2]
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
        elif parsed and parsed[0] == other_url:
            other_seen = True
            updated_line = f"# {other}"
        else:
            updated_line = line

        if updated_line != line:
            changed = True
        updated_lines.append(updated_line)

    if not target_seen:
        updated_lines.append(target)
        changed = True

    if not other_seen:
        updated_lines.append(f"# {other}")
        changed = True

    newline = "\n" if content.endswith("\n") else ""
    return "\n".join(updated_lines) + newline, changed, switching


def _ensure_elxr_repo_channel(internal: bool) -> bool:
    channel_name = "internal pre-release" if internal else "external release"

    try:
        with open(APT_SOURCE_FILE, "r", encoding="utf-8") as f:
            current_content = f.read()
    except OSError as e:
        click.echo(f"❌ Failed to read {APT_SOURCE_FILE}: {e}")
        return False

    new_content, changed, switching = _select_elxr_repo_channel(current_content, internal)
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
        subprocess.run(
            ["sudo", "tee", APT_SOURCE_FILE],
            input=new_content,
            text=True,
            stdout=subprocess.DEVNULL,
            check=True,
        )
        subprocess.check_call(["sync"])
    except subprocess.CalledProcessError:
        click.echo(f"❌ Failed to update {APT_SOURCE_FILE}")
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

    from InquirerPy import inquirer

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

                # A version was selected
                if installed_version and selected == installed_version:
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
                else:
                    cmd = [simaai_ota, "-f", "-o", "-v", selected]
                    desc = f"Update to specific version {selected}"
                break

        else:
            # version_or_url specified by user (non-interactive)
            cmd = [simaai_ota, "-f", "-o", "-v", version_or_url]
            desc = f"Update to specific version {version_or_url}"
            break

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
