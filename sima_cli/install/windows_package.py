"""Install a declarative Windows MSI and optional Python wheel."""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

import click

from sima_cli.playbooks.manager import SkillError, SkillManager

try:
    import ctypes
except ImportError:  # A Python built without libffi; only the Windows paths use ctypes,
    ctypes = None  # and each of them falls back when an attribute lookup fails.


MSI_RESTART_INITIATED = 1641
MSI_RESTART_REQUIRED = 3010
MSI_INSTALL_REJECTED_BY_POLICY = 1625
WINDOWS_ELEVATION_CANCELLED = 1223
ELEVATED_INSTALLER_EXIT_CODE_MISSING = 9001
ELEVATED_INSTALLER_LAUNCH_FAILED = 9002
ELEVATED_INSTALLER_HASH_MISMATCH = 9003
MSI_UNKNOWN_PRODUCT = 1605
MSI_INSTALL_FAILURE = 1603
INSTALLSTATE_DEFAULT = 5
_PRODUCT_CODE = re.compile(r"\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", re.IGNORECASE)
PYTHON_WINDOWS_DOWNLOAD_URL = "https://www.python.org/downloads/windows/"
# The variable install_pciehost.sh reads on Linux to skip the same setup step.
SETUP_SKIP_ENVIRONMENT = "SIMAPCIE_SKIP_SETUP"


class WindowsPackageRestartRequired(click.ClickException):
    """The native package installed, but Windows must restart before pip runs."""

    exit_code = MSI_RESTART_REQUIRED


class WindowsPythonInstallError(click.ClickException):
    """The MSI succeeded, but the requested Python installation failed."""


class WindowsNativeInstallError(click.ClickException):
    """One MSI failed after an earlier package may have installed."""


class WindowsPrerequisiteError(click.ClickException):
    """The host is not ready for the requested Windows package transaction."""


class WindowsSetupError(click.ClickException):
    """The packages installed, but their declared setup step did not complete."""


class _WindowsPnpDriverVersionError(RuntimeError):
    """A matching Windows PnP device set has no unambiguous driver version."""


def _is_windows() -> bool:
    return os.name == "nt"


def _interactive_console() -> bool:
    """Whether a person can answer a prompt in this process's console.

    isatty() alone is not enough on Windows: the NUL device is a character device, so a
    caller that starts sima-cli with stdin=subprocess.DEVNULL still reports a terminal.
    Input must be a console handle, and the console must have a window to type into.
    """
    if sys.stdin is None:
        return False
    try:
        if not sys.stdin.isatty():
            return False
        if not _is_windows():
            return True
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(sys.stdin.fileno()))
        mode = ctypes.c_ulong()
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))) and bool(
            kernel32.GetConsoleWindow()
        )
    except (AttributeError, ImportError, OSError, ValueError):
        return False


def _declared_resource(
    metadata: Dict, install_dir: str, resource: str, rerun: Optional[str] = None
) -> Path:
    resources = metadata.get("resources", [])
    if resource not in resources:
        raise click.ClickException(
            f"Windows package resource '{resource}' is not declared in metadata.resources."
        )
    if not resource or resource.startswith(("/", "\\")) or "\\" in resource:
        raise click.ClickException(f"Windows package resource is not a safe relative path: '{resource}'.")
    parts = Path(resource).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise click.ClickException(f"Windows package resource is not a safe relative path: '{resource}'.")

    root = Path(install_dir).resolve()
    path = root.joinpath(*parts).resolve()
    if root not in path.parents:
        raise click.ClickException(f"Windows package resource escapes the install directory: '{resource}'.")
    if not path.is_file():
        if isinstance(rerun, _BundleRerun):
            raise click.ClickException(
                f"Windows package resource is missing: '{path}'. {_fresh_copy(rerun)}"
            )
        raise click.ClickException(f"Downloaded Windows package resource is missing: '{path}'.")
    return path


def _declared_sha256(metadata: Dict, resource: str) -> str:
    return str(metadata.get("resources-checksum", {}).get(resource, "")).strip().lower()


_RERUN_SAME_COMMAND = "rerun the same command."


class _BundleRerun(str):
    """The rerun advice of a local offline bundle, whose files are copied, not downloaded."""


def _rerun(retry_command: Optional[str], local_bundle: bool = False) -> str:
    """End a recovery message with the exact command when the caller knows it."""
    if local_bundle:
        return _BundleRerun(_RERUN_SAME_COMMAND)
    return f"rerun this exact release: {retry_command}" if retry_command else _RERUN_SAME_COMMAND


def _fresh_copy(rerun: str) -> str:
    """Say how to replace a resource that is missing or no longer matches its SHA-256."""
    if isinstance(rerun, _BundleRerun):
        return f"Copy the bundle again from the computer that downloaded it, then {rerun}"
    return f"To download a verified copy, {rerun}"


# FILE_SHARE_READ: other processes may read the file, but not write, rename or delete it.
_FILE_SHARE_READ = 0x1


def _open_unchangeable(path: Path):
    """Open path for reading; on Windows nothing can change it until the file is closed."""
    if os.name != "nt":
        return open(path, "rb")
    import _winapi
    import msvcrt

    handle = _winapi.CreateFile(
        str(path), _winapi.GENERIC_READ, _FILE_SHARE_READ, _winapi.NULL,
        _winapi.OPEN_EXISTING, 0, _winapi.NULL,
    )
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


def _verify_sha256(
    resource: str,
    path: Path,
    expected: str,
    error_type,
    rerun: str = _RERUN_SAME_COMMAND,
    hold: bool = False,
):
    """Refuse a resource whose bytes differ from the declared SHA-256.

    With hold, the file is opened before it is hashed and returned open, so nothing can
    change it until the caller closes it.
    """
    from sima_cli.install.metadata_installer import _compute_sha256

    held = None
    try:
        held = _open_unchangeable(path) if hold else None
        actual = _compute_sha256(path)
    except OSError as error:
        if held is not None:
            held.close()
        raise error_type(
            f"Windows package resource '{resource}' could not be read for SHA-256 "
            f"verification at '{path}': {error}; it was not started. "
            f"Close any program using the file, then {rerun}"
        ) from error
    if actual != expected:
        if held is not None:
            held.close()
        raise error_type(
            f"Windows package resource '{resource}' no longer matches its declared SHA-256 "
            f"(expected {expected}, found {actual}); it was not started. "
            f"{_fresh_copy(rerun)}"
        )
    return held


def _verify_declared_checksum(
    metadata: Dict, resource: str, path: Path, error_type, rerun: str = _RERUN_SAME_COMMAND
) -> None:
    _verify_sha256(resource, path, _declared_sha256(metadata, resource), error_type, rerun)


def _run(command, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, **kwargs)


def _system_program(*parts: str) -> str:
    """Return the full path of a program in the Windows system directory.

    Windows searches the Python and current directories before System32 for a bare
    name, so a planted msiexec.exe or powershell.exe there would run elevated.
    """
    directory = r"C:\Windows\System32"
    try:
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)):
            directory = buffer.value
    except AttributeError:
        pass
    return str(PureWindowsPath(directory, *parts))


