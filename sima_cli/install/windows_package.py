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


class WindowsPackageRestartRequired(click.ClickException):
    """The native package installed, but Windows must restart before pip runs."""


class WindowsPythonInstallError(click.ClickException):
    """The MSI succeeded, but the requested Python installation failed."""


class WindowsNativeInstallError(click.ClickException):
    """One MSI failed after an earlier package may have installed."""


class WindowsPrerequisiteError(click.ClickException):
    """The host is not ready for the requested Windows package transaction."""


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

    script = r"""
$ErrorActionPreference = 'Stop'
try {
  $process = Start-Process -FilePath 'msiexec.exe' `
    -ArgumentList $env:SIMA_CLI_MSI_ARGUMENTS -Verb RunAs -Wait -PassThru
  exit $process.ExitCode
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1223
}
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
if ($matches.Count -ne 1 -or -not $matches[0].DriverVersion) { exit 3 }
[Console]::Out.Write([string]$matches[0].DriverVersion)
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
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


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
        if kind == "executable":
            passed = shutil.which(prerequisite["value"]) is not None
        elif kind == "windows-service":
            passed = _check_windows_service(prerequisite["value"])
        elif kind == "windows-product":
            actual = _windows_product_version(prerequisite["value"])
            passed = actual == prerequisite["version"]
        elif kind == "windows-pnp-driver":
            actual = _windows_pnp_driver_version(prerequisite["value"])
            passed = actual == prerequisite["version"]
        elif kind == "command-version":
            passed, actual = _check_command_version(prerequisite)
        else:
            passed = any(
                path is not None and path.is_file()
                for path in (_path_from_spec(candidate) for candidate in prerequisite["candidates"])
            )
        if not passed:
            failures.append((prerequisite, actual))

    if failures:
        details = "\n".join(
            f"- {item['name']}: "
            + (f"found {actual or 'no version'}, expected {item['version']}. "
               if "version" in item else "")
            + item["guidance"]
            for item, actual in failures
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
            f"Selected Python environment cannot run pip; no MSI was started: {detail}"
        )
    compatibility_error = _python_compatibility_error(python_package, facts)
    if compatibility_error:
        raise WindowsPrerequisiteError(f"{compatibility_error} No MSI was started.")
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

    required_version = python_package.get("requires-python")
    selected = None
    for command in _python_bootstrap_candidates(required_version):
        facts, _ = _inspect_python(command)
        if facts is not None and _python_compatibility_error(python_package, facts) is None:
            selected = command
            break
    if selected is None:
        required_arch = python_package.get("arch", "the required architecture")
        raise WindowsPrerequisiteError(
            f"A compatible Python {required_version or ''} {required_arch} installation is required "
            f"to create '{environment}'. Install Python, use --python-executable <path>, or use "
            "--native-only. No MSI was started."
        )

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

    wheel = _declared_resource(metadata, install_dir, python_package["wheel"])
    distribution = python_package["distribution"]
    version = python_package["version"]
    module = python_package.get("import", distribution)
    installed = _python_distribution_version(str(executable), distribution)
    force_reinstall = False
    if installed == version:
        try:
            _verify_python_import(str(executable), distribution, module, version)
        except WindowsPythonInstallError:
            click.echo(
                f"Python package {distribution} {version} is present but failed verification; "
                "reinstalling it."
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


def _install_native_packages(metadata: Dict, install_dir: str) -> List[Dict[str, str]]:
    installed = []
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
        try:
            result = _run_native_installer(command, log_path)
        except OSError as error:
            raise WindowsNativeInstallError(
                f"Could not start Windows Installer for {owner}: {error}. "
                "Check the current account's permissions and Windows application-control policy; "
                f"do not disable security controls. Intended installer log: {log_path}"
            ) from error
        if result.returncode in {MSI_RESTART_INITIATED, MSI_RESTART_REQUIRED}:
            raise WindowsPackageRestartRequired(
                f"Windows Installer completed {owner} with restart code {result.returncode}. "
                "Restart Windows, then rerun the command to continue the coordinated installation. "
                f"Installer log: {log_path}"
            )
        if result.returncode == WINDOWS_ELEVATION_CANCELLED:
            raise WindowsNativeInstallError(
                f"Windows administrator approval was cancelled for {owner}. "
                "No later native package or Python package was installed. Rerun the same "
                f"command when approval is available. Intended installer log: {log_path}"
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


def _install_customer_skills(metadata: Dict) -> List[str]:
    skill_specs = metadata["installation"].get("skills", [])
    if not skill_specs:
        return []
    try:
        manager = SkillManager()
    except (OSError, SkillError) as error:
        raise WindowsNativeInstallError(
            "Native MSI installation completed, but the customer skill registry could not be "
            f"opened: {error}. The native installation was kept."
        ) from error
    installed = []
    for spec in skill_specs:
        source = _path_from_spec(spec)
        if source is None or not source.is_dir():
            raise WindowsNativeInstallError(
                "Native MSI installation completed, but an installed customer skill is missing. "
                "The native installation was kept."
            )
        try:
            installed.extend(manager.install(str(source), force=True))
        except (OSError, SkillError) as error:
            raise WindowsNativeInstallError(
                "Native MSI installation completed, but customer skill installation failed: "
                f"{error}. The native installation was kept."
            ) from error
    if installed:
        click.echo("Installed customer skills: " + ", ".join(installed))
    return installed


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

    _check_prerequisites(metadata)
    selected_python = _prepare_python_executable(metadata, python_executable, native_only)
    installed = _install_native_packages(metadata, install_dir)
    click.echo("Coordinated native installation completed successfully.")
    verification = _verify_native_installation(metadata)
    skills = _install_customer_skills(metadata)
    python_status = _install_python_wheel(metadata, install_dir, selected_python)
    return {
        "native": "installed",
        "native_packages": installed,
        "verification": verification,
        "skills": skills,
        "python": python_status,
        "python_executable": selected_python,
    }
