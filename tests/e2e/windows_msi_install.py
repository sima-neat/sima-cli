#!/usr/bin/env python3
"""Exercise the real Windows Installer boundary with a disposable MSI.

The MSI is built twice, as 1.0.0 and 1.1.0 with the same upgrade code. Through the installed
sima-cli command it is installed, upgraded, refused as a downgrade and removed; the repair of
an installed release runs through the package installer in this process. Then it is installed
and removed once more through the administrator approval a window that is not elevated uses.

The same MSIs are then installed from offline bundle folders: each holds a local metadata.json,
the MSI, a Python package wheel and a wheelhouse with its pinned dependency. Every request
sent through the proxy variables reaches a local listener, which must record none. Only the
update check is shown to reach it; a connection that ignores those variables is not seen.
"""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import threading
import uuid
import zipfile
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


# Runs the installed sima-cli as from a PowerShell window that is not elevated, the default
# customer path: the approval wrapper, the elevated Windows PowerShell and its result file run
# for real. The runner is already elevated, so Windows grants the approval without a prompt.
# -I keeps the checkout off sys.path but ignores PYTHONIOENCODING, so the streams are set here.
APPROVAL_PATH_CLI = """\
import sys
from unittest import mock
from sima_cli.cli import main
from sima_cli.install import windows_package
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
with mock.patch.object(windows_package, "_is_windows_administrator", return_value=False), \\
        mock.patch.object(windows_package, "_interactive_console", return_value=True):
    main(prog_name="sima-cli")
"""
APPROVAL_REQUEST = "Requesting Windows administrator approval once for the native packages."


def sima_cli(home: Path, *arguments: object, expect: int = 0, approval: bool = False,
             proxy: str | None = None) -> str:
    """Run the installed sima-cli command without input, with its state under home.

    With proxy, every proxy variable points to it and the update check is left enabled.
    """
    command = [str(Path(sysconfig.get_path("scripts")) / "sima-cli.exe"), *map(str, arguments)]
    if approval:
        command = [sys.executable, "-I", "-c", APPROVAL_PATH_CLI, *command[1:]]
    environment = dict(os.environ, USERPROFILE=str(home), SIMA_CLI_CHECK_FOR_UPDATE="0",
                       PYTHONIOENCODING="utf-8", NO_PROXY="127.0.0.1")
    if proxy:
        for name in ("SIMA_CLI_CHECK_FOR_UPDATE", "NO_PROXY", "no_proxy"):
            environment.pop(name, None)
        environment.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy)
    print("+", subprocess.list2cmdline(command), flush=True)
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=environment, encoding="utf-8", errors="replace", timeout=CHILD_TIMEOUT_SECONDS,
            check=False)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"sima-cli did not return within {CHILD_TIMEOUT_SECONDS} s")
    # The job's console is cp1252; the CLI's output has characters it cannot encode.
    sys.stdout.buffer.write(result.stdout.encode(sys.stdout.encoding or "utf-8", "replace") + b"\n")
    sys.stdout.flush()
    if result.returncode != expect:
        raise SystemExit(f"sima-cli exited with {result.returncode}, expected {expect}")
    return result.stdout


def build_wheel(folder: Path, name: str, version: str, requires: tuple[str, ...] = ()) -> Path:
    """Write a minimal pure-Python wheel."""
    module = name.replace("-", "_")
    dist_info = f"{module}-{version}.dist-info"
    files = {
        f"{module}/__init__.py": f"__version__ = {version!r}\n",
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires),
        f"{dist_info}/WHEEL": "Wheel-Version: 1.0\nGenerator: sima-cli-e2e\n"
        "Root-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for path, text in files.items():
        data = text.encode("utf-8")
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(data)}")
    files[f"{dist_info}/RECORD"] = "\n".join(record + [f"{dist_info}/RECORD,,"]) + "\n"
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel


OFFLINE_PACKAGE = "sima-cli-e2e-package"
OFFLINE_DEPENDENCY = "sima-cli-e2e-dependency"


