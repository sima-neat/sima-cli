"""Local Model Compiler bundle discovery and container staging."""
from contextlib import contextmanager
import json
from pathlib import Path, PurePosixPath
import stat
import uuid
import zipfile

import click
from InquirerPy import inquirer

from sima_cli.sdk.docker_staging import docker_cp_staging_dir


def normalize_arch(machine):
    if not isinstance(machine, str):
        return ""
    return {"aarch64": "arm64", "arm64": "arm64",
            "x86_64": "amd64", "amd64": "amd64"}.get(machine.strip().lower(), "")


def validate_archive(archive, arch, expected_version):
    """Check the official flat bundle layout before extracting any files."""
    names = set()
    for entry in archive.infolist():
        path = PurePosixPath(entry.filename)
        mode = entry.external_attr >> 16
        if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                or ":" in entry.filename or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))):
            raise ValueError(f"Unsafe archive entry: {entry.filename}")
        if str(path) in names:
            raise ValueError(f"Duplicate archive entry: {entry.filename}")
        names.add(str(path))
    files = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
    for required in ("install_modelsdk_wheels.sh", "source.json", "manifest.txt"):
        if required not in files:
            raise ValueError(f"Missing {required} at the ZIP root")
    source = json.loads(archive.read("source.json"))
    if not isinstance(source, dict):
        raise ValueError("source.json must contain an object")
    version = source.get("sdk_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("source.json must declare a non-empty sdk_version")
    if version != expected_version:
        raise ValueError(f"Bundle SDK version {version} does not match {expected_version}")
    target = source.get("target_arch")
    if target and normalize_arch(target) != arch:
        raise ValueError(f"Bundle architecture {target} does not match {arch}")
    entries = archive.read("manifest.txt").decode("utf-8").splitlines()
    entries = [entry for entry in entries if entry]
    if not entries:
        raise ValueError("manifest.txt contains no packages")
    wrong_suffix = "_x86_64.whl" if arch == "arm64" else "_aarch64.whl"
    for entry in entries:
        if ("/" in entry or "\\" in entry or entry.startswith(".")
                or not entry.endswith((".whl", ".tar.gz", ".zip"))):
            raise ValueError(f"Invalid manifest entry: {entry}")
        variants = {entry, entry.replace("+", "%2B"),
                    entry.replace("%2B", "+").replace("%2b", "+")}
        if not variants.intersection(files):
            raise ValueError(f"Missing package from manifest: {entry}")
        if entry.endswith(wrong_suffix):
            raise ValueError(f"Package architecture does not match {arch}: {entry}")

    # Binary archives are intentionally omitted from manifest.txt by the bundle builder.
    machine = "aarch64" if arch == "arm64" else "x86_64"
    arch_source = source.get(machine, {})
    if not isinstance(arch_source, dict):
        raise ValueError(f"Invalid {machine} package configuration")
    binaries = arch_source.get("binary-packages", source.get("binary-packages", []))
    if not isinstance(binaries, list):
        raise ValueError("binary-packages must be a list")
    for package in binaries:
        if not isinstance(package, dict):
            raise ValueError("Invalid binary package configuration")
        name = str(package.get("name", "")).strip("/").rsplit("/", 1)[-1]
        version = str(package.get("version", ""))
        extension = str(package.get("extension") or package.get("archive-type") or "zip").lstrip(".")
        if not name or not version:
            raise ValueError("Binary package requires name and version")
        if name == "mla-toolchain" and extension == "zip":
            suffix = "aarch64" if arch == "arm64" else "x86"
            version = f"{version}-{suffix}-ubuntu"
        filename = f"{name}-{version}.{extension}"
        if filename not in files:
            raise ValueError(f"Missing binary package: {filename}")


def discover_archive(arch, expected_version):
    """Only consider the architecture-specific ZIP in the invocation directory."""
    path = Path.cwd() / f"model-compiler-{arch}.zip"
    if not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            validate_archive(archive, arch, expected_version)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        click.echo(f"Ignoring invalid local Model Compiler package {path}: {exc}")
        return None
    return path.resolve()


def select_source(local_archive, noninteractive=False, yes=False):
    if local_archive:
        click.echo(f"Local Model Compiler offline installation package found: {local_archive}")
    if noninteractive or yes:
        return "local" if local_archive else ("online" if yes else "skip")
    choices = ["local", "online", "skip"] if local_archive else ["online", "skip"]
    labels = {"local": "Install from local source", "online": "Install from online source", "skip": "Skip"}
    try:
        return inquirer.select(
            message="Select Model Compiler installation:",
            choices=[{"name": labels[choice], "value": choice} for choice in choices],
            default="skip",
            instruction="(↑/↓ to move, Enter to select)",
        ).execute()
    except (KeyboardInterrupt, EOFError):
        raise click.Abort() from None



@contextmanager
def stage_archive(path, arch, expected_version, container, owner, run_command):
    """Copy a validated extracted bundle without relying on host bind mounts."""
    destination = f"/tmp/sima-model-compiler-{uuid.uuid4().hex}"
    with docker_cp_staging_dir() as staging:
        try:
            with zipfile.ZipFile(path) as archive:
                validate_archive(archive, arch, expected_version)
                archive.extractall(staging)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            raise click.ClickException(f"Cannot extract local Model Compiler package {path}: {exc}") from exc
        try:
            run_command(["docker", "exec", "-u", "root", container, "mkdir", "-p", destination])
            run_command(["docker", "cp", staging + "/.", f"{container}:{destination}"])
            run_command(["docker", "exec", "-u", "root", container, "chown", "-R", owner, destination])
            yield destination
        finally:
            run_command(["docker", "exec", "-u", "root", container, "rm", "-rf", destination], fatal=False)
