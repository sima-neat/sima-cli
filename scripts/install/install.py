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
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_BASE_URL = os.environ.get(
    "SIMA_CLI_ARTIFACT_BASE_URL",
    "https://artifacts.sima-neat.com/sima-cli",
).rstrip("/")


def branch_key(ref: str) -> str:
    return ref.replace("/", "-").replace(" ", "-")


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "sima-cli-installer/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8")


def fetch_json(url: str) -> Dict[str, Any]:
    return json.loads(fetch_text(url))


def normalize_index(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    branches = sorted({str(x).strip() for x in payload.get("branches", []) if str(x).strip()})
    tags = sorted({str(x).strip() for x in payload.get("tags", []) if str(x).strip()})
    if not tags:
        tags = sorted({str(x).strip() for x in payload.get("releases", []) if str(x).strip()})
    return branches, tags


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


def resolve_ref(base_url: str, requested_ref: Optional[str], noninteractive: bool) -> str:
    if requested_ref:
        return requested_ref
    payload = fetch_json(f"{base_url}/branches.json")
    branches, releases = normalize_index(payload)
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
    url = f"{base_url}/{branch_key(ref)}/{tag}.json"
    payload = fetch_json(url)
    payload["_metadata_url"] = url
    return payload


def iter_artifacts(metadata: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for item in metadata.get("artifacts", []):
        if isinstance(item, dict):
            yield item


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
        helper = find_one(package_dir, "sima-cli-install.bat")
        cmd = ["cmd.exe", "/c", str(helper), str(wheel_path)]
    else:
        helper = find_one(package_dir, "sima-cli-installer.sh")
        helper.chmod(helper.stat().st_mode | 0o755)
        cmd = ["bash", str(helper), str(wheel_path)]
    subprocess.run(cmd, check=True)


def install(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    ref = resolve_ref(base_url, args.ref, args.noninteractive)
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
