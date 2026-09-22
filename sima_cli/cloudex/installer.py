"""Install the native CloudEx forwarder from Kerrigan's Vulcan artifacts."""

import hashlib
import platform
import re
import subprocess
import tempfile
from pathlib import Path

from sima_cli.install.metadata_validator import MetadataValidationError, validate_metadata
from sima_cli.vulcan.artifacts import (
    ArtifactClient,
    VulcanArtifactError,
    join_url,
    resolve_install_metadata_url,
)

from .client import CloudExError, FORWARDER_NAME, authorize_admin, find_forwarder


DEFAULT_BRANCH = "develop"
PACKAGE_TARGET = "kerrigan/p2p-forwarder"
ARTIFACT_ENVIRONMENT = "production"
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def _validate_branch(branch):
    value = branch.strip()
    if (
        not _BRANCH_RE.fullmatch(value)
        or ".." in value
        or "//" in value
        or "@{" in value
        or value.endswith(("/", "."))
    ):
        raise CloudExError("branch is not a valid Git branch name")
    return value


def _platform_variant():
    system = platform.system()
    machine = platform.machine().lower()
    arches = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }
    arch = arches.get(machine)
    if system == "Darwin" and arch == "arm64":
        return "darwin-arm64"
    if system == "Linux" and arch:
        return "linux-" + arch
    raise CloudExError(
        "CloudEx forwarder packages are unavailable for {} {}".format(system, machine)
    )


def _checked_metadata(client, metadata_url, variant):
    try:
        metadata = client.read_json(metadata_url)
        if not isinstance(metadata, dict):
            raise CloudExError("CloudEx forwarder metadata is not a JSON object")
        validate_metadata(metadata)
    except (VulcanArtifactError, MetadataValidationError, TypeError, AttributeError) as exc:
        raise CloudExError("CloudEx forwarder metadata is invalid: {}".format(exc)) from exc

    root = "{}/p2p-forwarder".format(variant)
    binary_resource = root + "/bin/" + FORWARDER_NAME
    script_resource = root + "/install_kerrigan_p2p_forwarder.sh"
    expected = [binary_resource, script_resource]
    checksums = metadata.get("resources-checksum")
    script = metadata.get("installation", {}).get("script")
    if (
        metadata.get("name") != "gh:sima-neat/kerrigan"
        or metadata.get("resources") != expected
        or not isinstance(checksums, dict)
        or set(checksums) != set(expected)
        or script != "./" + script_resource
    ):
        raise CloudExError("CloudEx forwarder metadata does not match the expected package contract")
    return metadata, expected, script_resource


def _download_resource(client, base_url, resource, expected_sha256, destination):
    try:
        payload = client.read_bytes(join_url(base_url, resource))
    except VulcanArtifactError as exc:
        raise CloudExError("CloudEx forwarder download failed: {}".format(exc)) from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256.lower():
        raise CloudExError("CloudEx forwarder download failed checksum validation")
    path = destination / resource
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    except OSError as exc:
        raise CloudExError("CloudEx forwarder download could not be saved") from exc
    return path


def install_forwarder(branch=DEFAULT_BRANCH, progress=None, client=None):
    """Install or update the forwarder and return artifact/install details."""
    branch = _validate_branch(branch)
    variant = _platform_variant()
    client = client or ArtifactClient()
    if progress:
        progress.update("Resolving Kerrigan {} forwarder".format(branch))
    try:
        result = resolve_install_metadata_url(
            environment=ARTIFACT_ENVIRONMENT,
            target="{}@{}".format(PACKAGE_TARGET, branch),
            package_type=variant,
            client=client,
        )
    except (KeyError, VulcanArtifactError) as exc:
        raise CloudExError("CloudEx forwarder artifact could not be resolved: {}".format(exc)) from exc

    metadata, resources, script_resource = _checked_metadata(
        client, result.metadata_url, variant
    )
    base_url = result.metadata_url.rsplit("/", 1)[0]
    with tempfile.TemporaryDirectory(prefix="sima-cloudex-forwarder-") as temporary:
        root = Path(temporary)
        downloaded = {}
        if progress:
            progress.update("Downloading verified CloudEx forwarder")
        for resource in resources:
            downloaded[resource] = _download_resource(
                client,
                base_url,
                resource,
                metadata["resources-checksum"][resource],
                root,
            )
        script = downloaded[script_resource]
        script.chmod(0o700)
        if progress:
            progress.update("Authorizing CloudEx forwarder installation")
        authorize_admin()
        if progress:
            progress.update("Installing CloudEx forwarder")
        try:
            completed = subprocess.run(
                ["sudo", "-n", "--", str(script)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise CloudExError("CloudEx forwarder installer could not be started") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "installer failed").strip()
            raise CloudExError("CloudEx forwarder installation failed: {}".format(detail[:1024]))

    installed = find_forwarder()
    return {
        "path": installed,
        "branch": branch,
        "version": metadata["version"],
        "artifact": result.resolved_spec,
    }
