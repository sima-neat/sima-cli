#!/usr/bin/env python3
"""Write the WinGet manifest (version, installer and defaultLocale files) for a built MSI.

Version, SHA-256 and ProductCode come from the installer.json that build_msi.py writes next
to the MSI; the package identifier, publisher, product name and upgrade code come from
sima_cli/utils/windows_installer.py. The templates are in winget/.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import runpy
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TEMPLATES = HERE / "winget"
IDENTITY = runpy.run_path(str(ROOT / "sima_cli" / "utils" / "windows_installer.py"))
# Every field used here exists in 1.12.0, and older WinGet clients still read it.
MANIFEST_VERSION = "1.12.0"
# Template -> file name suffix after the package identifier.
MANIFEST_FILES = {
    "version.yaml": ".yaml",
    "installer.yaml": ".installer.yaml",
    "defaultLocale.yaml": ".locale.en-US.yaml",
}
GUID = r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}"


def manifest_values(installer: dict, installer_url: str, release_date: str) -> dict:
    checks = (
        ("installer URL", installer_url, r"https?://\S+"),
        ("MSI version", installer["msi_version"], r"\d+\.\d+\.\d+"),
        ("SHA-256", installer["sha256"], r"[0-9a-f]{64}"),
        ("ProductCode", installer["product_code"], GUID),
        ("UpgradeCode", installer["upgrade_code"], GUID),
        ("release date", release_date, r"\d{4}-\d{2}-\d{2}"),
    )
    for label, value, pattern in checks:
        if not re.fullmatch(pattern, value):
            raise SystemExit(f"Not a valid {label}: {value!r}")
    if installer["upgrade_code"] != IDENTITY["UPGRADE_CODE"]:
        raise SystemExit(f"installer.json has upgrade code {installer['upgrade_code']}, "
                         f"not {IDENTITY['UPGRADE_CODE']}")
    return {
        "identifier": IDENTITY["WINGET_PACKAGE_IDENTIFIER"],
        "publisher": IDENTITY["PUBLISHER"],
        "product_name": IDENTITY["PRODUCT_NAME"],
        # The Apps list shows the MSI ProductVersion; WinGet matches PackageVersion to it.
        "version": installer["msi_version"],
        "installer_url": installer_url,
        "sha256": installer["sha256"].upper(),
        "product_code": installer["product_code"],
        "upgrade_code": installer["upgrade_code"],
        "release_date": release_date,
        "manifest_version": MANIFEST_VERSION,
    }


def write_manifests(installer_json: Path, installer_url: str, output: Path,
                    release_date: str | None = None) -> list:
    installer = json.loads(installer_json.read_text(encoding="utf-8"))
    values = manifest_values(installer, installer_url,
                             release_date or datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    output.mkdir(parents=True, exist_ok=True)
    written = []
    for template, suffix in MANIFEST_FILES.items():
        text = Template((TEMPLATES / template).read_text(encoding="utf-8")).substitute(values)
        path = output / f"{values['identifier']}{suffix}"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer-json", type=Path, required=True, help="installer.json from build_msi.py")
    parser.add_argument("--installer-url", required=True, help="Where WinGet downloads the MSI")
    parser.add_argument("--output-directory", type=Path, required=True, help="Folder for the three manifest files")
    parser.add_argument("--release-date", help="YYYY-MM-DD (default: today, UTC)")
    args = parser.parse_args(argv)
    for path in write_manifests(args.installer_json, args.installer_url, args.output_directory, args.release_date):
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
