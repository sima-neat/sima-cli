"""Install a declarative Windows MSI and optional Python wheel."""

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional

import click


MSI_RESTART_INITIATED = 1641
MSI_RESTART_REQUIRED = 3010


class WindowsPackageRestartRequired(click.ClickException):
    """The native package installed, but Windows must restart before pip runs."""


class WindowsPythonInstallError(click.ClickException):
    """The MSI succeeded, but the requested Python installation failed."""


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
        click.echo(
            "Python support was not requested; native installation is complete. "
            "Re-run with --python-executable <path> to install the wheel into an application environment."
        )
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


def install_windows_package(
    metadata: Dict,
    install_dir: str,
    python_executable: Optional[str] = None,
) -> Dict[str, str]:
    """Install the metadata's MSI, then its optional wheel in the selected Python."""
    if not _is_windows():
        raise click.ClickException("The windows-msi installation type can only run on Windows.")

    installation = metadata["installation"]
    msi = _declared_resource(metadata, install_dir, installation["msi"])
    log_path = Path(install_dir).resolve() / "sima-cli-msi-install.log"
    command = [
        "msiexec.exe",
        "/i",
        str(msi),
        "/passive",
        "/norestart",
        "/L*v",
        str(log_path),
    ]
    click.echo(f"Installing native package with Windows Installer: {msi.name}")
    result = _run(command)
    if result.returncode in {MSI_RESTART_INITIATED, MSI_RESTART_REQUIRED}:
        raise WindowsPackageRestartRequired(
            f"Windows Installer completed with restart code {result.returncode}. "
            "Restart Windows, then rerun the command to continue with Python installation. "
            f"Installer log: {log_path}"
        )
    if result.returncode != 0:
        raise click.ClickException(
            f"Windows Installer failed with exit code {result.returncode}. Installer log: {log_path}"
        )

    click.echo("Native MSI installation completed successfully.")
    python_status = _install_python_wheel(metadata, install_dir, python_executable)
    return {"native": "installed", "python": python_status, "msi_log": str(log_path)}
