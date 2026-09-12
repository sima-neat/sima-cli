"""Model Compiler local ZIP selection and installation regression coverage."""
import json
from pathlib import Path
import stat
from unittest.mock import Mock, patch
import zipfile

import click
from click.testing import CliRunner
import pytest

from sima_cli.sdk.model_compiler import (
    discover_archive, normalize_arch, select_source, stage_archive,
)
from sima_cli.sdk.utils import ensure_model_sdk_extension_installed


def bundle(path, arch="arm64", version="2.1.3", overrides=None):
    wheel = f"compiler-1.0-py3-none-manylinux2014_{'aarch64' if arch == 'arm64' else 'x86_64'}.whl"
    files = {
        "source.json": json.dumps({"sdk_version": version}),
        "manifest.txt": wheel + "\n",
        "install_modelsdk_wheels.sh": "#!/bin/bash\nexit 0\n",
        wheel: b"fixture wheel",
    }
    files.update(overrides or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            if data is not None:
                archive.writestr(name, data)
    return path


@pytest.mark.parametrize("machine,arch", [("aarch64", "arm64"), ("arm64", "arm64"),
                                         ("x86_64", "amd64"), ("AMD64\n", "amd64"), ("ppc64", "")])
def test_normalize_arch(machine, arch):
    assert normalize_arch(machine) == arch


@pytest.mark.parametrize("arch", ["arm64", "amd64"])
def test_discovery_only_current_zip(tmp_path, monkeypatch, arch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / f"model-compiler-{arch}").mkdir()
    (tmp_path / "workspace").mkdir()
    bundle(tmp_path / "workspace" / f"model-compiler-{arch}.zip", arch)
    assert discover_archive(arch, "2.1.3") is None
    wrong = "amd64" if arch == "arm64" else "arm64"
    bundle(tmp_path / f"model-compiler-{wrong}.zip", wrong)
    assert discover_archive(arch, "2.1.3") is None
    path = bundle(tmp_path / f"model-compiler-{arch}.zip", arch)
    assert discover_archive(arch, "2.1.3") == path
    child = tmp_path / "child with spaces"
    child.mkdir()
    monkeypatch.chdir(child)
    assert discover_archive(arch, "2.1.3") is None


@pytest.mark.parametrize("overrides", [
    {"source.json": "not json"},
    {"source.json": "[]"},
    {"source.json": '{"sdk_version":"2.0.0"}'},
    {"source.json": '{"target_arch":"x86_64"}'},
    {"source.json": '{"binary-packages":[{"name":"runtime","version":"1"}]}'},
    {"manifest.txt": "missing.whl\n"},
    {"manifest.txt": ""},
    {"manifest.txt": "../payload.whl\n"},
    {"install_modelsdk_wheels.sh": None},
    {"../outside": "unsafe"},
    {"/absolute": "unsafe"},
    {"a\\b": "unsafe"},
    {"C:/outside": "unsafe"},
])
def test_invalid_zip_is_reported(tmp_path, monkeypatch, capsys, overrides):
    monkeypatch.chdir(tmp_path)
    bundle(tmp_path / "model-compiler-arm64.zip", overrides=overrides)
    assert discover_archive("arm64", "2.1.3") is None
    assert "Ignoring invalid local Model Compiler package" in capsys.readouterr().out


def test_wrong_wheel_architecture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle(tmp_path / "model-compiler-arm64.zip", arch="amd64")
    assert discover_archive("arm64", "2.1.3") is None


def test_corrupt_zip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model-compiler-arm64.zip").write_bytes(b"not a ZIP")
    assert discover_archive("arm64", "2.1.3") is None


def test_symlink_entry_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = bundle(tmp_path / "model-compiler-arm64.zip")
    with zipfile.ZipFile(path, "a") as archive:
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/etc/passwd")
    assert discover_archive("arm64", "2.1.3") is None


def test_encoded_manifest_and_binary_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = bundle(tmp_path / "model-compiler-arm64.zip", overrides={
        "manifest.txt": "package-1%2Bdev-py3-none-any.whl\n",
        "package-1+dev-py3-none-any.whl": b"wheel",
        "source.json": json.dumps({"aarch64": {"binary-packages": [
            {"name": "mla/mla-toolchain", "version": "1", "extension": ".zip"}]}}),
        "mla-toolchain-1-aarch64-ubuntu.zip": b"binary",
    })
    assert discover_archive("arm64", "2.1.3") == path


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("noninteractive,yes", [(True, False), (True, True), (False, True)])
def test_unattended_matrix(local, noninteractive, yes):
    with patch("sima_cli.sdk.model_compiler.click.prompt") as prompt:
        result = select_source(Path("/tmp/local.zip") if local else None, noninteractive, yes)
    assert result == ("local" if local else ("online" if yes else "skip"))
    prompt.assert_not_called()


@pytest.mark.parametrize("local,number,expected", [
    (True, "1", "local"), (True, "2", "online"), (True, "3", "skip"),
    (False, "1", "online"), (False, "2", "skip"), (True, "", "skip"),
    (False, "", "skip"),
])
def test_menus(local, number, expected):
    @click.command()
    def command():
        click.echo("RESULT=" + select_source(Path("/path with spaces/package.zip") if local else None))
    result = CliRunner().invoke(command, input=number + "\n")
    assert result.exit_code == 0, result.output
    assert f"RESULT={expected}" in result.output
    if local:
        assert "/path with spaces/package.zip" in result.output
        assert "1. Install from local source\n2. Install from online source\n3. Skip" in result.output
    else:
        assert "1. Install from online source\n2. Skip" in result.output


@pytest.mark.parametrize("failure", [None, "copy", "install"])
def test_staging_cleanup_and_original_preserved(tmp_path, failure):
    path = bundle(tmp_path / "package with spaces.zip")
    commands, host_staging = [], []

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["docker", "cp"]:
            staging = Path(command[2])
            host_staging.append(staging)
            assert (staging / "install_modelsdk_wheels.sh").is_file()
            if failure == "copy":
                raise SystemExit(1)
    try:
        with stage_archive(path, "arm64", "2.1.3", "container", "1000:1000", run) as destination:
            assert destination.startswith("/tmp/sima-model-compiler-")
            if failure == "install":
                raise SystemExit(1)
    except SystemExit:
        assert failure is not None
    assert path.is_file()
    assert host_staging and not host_staging[0].exists()
    assert commands[-1][-3:-1] == ["rm", "-rf"]
    assert any("1000:1000" in command for command in commands) == (failure != "copy")


@pytest.mark.parametrize("local,yes,noninteractive,expected", [
    (True, False, True, "local"), (True, True, True, "local"), (True, True, False, "local"),
    (False, False, True, "skip"), (False, True, True, "online"), (False, True, False, "online"),
    (False, False, False, "skip"),  # No stdin TTY.
])
def test_install_routing_uses_container_arch(tmp_path, monkeypatch, local, yes, noninteractive, expected):
    monkeypatch.chdir(tmp_path)
    path = bundle(tmp_path / "model-compiler-arm64.zip") if local else None
    with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.3"), \
         patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"), \
         patch("sima_cli.sdk.utils.subprocess.run", side_effect=[
             Mock(returncode=0, stdout="SDK Version = 2.1.3_Palette_SDK_neat_main_123\n"),
             Mock(returncode=0, stdout="aarch64\n")]), \
         patch("sima_cli.sdk.utils.sys.stdin.isatty", return_value=False), \
         patch("sima_cli.sdk.model_compiler.click.prompt") as prompt, \
         patch("sima_cli.sdk.utils.run_command") as run:
        ensure_model_sdk_extension_installed("container", "developer", auto_install=yes,
                                             noninteractive=noninteractive, uid=123, gid=456)
    prompt.assert_not_called()
    commands = [call.args[0] for call in run.call_args_list]
    if expected == "skip":
        assert not commands
        return
    scripts = [cmd[-1] for cmd in commands if "bash" in cmd]
    assert len(scripts) == 1
    script = scripts[0]
    assert "su -s /bin/bash developer -c" in script
    assert "trap cleanup_model_sdk_install EXIT" in script
    assert "chown -R 123:456" in script
    if expected == "local":
        assert "bash ./install_modelsdk_wheels.sh" in script
        assert "sima-cli login" not in script and "neat install" not in script
        assert commands[-1][-3:-1] == ["rm", "-rf"]
        assert path.exists()
    else:
        assert 'neat install model-compiler/arm64@v2.1.3' in script
        assert "install_modelsdk_wheels.sh" not in script


