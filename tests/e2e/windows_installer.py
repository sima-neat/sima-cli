#!/usr/bin/env python3
"""Install, upgrade and remove the sima-cli Windows installer (MSI) on native Windows.

lifecycle: with Windows Installer directly. An older MSI is installed, the candidate upgrades
  it, the older one is then refused as a downgrade, and the candidate is removed. After each
  install a new process, given the PATH a new window gets from the registry, must run the MSI's
  sima-cli ahead of a sima-cli.cmd stub later in the user PATH.
winget: with WinGet from a local manifest whose InstallerUrl is served on 127.0.0.1.

Only the standard library is used: the host Python never gets sima-cli.
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 600
INSTALL_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "sima-cli"
INSTALL_BIN = INSTALL_ROOT / "bin"
SIMA_CLI = INSTALL_BIN / "sima-cli.exe"
UNINSTALL_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
USER_ENVIRONMENT = "Environment"
MACHINE_ENVIRONMENT = "SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment"
# Modules the bundled runtime must import: the CLI's own and PCIe discovery's dependencies.
IMPORTS = "import rich, click, paramiko, cryptography, huggingface_hub, hf_xet, InquirerPy, psutil, yaml, wmi, win32com.client"
MSI_DOWNGRADE_REFUSED = 1603
MSIHANDLE = wintypes.DWORD
INSTALLSTATE_DEFAULT = 5


def log(message: str) -> None:
    sys.stdout.buffer.write(message.encode(sys.stdout.encoding or "utf-8", "replace") + b"\n")
    sys.stdout.flush()


def run(command: list, *, expect: int | None = 0, **kwargs) -> subprocess.CompletedProcess:
    argv = [str(part) for part in command]
    log("+ " + subprocess.list2cmdline(argv))
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS, check=False, **kwargs)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{argv[0]} did not return within {TIMEOUT_SECONDS} s")
    if result.stdout:
        log(result.stdout.rstrip())
    if expect is not None and result.returncode != expect:
        raise SystemExit(f"{argv[0]} exited with {result.returncode}, expected {expect}")
    return result


def system_tool(name: str) -> str:
    return str(Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / name)


def msiexec(action: str, target, log_file: Path, expect: int = 0) -> None:
    run([system_tool("msiexec.exe"), action, target, "/quiet", "/norestart", "/L*v", log_file], expect=expect)


def msi_dll():
    msi = ctypes.WinDLL("msi")
    msi.MsiQueryProductStateW.argtypes = [wintypes.LPCWSTR]
    msi.MsiQueryProductStateW.restype = ctypes.c_int
    msi.MsiEnumRelatedProductsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPWSTR]
    msi.MsiGetSummaryInformationW.argtypes = [MSIHANDLE, wintypes.LPCWSTR, wintypes.UINT, ctypes.POINTER(MSIHANDLE)]
    msi.MsiSummaryInfoGetPropertyW.argtypes = [
        MSIHANDLE, wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    msi.MsiCloseHandle.argtypes = [MSIHANDLE]
    return msi


def installed(product_code: str) -> bool:
    return msi_dll().MsiQueryProductStateW(product_code) == INSTALLSTATE_DEFAULT


def related_products(upgrade_code: str) -> list:
    msi, found, index = msi_dll(), [], 0
    while True:
        buffer = ctypes.create_unicode_buffer(39)
        if msi.MsiEnumRelatedProductsW(upgrade_code, 0, index, buffer) != 0:
            return found
        found.append(buffer.value)
        index += 1


def word_count(msi_path: Path) -> int:
    """Summary property 15; bit 3 set means the package needs no elevation."""
    msi, handle = msi_dll(), MSIHANDLE()
    if msi.MsiGetSummaryInformationW(0, str(msi_path), 0, ctypes.byref(handle)) != 0:
        raise SystemExit(f"cannot read the summary information of {msi_path}")
    try:
        kind, value = wintypes.UINT(), ctypes.c_int()
        if msi.MsiSummaryInfoGetPropertyW(handle, 15, ctypes.byref(kind), ctypes.byref(value), None, None, None):
            raise SystemExit(f"cannot read the Word Count of {msi_path}")
        return value.value
    finally:
        msi.MsiCloseHandle(handle)


def registry_values(hive, path: str) -> dict:
    import winreg

    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            count = winreg.QueryInfoKey(key)[1]
            return {name: value for name, value, _kind in (winreg.EnumValue(key, i) for i in range(count))}
    except FileNotFoundError:
        return {}


def raw_path(hive, path: str):
    """The PATH value as stored, unexpanded, and its registry kind; (None, None) when absent."""
    import winreg

    with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
        try:
            return winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return None, None


def set_user_path(value, kind) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_ENVIRONMENT, 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, "Path")
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, "Path", 0, kind, value)


def same_folder(entry: str, folder: Path) -> bool:
    import winreg

    return winreg.ExpandEnvironmentStrings(entry).strip().strip('"').rstrip("\\").lower() == str(folder).lower()


def new_window_environment() -> dict:
    """The PATH a new window gets: the system value, then the user value, both from the registry."""
    import winreg

    parts = [raw_path(winreg.HKEY_LOCAL_MACHINE, MACHINE_ENVIRONMENT)[0],
             raw_path(winreg.HKEY_CURRENT_USER, USER_ENVIRONMENT)[0]]
    path = ";".join(winreg.ExpandEnvironmentStrings(part) for part in parts if part)
    return dict(os.environ, PATH=path, SIMA_CLI_CHECK_FOR_UPDATE="0")


def check_new_window(root: Path, version: str, stub: Path) -> None:
    """A new process resolves sima-cli through PATH to the MSI's launcher, ahead of the stub."""
    empty = root / "empty-current-directory"
    empty.mkdir(exist_ok=True)
    environment = new_window_environment()
    hits = [Path(line.strip()) for line in run([system_tool("where.exe"), "sima-cli"], cwd=empty,
                                                env=environment).stdout.splitlines() if line.strip()]
    if not hits or str(hits[0]).lower() != str(SIMA_CLI).lower():
        raise SystemExit(f"a new window runs {hits[:1]}, not {SIMA_CLI}")
    if str(stub).lower() not in [str(hit).lower() for hit in hits[1:]]:
        raise SystemExit(f"the stub {stub} is not on the PATH after the MSI's sima-cli: {hits}")
    output = run([system_tool("cmd.exe"), "/d", "/c", "sima-cli", "--version"], cwd=empty, env=environment).stdout
    if not output.strip().endswith(f"SiMa CLI version: {version}"):
        raise SystemExit(f"sima-cli --version from a new window did not report {version}")
    run([system_tool("cmd.exe"), "/d", "/c", "sima-cli", "--help"], cwd=empty, env=environment)


