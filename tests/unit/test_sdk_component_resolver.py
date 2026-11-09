"""Cross-repository SDK resolver contract and setup failure regressions."""
import json
import os
import subprocess
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from sima_cli.sdk.utils import ensure_codex_vscode_extension_installed
from sima_cli.sdk.vscode_extensions import resolve_sdk_components

RESOLVED = {
    "schema_version": 1, "available": True, "sdk_release": "3.0.0.1",
    "cohort": "3.0.0", "status": "qualified", "source": "online",
    "extensions": {"openai.chatgpt": "26.908.40401", "anthropic.claude-code": "2.1.316"},
}


def result(body=RESOLVED, code=0):
    return Mock(returncode=code, stdout=json.dumps(body), stderr="")


def test_query_uses_mapped_user_and_home():
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=result()) as run:
        assert resolve_sdk_components("sdk", "alice") == RESOLVED
    args = run.call_args.args[0]
    assert args[:7] == ["docker", "exec", "-u", "alice", "-e", "HOME=/home/alice", "sdk"]
    assert args[-1] == "/usr/local/bin/sdk-components"


@pytest.mark.parametrize("code,body,error", [
    (1, {"schema_version": 1, "available": False, "error": "components are revoked"}, "revoked"),
    (0, {**RESOLVED, "schema_version": 2}, "schema"),
    (0, {**RESOLVED, "schema_version": True}, "schema"),
    (0, {**RESOLVED, "extensions": {}}, "versions"),
    (0, {**RESOLVED, "status": "revoked"}, "status"),
    (0, [], "resolution"),
])
def test_resolver_failures_never_use_legacy_pins(code, body, error):
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=result(body, code)), \
         patch("sima_cli.sdk.vscode_extensions.load_extension_versions") as fallback:
        with pytest.raises(ValueError, match=error):
            resolve_sdk_components("sdk", "alice")
    fallback.assert_not_called()


def test_only_legacy_images_allow_fallback():
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=Mock(returncode=44)):
        assert resolve_sdk_components("sdk", "alice") is None
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=Mock(returncode=45)):
        assert resolve_sdk_components("sdk", "alice") == {"interface": "code-server", "extensions": {}}
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=Mock(returncode=46)):
        with pytest.raises(ValueError, match="wrapper is missing"):
            resolve_sdk_components("sdk", "alice")


@pytest.mark.parametrize("override", [None, "anthropic.claude-code@2.1.316"])
def test_setup_uses_sdk_versions_and_install_interface_without_cli_pins(override):
    env = {"SDK_SKIP_CODING_AGENT_INSTALL": "0"}
    if override:
        env["SIMA_CLI_CLAUDE_EXTENSION_ID"] = override
    with patch.dict(os.environ, env), \
         patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:3.0.0.1"), \
         patch("sima_cli.sdk.utils._container_code_ui_backend", return_value="code-server"), \
         patch("sima_cli.sdk.utils.select_browser_extensions", return_value=["codex", "claude"]), \
         patch("sima_cli.sdk.utils.resolve_sdk_components", return_value=RESOLVED), \
         patch("sima_cli.sdk.utils.load_extension_versions") as fallback, \
         patch("sima_cli.sdk.utils.subprocess.run", return_value=Mock(returncode=0, stdout="", stderr="")) as run:
        ensure_codex_vscode_extension_installed("sdk", "alice", uid=1001, gid=1001)
    fallback.assert_not_called()
    script = run.call_args.args[0][-1]
    assert "sdk-components install --json --extension anthropic.claude-code@2.1.316" in script
    assert "sdk-components install --json --extension openai.chatgpt@26.908.40401" in script
    assert "2.1.266" not in script
    assert "Retrying Claude" not in script
    assert "--extensions-dir /home/alice/.local/share/code-server/extensions" in script
    subprocess.run(["bash", "-n", "-c", script], check=True)


def test_incompatible_override_stops_before_installation():
    with patch.dict(os.environ, {"SIMA_CLI_CLAUDE_EXTENSION_ID": "anthropic.claude-code@2.1.266", "SDK_SKIP_CODING_AGENT_INSTALL": "0"}), \
         patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:3.0.0.1"), \
         patch("sima_cli.sdk.utils._container_code_ui_backend", return_value="code-server"), \
         patch("sima_cli.sdk.utils.select_browser_extensions", return_value=["claude"]), \
         patch("sima_cli.sdk.utils.resolve_sdk_components", return_value=RESOLVED), \
         patch("sima_cli.sdk.utils.subprocess.run") as run:
        with pytest.raises(RuntimeError, match="requested.*not qualified"):
            ensure_codex_vscode_extension_installed("sdk", "alice")
    run.assert_not_called()


def test_setup_command_exits_nonzero_without_success_on_component_failure():
    from sima_cli.sdk.commands import setup
    with patch("sima_cli.sdk.commands._resolve_devkit_credentials", return_value=(None, None)), \
         patch("sima_cli.sdk.commands.setup_and_start", side_effect=RuntimeError("SDK setup incomplete: Claude installation failed")):
        outcome = CliRunner().invoke(setup, ["--noninteractive"], obj={})
    assert outcome.exit_code != 0
    assert "SDK setup incomplete" in outcome.output
    assert "All selected containers started successfully" not in outcome.output