def offline_bundle(folder: Path, msi: Path, version: str, install_name: str) -> Path:
    """Lay out an offline bundle as Core ships one; return its metadata.json.

    The wheelhouse also holds a newer dependency that the metadata does not list.
    """
    folder.mkdir(parents=True)
    shutil.copy2(msi, folder / msi.name)
    wheel = build_wheel(folder, OFFLINE_PACKAGE, version, (OFFLINE_DEPENDENCY,))
    dependency = build_wheel(folder / "wheelhouse", OFFLINE_DEPENDENCY, version)
    build_wheel(folder / "wheelhouse", OFFLINE_DEPENDENCY, "9.0.0")
    resources = [msi.name, wheel.name, f"wheelhouse/{dependency.name}"]
    metadata = {
        "name": PACKAGE,
        "version": version,
        "release": "ci",
        "platforms": [{"type": "host", "os": ["windows"], "arch": ["amd64"]}],
        "resources": resources,
        "resources-checksum": {
            resource: sha256(folder.joinpath(*resource.split("/"))) for resource in resources
        },
        "installation": {
            "type": "windows-msi",
            "msi": msi.name,
            "python": {
                "wheel": wheel.name,
                "distribution": OFFLINE_PACKAGE,
                "version": version,
                "import": OFFLINE_PACKAGE.replace("-", "_"),
                "requires-python": f"{sys.version_info.major}.{sys.version_info.minor}",
                "arch": "amd64",
                "wheelhouse": [{
                    "wheel": f"wheelhouse/{dependency.name}",
                    "distribution": OFFLINE_DEPENDENCY,
                    "version": version,
                }],
            },
            "setup": {"command": {
                "environment": "ProgramFiles", "relative": f"{install_name}\\share\\setup.cmd",
            }},
        },
    }
    path = folder / "metadata.json"
    path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    validate_metadata(json.loads(path.read_text(encoding="utf-8")))
    return path


@contextmanager
def connection_recorder():
    """Accept and close every TCP connection on this host; yield its URL and the count."""
    connections = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def accept():
        while True:
            try:
                client, _address = listener.accept()
            except OSError:
                return
            with client:
                client.settimeout(5)
                try:
                    connections.append(client.recv(200))
                except OSError as error:
                    connections.append(repr(error).encode())

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}", connections
    finally:
        listener.close()
        thread.join(timeout=5)


