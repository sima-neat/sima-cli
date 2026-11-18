"""Install a declarative Windows MSI and optional Python wheel."""

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

import click

from sima_cli.playbooks.manager import SkillError, SkillManager


MSI_RESTART_INITIATED = 1641
MSI_RESTART_REQUIRED = 3010
MSI_INSTALL_REJECTED_BY_POLICY = 1625
WINDOWS_ELEVATION_CANCELLED = 1223
ELEVATED_INSTALLER_EXIT_CODE_MISSING = 9001
ELEVATED_INSTALLER_LAUNCH_FAILED = 9002
PYTHON_WINDOWS_DOWNLOAD_URL = "https://www.python.org/downloads/windows/"


class WindowsPackageRestartRequired(click.ClickException):
    """The native package installed, but Windows must restart before pip runs."""

    exit_code = MSI_RESTART_REQUIRED


class WindowsPythonInstallError(click.ClickException):
    """The MSI succeeded, but the requested Python installation failed."""


class WindowsNativeInstallError(click.ClickException):
    """One MSI failed after an earlier package may have installed."""


class WindowsPrerequisiteError(click.ClickException):
    """The host is not ready for the requested Windows package transaction."""


class _WindowsPnpDriverVersionError(RuntimeError):
    """A matching Windows PnP device set has no unambiguous driver version."""


def _is_windows() -> bool:
    return os.name == "nt"


def _declared_resource(metadata: Dict, install_dir: str, resource: str) -> Path:
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
        raise click.ClickException(f"Downloaded Windows package resource is missing: '{path}'.")
    return path


def _declared_sha256(metadata: Dict, resource: str) -> str:
    return str(metadata.get("resources-checksum", {}).get(resource, "")).strip().lower()


def _verify_declared_checksum(metadata: Dict, resource: str, path: Path, error_type) -> None:
    from sima_cli.install.metadata_installer import _compute_sha256

    expected = _declared_sha256(metadata, resource)
    try:
        actual = _compute_sha256(path)
    except OSError as error:
        raise error_type(
            f"Windows package resource '{resource}' could not be read for SHA-256 "
            f"verification at '{path}': {error}; it was not started. "
            "Close any program using the file, then rerun the same command."
        ) from error
    if actual != expected:
        raise error_type(
            f"Windows package resource '{resource}' no longer matches its declared SHA-256 "
            f"(expected {expected}, found {actual}); it was not started. "
            "Rerun the same command to download a verified copy."
        )


def _run(command, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, **kwargs)


def _is_windows_administrator() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _run_native_installer(command: List[str], log_path: Path) -> subprocess.CompletedProcess:
    if _is_windows_administrator():
        return _run(command)
    if not sys.stdin.isatty():
        raise WindowsNativeInstallError(
            "Installing the machine-wide Windows packages requires administrator approval, "
            "but this standard-user session is not interactive. Rerun the same command in an "
            "interactive PowerShell window so Windows can request approval, or ask an "
            f"administrator to install the native packages. Intended installer log: {log_path}"
        )

    # Start-Process drops the Win32 error code, so only Process.Start can tell a
    # declined approval prompt (1223) apart from any other launch failure.
    script = r"""
$ErrorActionPreference = 'Stop'
try {
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo `
    'msiexec.exe', $env:SIMA_CLI_MSI_ARGUMENTS
  $startInfo.Verb = 'runas'
  $startInfo.UseShellExecute = $true
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
    environment = os.environ.copy()
    environment["SIMA_CLI_MSI_ARGUMENTS"] = subprocess.list2cmdline(command[1:])
    click.echo("Requesting Windows administrator approval for the native package.")
    return _run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        env=environment,
        stderr=subprocess.PIPE,
        text=True,
    )


def _path_from_spec(spec: Dict[str, str]) -> Optional[Path]:
    root = os.environ.get(spec["environment"])
    if not root:
        return None
    return Path(root).joinpath(*PureWindowsPath(spec["relative"]).parts)


def _check_windows_service(name: str) -> bool:
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        f"(Get-Service -Name '{name}' -ErrorAction Stop).Status.ToString()",
    ]
    try:
        result = _run(command, text=True, capture_output=True)
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "running"


def _windows_product_version(name: str) -> Optional[str]:
    script = r"""