def test_local_install_failure_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle(tmp_path / "model-compiler-arm64.zip")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if "bash" in command:
            raise SystemExit(1)
    with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.3"), \
         patch("sima_cli.sdk.utils.subprocess.run", side_effect=[
             Mock(returncode=0, stdout="SDK Version = 2.1.3_Palette_SDK_neat_main_123\n"),
             Mock(returncode=0, stdout="aarch64\n")]), \
         patch("sima_cli.sdk.utils.run_command", side_effect=run):
        with pytest.raises(SystemExit):
            ensure_model_sdk_extension_installed("container", "developer", auto_install=True)
    assert commands[-1][-3:-1] == ["rm", "-rf"]
    assert not any("neat install" in command[-1] or "sima-cli login" in command[-1] for command in commands)


@pytest.mark.parametrize("yes,noninteractive", [(False, True), (True, False), (True, True)])
def test_configure_preserves_separate_automation_flags(yes, noninteractive):
    from sima_cli.sdk.utils import configure_container
    with patch("sima_cli.sdk.utils.check_os", return_value="windows"), \
         patch("sima_cli.sdk.utils.run_command"), \
         patch("sima_cli.sdk.utils._copy_sima_cli_auth_cache_to_container"), \
         patch("sima_cli.sdk.utils.ensure_sima_cli_installed"), \
         patch("sima_cli.sdk.utils.ensure_model_sdk_extension_installed") as install, \
         patch("sima_cli.sdk.utils._sync_codex_skills"), \
         patch("sima_cli.sdk.utils.install_neat_playbooks"), \
         patch("sima_cli.sdk.utils.ensure_codex_vscode_extension_installed"):
        configure_container("container", yes_to_all=yes, noninteractive=noninteractive)
    assert install.call_args.kwargs["auto_install"] is yes
    assert install.call_args.kwargs["noninteractive"] is noninteractive
