from unittest.mock import Mock, patch

import pytest

from sima_cli.sdk.utils import (
    _extract_sdk_base_version,
    _load_sdk_release_mapping,
    _model_sdk_extension_install_args,
    ensure_model_sdk_extension_installed,
)


@pytest.mark.parametrize("version", ["2.0", "2.0.0", "2.1.3", "3.0.0.0", "3.0.0.1"])
@pytest.mark.parametrize("suffix", ["", "_Palette_SDK_neat_main_780365a"])
def test_sdk_release_retains_complete_version(version, suffix):
    release = "SDK Release\nSDK Version = {}{}\nBuild Date = 2026-11-06\n".format(version, suffix)
    assert _extract_sdk_base_version(release) == version


@pytest.mark.parametrize("release", ["", "SDK Version = unknown\n", "Build Version = 3.0.0.1\n"])
def test_sdk_release_without_version(release):
    assert _extract_sdk_base_version(release) == ""


@pytest.mark.parametrize("machine,arch", [("x86_64", "amd64"), ("amd64", "amd64"), ("arm64", "arm64"), ("aarch64", "arm64")])
@pytest.mark.parametrize("sdk_version,compiler_version", [
    ("2.1.3", "2.1.3.1"),
    ("2.2.0", "2.1.3.1"),
    ("3.0.0", "3.0.0"),
    ("3.0.0.0", "3.0.0"),
    ("3.0.0.1", "3.0.0.1"),
    ("3.0.0.2", "2.1.3.1"),
])
def test_compiler_release_pins_and_historical_fallback(machine, arch, sdk_version, compiler_version):
    with patch("sima_cli.sdk.utils.platform.machine", return_value=machine):
        assert _model_sdk_extension_install_args(sdk_version) == [
            "neat", "install", "model-compiler/{}@v{}".format(arch, compiler_version),
        ]


@pytest.mark.parametrize("sdk_version", ["2.0.0", "2.1.0", "2.1.1", "2.1.2"])
@pytest.mark.parametrize("machine,arch", [("x86_64", "amd64"), ("aarch64", "arm64")])
def test_legacy_compiler_versions_and_architecture_support(sdk_version, machine, arch):
    with patch("sima_cli.sdk.utils.platform.machine", return_value=machine):
        expected = [] if arch == "arm64" and sdk_version in {"2.0.0", "2.1.0"} else [
            "install", "-v", sdk_version, "tools/model-compiler/{}".format(arch),
        ]
        assert _model_sdk_extension_install_args(sdk_version) == expected


def test_unsupported_architecture_does_not_install():
    with patch("sima_cli.sdk.utils.platform.machine", return_value="riscv64"):
        assert _model_sdk_extension_install_args("3.0.0.1") == []


def test_new_release_can_be_selected_by_mapping_alone():
    mapping = _load_sdk_release_mapping()
    mapping["model_compiler"]["releases"].insert(0, {
        "sdk_versions": "==3.1.0.1",
        "install_args": ["neat", "install", "model-compiler/{arch}@v3.1.0.1"],
    })
    with patch("sima_cli.sdk.utils._load_sdk_release_mapping", return_value=mapping), \
         patch("sima_cli.sdk.utils.platform.machine", return_value="x86_64"):
        assert _model_sdk_extension_install_args("3.1.0.1") == [
            "neat", "install", "model-compiler/amd64@v3.1.0.1",
        ]


@pytest.mark.parametrize("machine,arch", [("x86_64", "amd64"), ("aarch64", "arm64")])
@pytest.mark.parametrize("sdk_version,compiler_version", [("3.0.0.0", "3.0.0"), ("3.0.0.1", "3.0.0.1")])
def test_sdk_setup_installs_matching_four_component_compiler(machine, arch, sdk_version, compiler_version):
    release = Mock(returncode=0, stdout="SDK Version = {}_Palette_SDK_neat_main_780365a\n".format(sdk_version))
    with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:{}".format(sdk_version)), \
         patch("sima_cli.sdk.utils.platform.machine", return_value=machine), \
         patch("sima_cli.sdk.utils.subprocess.run", side_effect=[release, Mock(returncode=0, stdout=machine)]), \
         patch("sima_cli.sdk.utils.discover_archive", return_value=None) as discover, \
         patch("sima_cli.sdk.utils.run_command", return_value=True) as run:
        ensure_model_sdk_extension_installed("container", "docker", auto_install=True)

    discover.assert_called_once_with(arch, compiler_version)
    assert run.call_count == 1
    script = run.call_args.args[0][-1]
    assert '"$SIMA_CLI_BIN" neat install model-compiler/{}@v{}'.format(arch, compiler_version) in script
    assert "@v2.1.3" not in script
    assert "tools/model-compiler" not in script


@pytest.mark.parametrize("sdk_version,arch,expected", [
    ("2.0.0", "arm64", []),
    ("2.1.1", "arm64", ["install", "-v", "2.1.1", "tools/model-compiler/arm64"]),
    ("3.0.0.0", "arm64", ["neat", "install", "model-compiler/arm64@v3.0.0"]),
    ("3.0.0.1", "amd64", ["neat", "install", "model-compiler/amd64@v3.0.0.1"]),
    ("3.0.0.1", "", []),
    ("3.0.0.1", "riscv64", []),
])
def test_explicit_container_architecture_uses_mapping(sdk_version, arch, expected):
    assert _model_sdk_extension_install_args(sdk_version, arch) == expected
