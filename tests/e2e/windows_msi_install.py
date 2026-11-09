#!/usr/bin/env python3
"""Exercise the real Windows Installer boundary with a disposable MSI."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from sima_cli.install.windows_package import install_windows_package


def run(command: list[object], *, cwd: Path | None = None) -> None:
    argv = [str(value) for value in command]
    print("+", subprocess.list2cmdline(argv), flush=True)
    subprocess.run(argv, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if os.name != "nt":
        raise SystemExit("this end-to-end test requires native Windows")
    cmake = shutil.which("cmake.exe")
    cpack = shutil.which("cpack.exe")
    if not cmake or not cpack:
        raise SystemExit("cmake.exe and cpack.exe are required")

    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    install_name = f"SimaCliMsiTest-{run_id}"
    install_root = Path(os.environ["ProgramFiles"]) / install_name
    if install_root.exists():
        raise SystemExit(f"refusing to overwrite pre-existing path: {install_root}")

    namespace = uuid.UUID("9d9b0a84-c52b-4fac-8dd9-12187d5f748e")
    upgrade_guid = str(uuid.uuid5(namespace, "sima-cli-msi-e2e-upgrade")).upper()
    product_guid = str(uuid.uuid5(namespace, f"sima-cli-msi-e2e-{run_id}")).upper()

    with tempfile.TemporaryDirectory(prefix="sima-cli-msi-e2e-") as directory:
        root = Path(directory)
        source = root / "source"
        build = root / "build"
        dist = root / "dist"
        source.mkdir()
        dist.mkdir()
        (source / "payload.txt").write_text("sima-cli Windows MSI e2e\n", encoding="utf-8")
        (source / "CMakeLists.txt").write_text(
            f"""cmake_minimum_required(VERSION 3.20)
project(SimaCliMsiE2E VERSION 1.0.0 LANGUAGES NONE)
install(FILES payload.txt DESTINATION share COMPONENT Runtime)
set(CPACK_GENERATOR "WIX")
set(CPACK_PACKAGE_NAME "sima-cli-msi-e2e")
set(CPACK_PACKAGE_VERSION "1.0.0")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "{install_name}")
set(CPACK_WIX_VERSION 3)
set(CPACK_WIX_ARCHITECTURE x64)
set(CPACK_WIX_INSTALL_SCOPE perMachine)
set(CPACK_WIX_UPGRADE_GUID "{upgrade_guid}")
set(CPACK_WIX_PRODUCT_GUID "{product_guid}")
set(CPACK_COMPONENTS_ALL Runtime)
include(CPack)
""",
            encoding="utf-8",
        )
        run([cmake, "-S", source, "-B", build])
        run([cpack, "--config", build / "CPackConfig.cmake", "-G", "WIX", "-B", dist])
        packages = list(dist.glob("*.msi"))
        if len(packages) != 1:
            raise SystemExit(f"expected one disposable MSI, found {len(packages)}")
        msi = packages[0]
        metadata = {
            "name": "gh:sima-neat/tests/windows-msi",
            "version": "1.0.0",
            "release": "ci",
            "platforms": [{"type": "host", "os": ["windows"], "arch": ["amd64"]}],
            "resources": [msi.name],
            "resources-checksum": {msi.name: sha256(msi)},
            "installation": {"type": "windows-msi", "msi": msi.name},
        }
        (dist / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

        installed = False
        try:
            result = install_windows_package(metadata, str(dist))
            installed = True
            if result["native"] != "installed":
                raise SystemExit(f"unexpected native result: {result}")
            payload = install_root / "share" / "payload.txt"
            if payload.read_text(encoding="utf-8") != "sima-cli Windows MSI e2e\n":
                raise SystemExit("installed MSI payload differs")
            repeated = install_windows_package(metadata, str(dist))
            if repeated["native"] != "installed":
                raise SystemExit(f"unexpected repeat-install result: {repeated}")
            if payload.read_text(encoding="utf-8") != "sima-cli Windows MSI e2e\n":
                raise SystemExit("repeat installation changed the MSI payload")
        finally:
            installed = installed or install_root.exists()
            if installed:
                uninstall_log = dist / "msi-uninstall.log"
                result = subprocess.run(
                    [
                        "msiexec.exe", "/x", str(msi), "/quiet", "/norestart",
                        "/L*v", str(uninstall_log),
                    ],
                    check=False,
                )
                if result.returncode != 0:
                    raise SystemExit(
                        f"disposable MSI removal failed with {result.returncode}; "
                        f"see {uninstall_log}")
        if install_root.exists():
            raise SystemExit(f"disposable MSI left its installation root: {install_root}")

    print("WINDOWS_MSI_INSTALL_E2E_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