def check_runtime() -> None:
    python = INSTALL_ROOT / "runtime" / "python.exe"
    run([python, "-c", IMPORTS])
    run([python, "-c", "from sima_cli.utils.env import installed_by_windows_msi as f; assert f()"])
    if run([python, "-c", "import pip"], expect=None).returncode == 0:
        raise SystemExit("the bundled runtime has pip; nothing may install into the MSI's folder")


def installed_files() -> set:
    return {path.relative_to(INSTALL_ROOT) for path in INSTALL_ROOT.rglob("*")}


def check_installed(record: dict, path_before: str, kind_before) -> None:
    """Per-user placement: files under %LOCALAPPDATA%, the Apps entry in HKCU, PATH prepended once."""
    import winreg

    if not SIMA_CLI.is_file() or not (INSTALL_ROOT / "runtime" / "python.exe").is_file():
        raise SystemExit(f"the MSI did not install {SIMA_CLI} and its runtime")
    if not installed(record["product_code"]):
        raise SystemExit(f"Windows Installer does not report {record['product_code']} as installed")
    entry = registry_values(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY + record["product_code"])
    expected = {"DisplayName": "sima-cli", "Publisher": "SiMa.ai", "DisplayVersion": record["msi_version"]}
    if {name: entry.get(name) for name in expected} != expected:
        raise SystemExit(f"unexpected Apps list entry in HKCU: {entry}")
    if registry_values(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY + record["product_code"]):
        raise SystemExit("the per-user MSI wrote a machine-wide Apps list entry")
    value, kind = raw_path(winreg.HKEY_CURRENT_USER, USER_ENVIRONMENT)
    first, _, rest = value.partition(";")
    if not same_folder(first, INSTALL_BIN):
        raise SystemExit(f"the user PATH does not start with {INSTALL_BIN}: {value}")
    if rest != path_before:
        raise SystemExit(f"Windows Installer changed other user PATH entries: {value!r}, was {path_before!r}")
    if kind != kind_before:
        raise SystemExit(f"Windows Installer changed the user PATH kind from {kind_before} to {kind}")


