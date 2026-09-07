import importlib.metadata
import urllib.request
import subprocess
import json
import socket
import click
import sys
import shutil
import glob
import os
import re
import time
from pathlib import Path

PUBLIC_PYPI_SIMPLE_URL = "https://pypi.org/simple"
AUTO_ACCEPT_UPDATE_ENV = "SIMA_CLI_AUTO_ACCEPT_UPDATE"
FORCE_UPDATE_CHECK_RESULT_ENV = "FORCE_UPDATE_CHECK_RESULT"
UPDATE_CHECK_CACHE_ENV = "SIMA_CLI_UPDATE_CHECK_CACHE"
UPDATE_CHECK_CACHE_TTL_SECONDS = 60 * 60
UPDATE_CHECK_FAILURE_TTL_SECONDS = 5 * 60

# Snapshot at import time (before any in-process override) whether the user
# disabled the update check via the real launch environment. This lets us warn
# only when the *user* disabled it — not when the code disables it internally
# (e.g. the interactive shell silences per-command checks).
_UPDATE_CHECK_DISABLED_BY_USER = (
    os.environ.get("SIMA_CLI_CHECK_FOR_UPDATE", "1") != "1"
)


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def _confirm_and_update(package_name: str) -> bool:
    if _env_flag_enabled(AUTO_ACCEPT_UPDATE_ENV):
        if sys.platform.startswith("win"):
            click.secho(
                f"⚠️  {AUTO_ACCEPT_UPDATE_ENV}=1 is ignored on Windows because automatic self-update is not supported while the CLI is running.",
                fg="yellow",
                bold=True,
            )
            update_package(package_name)
            return False
        click.secho(f"🔔 {AUTO_ACCEPT_UPDATE_ENV}=1 set; automatically updating {package_name}.", fg="yellow")
        return update_package(package_name)

    if click.confirm(f"🔔 Do you want to update {package_name} now?", default=True):
        return update_package(package_name)
    return False


def _force_update_check_result() -> bool:
    return _env_flag_enabled(FORCE_UPDATE_CHECK_RESULT_ENV)

def cleanup_pip_leftovers():
    """Remove ~-prefixed leftover dirs in site-packages."""
    for path in sys.path:
        if path.endswith("site-packages") and os.path.isdir(path):
            junk_dirs = glob.glob(os.path.join(path, "~*"))
            for d in junk_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception as e:
                    click.secho(f"⚠️ Failed to remove {d}: {e}", fg="yellow")

def update_package(package_name: str) -> bool:
    """Suggest manual update on Windows; auto-update elsewhere."""
    pip_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--isolated",
        "--upgrade",
        "--index-url",
        PUBLIC_PYPI_SIMPLE_URL,
        package_name,
    ]

    if sys.platform.startswith("win"):
        click.secho("⚠️  Automatic self-update is not supported on Windows while the CLI is running.", fg="yellow", bold=True)
        click.echo(f"Please run the following command in a new terminal:\n\n    {' '.join(pip_cmd)}\n")
        return False

    try:
        env = os.environ.copy()
        env["PIP_CONFIG_FILE"] = os.devnull
        subprocess.run(pip_cmd, check=True, env=env)
        cleanup_pip_leftovers()
        click.secho(f"✅ {package_name} updated successfully.", fg="green", bold=True)
        return True
    except subprocess.CalledProcessError as e:
        click.secho(f"❌ Failed to update {package_name}: {e}", fg="red", bold=True)
        return False

def has_internet(timeout: float = 1.0) -> bool:
    """
    Quick check for internet connectivity by connecting to a known DNS server.
    First tries Cloudflare (1.1.1.1), falls back to Google (8.8.8.8).
    Uses IP to avoid DNS lookup delays.
    """
    def try_connect(ip: str, port: int = 53) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((ip, port))
            return True
        except OSError:
            return False

    return try_connect("1.1.1.1") or try_connect("8.8.8.8")


