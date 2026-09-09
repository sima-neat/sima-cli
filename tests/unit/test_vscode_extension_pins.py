"""Version selection and executable installer regressions for SDK extensions."""

import json
import os
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from sima_cli.sdk.utils import ensure_codex_vscode_extension_installed
from sima_cli.sdk.vscode_extensions import (
    DEFAULT_EXTENSION_VERSIONS,
    SDK_EXTENSION_MANIFEST,
    extension_install_command,
    load_extension_versions,
    pin_extensions_command,
    resolve_extension_target,
)


def manifest_result(manifest):
    return Mock(returncode=0, stdout=json.dumps(manifest), stderr="")


def test_missing_sdk_manifest_uses_exact_fallbacks():
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=Mock(returncode=44)) as run:
        assert load_extension_versions("sdk") == {
            "openai.chatgpt": "26.5825.51511",
            "anthropic.claude-code": "2.1.266",
        }
    assert run.call_args.args[0][-1] == SDK_EXTENSION_MANIFEST
    assert run.call_args.kwargs["timeout"] == 30


def test_sdk_manifest_takes_precedence_over_fallbacks():
    versions = {"openai.chatgpt": "27.1.2", "anthropic.claude-code": "3.1.2"}
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=manifest_result({
        "schema_version": 1, "extensions": versions,
    })):
        assert load_extension_versions("sdk") == versions


@pytest.mark.parametrize("manifest", [
    [], {}, {"schema_version": True}, {"schema_version": 2},
    {"schema_version": 1, "extensions": []},
    {"schema_version": 1, "extensions": {"openai.chatgpt": "1.2.3"}},
    {"schema_version": 1, "extensions": {**DEFAULT_EXTENSION_VERSIONS, "openai.chatgpt": "latest"}},
    {"schema_version": 1, "extensions": {**DEFAULT_EXTENSION_VERSIONS, "anthropic.claude-code": 123}},
])
def test_invalid_manifest_does_not_fall_back(manifest):
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=manifest_result(manifest)):
        with pytest.raises(ValueError):
            load_extension_versions("sdk")


@pytest.mark.parametrize("result", [
    Mock(returncode=1, stderr="Permission denied"),
    Mock(returncode=0, stdout="not json", stderr=""),
])
def test_unreadable_or_malformed_manifest_does_not_fall_back(result):
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=result):
        with pytest.raises(ValueError):
            load_extension_versions("sdk")


@pytest.mark.parametrize("target, expected", [
    ("", ""), ("  ", ""),
    ("openai.chatgpt", "openai.chatgpt@26.5825.51511"),
    ("anthropic.claude-code", "anthropic.claude-code@2.1.266"),
    ("openai.chatgpt@27.1.2", "openai.chatgpt@27.1.2"),
    ("custom.agent@1.2.3", "custom.agent@1.2.3"),
])
def test_resolve_overrides_and_opt_out(target, expected):
    assert resolve_extension_target(target, DEFAULT_EXTENSION_VERSIONS) == expected


@pytest.mark.parametrize("target", [
    "openai.chatgpt@latest", "openai.chatgpt@", "openai.chatgpt@^1.2.3",
    "custom.agent", "openai.chatgpt@1.2.3;touch /tmp/injected", "$(echo evil).agent@1.2.3",
])
def test_invalid_or_floating_override_is_rejected(target):
    with pytest.raises(ValueError):
        resolve_extension_target(target, DEFAULT_EXTENSION_VERSIONS)


def test_bad_manifest_skips_install_and_reports_error(capsys):
    with patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.3"), \
         patch("sima_cli.sdk.utils.subprocess.run", return_value=Mock(returncode=0)) as run, \
         patch("sima_cli.sdk.utils.load_extension_versions", side_effect=ValueError("bad schema")):
        ensure_codex_vscode_extension_installed("sdk", "user", auto_install=True)
    assert run.call_count == 1
    assert "bad schema" in capsys.readouterr().out


def test_both_opted_out_skip_manifest_and_ai_install():
    with patch.dict(os.environ, {"SIMA_CLI_CODEX_EXTENSION_ID": "", "SIMA_CLI_CLAUDE_EXTENSION_ID": ""}), \
         patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.3"), \
         patch("sima_cli.sdk.utils.subprocess.run", return_value=Mock(returncode=0, stdout="", stderr="")) as run, \
         patch("sima_cli.sdk.utils.load_extension_versions") as load:
        ensure_codex_vscode_extension_installed("sdk", "user", auto_install=True)
    load.assert_not_called()
    script = run.call_args.args[0][-1]
    assert "sima-neat.vsix" in script
    assert "--install-extension openai.chatgpt" not in script
    assert "--install-extension anthropic.claude-code" not in script
    subprocess.run(["bash", "-n", "-c", script], capture_output=True, check=True)


