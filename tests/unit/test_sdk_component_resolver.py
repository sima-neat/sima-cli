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
        with pytest.raises(ValueError, match="Update the SDK image"):
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