def installed_version(home: Path, distribution: str) -> str:
    """Return a distribution's version in the managed environment sima-cli created."""
    python = home / OFFLINE_PACKAGE / "Scripts" / "python.exe"
    return subprocess.run(
        [str(python), "-I", "-c",
         f"import importlib.metadata as m; print(m.version({distribution!r}))"],
        check=True, stdout=subprocess.PIPE, encoding="utf-8").stdout.strip()


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

            # Install and remove once more through the administrator approval path.
            with served(root / "dist") as channel:
                output = sima_cli(home, "install", "-m", f"{channel}/{new}/metadata.json",
                                  "-d", root / "downloads-approval", approval=True)
            if APPROVAL_REQUEST not in output:
                raise SystemExit("the install did not request administrator approval")
            if not installed(new):
                raise SystemExit("the install through administrator approval left 1.1.0 uninstalled")
            check_payload(new)
            if [(entry["version"], entry["state"]) for entry in receipts(home)] != [(new, "installed")]:
                raise SystemExit(f"unexpected receipts after the approved install: {receipts(home)}")
            output = sima_cli(home, "packages", "remove", PACKAGE, "--version", new, "--yes",
                              approval=True)
            if APPROVAL_REQUEST not in output:
                raise SystemExit("packages remove did not request administrator approval")
            if installed(new) or install_root.exists():
                raise SystemExit("packages remove through administrator approval left 1.1.0 installed")
            if receipts(home):
                raise SystemExit(f"receipts remain after the approved removal: {receipts(home)}")

            # Offline: the same releases from bundle folders, with no index or update check.
            offline_home = root / "offline-home"
            offline_home.mkdir()
            bundles = {
                version: offline_bundle(root / "offline bundle" / version, msi, version, install_name)
                for version, msi in msis.items()
            }
            changed = root / "offline bundle" / "1.0.0-changed"
            shutil.copytree(bundles[old].parent, changed)
            dependency = f"wheelhouse/sima_cli_e2e_dependency-{old}-py3-none-any.whl"
            changed_wheel = changed.joinpath(*dependency.split("/"))
            changed_wheel.write_bytes(changed_wheel.read_bytes() + b"\0")
            with connection_recorder() as (proxy, connections):
                # The listener must see what the offline install must not do: the update check.
                sima_cli(root / "canary-home", "packages", "list", proxy=proxy)
                if not connections:
                    raise SystemExit("the update check did not reach the proxy listener")
                connections.clear()

                def offline(version_folder: Path, expect: int = 0) -> str:
                    return sima_cli(offline_home, "install", "-m", version_folder / "metadata.json",
                                    expect=expect, proxy=proxy)

                output = offline(changed, expect=1)
                if f"{dependency}: SHA-256 differs" not in output or installed(old):
                    raise SystemExit("a changed bundle file did not stop the install before the MSI")

                output = offline(bundles[old].parent)
                if f"Installing from the packages in: {bundles[old].parent}" not in output:
                    raise SystemExit("the install did not use the bundle folder")
                check_payload(old)
                if [(entry["version"], entry["state"], [package["product_code"] for package in entry["native_packages"]])
                        for entry in receipts(offline_home)] != [(old, "installed", [codes[old]])]:
                    raise SystemExit(f"unexpected offline receipts: {receipts(offline_home)}")
                if not (bundles[old].parent / "sima-cli-msi-install-01-native.log").is_file():
                    raise SystemExit("the MSI log is not beside the bundle's packages")
                # The pinned dependency, not the newer one in the same folder.
                if (installed_version(offline_home, OFFLINE_PACKAGE), installed_version(
                        offline_home, OFFLINE_DEPENDENCY)) != (old, old):
                    raise SystemExit("the offline install did not install the pinned versions")

                output = offline(bundles[old].parent)
                if ("Repairing installed" not in output
                        or f"Python package {OFFLINE_PACKAGE} {old} is already installed and verified." not in output):
                    raise SystemExit("rerunning the offline install did not repair it")

                output = offline(bundles[new].parent)
                if f"Replaced the registry receipt of {PACKAGE} {old} with {new}." not in output:
                    raise SystemExit("the offline upgrade did not report the replaced receipt")
                if installed(old) or not installed(new):
                    raise SystemExit("the offline upgrade did not replace 1.0.0 with 1.1.0")
                if installed_version(offline_home, OFFLINE_DEPENDENCY) != new:
                    raise SystemExit("the offline upgrade did not install the new pinned dependency")

                output = offline(bundles[old].parent, expect=1)
                if "because a newer version of it is already installed" not in output:
                    raise SystemExit("the offline downgrade did not print the refusal")
                if installed(old) or not installed(new):
                    raise SystemExit("the refused offline downgrade changed the installed product")

                sima_cli(offline_home, "packages", "remove", PACKAGE, "--version", new, "--yes")
                sima_cli(offline_home, "packages", "remove", PACKAGE, "--version", old, "--yes")
                if installed(new) or install_root.exists() or receipts(offline_home):
                    raise SystemExit("packages remove left the offline install behind")
                if connections:
                    raise SystemExit(
                        f"the offline install sent requests through the proxy: {connections}")
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
            "approval_install": "PASS", "approval_remove": "PASS",
            "offline_changed_file_refused": "PASS", "offline_install": "PASS",
            "offline_pinned_dependency": "PASS", "offline_repair": "PASS",
            "offline_upgrade": "PASS", "offline_downgrade_refused": "PASS",
            "offline_packages_remove": "PASS", "offline_no_proxied_request": "PASS",
            "cleanup": "PASS",
        }, indent=2) + "\n", encoding="utf-8")

    print("WINDOWS_MSI_INSTALL_E2E_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