def _run_powershell(script: str, variables: Optional[Dict[str, str]] = None, **kwargs):
    """Run a Windows PowerShell script; values reach it as environment variables.

    PSModulePath is dropped: inherited from PowerShell 7 through this process, it makes
    Windows PowerShell 5.1 autoload PowerShell 7 modules; unset, 5.1 builds its default.
    """
    environment = {
        name: value for name, value in os.environ.items() if name.upper() != "PSMODULEPATH"
    }
    return _run(
        [
            _system_program("WindowsPowerShell", "v1.0", "powershell.exe"),
            "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script,
        ],
        env={**environment, **(variables or {})},
        text=True,
        errors="replace",
        **kwargs,
    )


def _is_windows_administrator() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


# Runs in the elevated process. It receives the whole ordered request on its command
# line, refuses an MSI whose bytes differ from the hash the CLI verified (holding a
# read lock so the file cannot change before msiexec reads it), stops at the first
# exit code outside continue_codes, and rewrites the CLI's result file after each
# package so a result survives a later crash.
_ELEVATED_MSI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$request = [Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String('@REQUEST@')) | ConvertFrom-Json
$msiexec = Join-Path ([Environment]::SystemDirectory) 'msiexec.exe'
$results = @()
foreach ($package in $request.packages) {
  $result = [ordered]@{ owner = $package.owner; exit_code = 9001; error = '' }
  $lock = $null
  try {
    if ($package.msi) {
      $lock = [IO.File]::Open($package.msi, [IO.FileMode]::Open,
        [IO.FileAccess]::Read, [IO.FileShare]::Read)
      $actual = [BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash($lock)
      ).Replace('-', '').ToLowerInvariant()
      if ($actual -ne $package.sha256) {
        $result.exit_code = 9003
        $result.error = "found $actual"
      }
    }
    if ($result.exit_code -ne 9003) {
      $info = New-Object System.Diagnostics.ProcessStartInfo $msiexec, $package.arguments
      $info.UseShellExecute = $false
      $process = [System.Diagnostics.Process]::Start($info)
      $process.WaitForExit()
      $result.exit_code = $process.ExitCode
    }
  } catch {
    $result.exit_code = 9002
    $result.error = $_.Exception.Message
  } finally {
    if ($null -ne $lock) { $lock.Dispose() }
  }
  $results += New-Object PSObject -Property $result
  [IO.File]::WriteAllText($request.result,
    (ConvertTo-Json -InputObject @{ packages = $results } -Depth 4 -Compress))
  if ($request.continue_codes -notcontains $result.exit_code) { break }
}
"""

# Runs unelevated. Start-Process drops the Win32 error code, so only Process.Start
# can tell a declined approval prompt (1223) apart from any other launch failure.
_ELEVATION_WRAPPER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo `
    (Join-Path $PSHOME 'powershell.exe'), $env:SIMA_CLI_ELEVATED_ARGUMENTS
  $startInfo.Verb = 'runas'
  $startInfo.UseShellExecute = $true
  $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
  $process = [System.Diagnostics.Process]::Start($startInfo)
} catch {
  $exception = $_.Exception
  [Console]::Error.WriteLine($exception.Message)
  while ($null -ne $exception) {
    if ($exception -is [System.ComponentModel.Win32Exception] -and
        $exception.NativeErrorCode -eq 1223) {
      exit 1223
    }
    $exception = $exception.InnerException
  }
  exit 9002
}
if ($null -eq $process) {
  [Console]::Error.WriteLine('Windows Installer did not report an exit code.')
  exit 9001
}
$process.WaitForExit()
exit $process.ExitCode
"""