$paths = @(
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$matches = @(Get-ItemProperty $paths -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -eq $env:SIMA_CLI_PREREQ_PRODUCT })
if ($matches.Count -ne 1 -or -not $matches[0].DisplayVersion) { exit 3 }
[Console]::Out.Write([string]$matches[0].DisplayVersion)
"""
    environment = os.environ.copy()
    environment["SIMA_CLI_PREREQ_PRODUCT"] = name
    try:
        result = _run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            text=True,
            capture_output=True,
            env=environment,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


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
    environment = os.environ.copy()
    environment["SIMA_CLI_PREREQ_DEVICE"] = device_name
    try:
        result = _run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            text=True,
            capture_output=True,
            env=environment,
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
        elif kind == "windows-product":
            actual = _windows_product_version(prerequisite["value"])
            passed = actual == prerequisite["version"]
        elif kind == "windows-pnp-driver":
            try:
                actual = _windows_pnp_driver_version(prerequisite["value"])
                passed = actual == prerequisite["version"]
            except _WindowsPnpDriverVersionError as error:
                passed = False
                problem = str(error)
        elif kind == "command-version":
            passed, actual = _check_command_version(prerequisite)
        else:
            passed = any(
                path is not None and path.is_file()
                for path in (_path_from_spec(candidate) for candidate in prerequisite["candidates"])
            )
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


def _python_compatibility_script() -> str:
    return (
        "import json, platform, sys; import pip; "
        "print(json.dumps({'version': f'{sys.version_info.major}.{sys.version_info.minor}', "
        "'arch': platform.machine()}))"
    )


def _inspect_python(command: List[str]) -> Tuple[Optional[Dict[str, str]], str]:
    try:
        result = _run(
            [*command, "-c", _python_compatibility_script()],
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
    return None


def _python_install_guidance(python_package: Dict) -> str:
    required = " ".join(filter(None, ["Python", python_package.get("requires-python")]))
    if python_package.get("arch"):
        required += f" ({python_package['arch']})"
    return f"Install {required} for Windows from {PYTHON_WINDOWS_DOWNLOAD_URL}"


def _preflight_python(metadata: Dict, python_executable: Optional[str]) -> None:
    python_package = metadata["installation"].get("python")
    if not python_package or not python_executable:
        return
    executable = Path(python_executable).expanduser()
    if not executable.is_file():
        raise WindowsPrerequisiteError(
            f"Selected Python executable does not exist; no MSI was started: '{executable}'."
        )
    facts, detail = _inspect_python([str(executable)])
    if facts is None:
        raise WindowsPrerequisiteError(
            f"Selected Python environment cannot run pip; no MSI was started: {detail}. "
            f"Restore pip with \"{executable}\" -m ensurepip --upgrade, or select a working "
            f"interpreter with --python-executable <path>. "
            f"{_python_install_guidance(python_package)}."
        )
    compatibility_error = _python_compatibility_error(python_package, facts)
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
    if local_app_data and required_version:
        compact = required_version.replace(".", "")
        candidates.extend([
            [str(Path(local_app_data) / "Programs" / "Python" / f"Python{compact}" / "python.exe")],
            [str(Path(local_app_data) / "Python" / f"pythoncore-{required_version}-64" / "python.exe")],
        ])

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
    for command in _python_bootstrap_candidates(required_version):
        facts, _ = _inspect_python(command)
        if facts is not None and _python_compatibility_error(python_package, facts) is None:
            return command
    required_arch = python_package.get("arch", "the required architecture")
    raise WindowsPrerequisiteError(
        f"A compatible Python {required_version or ''} {required_arch} installation is required "
        f"to create '{environment}'. {_python_install_guidance(python_package)}, use "
        "--python-executable <path>, or use --native-only. No MSI was started."
    )


def _managed_python_executable(metadata: Dict) -> str:
    python_package = metadata["installation"]["python"]
    distribution = python_package["distribution"]
    environment = Path.home() / distribution
    executable = environment / "Scripts" / "python.exe"
    if executable.is_file():
        _preflight_python(metadata, str(executable))
        click.echo(f"Using managed Python environment: {environment}")
        return str(executable)
    if environment.exists():
        raise WindowsPrerequisiteError(
            f"Managed Python path exists but is not a valid environment: '{environment}'. "
            "Move or remove it, use --python-executable <path>, or use --native-only. "
            "No MSI was started."
        )

    selected = _bootstrap_python(python_package, environment)
    click.echo(f"Creating managed Python environment: {environment}")
    result = _run(
        [*selected, "-m", "venv", str(environment)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise WindowsPrerequisiteError(
            f"Could not create managed Python environment '{environment}': {detail}. "
            "No MSI was started."
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
        click.echo("Native-only installation requested; pyneatpcie will not be installed.")
        return None
    if python_executable:
        _preflight_python(metadata, python_executable)
        return python_executable
    return _managed_python_executable(metadata)


def _python_distribution_version(python_executable: str, distribution: str) -> Optional[str]:
    script = (
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
    script = (
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
    script = (
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


def _install_python_wheel(
    metadata: Dict,
    install_dir: str,
    python_executable: Optional[str],
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
        wheel = _declared_resource(metadata, install_dir, python_package["wheel"])
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
    _verify_declared_checksum(metadata, python_package["wheel"], wheel, WindowsPythonInstallError)
    result = _run(
        [str(executable), "-m", "pip", "install", *pip_options, str(wheel)],
        text=True,
    )
    if result.returncode != 0:
        raise WindowsPythonInstallError(
            f"Native MSI installation succeeded, but pip failed with exit code {result.returncode}. "
            "The native installation was kept; retry the Python step with the same environment."
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
    metadata: Dict, install_dir: str, installed: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    root = Path(install_dir).resolve()
    packages = _native_packages(metadata["installation"])
    for index, package in enumerate(packages, start=1):
        owner = package["owner"]
        msi = _declared_resource(metadata, install_dir, package["msi"])
        log_path = root / f"sima-cli-msi-install-{index:02d}-{_safe_log_label(owner)}.log"
        command = [
            "msiexec.exe",
            "/i",
            str(msi),
            "/passive",
            "/norestart",
            "/L*v",
            str(log_path),
        ]
        click.echo(
            f"Installing {owner} native package {index}/{len(packages)}: {msi.name}"
        )
        _verify_declared_checksum(metadata, package["msi"], msi, WindowsNativeInstallError)
        try:
            result = _run_native_installer(command, log_path)
        except OSError as error:
            raise WindowsNativeInstallError(
                f"Could not start Windows Installer for {owner}: {error}. "
                "Check the current account's permissions and Windows application-control policy; "
                f"do not disable security controls. Intended installer log: {log_path}"
            ) from error
        if result.returncode in {MSI_RESTART_INITIATED, MSI_RESTART_REQUIRED}:
            completed_owners = ", ".join([item["owner"] for item in installed] + [owner])
            remaining = [item["owner"] for item in packages[index:]]
            remaining_text = (
                "native packages " + ", ".join(remaining) + " and any"
                if remaining
                else "any"
            )
            raise WindowsPackageRestartRequired(
                f"Windows Installer completed {owner} with restart code {result.returncode}. "
                f"Native packages installed in this run: {completed_owners}. "
                f"Not yet run: {remaining_text} verification, customer skill, and Python steps. "
                "Restart Windows, then rerun the same command to continue the coordinated "
                f"installation. Installer log: {log_path}"
            )
        if result.returncode == WINDOWS_ELEVATION_CANCELLED:
            raise WindowsNativeInstallError(
                f"Windows administrator approval was cancelled for {owner}. "
                "No later native package or Python package was installed. Rerun the same "
                f"command when approval is available. Intended installer log: {log_path}"
            )
        if result.returncode == ELEVATED_INSTALLER_LAUNCH_FAILED:
            detail = (result.stderr or "").strip() or "Windows did not report a reason"
            raise WindowsNativeInstallError(
                f"Could not start Windows Installer for {owner} with administrator approval: "
                f"{detail}. No later native package or Python package was installed. Check that "
                "msiexec.exe is available, the current account's permissions, and Windows "
                "application-control policy; do not disable security controls. Then rerun the "
                f"same command. Intended installer log: {log_path}"
            )
        if result.returncode == ELEVATED_INSTALLER_EXIT_CODE_MISSING:
            raise WindowsNativeInstallError(
                f"Windows Installer for {owner} ended without reporting an exit code, so its "
                "result is unknown. The release remains incomplete; check the installer log, "
                f"then rerun the same command. Installer log: {log_path}"
            )
        if result.returncode != 0:
            installed_owners = ", ".join(item["owner"] for item in installed) or "none"
            policy_detail = (
                " Windows Installer rejected this package under system policy; "
                "use an approved package or ask the Windows administrator to review the policy."
                if result.returncode == MSI_INSTALL_REJECTED_BY_POLICY
                else ""
            )
            raise WindowsNativeInstallError(
                f"Windows Installer failed for {owner} with exit code {result.returncode}. "
                f"{policy_detail}"
                f"Packages completed earlier in this run: {installed_owners}. "
                "The release remains incomplete; correct the failure and rerun the same command. "
                f"Installer log: {log_path}"
            )
        installed.append(
            {
                "owner": owner,
                "version": package["version"],
                "msi": str(msi),
                "log": str(log_path),
            }
        )
        click.echo(f"{owner} native package installed successfully.")
    return installed


def _verify_native_installation(metadata: Dict) -> Dict[str, str]:
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
        result = _run(invocation, text=True, capture_output=True)
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
            f"{detail}. The native installation was kept; correct the prerequisite and rerun "
            "the same command."
        )
    click.echo("Installed native runtime verified.")
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
            if python_executable or managed.is_file():
                _preflight_python(metadata, python_executable or str(managed))
            elif not environment.exists():
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
) -> Dict[str, Any]:
    """Install the ordered native package cohort and its Python wheel."""
    if not _is_windows():
        raise click.ClickException("The windows-msi installation type can only run on Windows.")
    if native_only and python_executable:
        raise click.ClickException("--native-only cannot be combined with --python-executable.")

    installed: List[Dict[str, str]] = []
    try:
        _check_prerequisites(metadata)
        skill_manager = _preflight_customer_skills(metadata)
        selected_python = _prepare_python_executable(metadata, python_executable, native_only)
        _install_native_packages(metadata, install_dir, installed)
        click.echo("Coordinated native installation completed successfully.")
        verification = _verify_native_installation(metadata)
        skills = _install_customer_skills(metadata, skill_manager)
        python_status = _install_python_wheel(metadata, install_dir, selected_python)
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
    }
