#!/usr/bin/env python3
"""Cross-platform sima-cli installer stub.

This script resolves a branch or release from the sima-cli artifact index,
downloads the matching installer package, and invokes the platform helper
inside that package.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_BASE_URL = os.environ.get(
    "SIMA_CLI_ARTIFACT_BASE_URL",
    "https://artifacts.neat.sima.ai/sima-cli",
).rstrip("/")
PUBLIC_PYPI_JSON_URL = "https://pypi.org/pypi/sima-cli/json"
PUBLIC_PYPI_SIMPLE_URL = "https://pypi.org/simple"
DEFAULT_PYPI_RELEASE_LIMIT = 5


def branch_key(ref: str) -> str:
    return urllib.parse.quote(ref, safe="")


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "sima-cli-installer/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8")


def fetch_json(url: str) -> Dict[str, Any]:
    return json.loads(fetch_text(url))


def normalize_index(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    branch_names = []
    for item in payload.get("branches", []):
        if isinstance(item, dict):
            item = item.get("name") or item.get("key") or ""
        name = str(item).strip()
        if name:
            branch_names.append(name)

    tag_names = []
    for item in payload.get("tags", []):
        if isinstance(item, dict):
            item = item.get("name") or item.get("tag") or ""
        name = str(item).strip()
        if name:
            tag_names.append(name)

    branches = sorted(set(branch_names))
    tags = sorted(set(tag_names))
    if not tags:
        release_names = []
        for item in payload.get("releases", []):
            if isinstance(item, dict):
                item = item.get("name") or item.get("tag") or ""
            name = str(item).strip()
            if name:
                release_names.append(name)
        tags = sorted(set(release_names))
    return branches, tags


def is_pypi_release_ref(ref: str) -> bool:
    return re.fullmatch(r"v\d+\.\d+\.\d+(?:[a-zA-Z0-9_.-]+)?", ref or "") is not None


def version_from_release_ref(ref: str) -> str:
    if not is_pypi_release_ref(ref):
        raise SystemExit(f"PyPI release refs must look like v2.1.5, got: {ref}")
    return ref[1:]


def _version_sort_key(version: str) -> Tuple[Tuple[int, ...], str]:
    numeric = []
    for part in re.split(r"[._+-]", version):
        if part.isdigit():
            numeric.append(int(part))
        else:
            break
    return tuple(numeric), version


def fetch_pypi_releases(limit: Optional[int] = None) -> List[str]:
    payload = fetch_json(PUBLIC_PYPI_JSON_URL)
    releases = payload.get("releases", {})
    if not isinstance(releases, dict):
        return []
    versions = [version for version, files in releases.items() if files]
    sorted_versions = sorted(versions, key=_version_sort_key)
    if limit is not None and limit > 0:
        sorted_versions = sorted_versions[-limit:]
    return [f"v{version}" for version in sorted_versions]


def choose_ref(branches: List[str], releases: List[str], noninteractive: bool) -> str:
    choices: List[Tuple[str, str]] = []
    for name in branches:
        label = f"branch: {name}"
        if name == "main":
            label += " (default)"
        choices.append((name, label))
    for name in releases:
        choices.append((name, f"release: {name}"))

    if len(choices) == 1:
        return choices[0][0]

    if noninteractive or not sys.stdin.isatty():
        if "main" in branches:
            return "main"
        if branches:
            return branches[0]
        if releases:
            return releases[-1]
        raise SystemExit("No branches or releases found in branches.json")

    if not choices:
        raise SystemExit("No branches or releases found in branches.json")

    print("Select a sima-cli branch or release to install:")
    default_index = 0
    for idx, (value, label) in enumerate(choices, start=1):
        if value == "main":
            default_index = idx - 1
        print(f"  {idx}. {label}")

    while True:
        raw = input(f"Choice [{default_index + 1}]: ").strip()
        if not raw:
            return choices[default_index][0]
        try:
            selected = int(raw)
        except ValueError:
            print("Enter a number from the list.")
            continue
        if 1 <= selected <= len(choices):
            return choices[selected - 1][0]
        print("Choice out of range.")


def resolve_ref(
    base_url: str,
    requested_ref: Optional[str],
    noninteractive: bool,
    pypi_release_limit: Optional[int] = None,
) -> str:
    if requested_ref:
        return requested_ref
    payload = fetch_json(f"{base_url}/branches.json")
    branches, releases = normalize_index(payload)
    try:
        releases = sorted(
            set(releases + fetch_pypi_releases(limit=pypi_release_limit)),
            key=lambda item: _version_sort_key(item.lstrip("v")),
        )
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print(f"Warning: could not fetch releases from PyPI ({exc}); continuing with artifact index releases only.", file=sys.stderr)
    return choose_ref(branches, releases, noninteractive)


def resolve_tag(base_url: str, ref: str, requested_tag: str) -> str:
    if requested_tag != "latest":
        return requested_tag
    url = f"{base_url}/{branch_key(ref)}/latest.tag"
    tag = fetch_text(url).strip()
    if not tag:
        raise SystemExit(f"latest.tag is empty for {ref}")
    return tag


def resolve_metadata(base_url: str, ref: str, tag: str) -> Dict[str, Any]:
    urls = [
        f"{base_url}/{branch_key(ref)}/{tag}/metadata.json",
        f"{base_url}/{branch_key(ref)}/{tag}.json",
    ]
    last_http_error: Optional[urllib.error.HTTPError] = None
    for url in urls:
        try:
            payload = fetch_json(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in (403, 404):
                raise
            last_http_error = exc
            continue
        payload["_metadata_url"] = url
        payload["_resource_base_url"] = url.rsplit("/", 1)[0]
        return payload
    if last_http_error is not None:
        raise last_http_error
    raise SystemExit(f"No metadata found for {ref} @ {tag}")


def iter_artifacts(metadata: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for item in metadata.get("artifacts", []):
        if isinstance(item, dict):
            yield item
    resource_base_url = str(metadata.get("_resource_base_url", "")).rstrip("/")
    for item in metadata.get("resources", []):
        filename = str(item).strip()
        if filename:
            artifact = {"filename": filename}
            if resource_base_url:
                artifact["url"] = f"{resource_base_url}/{urllib.parse.quote(filename)}"
            yield artifact


def find_artifact(metadata: Dict[str, Any], suffix: str, contains: str = "") -> Dict[str, Any]:
    matches = []
    for item in iter_artifacts(metadata):
        filename = str(item.get("filename", ""))
        if filename.endswith(suffix) and (not contains or contains in filename):
            matches.append(item)
    if not matches:
        raise SystemExit(f"No {suffix} artifact found in metadata from {metadata.get('_metadata_url')}")
    return sorted(matches, key=lambda x: str(x.get("filename", "")))[-1]


def artifact_url(base_url: str, artifact: Dict[str, Any]) -> str:
    url = str(artifact.get("url", "")).strip()
    if url:
        return url
    filename = str(artifact.get("filename", "")).strip()
    if not filename:
        raise SystemExit("Artifact entry is missing filename")
    return f"{base_url}/{filename}"


def download_file(url: str, dest: Path) -> None:
    print(f"Downloading {url}")
    dest.write_bytes(fetch_bytes(url))


def extract_package(package_path: Path, workdir: Path) -> Path:
    target = workdir / "package"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path) as zf:
        zf.extractall(target)
    return target


def find_one(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern))
    if not matches:
        raise SystemExit(f"Package does not contain {pattern}")
    return matches[-1]


def run_helper(package_dir: Path, wheel_path: Path) -> None:
    if platform.system().lower() == "windows":
        helper = find_one(package_dir, "windows.bat")
        cmd = ["cmd.exe", "/c", str(helper), str(wheel_path)]
    else:
        helper = find_one(package_dir, "linux-mac.sh")
        helper.chmod(helper.stat().st_mode | 0o755)
        cmd = ["bash", str(helper), str(wheel_path)]
    subprocess.run(cmd, check=True)


def install_wheel_current_env(wheel_path: Path) -> None:
    print(f"Installing sima-cli wheel into current Python environment: {sys.executable}")
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--force-reinstall",
            str(wheel_path),
        ],
        check=True,
        env=env,
    )


def _pypi_install_dir() -> Path:
    if platform.system().lower() == "windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".sima-cli-env"
    return Path.home() / ".sima-cli" / ".venv"


def _venv_python(venv_dir: Path) -> Path:
    if platform.system().lower() == "windows":
        return venv_dir / "Scripts" / "python.exe"
    python = venv_dir / "bin" / "python3"
    if python.exists():
        return python
    return venv_dir / "bin" / "python"


def _venv_binary(venv_dir: Path) -> Path:
    if platform.system().lower() == "windows":
        return venv_dir / "Scripts" / "sima-cli.exe"
    return venv_dir / "bin" / "sima-cli"


def _append_line_once(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if line not in existing.splitlines():
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        return


def _configure_pypi_shell_path(venv_dir: Path) -> None:
    if platform.system().lower() == "windows":
        scripts = str(venv_dir / "Scripts")
        subprocess.run(["setx", "PATH", f"%PATH%;{scripts}"], check=False)
        return

    rc_file = Path.home() / (".zshrc" if platform.system().lower() == "darwin" else ".bashrc")
    _append_line_once(rc_file, f'export PATH="$PATH:{venv_dir / "bin"}"')
    aliases = {
        "sima-cli": str(venv_dir / "bin" / "sima-cli"),
        "sdk": "sima-cli sdk",
        "mpk": "sima-cli sdk mpk",
        "modelsdk": "sima-cli sdk model",
        "yocto": "sima-cli sdk yocto",
        "elxr": "sima-cli sdk elxr",
    }
    for name, command in aliases.items():
        _append_line_once(rc_file, f"alias {name}='{command}'")


def install_from_pypi(ref: str) -> None:
    version = version_from_release_ref(ref)
    venv_dir = _pypi_install_dir()
    print(f"Installing sima-cli {version} from public PyPI")
    if not venv_dir.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    python = _venv_python(venv_dir)
    if not python.exists():
        raise SystemExit(f"No Python interpreter found in virtual environment at {venv_dir}")

    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True, env=env)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--force-reinstall",
            "--index-url",
            PUBLIC_PYPI_SIMPLE_URL,
            f"sima-cli=={version}",
        ],
        check=True,
        env=env,
    )
    _configure_pypi_shell_path(venv_dir)
    print(f"sima-cli successfully installed from PyPI: {_venv_binary(venv_dir)}")


def install(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    ref = resolve_ref(
        base_url,
        args.ref,
        args.noninteractive,
        pypi_release_limit=getattr(args, "pypi_release_limit", None),
    )
    if is_pypi_release_ref(ref):
        if args.version != "latest":
            raise SystemExit("When installing a PyPI release ref such as v2.1.5, omit the artifact version argument.")
        if args.current_env:
            version = version_from_release_ref(ref)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "--force-reinstall",
                    "--index-url",
                    PUBLIC_PYPI_SIMPLE_URL,
                    f"sima-cli=={version}",
                ],
                check=True,
            )
            return
        install_from_pypi(ref)
        return

    tag = resolve_tag(base_url, ref, args.version)
    metadata = resolve_metadata(base_url, ref, tag)
    package = find_artifact(metadata, ".zip", "sima-cli-package")
    package_url = artifact_url(base_url, package)

    print(f"Installing sima-cli from {ref} @ {tag}")
    with tempfile.TemporaryDirectory(prefix="sima-cli-install-") as tmp:
        tmpdir = Path(tmp)
        package_path = tmpdir / str(package["filename"])
        download_file(package_url, package_path)
        package_dir = extract_package(package_path, tmpdir)
        wheel_path = find_one(package_dir, "*.whl")
        if args.current_env:
            install_wheel_current_env(wheel_path)
        else:
            run_helper(package_dir, wheel_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install sima-cli from SiMa NEAT artifacts.")
    parser.add_argument("ref", nargs="?", help="Branch or release tag. Defaults to interactive selection.")
    parser.add_argument(
        "version",
        nargs="?",
        default="latest",
        help="Artifact version, usually latest or a short commit hash.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Artifact base URL.")
    parser.add_argument(
        "--noninteractive",
        action="store_true",
        help="Do not prompt; defaults to main latest when no ref is provided.",
    )
    parser.add_argument(
        "--current-env",
        action="store_true",
        help="Install into the current Python environment instead of creating or using the managed sima-cli venv.",
    )
    parser.add_argument(
        "--pypi-release-limit",
        type=int,
        default=DEFAULT_PYPI_RELEASE_LIMIT,
        help=f"Limit how many recent PyPI releases are included in interactive release selection (default: {DEFAULT_PYPI_RELEASE_LIMIT}).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        install(args)
    except urllib.error.HTTPError as exc:
        print(f"HTTP error fetching {exc.url}: {exc.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Installer helper failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