def test_exact_override_keeps_cleanup_at_base_id():
    with patch.dict(os.environ, {"SIMA_CLI_CODEX_EXTENSION_ID": "custom.agent@1.2.3", "SIMA_CLI_CLAUDE_EXTENSION_ID": ""}), \
         patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:2.1.3"), \
         patch("sima_cli.sdk.utils.load_extension_versions", return_value=DEFAULT_EXTENSION_VERSIONS), \
         patch("sima_cli.sdk.utils.subprocess.run", return_value=Mock(returncode=0, stdout="", stderr="")) as run:
        ensure_codex_vscode_extension_installed("sdk", "user", auto_install=True)
    script = run.call_args.args[0][-1]
    assert "--install-extension custom.agent@1.2.3" in script
    assert "-name 'custom.agent-*'" in script
    assert "-name 'custom.agent@" not in script
    assert script.index("su -s /bin/bash") < script.index("find /opt/openvscode-server/extensions")
    subprocess.run(["bash", "-n", "-c", script], capture_output=True, check=True)


@pytest.fixture
def fake_server(tmp_path):
    """Exercise the emitted shell against a CLI double with persistent state."""
    server = tmp_path / "server with spaces"
    server.write_text(f"#!{sys.executable}\n" + """\
import json
from pathlib import Path
import sys

args = sys.argv[1:]
root = Path(args[args.index('--extensions-dir') + 1])
profile = root / 'extensions.json'
entries = json.loads(profile.read_text())
if '--list-extensions' in args:
    assert '--show-versions' in args
    for entry in entries:
        print(entry['identifier']['id'] + '@' + entry['version'])
else:
    target = args[args.index('--install-extension') + 1]
    assert '--force' in args
    with (root / 'calls').open('a') as handle:
        handle.write(target + '\\n')
    if (root / 'fail').exists():
        sys.exit(9)
    extension_id, version = target.split('@')
    entries = [entry for entry in entries if entry['identifier']['id'] != extension_id]
    entries.append({'identifier': {'id': extension_id}, 'version': version})
    profile.write_text(json.dumps(entries))
""", encoding="utf-8")
    server.chmod(0o755)
    return str(server)


def extension_entry(extension_id, version, **metadata):
    return {"identifier": {"id": extension_id}, "version": version, "metadata": metadata}


@pytest.mark.parametrize("extension_id", list(DEFAULT_EXTENSION_VERSIONS))
@pytest.mark.parametrize("installed_version", [None, "99.9.9", "0.0.1", "matching"])
def test_fresh_upgrade_downgrade_and_existing_unpinned_are_idempotent(tmp_path, fake_server, extension_id, installed_version):
    version = DEFAULT_EXTENSION_VERSIONS[extension_id]
    target = f"{extension_id}@{version}"
    unrelated = extension_entry("other.extension", "1.0.0", custom="preserved")
    entries = [unrelated]
    if installed_version:
        entries.append(extension_entry(extension_id, version if installed_version == "matching" else installed_version))
    profile = tmp_path / "extensions.json"
    profile.write_text(json.dumps(entries))
    command = "set -e; " + extension_install_command(fake_server, str(tmp_path), "Agent", target)
    command += "; " + pin_extensions_command(str(tmp_path), {extension_id: version})
    subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=True)
    updated = json.loads(profile.read_text())
    assert unrelated in updated
    entry = next(entry for entry in updated if entry["identifier"]["id"] == extension_id)
    assert entry["version"] == version
    assert entry["metadata"]["pinned"] is True
    before = profile.stat().st_mtime_ns
    subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=True)
    assert profile.stat().st_mtime_ns == before
    if installed_version == "matching":
        assert not (tmp_path / "calls").exists()
    else:
        assert (tmp_path / "calls").read_text().splitlines() == [target]


def test_failed_version_install_does_not_pin_old_version_or_fall_back(tmp_path, fake_server):
    profile = tmp_path / "extensions.json"
    original = json.dumps([extension_entry("openai.chatgpt", "99.9.9")])
    profile.write_text(original)
    (tmp_path / "fail").touch()
    target = "openai.chatgpt@26.5825.51511"
    command = "set -e; " + extension_install_command(fake_server, str(tmp_path), "Codex", target)
    command += "; " + pin_extensions_command(str(tmp_path), {"openai.chatgpt": "26.5825.51511"})
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == 9
    assert profile.read_text() == original
    assert (tmp_path / "calls").read_text().splitlines() == [target]


def test_version_check_drains_cli_output_without_sigpipe(tmp_path, fake_server):
    entries = [extension_entry("openai.chatgpt", "26.5825.51511")]
    entries.extend(extension_entry(f"other.extension-{i}", "1.0.0") for i in range(5000))
    (tmp_path / "extensions.json").write_text(json.dumps(entries))
    command = "set -eo pipefail; " + extension_install_command(
        fake_server, str(tmp_path), "Codex", "openai.chatgpt@26.5825.51511",
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=True)
    assert not result.stderr
    assert not (tmp_path / "calls").exists()


def test_pin_verification_rejects_wrong_installed_version_without_writing(tmp_path):
    profile = tmp_path / "extensions.json"
    original = json.dumps([extension_entry("openai.chatgpt", "99.9.9")])
    profile.write_text(original)
    result = subprocess.run([
        "bash", "-c", pin_extensions_command(str(tmp_path), {"openai.chatgpt": "26.5825.51511"}),
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert "does not match requested pin" in result.stderr
    assert profile.read_text() == original
