import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sima_cli.cloudex import installer
from sima_cli.cloudex.client import CloudExError


def _package():
    variant = "linux-amd64"
    root = variant + "/p2p-forwarder"
    binary = root + "/bin/kerrigan-p2p-forwarder"
    script = root + "/install_kerrigan_p2p_forwarder.sh"
    payloads = {binary: b"native-binary", script: b"#!/bin/sh\n"}
    metadata = {
        "name": "gh:sima-neat/kerrigan",
        "version": "0.1.0+abc1234",
        "release": "",
        "platforms": [{"type": "host", "os": ["linux"], "arch": ["amd64"]}],
        "resources": [binary, script],
        "resources-checksum": {
            name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()
        },
        "installation": {"script": "./" + script},
    }
    return metadata, payloads


class PackageClient:
    def __init__(self, metadata, payloads):
        self.metadata = metadata
        self.payloads = payloads

    def read_json(self, _url):
        return self.metadata

    def read_bytes(self, url):
        for name, payload in self.payloads.items():
            if url.endswith("/" + name):
                return payload
        raise AssertionError("unexpected URL " + url)


def test_install_forwarder_resolves_branch_verifies_and_runs_as_root(monkeypatch, tmp_path):
    metadata, payloads = _package()
    client = PackageClient(metadata, payloads)
    resolved = SimpleNamespace(
        metadata_url="https://artifacts.example/kerrigan/develop/abc/p2p-forwarder/metadata-linux-amd64.json",
        resolved_spec="abc1234",
    )
    resolve = Mock(return_value=resolved)
    monkeypatch.setattr(installer, "resolve_install_metadata_url", resolve)
    monkeypatch.setattr(installer, "_platform_variant", Mock(return_value="linux-amd64"))
    authorize = Mock()
    monkeypatch.setattr(installer, "authorize_admin", authorize)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)
    installed = tmp_path / "kerrigan-p2p-forwarder"
    installed.write_text("installed")
    monkeypatch.setattr(installer, "find_forwarder", Mock(return_value=installed))

    result = installer.install_forwarder("feature/cloudex", client=client)

    assert result == {
        "path": installed,
        "branch": "feature/cloudex",
        "version": "0.1.0+abc1234",
        "artifact": "abc1234",
    }
    assert resolve.call_args.kwargs["target"] == "kerrigan/p2p-forwarder@feature/cloudex"
    assert resolve.call_args.kwargs["package_type"] == "linux-amd64"
    authorize.assert_called_once_with()
    command = run.call_args.args[0]
    assert command[:3] == ["sudo", "-n", "--"]
    assert command[3].endswith("/linux-amd64/p2p-forwarder/install_kerrigan_p2p_forwarder.sh")
    assert run.call_args.kwargs["stdin"] is installer.subprocess.DEVNULL


@pytest.mark.parametrize("branch", ["", "../main", "feature//name", "main:deadbee", "name@{1}"])
def test_install_forwarder_rejects_invalid_branch_before_network(branch):
    with pytest.raises(CloudExError, match="valid Git branch"):
        installer.install_forwarder(branch, client=Mock())


def test_install_forwarder_rejects_unexpected_root_install_script(monkeypatch):
    metadata, payloads = _package()
    metadata["installation"]["script"] = "./install-anything.sh"
    client = PackageClient(metadata, payloads)
    monkeypatch.setattr(installer, "_platform_variant", Mock(return_value="linux-amd64"))
    monkeypatch.setattr(
        installer,
        "resolve_install_metadata_url",
        Mock(return_value=SimpleNamespace(metadata_url="https://artifacts.example/metadata.json")),
    )

    with pytest.raises(CloudExError, match="expected package contract"):
        installer.install_forwarder(client=client)


def test_platform_variant_supports_linux_and_rejects_intel_mac(monkeypatch):
    monkeypatch.setattr(installer.platform, "system", Mock(return_value="Linux"))
    monkeypatch.setattr(installer.platform, "machine", Mock(return_value="aarch64"))
    assert installer._platform_variant() == "linux-arm64"

    monkeypatch.setattr(installer.platform, "system", Mock(return_value="Darwin"))
    monkeypatch.setattr(installer.platform, "machine", Mock(return_value="x86_64"))
    with pytest.raises(CloudExError, match="unavailable"):
        installer._platform_variant()


