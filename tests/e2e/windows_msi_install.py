#!/usr/bin/env python3
"""Exercise the real Windows Installer boundary with a disposable MSI.

The MSI is built twice, as 1.0.0 and 1.1.0 with the same upgrade code. Through the installed
sima-cli command it is installed, upgraded, refused as a downgrade and removed; the repair of
an installed release runs through the package installer in this process.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sysconfig
import tempfile
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest import mock

from sima_cli.install.metadata_validator import validate_metadata
from sima_cli.install import windows_package
from sima_cli.install.windows_package import install_windows_package

PACKAGE = "gh:sima-neat/tests/windows-msi"


def run(command: list[object], *, cwd: Path | None = None) -> None:
    argv = [str(value) for value in command]
    print("+", subprocess.list2cmdline(argv), flush=True)
    subprocess.run(argv, cwd=cwd, check=True)


CHILD_TIMEOUT_SECONDS = 600


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def held_open(path: Path):
    """Keep a file open as a running application keeps its DLLs: shared for reading only."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    generic_read, file_share_read, open_existing = 0x80000000, 0x1, 3
    handle = kernel32.CreateFileW(
        str(path), generic_read, file_share_read, None, open_existing, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


def build_msi(cmake: str, cpack: str, root: Path, version: str, install_name: str,
              upgrade_guid: str, product_guid: str) -> Path:
    """Build the disposable MSI for one version; every version shares the upgrade code."""
    source = root / f"source-{version}"
    build = root / f"build-{version}"
    dist = root / "dist" / version
    source.mkdir()
    dist.mkdir(parents=True)
    (source / "payload.txt").write_text(f"sima-cli Windows MSI e2e {version}\n", encoding="utf-8")
    (source / "in-use.txt").write_text("held open during the repair\n", encoding="utf-8")
    # The installed setup step records that it ran, as pcie-setup.cmd would enroll a card.
    (source / "setup.cmd").write_text(
        '@echo off\r\n>"%SIMA_CLI_E2E_SETUP_MARKER%" echo setup ran\r\n', encoding="utf-8")
    (source / "CMakeLists.txt").write_text(
        f"""cmake_minimum_required(VERSION 3.20)
project(SimaCliMsiE2E VERSION {version} LANGUAGES C)
install(FILES payload.txt in-use.txt setup.cmd DESTINATION share COMPONENT Runtime)
set(CPACK_GENERATOR "WIX")
set(CPACK_PACKAGE_NAME "sima-cli-msi-e2e")
set(CPACK_PACKAGE_VERSION "{version}")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "{install_name}")
set(CPACK_WIX_VERSION 3)
set(CPACK_WIX_ARCHITECTURE x64)
set(CPACK_WIX_INSTALL_SCOPE perMachine)
set(CPACK_WIX_UPGRADE_GUID "{upgrade_guid}")
set(CPACK_WIX_PRODUCT_GUID "{product_guid}")
set(CPACK_WIX_CMAKE_PACKAGE_REGISTRY "{install_name}")
set(CPACK_COMPONENTS_ALL Runtime)
include(CPack)
""",
        encoding="utf-8",
    )
    run([cmake, "-S", source, "-B", build, "-A", "x64"])
    run([cpack, "--config", build / "CPackConfig.cmake", "-G", "WIX", "-B", dist])
    packages = list(dist.glob("*.msi"))
    if len(packages) != 1:
        raise SystemExit(f"expected one disposable {version} MSI, found {len(packages)}")
    return packages[0]


def package_metadata(msi: Path, version: str, install_name: str) -> dict:
    metadata = {
        "name": PACKAGE,
        "version": version,
        "release": "ci",
        "platforms": [{"type": "host", "os": ["windows"], "arch": ["amd64"]}],
        "resources": [msi.name],
        "resources-checksum": {msi.name: sha256(msi)},
        "installation": {
            "type": "windows-msi",
            "msi": msi.name,
            "setup": {"command": {
                "environment": "ProgramFiles", "relative": f"{install_name}\\share\\setup.cmd",
            }},
        },
    }
    (msi.parent / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    validate_metadata(json.loads((msi.parent / "metadata.json").read_text(encoding="utf-8")))
    return metadata


@contextmanager
def served(directory: Path):
    """Serve directory over HTTP on this host, as a release channel serves metadata."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def sima_cli(home: Path, *arguments: object, expect: int = 0) -> str:
    """Run the installed sima-cli command without input, with its state under home."""
    command = [str(Path(sysconfig.get_path("scripts")) / "sima-cli.exe"), *map(str, arguments)]
    environment = dict(os.environ, USERPROFILE=str(home), SIMA_CLI_CHECK_FOR_UPDATE="0",
                       PYTHONIOENCODING="utf-8", NO_PROXY="127.0.0.1")
    print("+", subprocess.list2cmdline(command), flush=True)
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=environment, encoding="utf-8", errors="replace", timeout=CHILD_TIMEOUT_SECONDS,
            check=False)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"sima-cli did not return within {CHILD_TIMEOUT_SECONDS} s")
    print(result.stdout, flush=True)
    if result.returncode != expect:
        raise SystemExit(f"sima-cli exited with {result.returncode}, expected {expect}")
    return result.stdout


def receipts(home: Path) -> list[dict]:
    registry = home / ".sima-cli" / "registry.json"
    data = json.loads(registry.read_text(encoding="utf-8")) if registry.is_file() else []
    return [entry for entry in data if entry["package_name"] == PACKAGE]


def registry_values(key_path: str) -> dict[str, object]:
    import winreg

    access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
            count = winreg.QueryInfoKey(key)[1]
            return {name: value for name, value, _kind in
                    (winreg.EnumValue(key, index) for index in range(count))}
    except FileNotFoundError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-directory", type=Path,
                        help="Fresh directory retained for CI diagnostics")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("this end-to-end test requires native Windows")
    import winreg

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
    old, new = "1.0.0", "1.1.0"

    if args.work_directory is not None:
        args.work_directory.mkdir(parents=True, exist_ok=False)
    workspace = (nullcontext(args.work_directory) if args.work_directory is not None
                 else tempfile.TemporaryDirectory(prefix="sima-cli-msi-e2e-"))
    with workspace as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        setup_marker = root / "setup-ran.txt"
        os.environ["SIMA_CLI_E2E_SETUP_MARKER"] = str(setup_marker)
        os.environ.pop("SIMAPCIE_SKIP_SETUP", None)
        msis = {
            version: build_msi(
                cmake, cpack, root, version, install_name, upgrade_guid,
                str(uuid.uuid5(namespace, f"sima-cli-msi-e2e-{run_id}-{version}")).upper())
            for version in (old, new)
        }
        metadata = {version: package_metadata(msi, version, install_name) for version, msi in msis.items()}
        codes = {version: windows_package._msi_product_code(msi) for version, msi in msis.items()}
        if codes[old] == codes[new]:
            raise SystemExit(f"both versions have product code {codes[old]}")
        payload = install_root / "share" / "payload.txt"

        def installed(version: str) -> bool:
            return windows_package._msi_product_installed(codes[version])

        def check_payload(version: str) -> None:
            if not payload.is_file() or payload.read_text(encoding="utf-8") != f"sima-cli Windows MSI e2e {version}\n":
                raise SystemExit(f"installed MSI payload is not the {version} one")

        removed = False
        try:
            with served(root / "dist") as channel:
                def install(version: str, expect: int = 0) -> str:
                    return sima_cli(home, "install", "-m", f"{channel}/{version}/metadata.json",
                                    "-d", root / f"downloads-{version}", expect=expect)

                # Without input (stdin is the NUL device, which isatty() accepts on Windows) the
                # install skips the setup step, which may prompt for a password, and prints it.
                output = install(old)
                if "Setup step skipped" not in output or setup_marker.exists():
                    raise SystemExit("install without input did not skip setup")
                check_payload(old)
                if [(entry["version"], entry["state"], [package["product_code"] for package in entry["native_packages"]])
                        for entry in receipts(home)] != [(old, "installed", [codes[old]])]:
                    raise SystemExit(f"unexpected receipts after the first install: {receipts(home)}")
                # The Scheduler and Core MSIs write the same kind of machine registry entry.
                package_key = f"SOFTWARE\\Kitware\\CMake\\Packages\\{install_name}"
                package_values = registry_values(package_key)
                if not package_values:
                    raise SystemExit(f"MSI did not write HKLM\\{package_key}")
                # Rerunning the same release repairs it: a deleted file and a deleted machine
                # registry entry come back, and an unchanged file that an application holds
                # open is left alone instead of requiring a restart.
                payload.unlink()
                winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, package_key, winreg.KEY_WOW64_64KEY)
                # Stand in for an interactive console so the repeat runs the installed setup
                # step through cmd.exe from a Program Files path that contains a space.
                with mock.patch.object(windows_package, "_interactive_console", return_value=True), \
                        held_open(install_root / "share" / "in-use.txt"):
                    repeated = install_windows_package(metadata[old], str(msis[old].parent))
                if repeated["setup"]["status"] != "passed" or not setup_marker.is_file():
                    raise SystemExit(f"interactive install did not run setup: {repeated}")
                if repeated["native"] != "installed":
                    raise SystemExit(f"unexpected repeat-install result: {repeated}")
                check_payload(old)
                if registry_values(package_key) != package_values:
                    raise SystemExit("repeat installation did not repair the MSI registry entry")

                # A newer release upgrades in place and replaces the older receipt.
                output = install(new)
                if f"Replaced the registry receipt of {PACKAGE} {old} with {new}." not in output:
                    raise SystemExit("upgrade did not report the replaced receipt")
                if installed(old) or not installed(new):
                    raise SystemExit("Windows Installer did not replace 1.0.0 with 1.1.0")
                check_payload(new)
                if [(entry["version"], entry["state"]) for entry in receipts(home)] != [(new, "installed")]:
                    raise SystemExit(f"unexpected receipts after the upgrade: {receipts(home)}")

                # An older release is refused, with the way out, and the newer one stays.
                output = install(old, expect=1)
                if ("because a newer version of it is already installed" not in output
                        or f"sima-cli packages remove {PACKAGE} --version <installed version>" not in output):
                    raise SystemExit("downgrade did not print the refusal")
                if installed(old) or not installed(new):
                    raise SystemExit("the refused downgrade changed the installed product")
                check_payload(new)

            # Remove through the command, as a customer does: the refused downgrade left a
            # receipt for 1.0.0, whose package is not installed.
            sima_cli(home, "packages", "remove", PACKAGE, "--version", new, "--yes")
            if installed(new) or install_root.exists():
                raise SystemExit("packages remove left the 1.1.0 MSI installed")
            output = sima_cli(home, "packages", "remove", PACKAGE, "--version", old, "--yes")
            if "native package is not installed; nothing to remove." not in output:
                raise SystemExit("removing the receipt of a package that is not installed did not say so")
            if receipts(home):
                raise SystemExit(f"receipts remain after removal: {receipts(home)}")
            removed = True
        finally:
            if not removed:
                for version, code in codes.items():
                    uninstall_log = root / f"msi-uninstall-{version}.log"
                    result = subprocess.run(
                        ["msiexec.exe", "/x", code, "/quiet", "/norestart", "/L*v", str(uninstall_log)],
                        check=False,
                    )
                    if result.returncode not in (0, windows_package.MSI_UNKNOWN_PRODUCT):
                        raise SystemExit(
                            f"disposable {version} MSI removal failed with {result.returncode}; "
                            f"see {uninstall_log}")
        if install_root.exists():
            raise SystemExit(f"disposable MSI left its installation root: {install_root}")
        (root / "result.json").write_text(json.dumps({
            "status": "PASS", "msi_sha256": {version: sha256(msi) for version, msi in msis.items()},
            "product_codes": codes, "install_root": str(install_root),
            "first_install": "PASS", "setup_skipped_without_input": "PASS",
            "repeat_install_repairs": "PASS", "repair_restores_registry": "PASS",
            "repair_leaves_file_in_use": "PASS", "setup_ran_interactive": "PASS",
            "upgrade": "PASS", "downgrade_refused": "PASS", "packages_remove": "PASS",
            "cleanup": "PASS",
        }, indent=2) + "\n", encoding="utf-8")

    print("WINDOWS_MSI_INSTALL_E2E_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