def _update_cache_path() -> Path:
    override = os.environ.get(UPDATE_CHECK_CACHE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sima-cli" / "update-check.json"


def _read_update_cache() -> dict:
    try:
        return json.loads(_update_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_update_cache(cache: dict) -> None:
    path = _update_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return


def _cached_update_entry(package_name: str, now: float) -> dict:
    cache = _read_update_cache()
    if cache.get("package") != package_name:
        return {}

    try:
        checked_at = float(cache.get("checked_at", 0))
    except (TypeError, ValueError):
        return {}

    if cache.get("status") == "ok" and cache.get("latest_version"):
        if now - checked_at < UPDATE_CHECK_CACHE_TTL_SECONDS:
            return cache
    if cache.get("status") == "unavailable":
        if now - checked_at < UPDATE_CHECK_FAILURE_TTL_SECONDS:
            return cache
    return {}


def _fetch_latest_version(package_name: str, timeout: float) -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package_name}/json", timeout=timeout) as resp:
        return json.load(resp)["info"]["version"]


def _latest_version_for_update_check(package_name: str, timeout: float) -> str:
    now = time.time()
    cached = _cached_update_entry(package_name, now)
    if cached.get("status") == "ok":
        return str(cached["latest_version"])
    if cached.get("status") == "unavailable":
        return ""

    try:
        latest_version = _fetch_latest_version(package_name, timeout)
    except Exception:
        _write_update_cache({
            "package": package_name,
            "status": "unavailable",
            "checked_at": now,
        })
        print("⚠️  sima-cli update check unavailable; continuing without checking for updates.")
        return ""

    _write_update_cache({
        "package": package_name,
        "status": "ok",
        "checked_at": now,
        "latest_version": latest_version,
    })
    return latest_version


def _parse_numeric_version(version: str):
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", version or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _compare_versions(left: str, right: str):
    left_parts = _parse_numeric_version(left)
    right_parts = _parse_numeric_version(right)
    if left_parts is None or right_parts is None:
        return 0 if left == right else None

    size = max(len(left_parts), len(right_parts))
    left_parts = left_parts + (0,) * (size - len(left_parts))
    right_parts = right_parts + (0,) * (size - len(right_parts))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def check_for_update(package_name: str, timeout: float = 2.0):

    if os.environ.get("SIMA_CLI_CHECK_FOR_UPDATE", "1") != "1":
        if _UPDATE_CHECK_DISABLED_BY_USER:
            print(f'⚠️  You have disabled update check with SIMA_CLI_CHECK_FOR_UPDATE environment variable, skipping sima-cli update check..')
        return False
    
    try:
        current_version = importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        print(f'❌ package not found {package_name}')
        return False

    latest_version = _latest_version_for_update_check(package_name, timeout)
    if not latest_version:
        return False

    version_comparison = _compare_versions(current_version, latest_version)
    if version_comparison is None:
        if current_version == latest_version:
            click.secho('✅ sima-cli is up-to-date', fg='green')
        else:
            click.secho(
                f"🔔 Current sima-cli is not the latest published version: {current_version} → {latest_version}",
                fg="green",
                bold=True,
            )
        if _force_update_check_result():
            click.secho(
                f"🔔 {FORCE_UPDATE_CHECK_RESULT_ENV}=1 set; prompting for update even though the version check did not require it.",
                fg="yellow",
            )
            return _confirm_and_update(package_name)
        return False

    if version_comparison < 0:
        click.secho(
            f"🔔 Current sima-cli is not the latest published version: {current_version} → {latest_version}",
            fg="green",
            bold=True,
        )
        click.secho(f"🔔 If you don't want to automatically check for updates, set SIMA_CLI_CHECK_FOR_UPDATE environment variable to 0")
        return _confirm_and_update(package_name)
    elif version_comparison > 0:
        click.secho(
            f"ℹ️  Current sima-cli ({current_version}) is newer than the latest published version ({latest_version}); skipping automatic update.",
            fg="yellow",
        )
        click.secho(
            "ℹ️  If you want to force downgrade, run `sima-cli selfupdate`.",
            fg="yellow",
        )
        return False
    else:
        print('✅ sima-cli is up-to-date')
        if _force_update_check_result():
            click.secho(
                f"🔔 {FORCE_UPDATE_CHECK_RESULT_ENV}=1 set; prompting for update even though sima-cli is already up-to-date.",
                fg="yellow",
            )
            return _confirm_and_update(package_name)
        return False
