import json
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

from sima_cli.sdk.utils import (
    AGENT_SESSIONS_DIRNAME,
    _agent_sessions_script,
    _link_agent_sessions,
)

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run_script(home, mount, retention_days=365):
    script = _agent_sessions_script(
        str(home), os.getuid(), os.getgid(), mount=str(mount), retention_days=retention_days
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return result.stdout


@pytest.fixture
def layout(tmp_path):
    home = tmp_path / "home"
    mount = tmp_path / "sdk-extensions"
    home.mkdir()
    mount.mkdir()
    return home, mount, mount / AGENT_SESSIONS_DIRNAME


def test_script_links_only_session_data():
    script = _agent_sessions_script("/home/dev", 1000, 1000)

    for path in (".claude/projects", ".codex/sessions", ".codex/archived_sessions", ".codex/session_index.jsonl"):
        assert path in script
    for secret in (".credentials.json", ".claude.json", "auth.json", "config.toml"):
        assert secret not in script


def test_script_quotes_the_home_directory():
    script = _agent_sessions_script("/home/dev user", 1000, 1000)

    assert "home='/home/dev user'" in script


@needs_bash
def test_fresh_home_gets_links_into_the_mount(layout):
    home, mount, root = layout

    output = run_script(home, mount)

    assert "__SIMA_AGENT_SESSIONS_STATUS=linked" in output
    assert os.readlink(home / ".claude/projects") == str(root / "claude/projects")
    assert os.readlink(home / ".codex/sessions") == str(root / "codex/sessions")
    assert os.readlink(home / ".codex/archived_sessions") == str(root / "codex/archived_sessions")
    assert os.readlink(home / ".codex/session_index.jsonl") == str(root / "codex/session_index.jsonl")
    assert (root / "claude/projects").is_dir()
    assert (root / "codex/session_index.jsonl").is_file()
    assert oct(root.stat().st_mode & 0o777) == "0o700"


@needs_bash
def test_a_new_release_sees_the_sessions_of_the_previous_one(layout, tmp_path):
    old_home, mount, _ = layout
    run_script(old_home, mount)
    (old_home / ".claude/projects/session.jsonl").write_text("old turn\n")

    new_home = tmp_path / "new-home"
    new_home.mkdir()
    run_script(new_home, mount)

    assert (new_home / ".claude/projects/session.jsonl").read_text() == "old turn\n"


@needs_bash
def test_existing_sessions_move_to_the_host_without_overwriting(layout):
    home, mount, root = layout
    (home / ".claude/projects/proj").mkdir(parents=True)
    (home / ".claude/projects/proj/a.jsonl").write_text("container a\n")
    (home / ".claude/projects/proj/b.jsonl").write_text("container b\n")
    (root / "claude/projects/proj").mkdir(parents=True)
    (root / "claude/projects/proj/a.jsonl").write_text("host a\n")

    run_script(home, mount)

    assert (home / ".claude/projects").is_symlink()
    assert (root / "claude/projects/proj/a.jsonl").read_text() == "host a\n"
    assert (root / "claude/projects/proj/b.jsonl").read_text() == "container b\n"


@needs_bash
def test_existing_session_index_is_appended_to_the_host_copy(layout):
    home, mount, root = layout
    (home / ".codex").mkdir()
    (home / ".codex/session_index.jsonl").write_text('{"id":"b"}\n')
    (root / "codex").mkdir(parents=True)
    (root / "codex/session_index.jsonl").write_text('{"id":"a"}\n')

    run_script(home, mount)

    assert (root / "codex/session_index.jsonl").read_text() == '{"id":"a"}\n{"id":"b"}\n'
    assert (home / ".codex/session_index.jsonl").is_symlink()


@needs_bash
def test_second_run_changes_nothing(layout):
    home, mount, root = layout
    run_script(home, mount)
    (root / "codex/session_index.jsonl").write_text('{"id":"a"}\n')

    output = run_script(home, mount)

    assert "CONFLICT" not in output
    assert (root / "codex/session_index.jsonl").read_text() == '{"id":"a"}\n'


@needs_bash
def test_a_symlink_to_somewhere_else_is_left_alone(layout, tmp_path):
    home, mount, _ = layout
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / ".claude").mkdir()
    os.symlink(elsewhere, home / ".claude/projects")

    output = run_script(home, mount)

    assert f"__SIMA_AGENT_SESSIONS_CONFLICT={home}/.claude/projects" in output
    assert os.readlink(home / ".claude/projects") == str(elsewhere)