@pytest.mark.parametrize("override,expected", [
    ("anthropic.claude-code", "anthropic.claude-code"),
    ("anthropic.claude-code@latest", "anthropic.claude-code"),
    ("anthropic.claude-code@2.1.316", "anthropic.claude-code@2.1.316"),
])
def test_shipped_sdk_wrapper_needs_no_new_interface_or_cli_pins(override, expected, capsys):
    with patch.dict(os.environ, {"SIMA_CLI_CLAUDE_EXTENSION_ID": override, "SDK_SKIP_CODING_AGENT_INSTALL": "0"}), \
         patch("sima_cli.sdk.utils._get_container_image_ref", return_value="ghcr.io/sima-neat/sdk:3.0.0.1"), \
         patch("sima_cli.sdk.utils._container_code_ui_backend", return_value="code-server"), \
         patch("sima_cli.sdk.utils.select_browser_extensions", return_value=["codex", "claude"]), \
         patch("sima_cli.sdk.utils.resolve_sdk_components", return_value={"interface": "code-server", "extensions": {}}), \
         patch("sima_cli.sdk.utils.load_extension_versions") as fallback, \
         patch("sima_cli.sdk.utils.subprocess.run", return_value=Mock(returncode=0, stdout="", stderr="")) as run:
        ensure_codex_vscode_extension_installed("sdk", "alice", uid=1001, gid=1001)
    fallback.assert_not_called()
    script = run.call_args.args[0][-1]
    assert f"--install-extension {expected} --force" in script
    assert "--install-extension openai.chatgpt --force" in script
    assert "sdk-components install" not in script
    assert "2.1.266" not in script
    assert "Retrying Claude" not in script
    # SDK profile pins are inspected, never replaced by the CLI's pin writer.
    assert "metadata['pinned']" not in script
    subprocess.run(["bash", "-n", "-c", script], check=True)
    if "@" not in expected:
        assert "SDK-selected qualified version" in capsys.readouterr().out


@pytest.mark.parametrize("qualified_files,wrapper_files,expected", [
    (False, False, 44), (True, False, 46), (True, True, 45),
])
def test_capability_probe_recognizes_shipped_wrapper_without_sdk_338(qualified_files, wrapper_files, expected):
    import pathlib
    import sys
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=Mock(returncode=44)) as run:
        resolve_sdk_components("sdk", "alice")
    probe = run.call_args.args[0][-2]
    def fake_path(name):
        return Mock(exists=Mock(return_value=qualified_files and name != "/usr/local/bin/sdk-components"),
                    is_file=Mock(return_value=wrapper_files))
    with patch.object(pathlib, "Path", side_effect=fake_path), \
         patch.object(sys, "argv", ["probe", "/usr/local/bin/sdk-components"]):
        with pytest.raises(SystemExit) as error:
            exec(probe, {})
    assert error.value.code == expected


@pytest.mark.parametrize("state,expected_success", [("pinned", True), ("unpinned", False), ("rejected", False), ("wrong-version", False)])
def test_shipped_wrapper_install_checks_result_and_pin_without_retries(tmp_path, state, expected_success):
    import sys
    from sima_cli.sdk.vscode_extensions import sdk_component_install_command
    wrapper = tmp_path / "wrapper"
    calls = tmp_path / "calls"
    wrapper.write_text(f"#!{sys.executable}\n" + '''
import json, pathlib, sys
args = sys.argv[1:]
directory = pathlib.Path(args[args.index('--extensions-dir') + 1])
target = args[args.index('--install-extension') + 1]
(directory / 'calls').open('a').write(target + '\\n')
state = (directory / 'state').read_text()
if state == 'rejected':
    sys.stderr.write('SDK component installation blocked: components are revoked\\n')
    sys.exit(1)
(directory / 'extensions.json').write_text(json.dumps([{
    'identifier': {'id': 'anthropic.claude-code'},
    'version': '2.1.266' if state == 'wrong-version' else '2.1.316',
    'metadata': {'pinned': state == 'pinned' or state == 'wrong-version'}
}]))
''')
    wrapper.chmod(0o755)
    (tmp_path / "state").write_text(state)
    command = sdk_component_install_command(str(tmp_path), "anthropic.claude-code@2.1.316", "code-server")
    command = command.replace("/opt/code-server/bin/code-server", str(wrapper), 1)
    result = subprocess.run(["bash", "-c", "set -e; " + command], text=True, capture_output=True)
    assert (result.returncode == 0) == expected_success
    assert calls.read_text().splitlines() == ["anthropic.claude-code@2.1.316"]
    if expected_success:
        assert "Installed SDK-managed extension: anthropic.claude-code@2.1.316" in result.stdout
    else:
        assert "SDK-managed extension" in result.stderr or "revoked" in result.stderr


@pytest.mark.parametrize("resolver_status", [1, 44, 45, 46])
def test_existing_resolver_error_never_looks_like_missing_capability(resolver_status):
    import pathlib
    import sys
    with patch("sima_cli.sdk.vscode_extensions.subprocess.run", return_value=Mock(returncode=44)) as run:
        resolve_sdk_components("sdk", "alice")
    probe = run.call_args.args[0][-2]
    resolver = Mock(exists=Mock(return_value=True))
    with patch.object(pathlib, "Path", return_value=resolver), \
         patch.object(sys, "argv", ["probe", "/usr/local/bin/sdk-components"]), \
         patch("subprocess.run", return_value=Mock(returncode=resolver_status)):
        with pytest.raises(SystemExit) as error:
            exec(probe, {})
    assert error.value.code == 1