@pytest.mark.parametrize("current", [b"native-binary", b"old-binary"])
def test_update_check_skips_current_binary_and_replaces_old(monkeypatch, tmp_path, current):
    metadata, payloads = _package()
    package = PackageClient(metadata, payloads)
    package.read_bytes = Mock(wraps=package.read_bytes)
    monkeypatch.setattr(installer, "_platform_variant", lambda: "linux-amd64")
    monkeypatch.setattr(installer, "resolve_install_metadata_url", Mock(return_value=SimpleNamespace(
        metadata_url="https://artifacts.example/metadata.json", resolved_spec="abc1234")))
    binary = tmp_path / "forwarder"
    binary.write_bytes(current)
    monkeypatch.setattr(installer, "find_forwarder", lambda: binary)
    authorize = Mock()
    monkeypatch.setattr(installer, "authorize_admin", authorize)

    def install(*args, **kwargs):
        binary.write_bytes(b"native-binary")
        return SimpleNamespace(returncode=0)

    run = Mock(side_effect=install)
    monkeypatch.setattr(installer.subprocess, "run", run)
    assert installer.install_forwarder(client=package, current_path=binary)["path"] == binary
    assert binary.read_bytes() == b"native-binary"
    assert run.call_count == (current != b"native-binary")
    assert authorize.call_count == run.call_count
    assert package.read_bytes.call_count == 2 * run.call_count


def test_ensure_preserves_explicit_override_without_network(monkeypatch, tmp_path):
    binary = tmp_path / "custom"
    monkeypatch.setenv("SIMA_CLOUDEX_FORWARDER", str(binary))
    monkeypatch.setattr(installer, "find_forwarder", Mock(return_value=binary))
    install = Mock()
    monkeypatch.setattr(installer, "install_forwarder", install)
    assert installer.ensure_forwarder() == binary
    install.assert_not_called()


def test_ensure_invalid_override_does_not_install(monkeypatch):
    monkeypatch.setenv("SIMA_CLOUDEX_FORWARDER", "/missing")
    monkeypatch.setattr(installer, "find_forwarder", Mock(side_effect=CloudExError("missing")))
    install = Mock()
    monkeypatch.setattr(installer, "install_forwarder", install)
    with pytest.raises(CloudExError, match="missing"):
        installer.ensure_forwarder()
    install.assert_not_called()


def test_update_check_failure_does_not_use_stale_binary(monkeypatch, tmp_path):
    monkeypatch.delenv("SIMA_CLOUDEX_FORWARDER", raising=False)
    monkeypatch.setattr(installer, "find_forwarder", lambda: tmp_path / "old")
    monkeypatch.setattr(installer, "_platform_variant", lambda: "linux-amd64")
    monkeypatch.setattr(installer, "resolve_install_metadata_url", Mock(
        side_effect=installer.VulcanArtifactError("unavailable")))
    run = Mock()
    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(CloudExError, match="could not be resolved"):
        installer.ensure_forwarder()
    run.assert_not_called()


def test_update_rejects_old_binary_still_selected_after_install(monkeypatch, tmp_path):
    metadata, payloads = _package()
    monkeypatch.setattr(installer, "_platform_variant", lambda: "linux-amd64")
    monkeypatch.setattr(installer, "resolve_install_metadata_url", Mock(return_value=SimpleNamespace(
        metadata_url="https://artifacts.example/metadata.json", resolved_spec="abc1234")))
    binary = tmp_path / "shadowing-forwarder"
    binary.write_bytes(b"old")
    monkeypatch.setattr(installer, "find_forwarder", lambda: binary)
    monkeypatch.setattr(installer, "authorize_admin", Mock())
    monkeypatch.setattr(installer.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0)))
    with pytest.raises(CloudExError, match="check PATH"):
        installer.install_forwarder(client=PackageClient(metadata, payloads), current_path=binary)