def _read_elevated_results(path: Path, items: List[Dict]) -> List[Tuple[int, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "null")
    except (OSError, ValueError):
        return []
    entries = data.get("packages") if isinstance(data, dict) else None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    results = []
    for item, entry in zip(items, entries):
        if (
            not isinstance(entry, dict)
            or entry.get("owner") != item["owner"]
            or type(entry.get("exit_code")) is not int
        ):
            break
        results.append((entry["exit_code"], str(entry.get("error") or "")))
    return results


def _run_elevated_msi_batch(
    items: List[Dict], continue_codes: List[int]
) -> Tuple[subprocess.CompletedProcess, List[Tuple[int, str]]]:
    """Run every msiexec step in one elevated process; return each step's result."""
    work = Path(tempfile.mkdtemp(prefix="sima-cli-msi-"))
    try:
        # The CLI creates the result file so the current user keeps owning it; the
        # elevated process only overwrites its contents.
        result_path = work / "result.json"
        result_path.write_bytes(b"")
        request = {
            "result": str(result_path),
            "continue_codes": sorted(continue_codes),
            "packages": [
                {
                    "owner": item["owner"],
                    "arguments": subprocess.list2cmdline(item["args"]),
                    "msi": str(item["msi"]) if "msi" in item else None,
                    "sha256": item.get("sha256"),
                }
                for item in items
            ],
        }
        encoded_request = base64.b64encode(json.dumps(request).encode("utf-8")).decode("ascii")
        script = _ELEVATED_MSI_SCRIPT.replace("@REQUEST@", encoded_request)
        elevated_arguments = (
            "-NoLogo -NoProfile -NonInteractive -EncodedCommand "
            + base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        )
        click.echo("Requesting Windows administrator approval once for the native packages.")
        launched = _run_powershell(
            _ELEVATION_WRAPPER_SCRIPT,
            {"SIMA_CLI_ELEVATED_ARGUMENTS": elevated_arguments},
            stderr=subprocess.PIPE,
        )
        return launched, _read_elevated_results(result_path, items)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_msi_operations(
    items: List[Dict],
    continue_codes: List[int],
    handle_result,
    nothing_done: str,
    rerun: str = _RERUN_SAME_COMMAND,
) -> None:
    """Run ordered msiexec steps, calling handle_result(item, exit_code, detail) for each.

    handle_result raises for every exit code outside continue_codes, so the steps stop
    at the first failure. A non-elevated session approves all steps with one prompt.
    """
    first_log = items[0]["log"]
    if _is_windows_administrator():
        for item in items:
            held = None
            if "msi" in item:
                # Held from the hash until msiexec returns, as the elevated script holds
                # it, so no other account can replace the verified MSI in between.
                held = _verify_sha256(
                    item["resource"], item["msi"], item["sha256"], WindowsNativeInstallError,
                    rerun, hold=True,
                )
            try:
                result = _run([_system_program("msiexec.exe"), *item["args"]])
            except OSError as error:
                raise WindowsNativeInstallError(
                    f"Could not start Windows Installer for {item['owner']}: {error}. "
                    "Check the current account's permissions and Windows application-control "
                    f"policy; do not disable security controls. Intended installer log: {item['log']}"
                ) from error
            finally:
                if held is not None:
                    held.close()
            handle_result(item, result.returncode, "")
        return
    if not _interactive_console():
        raise WindowsNativeInstallError(
            "Changing the machine-wide Windows packages requires administrator approval, "
            "but this session is not elevated and its input is not interactive. Rerun the same "
            "command in an interactive PowerShell window so Windows can request approval, or "
            "ask an administrator to run it in a PowerShell window opened with Run as "
            f"administrator. Intended installer log: {first_log}"
        )
    for item in items:
        if "msi" in item:
            _verify_sha256(
                item["resource"], item["msi"], item["sha256"], WindowsNativeInstallError, rerun
            )
    try:
        launched, results = _run_elevated_msi_batch(items, continue_codes)
    except OSError as error:
        raise WindowsNativeInstallError(
            f"Could not start Windows Installer for {items[0]['owner']}: {error}. "
            "Check the current account's permissions and Windows application-control "
            f"policy; do not disable security controls. Intended installer log: {first_log}"
        ) from error
    if launched.returncode == WINDOWS_ELEVATION_CANCELLED:
        raise WindowsNativeInstallError(
            f"Windows administrator approval was cancelled. {nothing_done} Rerun the same "
            f"command when approval is available. Intended installer log: {first_log}"
        )
    if launched.returncode == ELEVATED_INSTALLER_LAUNCH_FAILED:
        handle_result(items[0], ELEVATED_INSTALLER_LAUNCH_FAILED, launched.stderr or "")
    for index, item in enumerate(items):
        code, detail = (
            results[index] if index < len(results) else (ELEVATED_INSTALLER_EXIT_CODE_MISSING, "")
        )
        handle_result(item, code, detail)


def _msi_product_code(msi: Path) -> str:
    """Read an MSI's ProductCode so the receipt can drive msiexec /x later."""
    script = r"""
$ErrorActionPreference = 'Stop'
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.GetType().InvokeMember(
  'OpenDatabase', 'InvokeMethod', $null, $installer, @($env:SIMA_CLI_MSI_PATH, 0))
$view = $database.GetType().InvokeMember('OpenView', 'InvokeMethod', $null, $database,
  @("SELECT Value FROM Property WHERE Property = 'ProductCode'"))
$view.GetType().InvokeMember('Execute', 'InvokeMethod', $null, $view, $null) | Out-Null
$record = $view.GetType().InvokeMember('Fetch', 'InvokeMethod', $null, $view, $null)
[Console]::Out.Write(
  $record.GetType().InvokeMember('StringData', 'GetProperty', $null, $record, 1))
"""
    try:
        result = _run_powershell(script, {"SIMA_CLI_MSI_PATH": str(msi)}, capture_output=True)
    except OSError as error:
        result = subprocess.CompletedProcess([], 1, stdout="", stderr=str(error))
    code = (result.stdout or "").strip()
    if result.returncode != 0 or not _PRODUCT_CODE.fullmatch(code):
        detail = (result.stderr or "").strip() or f"unexpected value '{code}'"
        raise WindowsNativeInstallError(
            f"Could not read the ProductCode of '{msi.name}': {detail}. No MSI was started."
        )
    return code.upper()


# WiX MajorUpgrade sets this property when a newer version of the product is installed;
# the verbose log records it being added and in the final property dump.
_MSI_LOG_DOWNGRADE = re.compile(r"WIX_DOWNGRADE_DETECTED(?: property|\s*=\s*\{)")


def _msi_log_reports_downgrade(log: Path) -> bool:
    try:
        data = log.read_bytes()
    except OSError:
        return False
    if data[:2] == b"\xff\xfe" or b"\x00" in data[:200]:
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("latin-1")
    return _MSI_LOG_DOWNGRADE.search(text) is not None


def _msi_product_installed(product_code: str) -> bool:
    """Report whether Windows Installer has this exact product installed."""
    try:
        return ctypes.windll.msi.MsiQueryProductStateW(product_code) == INSTALLSTATE_DEFAULT
    except (AttributeError, OSError):
        return False


def _program_files_64() -> Optional[str]:
    # The 64-bit Program Files directory, where 64-bit MSIs and Python install. In a 32-bit
    # Python, ProgramFiles is C:\Program Files (x86); Windows sets ProgramW6432 only on
    # 64-bit systems.
    return os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")


def _path_from_spec(spec: Dict[str, str]) -> Optional[Path]:
    root = None
    if spec["environment"].lower() == "programfiles":
        root = _program_files_64()
    root = root or os.environ.get(spec["environment"])
    if not root:
        return None
    return Path(root).joinpath(*PureWindowsPath(spec["relative"]).parts)


def _check_windows_service(name: str) -> bool:
    try:
        result = _run_powershell(
            f"(Get-Service -Name '{name}' -ErrorAction Stop).Status.ToString()",
            capture_output=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "running"


def _windows_pnp_driver_version(device_name: str) -> Optional[str]:
    script = r"""
$matches = @(Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
  Where-Object { $_.DeviceName -eq $env:SIMA_CLI_PREREQ_DEVICE })
if ($matches.Count -lt 1) {
  [Console]::Error.Write(
    "No Windows PnP devices named '$env:SIMA_CLI_PREREQ_DEVICE' were found.")
  exit 3
}
$versions = @()
$missingVersionCount = 0
foreach ($match in $matches) {
  $version = [string]$match.DriverVersion
  if ([string]::IsNullOrWhiteSpace($version)) {
    $missingVersionCount += 1
  } else {
    $versions += $version.Trim()
  }
}
if ($missingVersionCount -gt 0) {
  [Console]::Error.Write(
    "$missingVersionCount of $($matches.Count) Windows PnP devices named " +
    "'$env:SIMA_CLI_PREREQ_DEVICE' do not report DriverVersion.")
  exit 3
}
$versions = @($versions | Sort-Object -Unique)
if ($versions.Count -ne 1) {
  [Console]::Error.Write(
    "Windows PnP devices named '$env:SIMA_CLI_PREREQ_DEVICE' report mixed " +
    "DriverVersion values: $($versions -join ', ').")
  exit 3
}
[Console]::Out.Write([string]$versions[0])
"""
    try:
        result = _run_powershell(
            script, {"SIMA_CLI_PREREQ_DEVICE": device_name}, capture_output=True
        )
    except OSError as error:
        raise _WindowsPnpDriverVersionError(
            f"Could not query Windows PnP drivers for '{device_name}': {error}."
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise _WindowsPnpDriverVersionError(
            detail or (
                f"Windows PnP driver query for '{device_name}' failed with "
                f"exit code {result.returncode}."
            )
        )
    version = result.stdout.strip()
    if not version:
        raise _WindowsPnpDriverVersionError(
            f"Windows returned an invalid PnP driver response for '{device_name}'."
        )
    return version


def _check_command_version(prerequisite: Dict) -> Tuple[bool, Optional[str]]:
    command = next(
        (path for path in (_path_from_spec(item) for item in prerequisite["candidates"])
         if path is not None and path.is_file()),
        None,
    )
    if command is None:
        return False, None
    try:
        result = _run(
            [str(command), *prerequisite["arguments"]],
            text=True,
            errors="replace",
            capture_output=True,
        )
    except OSError:
        return False, None
    if result.returncode != 0:
        return False, None
    match = re.search(prerequisite["version-pattern"], result.stdout)
    actual = match.group(1) if match else None
    return actual == prerequisite["version"], actual


def _check_prerequisites(metadata: Dict) -> List[Dict[str, str]]:
    prerequisites = metadata["installation"].get("prerequisites", [])
    failures = []
    for prerequisite in prerequisites:
        kind = prerequisite["type"]
        actual = None
        problem = None
        if kind == "executable":
            passed = shutil.which(prerequisite["value"]) is not None
        elif kind == "windows-service":
            passed = _check_windows_service(prerequisite["value"])
        elif kind == "windows-pnp-driver":
            try:
                actual = _windows_pnp_driver_version(prerequisite["value"])
                passed = actual == prerequisite["version"]
            except _WindowsPnpDriverVersionError as error:
                passed = False
                problem = str(error)
        else:  # command-version, the remaining type the validator accepts
            passed, actual = _check_command_version(prerequisite)
        if not passed:
            failures.append((prerequisite, actual, problem))

    if failures:
        details = "\n".join(
            f"- {item['name']}: "
            + (f"{problem} " if problem else "")
            + (f"found {actual or 'no version'}, expected {item['version']}. "
               if "version" in item and not problem else "")
            + item["guidance"]
            for item, actual, problem in failures
        )
        raise WindowsPrerequisiteError(
            "Windows host prerequisites are not ready; no MSI was started:\n" + details
        )
    if prerequisites:
        click.echo("Windows host prerequisites verified.")
    return prerequisites


def _normalize_python_arch(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"amd64", "x86_64"}:
        return "amd64"
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    return normalized


# Starts every python -c helper. -c puts the current directory, by default the download
# directory, first on sys.path, so a planted json.py would run in place of the standard
# library, as administrator in an elevated console. -I would also hide the user
# site-packages that pip installs into for an all-users Python, so only that entry goes.
# python -m pip and -m venv run with the interpreter's own folder as current directory.
_NO_CURRENT_DIRECTORY = "import sys\nif sys.path and sys.path[0] == '': del sys.path[0]\n"

# Reports whether the interpreter accepts the wheel filename passed as its argument,
# using pip's own tag logic (a free-threaded CPython rejects a cp314-cp314 wheel).
_PYTHON_COMPATIBILITY_SCRIPT = _NO_CURRENT_DIRECTORY + """
import json, platform, sys
import pip
try:
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import parse_wheel_filename
except ImportError:
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
supported = list(sys_tags())
print(json.dumps({
    'version': f'{sys.version_info.major}.{sys.version_info.minor}',
    'arch': platform.machine(),
    'tag': str(supported[0]),
    'wheel_supported': not parse_wheel_filename(sys.argv[1])[3].isdisjoint(supported),
}))
"""


def _inspect_python(command: List[str], wheel: str) -> Tuple[Optional[Dict[str, str]], str]:
    try:
        result = _run(
            [*command, "-c", _PYTHON_COMPATIBILITY_SCRIPT, wheel],
            text=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        return None, detail
    try:
        facts = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None, "invalid compatibility information"
    if not isinstance(facts, dict):
        return None, "invalid compatibility information"
    return facts, ""


def _wheel_name(python_package: Dict) -> str:
    return PurePosixPath(python_package["wheel"]).name


def _python_compatibility_error(python_package: Dict, facts: Dict[str, str]) -> Optional[str]:
    required_version = python_package.get("requires-python")
    if required_version and facts.get("version") != required_version:
        return (
            f"Python {facts.get('version') or 'unknown'} was found; this package requires "
            f"Python {required_version}."
        )
    required_arch = python_package.get("arch")
    actual_arch = _normalize_python_arch(str(facts.get("arch", "")))
    if required_arch and actual_arch != _normalize_python_arch(required_arch):
        return (
            f"Python architecture {actual_arch or 'unknown'} was found; this package requires "
            f"{required_arch}."
        )
    if not facts.get("wheel_supported"):
        return (
            f"Python {facts.get('version')} {actual_arch} ({facts.get('tag') or 'unknown tag'}) "
            f"cannot install {_wheel_name(python_package)}; a free-threaded or other-ABI "
            "build does not accept this wheel."
        )
    return None


def _python_install_guidance(python_package: Dict) -> str:
    required = " ".join(filter(None, ["Python", python_package.get("requires-python")]))
    if python_package.get("arch"):
        required += f" ({python_package['arch']})"
    return f"Install {required} for Windows from {PYTHON_WINDOWS_DOWNLOAD_URL}"


def _venv_base_python(environment: Path) -> Optional[Path]:
    """Return the interpreter a virtual environment was created from, per pyvenv.cfg."""
    try:
        lines = (environment / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip().lower() == "home" and value.strip():
            return Path(value.strip()) / "python.exe"
    return None


def _managed_environment_remedy(environment: Path) -> str:
    return (
        "Remove it so the next install recreates it with a compatible Python "
        f'(PowerShell: Remove-Item -Recurse -Force "{environment}"), or select another '
        "interpreter with --python-executable <path>. No MSI was started."
    )


def _preflight_python(
    metadata: Dict, python_executable: Optional[str], managed_environment: Optional[Path] = None
) -> None:
    python_package = metadata["installation"].get("python")
    if not python_package or not python_executable:
        return
    executable = Path(python_executable).expanduser()
    if not executable.is_file():
        raise WindowsPrerequisiteError(
            f"Selected Python executable does not exist; no MSI was started: '{executable}'."
        )
    if managed_environment is not None:
        base = _venv_base_python(managed_environment)
        if base is not None and not base.is_file():
            raise WindowsPrerequisiteError(
                f"Managed Python environment '{managed_environment}' was created from "
                f"'{base}', which no longer exists, so the environment cannot run. "
                + _managed_environment_remedy(managed_environment)
            )
    facts, detail = _inspect_python([str(executable)], _wheel_name(python_package))
    if facts is None and managed_environment is not None:
        raise WindowsPrerequisiteError(
            f"Managed Python environment '{managed_environment}' cannot run pip: {detail}. "
            + _managed_environment_remedy(managed_environment)
        )
    if facts is None:
        raise WindowsPrerequisiteError(
            f"Selected Python environment cannot run pip; no MSI was started: {detail}. "
            f"Restore pip with \"{executable}\" -m ensurepip --upgrade, or select a working "
            f"interpreter with --python-executable <path>. "
            f"{_python_install_guidance(python_package)}."
        )
    compatibility_error = _python_compatibility_error(python_package, facts)
    if compatibility_error and managed_environment is not None:
        raise WindowsPrerequisiteError(
            f"Managed Python environment '{managed_environment}' cannot be reused: "
            f"{compatibility_error} " + _managed_environment_remedy(managed_environment)
        )
    if compatibility_error:
        raise WindowsPrerequisiteError(
            f"{compatibility_error} {_python_install_guidance(python_package)}, or select a "
            "compatible interpreter with --python-executable <path>. No MSI was started."
        )
    actual_arch = _normalize_python_arch(str(facts.get("arch", "")))
    click.echo(f"Selected Python environment verified: {facts['version']} {actual_arch}.")


def _python_bootstrap_candidates(required_version: Optional[str]) -> List[List[str]]:
    candidates: List[List[str]] = []
    launcher = shutil.which("py.exe") or shutil.which("py")
    if launcher and required_version:
        candidates.append([launcher, f"-{required_version}"])

    executable_names = []
    if required_version:
        compact = required_version.replace(".", "")
        executable_names.extend([f"python{required_version}.exe", f"python{compact}.exe"])
    executable_names.append("python.exe")
    for name in executable_names:
        executable = shutil.which(name)
        if executable:
            candidates.append([executable])

    candidates.append([sys.executable])
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = _program_files_64()
    if required_version:
        compact = required_version.replace(".", "")
        if local_app_data:
            candidates.extend([
                [str(Path(local_app_data) / "Programs" / "Python" / f"Python{compact}" / "python.exe")],
                [str(Path(local_app_data) / "Python" / f"pythoncore-{required_version}-64" / "python.exe")],
            ])
        if program_files:
            # The python.org installer's all-users location, used without the py launcher.
            candidates.append([str(Path(program_files) / f"Python{compact}" / "python.exe")])

    unique = []
    seen = set()
    for command in candidates:
        key = tuple(part.lower() for part in command)
        if key in seen:
            continue
        seen.add(key)
        if len(command) == 1 and not Path(command[0]).is_file():
            continue
        unique.append(command)
    return unique


def _bootstrap_python(python_package: Dict, environment: Path) -> List[str]:
    required_version = python_package.get("requires-python")
    tried = []
    for command in _python_bootstrap_candidates(required_version):
        facts, detail = _inspect_python(command, _wheel_name(python_package))
        if facts is None:
            lines = [line.strip() for line in detail.splitlines() if line.strip()]
            reason = (lines[-1] if lines else "it did not run")[:200]
        else:
            reason = _python_compatibility_error(python_package, facts)
            if reason is None:
                return command
        tried.append(f"  - {subprocess.list2cmdline(command)}: {reason}")
    required_arch = python_package.get("arch", "the required architecture")
    found = (
        "Interpreters tried:\n" + "\n".join(tried) if tried else "No Python interpreter was found."
    )
    raise WindowsPrerequisiteError(
        f"A compatible Python {required_version or ''} {required_arch} installation is required "
        f"to create '{environment}'. {found}\n{_python_install_guidance(python_package)}, use "
        "--python-executable <path>, or use --native-only. No MSI was started."
    )


def _invalid_managed_environment(environment: Path) -> WindowsPrerequisiteError:
    return WindowsPrerequisiteError(
        f"Managed Python path exists but is not a valid environment: '{environment}'. "
        "Move or remove it, use --python-executable <path>, or use --native-only. "
        "No MSI was started."
    )


def _managed_python_executable(metadata: Dict) -> str:
    python_package = metadata["installation"]["python"]
    distribution = python_package["distribution"]
    environment = Path.home() / distribution
    executable = environment / "Scripts" / "python.exe"
    if executable.is_file():
        _preflight_python(metadata, str(executable), environment)
        click.echo(f"Using managed Python environment: {environment}")
        return str(executable)
    if environment.exists():
        raise _invalid_managed_environment(environment)

    selected = _bootstrap_python(python_package, environment)
    click.echo(f"Creating managed Python environment: {environment}")
    result = _run(
        [*selected, "-m", "venv", str(environment)],
        text=True,
        capture_output=True,
        cwd=str(Path(selected[0]).parent),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise WindowsPrerequisiteError(
            f"Could not create managed Python environment '{environment}': {detail}. "
            "Select an existing environment with --python-executable <path>, or use "
            "--native-only. No MSI was started."
        )
    _preflight_python(metadata, str(executable))
    return str(executable)


def _prepare_python_executable(
    metadata: Dict,
    python_executable: Optional[str],
    native_only: bool,
) -> Optional[str]:
    python_package = metadata["installation"].get("python")
    if not python_package:
        return None
    if native_only:
        click.echo(
            f"Native-only installation requested; {python_package['distribution']} will not be installed."
        )
        return None
    if python_executable:
        _preflight_python(metadata, python_executable)
        return python_executable
    return _managed_python_executable(metadata)


def _python_distribution_version(python_executable: str, distribution: str) -> Optional[str]:
    script = _NO_CURRENT_DIRECTORY + (
        "import importlib.metadata as m, json; "
        f"name={json.dumps(distribution)}; "
        "\ntry: print(m.version(name))"
        "\nexcept m.PackageNotFoundError: raise SystemExit(3)"
    )
    result = _run(
        [python_executable, "-c", script],
        text=True,
        capture_output=True,
    )
    if result.returncode == 3:
        return None
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise WindowsPythonInstallError(
            f"Could not inspect Python environment '{python_executable}': {detail}"
        )
    return result.stdout.strip()


def _python_distribution_archive_sha256(python_executable: str, distribution: str) -> Optional[str]:
    script = _NO_CURRENT_DIRECTORY + (
        "import importlib.metadata as m; "
        f"print(m.distribution({json.dumps(distribution)}).read_text('direct_url.json') or '')"
    )
    result = _run(
        [python_executable, "-c", script],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        archive = json.loads(result.stdout).get("archive_info", {})
        sha256 = archive.get("hashes", {}).get("sha256")
        if not sha256 and str(archive.get("hash", "")).startswith("sha256="):
            sha256 = archive["hash"][len("sha256="):]
    except (AttributeError, TypeError, ValueError):
        return None
    return str(sha256).strip().lower() if sha256 else None


def _verify_python_import(
    python_executable: str,
    distribution: str,
    module: str,
    expected_version: str,
) -> None:
    script = _NO_CURRENT_DIRECTORY + (
        "import importlib, importlib.metadata as m; "
        f"importlib.import_module({json.dumps(module)}); "
        f"assert m.version({json.dumps(distribution)}) == {json.dumps(expected_version)}"
    )
    result = _run(
        [python_executable, "-c", script],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise WindowsPythonInstallError(
            f"Python package verification failed in '{python_executable}': {detail}"
        )


def _pip_install_from_wheelhouse(
    metadata: Dict,
    install_dir: str,
    executable: Path,
    wheel: Path,
    pip_options: List[str],
    no_deps: bool,
    rerun: str,
) -> subprocess.CompletedProcess:
    """Install the wheel and its declared dependencies from local files only.

    pip uses no index, reads no configuration file and no PIP_ variable, builds nothing
    from source, and refuses any file whose SHA-256 is not the declared one, including
    other versions in the folder.
    """
    python_package = metadata["installation"]["python"]
    # A direct file reference, so pip still records the archive hash that a rerun reads.
    lines = [f"{wheel.as_uri()} --hash=sha256:{_declared_sha256(metadata, python_package['wheel'])}"]
    folders: List[str] = []
    if not no_deps:
        for entry in python_package["wheelhouse"]:
            try:
                path = _declared_resource(metadata, install_dir, entry["wheel"], rerun)
            except click.ClickException as error:
                raise WindowsPythonInstallError(
                    f"{error.message} The native installation was kept."
                ) from error
            _verify_declared_checksum(
                metadata, entry["wheel"], path, WindowsPythonInstallError, rerun
            )
            lines.append(
                f"{entry['distribution']}=={entry['version']} "
                f"--hash=sha256:{_declared_sha256(metadata, entry['wheel'])}"
            )
            if str(path.parent) not in folders:
                folders.append(str(path.parent))
    work = tempfile.mkdtemp(prefix="sima-cli-pip-")
    try:
        requirements = Path(work) / "requirements.txt"
        requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return _run(
            [
                str(executable), "-m", "pip", "install", *pip_options,
                "--isolated", "--no-index", "--only-binary", ":all:", "--require-hashes",
                *[part for folder in folders for part in ("--find-links", folder)],
                "-r", str(requirements),
            ],
            text=True,
            cwd=str(executable.parent),
            # --isolated skips only the user file; this pip value skips every file.
            env={**os.environ, "PIP_CONFIG_FILE": os.devnull},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _install_python_wheel(
    metadata: Dict,
    install_dir: str,
    python_executable: Optional[str],
    rerun: str = _RERUN_SAME_COMMAND,
) -> str:
    python_package = metadata["installation"].get("python")
    if not python_package:
        return "not-present"
    if not python_executable:
        return "not-requested"

    executable = Path(python_executable).expanduser()
    if not executable.is_file():
        raise WindowsPythonInstallError(f"Selected Python executable does not exist: '{executable}'.")

    try:
        wheel = _declared_resource(metadata, install_dir, python_package["wheel"], rerun)
    except click.ClickException as error:
        raise WindowsPythonInstallError(
            f"{error.message} The native installation was kept."
        ) from error
    distribution = python_package["distribution"]
    version = python_package["version"]
    module = python_package.get("import", distribution)
    installed = _python_distribution_version(str(executable), distribution)
    force_reinstall = False
    no_deps = False
    if installed == version:
        recorded_sha256 = _python_distribution_archive_sha256(str(executable), distribution)
        if recorded_sha256 != _declared_sha256(metadata, python_package["wheel"]):
            click.echo(
                f"Python package {distribution} {version} was not installed from this "
                "release's wheel; reinstalling it."
            )
            force_reinstall = no_deps = True
        else:
            try:
                _verify_python_import(str(executable), distribution, module, version)
            except WindowsPythonInstallError:
                click.echo(
                    f"Python package {distribution} {version} is present but failed "
                    "verification; reinstalling it."
                )
                force_reinstall = True
            else:
                click.echo(
                    f"Python package {distribution} {version} is already installed and verified."
                )
                return "already-installed"

    if installed and not force_reinstall:
        click.echo(f"Replacing {distribution} {installed} with requested version {version}.")
    pip_options = ["--upgrade"]
    if force_reinstall:
        pip_options.append("--force-reinstall")
    if no_deps:
        pip_options.append("--no-deps")
    _verify_declared_checksum(
        metadata, python_package["wheel"], wheel, WindowsPythonInstallError, rerun
    )
    if python_package.get("wheelhouse"):
        result = _pip_install_from_wheelhouse(
            metadata, install_dir, executable, wheel, pip_options, no_deps, rerun
        )
    else:
        result = _run(
            [str(executable), "-m", "pip", "install", *pip_options, str(wheel)],
            text=True,
            cwd=str(executable.parent),
        )
    if result.returncode != 0:
        raise WindowsPythonInstallError(
            f"Native MSI installation succeeded, but pip failed with exit code {result.returncode}. "
            "The native installation was kept; to retry the Python step with the same "
            f"environment, {rerun}"
        )
    installed = _python_distribution_version(str(executable), distribution)
    if installed != version:
        raise WindowsPythonInstallError(
            f"pip completed, but {distribution} {installed or '(missing)'} is installed; expected {version}."
        )
    _verify_python_import(str(executable), distribution, module, version)
    click.echo(f"Python package {distribution} {version} installed and verified.")
    return "installed"


def _native_packages(installation: Dict) -> List[Dict[str, str]]:
    packages = installation.get("native-packages")
    if packages is not None:
        return packages
    return [{"owner": "native", "msi": installation["msi"], "version": "unspecified"}]


def _safe_log_label(owner: str) -> str:
    label = "".join(character if character.isalnum() else "-" for character in owner.lower())
    return label.strip("-") or "package"


def _install_native_packages(
    metadata: Dict,
    install_dir: str,
    installed: List[Dict[str, str]],
    record_receipt=None,
    python_executable: Optional[str] = None,
    rerun: str = _RERUN_SAME_COMMAND,
) -> List[Dict[str, str]]:
    root = Path(install_dir).resolve()
    packages = _native_packages(metadata["installation"])
    items = []
    for index, package in enumerate(packages, start=1):
        msi = _declared_resource(metadata, install_dir, package["msi"], rerun)
        log_path = root / f"sima-cli-msi-install-{index:02d}-{_safe_log_label(package['owner'])}.log"
        items.append({
            "index": index,
            "owner": package["owner"],
            "version": package["version"],
            "resource": package["msi"],
            "msi": msi,
            "sha256": _declared_sha256(metadata, package["msi"]),
            "log": log_path,
            "args": ["/i", str(msi), "/passive", "/norestart", "/L*v", str(log_path)],
        })
    for item in items:
        item["product_code"] = _msi_product_code(item["msi"])
        # Passing an installed package to /i again only enters maintenance mode and
        # rewrites nothing, so a rerun of the same release repairs it instead: files that
        # are missing or older, plus machine and user registry entries and shortcuts.
        # Unlike /fa, files that are present and current are not rewritten, so a file
        # in use only requires a restart when it actually needs replacing.
        item["repair"] = _msi_product_installed(item["product_code"])
        if item["repair"]:
            item["args"][0] = "/fomus"
    if record_receipt is not None:
        # Recorded before any MSI starts, so a later uninstall can find every package.
        record_receipt({
            "native_packages": [
                {key: item[key] for key in ("owner", "version", "product_code")}
                for item in items
            ],
            "python_executable": python_executable,
        })
    for item in items:
        action = "Repairing installed" if item["repair"] else "Installing"
        click.echo(
            f"{action} {item['owner']} native package {item['index']}/{len(items)}: "
            f"{item['msi'].name}"
        )

    def handle_result(item: Dict, returncode: int, detail: str) -> None:
        owner = item["owner"]
        log_path = item["log"]
        if returncode in {MSI_RESTART_INITIATED, MSI_RESTART_REQUIRED}:
            completed_owners = ", ".join([entry["owner"] for entry in installed] + [owner])
            remaining = [entry["owner"] for entry in items[item["index"]:]]
            remaining_text = (
                "native packages " + ", ".join(remaining) + " and any"
                if remaining
                else "any"
            )
            raise WindowsPackageRestartRequired(
                f"Windows Installer completed {owner} with restart code {returncode}. "
                f"Native packages installed in this run: {completed_owners}. "
                f"Not yet run: {remaining_text} verification, customer skill, and Python steps. "
                f"Installer log: {log_path}. To continue the coordinated installation, restart "
                f"Windows, then {rerun}"
            )
        if returncode == ELEVATED_INSTALLER_LAUNCH_FAILED:
            detail = detail.strip() or "Windows did not report a reason"
            raise WindowsNativeInstallError(
                f"Could not start Windows Installer for {owner} with administrator approval: "
                f"{detail}. No later native package or Python package was installed. Check that "
                "Windows PowerShell and msiexec.exe are available, the current account's "
                "permissions, and Windows application-control policy; do not disable security "
                f"controls. Intended installer log: {log_path}. Then {rerun}"
            )
        if returncode == ELEVATED_INSTALLER_EXIT_CODE_MISSING:
            raise WindowsNativeInstallError(
                f"Windows Installer for {owner} ended without reporting an exit code, so its "
                f"result is unknown. The release remains incomplete. Installer log: {log_path}. "
                f"Check the installer log, then {rerun}"
            )
        if returncode == ELEVATED_INSTALLER_HASH_MISMATCH:
            raise WindowsNativeInstallError(
                f"Windows package resource '{item['resource']}' no longer matched its declared "
                f"SHA-256 when the elevated installer checked it (expected {item['sha256']}, "
                f"{detail}); it was not started. {_fresh_copy(rerun)}"
            )
        if returncode != 0:
            installed_owners = ", ".join(entry["owner"] for entry in installed) or "none"
            if returncode == MSI_INSTALL_FAILURE and _msi_log_reports_downgrade(log_path):
                raise WindowsNativeInstallError(
                    f"Windows Installer refused {owner} {item['version']} because a newer "
                    "version of it is already installed; Windows Installer does not downgrade "
                    "a package in place. Packages completed earlier in this run: "
                    f"{installed_owners}. Installer log: {log_path}. To install this release, "
                    "find the installed version with sima-cli packages list, remove it with "
                    f"sima-cli packages remove {metadata.get('name')} --version <installed version> "
                    "(or from Settings > Apps > Installed apps if sima-cli did not install it), "
                    f"then {rerun}"
                )
            policy_detail = (
                " Windows Installer rejected this package under system policy; "
                "use an approved package or ask the Windows administrator to review the policy."
                if returncode == MSI_INSTALL_REJECTED_BY_POLICY
                else ""
            )
            raise WindowsNativeInstallError(
                f"Windows Installer failed for {owner} with exit code {returncode}. "
                f"{policy_detail}"
                f"Packages completed earlier in this run: {installed_owners}. "
                f"The release remains incomplete. Installer log: {log_path}. "
                f"Correct the failure, then {rerun}"
            )
        installed.append(
            {
                "owner": owner,
                "version": item["version"],
                "msi": str(item["msi"]),
                "log": str(log_path),
                "product_code": item["product_code"],
            }
        )
        click.echo(
            f"{owner} native package "
            + ("repaired successfully." if item["repair"] else "installed successfully.")
        )

    _run_msi_operations(
        items, [0], handle_result, "No native package or Python package was installed.", rerun
    )
    return installed


def _verify_native_installation(
    metadata: Dict, rerun: str = _RERUN_SAME_COMMAND
) -> Dict[str, str]:
    verification = metadata["installation"].get("verification")
    if not verification:
        return {"status": "not-requested"}
    command = _path_from_spec(verification["command"])
    if command is None or not command.is_file():
        raise WindowsNativeInstallError(
            "Native MSI installation completed, but its declared verification command is missing. "
            "The native installation was kept."
        )
    invocation = [str(command)]
    if command.suffix.lower() in {".bat", ".cmd"}:
        invocation = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(command)]
    try:
        result = _run(invocation, text=True, errors="replace", capture_output=True)
    except OSError as error:
        raise WindowsNativeInstallError(
            "Native MSI installation completed, but its verification command could not be "
            f"started: {error}. The native installation was kept."
        ) from error
    if result.stdout.strip():
        click.echo(result.stdout.strip())
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise WindowsNativeInstallError(
            "Native MSI installation completed, but installed-runtime verification failed: "
            f"{detail}. The native installation was kept; correct the prerequisite, then {rerun}"
        )
    click.echo("Installed native runtime verified.")
    return {"status": "passed", "command": str(command)}


def _run_setup_step(metadata: Dict, rerun: str = _RERUN_SAME_COMMAND) -> Dict[str, str]:
    """Run the package's declared setup command last, in this console, as on Linux.

    Like install_pciehost.sh, a failure keeps the installed packages and fails the
    command. Unlike Linux, a session without interactive input skips the step,
    because a first card enrollment asks for a password and Windows OpenSSH has
    no sshpass equivalent.
    """
    setup = metadata["installation"].get("setup")
    if not setup:
        return {"status": "not-requested"}
    command = _path_from_spec(setup["command"])
    if command is None or not command.is_file():
        raise WindowsSetupError(
            "Native packages are installed, but their setup command is missing: "
            f"'{command or setup['command']['relative']}'. The installation was kept; "
            f"to repair it, {rerun}"
        )
    run_it = f'  & "{command}"'
    skip_reason = None
    if os.environ.get(SETUP_SKIP_ENVIRONMENT, "").strip().lower() in {"1", "true", "yes", "on"}:
        skip_reason = f"{SETUP_SKIP_ENVIRONMENT} is set"
    elif not _interactive_console():
        skip_reason = "this session's input is not interactive and the step may ask for a password"
    if skip_reason:
        click.echo(
            f"Setup step skipped because {skip_reason}. To run it, enter this in an "
            f"interactive PowerShell window:\n{run_it}"
        )
        return {"status": "skipped", "command": str(command)}

    click.echo(f"Running the setup step:\n{run_it}")
    invocation = [str(command)]
    if command.suffix.lower() in {".bat", ".cmd"}:
        invocation = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(command)]
    try:
        result = _run(invocation)
    except OSError as error:
        raise WindowsSetupError(
            f"Native packages are installed, but the setup step could not be started: {error}. "
            f"The installation was kept. To run the step, enter this in PowerShell:\n{run_it}"
        ) from error
    except KeyboardInterrupt as error:
        # Cancelling the card password prompt still leaves every package installed.
        raise WindowsSetupError(
            "Native packages are installed, but the setup step was cancelled. The installation "
            f"was kept. To run the step, enter this in PowerShell:\n{run_it}"
        ) from error
    if result.returncode != 0:
        raise WindowsSetupError(
            f"Native packages are installed, but the setup step failed with exit code "
            f"{result.returncode}. The installation was kept. Correct the problem reported "
            f"above, then run the step again from PowerShell:\n{run_it}"
        )
    click.echo("Setup step completed.")
    return {"status": "passed", "command": str(command)}


def _declared_skill_id(spec: Dict[str, str]) -> str:
    skill_id = spec.get("id", "")
    if not skill_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", skill_id):
        raise WindowsPrerequisiteError(
            "The Windows package declares an invalid customer skill ID: "
            f"'{skill_id}'. No native package was installed."
        )
    return skill_id


def _preflight_customer_skills(metadata: Dict) -> Optional[SkillManager]:
    skill_specs = metadata["installation"].get("skills", [])
    if not skill_specs:
        return None
    try:
        manager = SkillManager()
    except (OSError, SkillError) as error:
        raise WindowsPrerequisiteError(
            "The customer skill registry could not be opened before native package "
            f"installation: {error}. No native package was installed."
        ) from error

    for spec in skill_specs:
        source = _path_from_spec(spec)
        if source is None:
            raise WindowsPrerequisiteError(
                "The Windows package customer skill location cannot be resolved because "
                f"the {spec['environment']} environment variable is not set. "
                "No native package was installed."
            )
        skill_id = _declared_skill_id(spec)
        try:
            manager.check_install_ownership(skill_id, str(source))
        except (OSError, SkillError) as error:
            raise WindowsPrerequisiteError(
                "Customer skill ownership validation failed before native package "
                f"installation: {error} No native package was installed."
            ) from error
    return manager


def _install_customer_skills(metadata: Dict, manager: Optional[SkillManager]) -> List[str]:
    skill_specs = metadata["installation"].get("skills", [])
    if not skill_specs:
        return []
    if manager is None:
        raise WindowsNativeInstallError(
            "Native MSI installation completed without a customer skill ownership check. "
            "The native installation was kept."
        )
    installed = []
    for spec in skill_specs:
        source = _path_from_spec(spec)
        if source is None or not source.is_dir():
            raise WindowsNativeInstallError(
                "Native MSI installation completed, but an installed customer skill is missing. "
                "The native installation was kept."
            )
        declared_id = _declared_skill_id(spec)
        try:
            discovered_ids = manager.install(str(source), dry_run=True)
            if discovered_ids != [declared_id]:
                discovered = ", ".join(discovered_ids) or "none"
                raise SkillError(
                    f"declared skill '{declared_id}', but discovered: {discovered}"
                )
            installed.extend(manager.install(str(source), replace_same_source=True))
        except (OSError, SkillError) as error:
            raise WindowsNativeInstallError(
                "Native MSI installation completed, but customer skill installation failed: "
                f"{error}. The native installation was kept."
            ) from error
    if installed:
        click.echo("Installed customer skills: " + ", ".join(installed))
    return installed


def require_windows_host() -> None:
    if not _is_windows():
        raise click.ClickException("The windows-msi installation type can only run on Windows.")


def preflight_windows_package(
    metadata: Dict,
    python_executable: Optional[str] = None,
    native_only: bool = False,
) -> None:
    """Report missing host prerequisites and Python before any resource is downloaded."""
    if not _is_windows():
        return
    problems = []
    try:
        _check_prerequisites(metadata)
    except WindowsPrerequisiteError as error:
        problems.append(error.message)
    python_package = metadata["installation"].get("python")
    if python_package and not native_only:
        environment = Path.home() / python_package["distribution"]
        managed = environment / "Scripts" / "python.exe"
        try:
            if python_executable:
                _preflight_python(metadata, python_executable)
            elif managed.is_file():
                _preflight_python(metadata, str(managed), environment)
            elif environment.exists():
                raise _invalid_managed_environment(environment)
            else:
                _bootstrap_python(python_package, environment)
        except WindowsPrerequisiteError as error:
            problems.append(error.message)
    if problems:
        raise WindowsPrerequisiteError("\n".join(problems))


def install_windows_package(
    metadata: Dict,
    install_dir: str,
    python_executable: Optional[str] = None,
    native_only: bool = False,
    record_receipt=None,
    check_prerequisites: bool = True,
    retry_command: Optional[str] = None,
    local_bundle: bool = False,
) -> Dict[str, Any]:
    """Install the ordered native package cohort and its Python wheel.

    record_receipt, when given, receives the package product codes and selected Python
    before any MSI starts. check_prerequisites=False skips host prerequisites that
    preflight_windows_package already verified. retry_command, when given, is the
    command pinned to this release that recovery messages print. local_bundle says that
    install_dir is an offline bundle folder, which recovery messages ask to copy again.
    """
    require_windows_host()
    if native_only and python_executable:
        raise click.ClickException("--native-only cannot be combined with --python-executable.")

    installed: List[Dict[str, str]] = []
    rerun = _rerun(retry_command, local_bundle)
    try:
        if check_prerequisites:
            _check_prerequisites(metadata)
        skill_manager = _preflight_customer_skills(metadata)
        selected_python = _prepare_python_executable(metadata, python_executable, native_only)
        _install_native_packages(
            metadata, install_dir, installed, record_receipt, selected_python, rerun
        )
        click.echo("Coordinated native installation completed successfully.")
        verification = _verify_native_installation(metadata, rerun)
        skills = _install_customer_skills(metadata, skill_manager)
        python_status = _install_python_wheel(metadata, install_dir, selected_python, rerun)
        setup = _run_setup_step(metadata, rerun)
    except BaseException as error:
        # The caller records whether any native package completed before the failure
        # or interruption.
        error.native_packages_installed = [item["owner"] for item in installed]
        raise
    return {
        "native": "installed",
        "native_packages": installed,
        "verification": verification,
        "skills": skills,
        "python": python_status,
        "python_executable": selected_python,
        "setup": setup,
    }


def uninstall_windows_package(receipt: Dict, shared: Optional[Dict[str, str]] = None) -> bool:
    """Remove a receipt's native packages in reverse install order.

    shared maps product codes that other receipts still record to one such receipt;
    those packages are kept. Returns True when Windows must restart to finish the removal.
    """
    if not _is_windows():
        raise click.ClickException("Windows MSI packages can only be uninstalled on Windows.")
    packages = receipt.get("native_packages")
    if not packages:
        click.echo(
            "This receipt records no Windows product codes, so no native package was "
            "uninstalled. Remove Scheduler or Core through Windows Installed apps if present."
        )
        return False

    log_root = Path(tempfile.gettempdir())
    items = []
    kept = []
    for index, package in enumerate(reversed(packages), start=1):
        owner = str(package.get("owner", ""))
        product_code = str(package.get("product_code", ""))
        if not _PRODUCT_CODE.fullmatch(product_code):
            raise click.ClickException(
                f"The receipt records an invalid product code for {owner or 'a package'}: "
                f"'{product_code}'. No native package was removed."
            )
        if product_code in (shared or {}):
            kept.append(f"{owner} (still recorded by {shared[product_code]})")
            continue
        log_path = log_root / f"sima-cli-msi-uninstall-{index:02d}-{_safe_log_label(owner)}.log"
        items.append({
            "owner": owner,
            "log": log_path,
            "args": ["/x", product_code, "/passive", "/norestart", "/L*v", str(log_path)],
        })
    if kept:
        click.echo("Keeping native packages another receipt uses: " + ", ".join(kept))
    if items:
        click.echo("Removing native packages: " + ", ".join(item["owner"] for item in items))

    removed: List[str] = []
    restart_required = False

    def handle_result(item: Dict, returncode: int, detail: str) -> None:
        nonlocal restart_required
        owner = item["owner"]
        if returncode == MSI_UNKNOWN_PRODUCT:
            click.echo(f"{owner} native package is not installed; nothing to remove.")
            return
        if returncode in {0, MSI_RESTART_REQUIRED}:
            removed.append(owner)
            restart_required = restart_required or returncode == MSI_RESTART_REQUIRED
            click.echo(f"{owner} native package removed.")
            return
        if returncode == ELEVATED_INSTALLER_LAUNCH_FAILED:
            reason = (
                f"Could not start Windows Installer to remove {owner} with administrator "
                f"approval: {detail.strip() or 'Windows did not report a reason'}."
            )
        elif returncode == ELEVATED_INSTALLER_EXIT_CODE_MISSING:
            reason = (
                f"Windows Installer did not report a result for removing {owner}, so it is "
                "unknown."
            )
        else:
            reason = f"Windows Installer failed to remove {owner} with exit code {returncode}."
        later = [entry["owner"] for entry in items[items.index(item) + 1:]]
        raise WindowsNativeInstallError(
            f"{reason} Removed in this run: {', '.join(removed) or 'none'}. "
            f"Not attempted: {', '.join(later) or 'none'}. Correct the failure and rerun the "
            f"same command. Installer log: {item['log']}"
        )

    if items:
        _run_msi_operations(
            items,
            [0, MSI_UNKNOWN_PRODUCT, MSI_RESTART_REQUIRED],
            handle_result,
            "No native package was removed.",
        )

    python_executable = receipt.get("python_executable")
    python_package = receipt.get("metadata", {}).get("installation", {}).get("python")
    if python_executable and python_package:
        distribution = python_package["distribution"]
        click.echo(
            f"The Python package {distribution} remains in '{python_executable}'; sima-cli "
            f"does not delete Python environments. To remove it, run: "
            f'& "{python_executable}" -m pip uninstall -y {distribution}'
        )
    for skill in receipt.get("metadata", {}).get("installation", {}).get("skills", []):
        click.echo(
            f"The customer skill {skill.get('id')} remains; to remove it, run: "
            f"sima-cli playbooks remove {skill.get('id')} --yes"
        )
    return restart_required