@needs_bash
def test_missing_mount_changes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    output = run_script(home, tmp_path / "absent")

    assert "__SIMA_AGENT_SESSIONS_STATUS=no_mount" in output
    assert list(home.iterdir()) == []


@needs_bash
def test_claude_retention_is_set_when_absent(layout):
    home, mount, _ = layout
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text(json.dumps({"theme": "dark"}))

    output = run_script(home, mount, retention_days=365)

    assert "__SIMA_AGENT_SESSIONS_RETENTION=set" in output
    assert json.loads((home / ".claude/settings.json").read_text()) == {"theme": "dark", "cleanupPeriodDays": 365}


@needs_bash
def test_claude_retention_chosen_by_the_user_is_kept(layout):
    home, mount, _ = layout
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text(json.dumps({"cleanupPeriodDays": 10}))

    output = run_script(home, mount)

    assert "__SIMA_AGENT_SESSIONS_RETENTION=kept" in output
    assert json.loads((home / ".claude/settings.json").read_text()) == {"cleanupPeriodDays": 10}


@needs_bash
def test_unreadable_claude_settings_are_not_rewritten(layout):
    home, mount, _ = layout
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text("{not json")

    output = run_script(home, mount)

    assert "__SIMA_AGENT_SESSIONS_RETENTION=unreadable" in output
    assert (home / ".claude/settings.json").read_text() == "{not json"


def test_link_reports_success(capsys):
    output = "__SIMA_AGENT_SESSIONS_RETENTION=set\n__SIMA_AGENT_SESSIONS_STATUS=linked"
    with patch("sima_cli.sdk.utils.run_command", return_value=output) as run:
        _link_agent_sessions("container", "dev", 1000, 1000)

    assert run.call_args.args[0][:6] == ["docker", "exec", "-u", "root", "container", "bash"]
    printed = capsys.readouterr().out
    assert "survive SDK upgrades" in printed
    assert "365 days" in printed


def test_link_reports_a_missing_mount(capsys):
    with patch("sima_cli.sdk.utils.run_command", return_value="__SIMA_AGENT_SESSIONS_STATUS=no_mount"):
        _link_agent_sessions("container", "dev", 1000, 1000)

    assert "will not carry over" in capsys.readouterr().out


def test_link_reports_conflicts_and_failures(capsys):
    output = "__SIMA_AGENT_SESSIONS_CONFLICT=/home/dev/.claude/projects\n__SIMA_AGENT_SESSIONS_STATUS=linked"
    with patch("sima_cli.sdk.utils.run_command", return_value=output):
        _link_agent_sessions("container", "dev", 1000, 1000)
    assert "Left /home/dev/.claude/projects as it is" in capsys.readouterr().out

    with patch("sima_cli.sdk.utils.run_command", return_value=False):
        _link_agent_sessions("container", "dev", 1000, 1000)
    assert "Failed to keep Claude and Codex sessions" in capsys.readouterr().out


def _configure(**kwargs):
    calls = []

    def record(name):
        return lambda *a, **k: calls.append(name)

    with patch("sima_cli.sdk.utils.check_os", return_value="windows"), \
         patch("sima_cli.sdk.utils.run_command"), \
         patch("sima_cli.sdk.utils._copy_sima_cli_auth_cache_to_container"), \
         patch("sima_cli.sdk.utils.ensure_sima_cli_installed"), \
         patch("sima_cli.sdk.utils.ensure_model_sdk_extension_installed"), \
         patch("sima_cli.sdk.utils._link_agent_sessions", side_effect=record("sessions")), \
         patch("sima_cli.sdk.utils._sync_codex_skills", side_effect=record("codex-skills")), \
         patch("sima_cli.sdk.utils.install_neat_playbooks", side_effect=record("playbooks")), \
         patch("sima_cli.sdk.utils.ensure_codex_vscode_extension_installed"), \
         patch("sima_cli.sdk.utils.ensure_edgematic_studio_installed"):
        from sima_cli.sdk.utils import configure_container

        configure_container("container", noninteractive=True, **kwargs)
    return calls


def test_configure_links_sessions_before_agents_are_set_up():
    assert _configure() == ["sessions", "codex-skills", "playbooks"]


def test_configure_minimal_skips_session_links():
    assert "sessions" not in _configure(minimal=True)