@contextmanager
def served(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def seeded_user_state(root: Path):
    """A sentinel sima-cli state folder and a sima-cli.cmd stub later in the user PATH.

    Yields (stub folder, user PATH before any install, its kind, state checker). The user PATH
    and the state folder are restored however the test ends.
    """
    import winreg

    state = Path.home() / ".sima-cli"
    registry = state / "registry.json"
    if registry.exists() or INSTALL_ROOT.exists():
        raise SystemExit(f"refusing to overwrite pre-existing {registry} or {INSTALL_ROOT}")
    original, original_kind = raw_path(winreg.HKEY_CURRENT_USER, USER_ENVIRONMENT)
    stub = root / "earlier-sima-cli"
    stub.mkdir()
    (stub / "sima-cli.cmd").write_text("@echo stub sima-cli\r\n", encoding="utf-8")
    created_state = not state.exists()
    state.mkdir(exist_ok=True)
    sentinel = b'[{"package_name": "gh:sima-neat/tests/windows-installer-sentinel", "version": "1.0.0"}]\n'
    registry.write_bytes(sentinel)
    kind = original_kind if original_kind is not None else winreg.REG_EXPAND_SZ
    seeded = f"{original.rstrip(';')};{stub}" if original else str(stub)
    set_user_path(seeded, kind)

    def check_state() -> None:
        if registry.read_bytes() != sentinel:
            raise SystemExit(f"{registry} changed")

    try:
        yield stub, seeded, kind, check_state
    finally:
        set_user_path(original, original_kind)
        if created_state:
            shutil.rmtree(state, ignore_errors=True)
        else:
            registry.unlink(missing_ok=True)


def remove_leftovers(root: Path, codes: list) -> None:
    for index, code in enumerate(codes):
        if installed(code):
            msiexec("/x", code, root / f"cleanup-{index}.log", expect=None)


def lifecycle(args) -> dict:
    import winreg

    root = args.work_directory
    new = json.loads(args.installer_json.read_text(encoding="utf-8"))
    old = json.loads(args.older_installer_json.read_text(encoding="utf-8"))
    new_msi = args.installer_json.parent / new["file"]
    old_msi = args.older_installer_json.parent / old["file"]
    if new["upgrade_code"] != old["upgrade_code"] or new["product_code"] == old["product_code"]:
        raise SystemExit("the two MSIs must share the upgrade code and differ in product code")
    if related_products(new["upgrade_code"]):
        raise SystemExit(f"a sima-cli MSI is already installed: {related_products(new['upgrade_code'])}")
    for msi in (new_msi, old_msi):
        if not word_count(msi) & 8:
            raise SystemExit(f"{msi.name} does not declare that it needs no elevation")

    with seeded_user_state(root) as (stub, path_before, kind_before, check_state):
        try:
            msiexec("/i", old_msi, root / "install-older.log")
            check_installed(old, path_before, kind_before)
            before = installed_files()
            check_new_window(root, new["version"], stub / "sima-cli.cmd")
            check_runtime()
            if installed_files() != before:
                raise SystemExit(f"running sima-cli wrote into the install folder: {installed_files() - before}")

            msiexec("/i", new_msi, root / "install-candidate.log")
            check_installed(new, path_before, kind_before)
            if installed(old["product_code"]) or related_products(new["upgrade_code"]) != [new["product_code"]]:
                raise SystemExit(f"the upgrade left {related_products(new['upgrade_code'])}")
            check_new_window(root, new["version"], stub / "sima-cli.cmd")

            msiexec("/i", old_msi, root / "downgrade.log", expect=MSI_DOWNGRADE_REFUSED)
            if installed(old["product_code"]) or not installed(new["product_code"]):
                raise SystemExit("the refused downgrade changed the installed product")
            check_installed(new, path_before, kind_before)

            msiexec("/x", new["product_code"], root / "uninstall.log")
            if INSTALL_ROOT.exists():
                raise SystemExit(f"uninstall left {sorted(map(str, installed_files()))[:20]} in {INSTALL_ROOT}")
            value, kind = raw_path(winreg.HKEY_CURRENT_USER, USER_ENVIRONMENT)
            if (value, kind) != (path_before, kind_before):
                raise SystemExit(f"uninstall did not restore the user PATH exactly: {value!r}")
            if registry_values(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY + new["product_code"]):
                raise SystemExit("uninstall left the Apps list entry")
            check_state()
        finally:
            remove_leftovers(root, [new["product_code"], old["product_code"]])
    return {"older_install": "PASS", "new_window_resolves_msi": "PASS", "runtime_imports": "PASS",
            "upgrade": "PASS", "downgrade_refused": "PASS", "uninstall_clean": "PASS",
            "user_state_kept": "PASS", "product_codes": [old["product_code"], new["product_code"]]}


def winget_install(args) -> dict:
    """WinGet installs and removes the MSI from a manifest that differs only in its URL."""
    import winreg

    root = args.work_directory
    record = json.loads(args.installer_json.read_text(encoding="utf-8"))
    winget = args.winget
    run([winget, "--version"])
    run([winget, "settings", "--enable", "LocalManifestFiles"])
    local = root / "local-manifest"
    result = {}
    with seeded_user_state(root) as (stub, path_before, kind_before, check_state), \
            served(args.installer_json.parent) as base:
        try:
            run([sys.executable, "-I", ROOT / "packaging/windows/winget_manifest.py",
                 "--installer-json", args.installer_json, "--installer-url", f"{base}/{record['file']}",
                 "--output-directory", local])
            run([winget, "install", "--manifest", local, "--silent", "--disable-interactivity",
                 "--accept-package-agreements", "--accept-source-agreements", "--log", root / "winget-install.log"])
            check_installed(record, path_before, kind_before)
            check_new_window(root, record["version"], stub / "sima-cli.cmd")
            listed = run([winget, "list", "--name", "sima-cli", "--disable-interactivity",
                          "--accept-source-agreements"]).stdout
            if record["msi_version"] not in listed:
                raise SystemExit(f"winget list does not show sima-cli {record['msi_version']}")
            removal = run([winget, "uninstall", "--manifest", local, "--silent", "--disable-interactivity",
                           "--accept-source-agreements"], expect=None)
            result["uninstall"] = "manifest"
            if removal.returncode != 0:
                run([winget, "uninstall", "--product-code", record["product_code"], "--silent",
                     "--disable-interactivity", "--accept-source-agreements"])
                result["uninstall"] = "product-code"
            if INSTALL_ROOT.exists() or installed(record["product_code"]):
                raise SystemExit("winget uninstall left sima-cli installed")
            if raw_path(winreg.HKEY_CURRENT_USER, USER_ENVIRONMENT) != (path_before, kind_before):
                raise SystemExit("winget uninstall did not restore the user PATH exactly")
            check_state()
        finally:
            remove_leftovers(root, [record["product_code"]])
    result.update({"winget_install": "PASS", "new_window_resolves_msi": "PASS", "winget_uninstall": "PASS"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=("lifecycle", "winget"))
    parser.add_argument("--installer-json", type=Path, required=True, help="installer.json of the candidate MSI")
    parser.add_argument("--older-installer-json", type=Path, help="installer.json of the older MSI (lifecycle)")
    parser.add_argument("--winget", help="Path of winget.exe (winget)")
    parser.add_argument("--work-directory", type=Path, required=True, help="New folder for logs and result.json")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("this end-to-end test requires native Windows")
    if args.phase == "lifecycle" and not args.older_installer_json:
        parser.error("lifecycle needs --older-installer-json")
    if args.phase == "winget" and not args.winget:
        parser.error("winget needs --winget")
    args.work_directory.mkdir(parents=True, exist_ok=False)
    result = lifecycle(args) if args.phase == "lifecycle" else winget_install(args)
    record = json.loads(args.installer_json.read_text(encoding="utf-8"))
    result.update({"status": "PASS", "phase": args.phase, "msi_sha256": record["sha256"]})
    (args.work_directory / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    log(f"WINDOWS_INSTALLER_{args.phase.upper()}_E2E_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
