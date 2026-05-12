#!/usr/bin/env python3
"""Set the package version for CI builds."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
VERSION_FILE = ROOT / "sima_cli" / "__version__.py"


def read_base_version() -> str:
    text = PYPROJECT.read_text()
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
    if not match:
        raise SystemExit("Could not find project.version in pyproject.toml")
    return match.group(1)


def sanitize_local_label(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", ".", value)
    value = value.strip(".")
    return value or "unknown"


def version_from_tag(tag: str) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.]+)?", version):
        raise SystemExit(f"Tag does not look like a package version: {tag}")
    return version


def compute_version(args: argparse.Namespace) -> str:
    if args.tag:
        return version_from_tag(args.tag)

    base_version = read_base_version()
    ref = args.ref or os.environ.get("GITHUB_REF_NAME") or "local"
    sha = args.sha or os.environ.get("GITHUB_SHA") or "unknown"
    short_sha = sanitize_local_label(sha[:12])
    local_ref = sanitize_local_label(ref)
    return f"{base_version}+{local_ref}.{short_sha}"


def replace_version(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text()
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Could not update version in {path}")
    path.write_text(new_text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Release tag, for example v2.1.6.")
    parser.add_argument("--ref", help="Branch or PR ref name for non-tagged builds.")
    parser.add_argument("--sha", help="Commit SHA for non-tagged builds.")
    args = parser.parse_args()

    version = compute_version(args)
    replace_version(PYPROJECT, r'^version\s*=\s*"[^"]+"\s*$', f'version = "{version}"')
    replace_version(VERSION_FILE, r'^__version__\s*=\s*"[^"]+"\s*$', f'__version__ = "{version}"')
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
