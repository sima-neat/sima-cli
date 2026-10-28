import json
import os
import shutil
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from sima_cli.sdk.utils import (
    AGENT_SESSIONS_DIRNAME,
    _agent_sessions_script,
    _link_agent_sessions,
    _rescue_agent_sessions,
    confirm_to_remove_exiting_container,
)

# The script only ever runs inside the Linux SDK container and relies on GNU coreutils.
needs_bash = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("bash") is None,
    reason="the script targets the Linux SDK container",
)
not_root = pytest.mark.skipif(
    sys.platform.startswith("linux") and os.geteuid() == 0, reason="root bypasses permission checks"
)


def run_script(home, mount, retention_days=365, owner=None):
    uid, gid = owner if owner is not None else (os.getuid(), os.getgid())
    script = _agent_sessions_script(str(home), uid, gid, mount=str(mount), retention_days=retention_days)
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


@needs_bash
def test_a_conflicting_container_copy_is_kept_not_deleted(layout):
    home, mount, root = layout
    (home / ".claude/projects/proj").mkdir(parents=True)
    (home / ".claude/projects/proj/a.jsonl").write_text("container a\n")
    (home / ".claude/projects/proj/same.jsonl").write_text("same\n")
    (root / "claude/projects/proj").mkdir(parents=True)
    (root / "claude/projects/proj/a.jsonl").write_text("host a\n")
    (root / "claude/projects/proj/same.jsonl").write_text("same\n")

    output = run_script(home, mount)

    kept = root / "conflicts/claude/projects/proj/a.jsonl"
    assert f"__SIMA_AGENT_SESSIONS_KEPT={root}/conflicts/claude/projects/proj/a.jsonl" in output
    assert kept.read_text() == "container a\n"
    assert (root / "claude/projects/proj/a.jsonl").read_text() == "host a\n"
    assert not (root / "conflicts/claude/projects/proj/same.jsonl").exists()
    assert (home / ".claude/projects").is_symlink()


@needs_bash
def test_an_existing_link_gets_its_host_folder_back(layout):
    home, mount, root = layout
    run_script(home, mount)
    shutil.rmtree(root)

    output = run_script(home, mount)

    assert "__SIMA_AGENT_SESSIONS_STATUS=linked" in output
    assert (home / ".claude/projects").resolve().is_dir()
    assert (home / ".codex/session_index.jsonl").resolve().is_file()


@needs_bash
@not_root
def test_a_read_only_mount_is_not_reported_as_missing(layout):
    home, mount, _ = layout
    mount.chmod(0o555)
    try:
        output = run_script(home, mount)
    finally:
        mount.chmod(0o755)

    assert "__SIMA_AGENT_SESSIONS_STATUS=not_writable" in output


@needs_bash
def test_session_index_without_trailing_newline_is_not_glued(layout):
    home, mount, root = layout
    (home / ".codex").mkdir()
    (home / ".codex/session_index.jsonl").write_text('{"id":"b"}\n')
    (root / "codex").mkdir(parents=True)
    (root / "codex/session_index.jsonl").write_text('{"id":"a"}')

    run_script(home, mount)

    assert (root / "codex/session_index.jsonl").read_text() == '{"id":"a"}\n{"id":"b"}\n'


@needs_bash
def test_claude_settings_symlink_is_followed_and_its_mode_kept(layout, tmp_path):
    home, mount, _ = layout
    real = tmp_path / "dotfiles/claude-settings.json"
    real.parent.mkdir()
    real.write_text("{}")
    real.chmod(0o600)
    (home / ".claude").mkdir()
    os.symlink(real, home / ".claude/settings.json")

    run_script(home, mount)

    assert (home / ".claude/settings.json").is_symlink()
    assert json.loads(real.read_text()) == {"cleanupPeriodDays": 365}
    assert oct(real.stat().st_mode & 0o777) == "0o600"


@needs_bash
@not_root
def test_failed_ownership_change_is_reported(layout):
    home, mount, _ = layout

    output = run_script(home, mount, owner=(0, 0))

    assert "__SIMA_AGENT_SESSIONS_CHOWN=failed" in output
    assert "__SIMA_AGENT_SESSIONS_STATUS=linked" in output


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


def test_link_reports_an_unwritable_mount_kept_copies_and_ownership(capsys):
    with patch("sima_cli.sdk.utils.run_command", return_value="__SIMA_AGENT_SESSIONS_STATUS=not_writable"):
        _link_agent_sessions("container", "dev", 1000, 1000)
    assert "is not writable" in capsys.readouterr().out

    output = "\n".join([
        "__SIMA_AGENT_SESSIONS_KEPT=/sdk-extensions/.agent-sessions/conflicts/claude/projects/p/a.jsonl",
        "__SIMA_AGENT_SESSIONS_CHOWN=failed",
        "__SIMA_AGENT_SESSIONS_STATUS=linked",
    ])
    with patch("sima_cli.sdk.utils.run_command", return_value=output):
        _link_agent_sessions("container", "dev", 1000, 1000)
    printed = capsys.readouterr().out
    assert "this container's copy was kept at /sdk-extensions/.agent-sessions/conflicts" in printed
    assert "Could not hand /sdk-extensions/.agent-sessions to 'dev'" in printed
    assert "survive SDK upgrades" in printed


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


# --- sessions of a container that sdk setup removes or reuses -------------------------------------

@pytest.fixture
def old_container(tmp_path):
    """A container filesystem, served to the code under test through a fake `docker cp`."""
    root = tmp_path / "container"
    store = tmp_path / "host-extensions"
    store.mkdir()

    def docker_cp_out(container, source, destination):
        path = root / source.lstrip("/")
        if os.path.islink(path):
            os.symlink(os.readlink(path), destination)
        elif path.is_dir():
            shutil.copytree(path, destination, symlinks=True)
        elif path.is_file():
            shutil.copy2(path, destination)
        else:
            return "missing"
        return "ok"

    with patch("sima_cli.sdk.utils._docker_cp_out", side_effect=docker_cp_out), \
         patch("sima_cli.sdk.utils._mount_source", return_value=str(store)):
        yield root / "home/dev", store / AGENT_SESSIONS_DIRNAME, str(store)


def test_rescue_copies_sessions_the_host_lacks(old_container, capsys):
    home, root, store = old_container
    (home / ".claude/projects/-workspace-demo").mkdir(parents=True)
    (home / ".claude/projects/-workspace-demo/s1.jsonl").write_text("old claude\n")
    (home / ".codex/sessions/2026/10/01").mkdir(parents=True)
    (home / ".codex/sessions/2026/10/01/rollout-1.jsonl").write_text("old codex\n")

    assert _rescue_agent_sessions("old", "dev", store) is True

    assert (root / "claude/projects/-workspace-demo/s1.jsonl").read_text() == "old claude\n"
    assert (root / "codex/sessions/2026/10/01/rollout-1.jsonl").read_text() == "old codex\n"
    assert "Saved Claude and Codex sessions from 'old'" in capsys.readouterr().out


def test_rescue_never_overwrites_the_host_and_keeps_the_difference(old_container):
    home, root, store = old_container
    (home / ".claude/projects/p").mkdir(parents=True)
    (home / ".claude/projects/p/a.jsonl").write_text("container a\n")
    (home / ".claude/projects/p/same.jsonl").write_text("same\n")
    (root / "claude/projects/p").mkdir(parents=True)
    (root / "claude/projects/p/a.jsonl").write_text("host a\n")
    (root / "claude/projects/p/same.jsonl").write_text("same\n")
    (root / "conflicts/claude/projects/p").mkdir(parents=True)
    (root / "conflicts/claude/projects/p/a.jsonl").write_text("earlier conflict\n")

    assert _rescue_agent_sessions("old", "dev", store) is True

    assert (root / "claude/projects/p/a.jsonl").read_text() == "host a\n"
    assert (root / "conflicts/claude/projects/p/a.jsonl").read_text() == "earlier conflict\n"
    assert (root / "conflicts/claude/projects/p/a.jsonl.~1~").read_text() == "container a\n"
    assert not (root / "conflicts/claude/projects/p/same.jsonl").exists()


def test_rescue_skips_a_container_that_is_already_linked(old_container, capsys):
    home, root, store = old_container
    (home / ".claude").mkdir(parents=True)
    os.symlink("/sdk-extensions/.agent-sessions/claude/projects", home / ".claude/projects")

    assert _rescue_agent_sessions("old", "dev", store) is True

    assert not root.exists()
    assert capsys.readouterr().out == ""


def test_rescue_appends_the_session_index_on_its_own_line(old_container):
    home, root, store = old_container
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/session_index.jsonl").write_text('{"id":"b"}\n')
    (root / "codex").mkdir(parents=True)
    (root / "codex/session_index.jsonl").write_text('{"id":"a"}')

    _rescue_agent_sessions("old", "dev", store)

    assert (root / "codex/session_index.jsonl").read_text() == '{"id":"a"}\n{"id":"b"}\n'


def test_rescue_reports_what_could_not_be_saved(old_container, capsys):
    home, _, store = old_container
    with patch("sima_cli.sdk.utils._docker_cp_out", return_value="error"):
        assert _rescue_agent_sessions("old", "dev", store) is False

    assert "Could not save ~/.claude/projects" in capsys.readouterr().out


def test_rescue_without_an_extensions_folder_warns_when_sessions_would_be_lost(old_container, capsys):
    home, _, _ = old_container
    assert _rescue_agent_sessions("old", "dev", "") is True

    (home / ".claude/projects/p").mkdir(parents=True)
    (home / ".claude/projects/p/s.jsonl").write_text("x\n")
    assert _rescue_agent_sessions("old", "dev", "") is False
    assert "no SDK extensions folder to save them to" in capsys.readouterr().out


def test_a_repeated_rescue_adds_nothing_twice(old_container):
    home, root, store = old_container
    (home / ".claude/projects/p").mkdir(parents=True)
    (home / ".claude/projects/p/a.jsonl").write_text("container a\n")
    (root / "claude/projects/p").mkdir(parents=True)
    (root / "claude/projects/p/a.jsonl").write_text("host a\n")
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/session_index.jsonl").write_text('{"id":"b"}\n')

    _rescue_agent_sessions("old", "dev", store)
    _rescue_agent_sessions("old", "dev", store)

    assert sorted(os.listdir(root / "conflicts/claude/projects/p")) == ["a.jsonl"]
    assert (root / "codex/session_index.jsonl").read_text() == '{"id":"b"}\n'


def test_a_dangling_link_on_the_host_does_not_swallow_the_container_copy(old_container):
    home, root, store = old_container
    (home / ".claude/projects/p").mkdir(parents=True)
    (home / ".claude/projects/p/a.jsonl").write_text("container a\n")
    (root / "claude/projects/p").mkdir(parents=True)
    os.symlink("/nowhere/a.jsonl", root / "claude/projects/p/a.jsonl")

    _rescue_agent_sessions("old", "dev", store)

    assert (root / "conflicts/claude/projects/p/a.jsonl").read_text() == "container a\n"


@pytest.mark.parametrize("stderr,expected", [
    ("Error response from daemon: Could not find the file /home/dev/.codex in container old", "missing"),
    ("Error: No such container:path: old:/home/dev/.codex", "missing"),
    ("Error response from daemon: No such container: old", "error"),
    ("permission denied", "error"),
])
def test_docker_cp_errors_are_told_apart_from_missing_paths(stderr, expected):
    from sima_cli.sdk.utils import _docker_cp_out

    with patch("sima_cli.sdk.utils.subprocess.run", return_value=Mock(returncode=1, stdout="", stderr=stderr)):
        assert _docker_cp_out("old", "/home/dev/.codex", "/tmp/x") == expected


def test_automated_removal_warns_loudly_when_sessions_could_not_be_saved():
    calls, run = _docker(["sdk-latest"])
    with patch("sima_cli.sdk.utils.subprocess.run", side_effect=run), \
         patch("sima_cli.sdk.utils.console") as console:
        confirm_to_remove_exiting_container("sdk-latest", yes_to_all=True, before_remove=lambda name: False)

    printed = " ".join(str(call.args[0]) for call in console.print.call_args_list)
    assert "deleted with it" in printed
    assert ["docker", "rm", "-f", "sdk-latest"] in calls


def _docker(existing_names):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return Mock(returncode=0, stdout="\n".join(existing_names))

    return calls, run


def test_removal_saves_sessions_first_when_automated():
    calls, run = _docker(["sdk-latest"])
    order = []
    with patch("sima_cli.sdk.utils.subprocess.run", side_effect=lambda cmd, **kw: (order.append(cmd[1]), run(cmd))[1]):
        result = confirm_to_remove_exiting_container(
            "sdk-latest", yes_to_all=True, before_remove=lambda name: order.append("rescue") or True
        )

    assert result is None
    assert order == ["ps", "rescue", "rm"]


def test_interactive_removal_keeps_the_container_when_sessions_could_not_be_saved():
    calls, run = _docker(["sdk-latest"])
    with patch("sima_cli.sdk.utils.subprocess.run", side_effect=run), \
         patch("builtins.input", side_effect=["", "n"]):
        result = confirm_to_remove_exiting_container("sdk-latest", before_remove=lambda name: False)

    assert result == "sdk-latest"
    assert not any(cmd[:2] == ["docker", "rm"] for cmd in calls)


def test_interactive_removal_can_go_ahead_anyway():
    calls, run = _docker(["sdk-latest"])
    with patch("sima_cli.sdk.utils.subprocess.run", side_effect=run), \
         patch("builtins.input", side_effect=["y", "y"]):
        result = confirm_to_remove_exiting_container("sdk-latest", before_remove=lambda name: False)

    assert result is None
    assert ["docker", "rm", "-f", "sdk-latest"] in calls


def _setup_existing(existing_container, minimal=False, running=True):
    from contextlib import ExitStack

    from sima_cli.sdk.install import setup_and_start

    image = "ghcr.io/sima-neat/sdk:latest"
    plain = [
        "sima_cli.sdk.neat.refresh_neat_certificates",
        "sima_cli.sdk.install.ensure_codex_vscode_extension_installed",
        "sima_cli.sdk.install.ensure_simasdkbridge_network",
        "sima_cli.sdk.install.syscheck",
        "sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk",
        "sima_cli.sdk.install.ensure_existing_neat_container_startable",
        "sima_cli.sdk.install.configure_container_user",
        "sima_cli.sdk.install._refresh_mpk_config_json",
    ]
    values = {
        "sima_cli.sdk.install.get_local_sima_images": [image],
        "sima_cli.sdk.install.prompt_image_selection": [image],
        "sima_cli.sdk.install.get_container_status": {},
        "sima_cli.sdk.install.get_workspace": "/tmp/workspace",
        "sima_cli.sdk.install._setup_devkit_share": None,
        "sima_cli.sdk.install._setup_sdk_extensions": "/tmp/extensions",
        "sima_cli.sdk.install.is_container_running": running,
        "sima_cli.sdk.install.check_os": "linux",
        "sima_cli.sdk.install.detect_current_user": ("devuser", 1000, 1000),
    }
    with ExitStack() as stack:
        for target in plain:
            stack.enter_context(patch(target))
        for target, value in values.items():
            stack.enter_context(patch(target, return_value=value))
        confirm = stack.enter_context(
            patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=existing_container)
        )
        rescue = stack.enter_context(patch("sima_cli.sdk.install._rescue_agent_sessions", return_value=True))
        link = stack.enter_context(patch("sima_cli.sdk.install._link_agent_sessions"))
        run = stack.enter_context(patch("sima_cli.sdk.install.subprocess.run", return_value=Mock(returncode=0)))

        setup_and_start(yes_to_all=True, noninteractive=True)
        started = [call.args[0] for call in run.mock_calls if call.args and call.args[0][:2] == ["docker", "start"]]
        run.reset_mock()
        rescue.side_effect = lambda *a: run.mock_calls.append("rescue") or True
        saved = confirm.call_args.kwargs["before_remove"]("sdk-latest")
        order = [call if call == "rescue" else call.args[0][:2] for call in run.mock_calls]
    return saved, rescue, link, order, started


def test_setup_saves_sessions_before_removing_a_neat_container():
    saved, rescue, _, order, _ = _setup_existing("sdk-latest")

    assert saved is True
    rescue.assert_called_once_with("sdk-latest", "devuser", "/tmp/extensions")
    # A running container is stopped first, so no agent appends a turn after it was copied.
    assert order == [["docker", "stop"], "rescue"]


def test_minimal_setup_still_rescues_sessions_into_the_default_host_store():
    from sima_cli.sdk.install import setup_and_start

    image = "ghcr.io/sima-neat/sdk:latest"
    with patch("sima_cli.sdk.install.ensure_simasdkbridge_network"), \
         patch("sima_cli.sdk.install.syscheck"), \
         patch("sima_cli.sdk.install.get_local_sima_images", return_value=[image]), \
         patch("sima_cli.sdk.install.prompt_image_selection", return_value=[image]), \
         patch("sima_cli.sdk.install.ensure_colima_resources_for_neat_sdk"), \
         patch("sima_cli.sdk.install.get_container_status", return_value={}), \
         patch("sima_cli.sdk.install.get_workspace", return_value="/tmp/workspace"), \
         patch("sima_cli.sdk.install._setup_devkit_share", return_value=None), \
         patch("sima_cli.sdk.install._setup_sdk_extensions", return_value="/tmp/extensions"), \
         patch("sima_cli.sdk.install.confirm_to_remove_exiting_container", return_value=None) as confirm, \
         patch("sima_cli.sdk.install.start_docker_container"), \
         patch("sima_cli.sdk.install.is_container_running", return_value=False), \
         patch("sima_cli.sdk.install.check_os", return_value="linux"), \
         patch("sima_cli.sdk.install.detect_current_user", return_value=("devuser", 1000, 1000)), \
         patch("sima_cli.sdk.install.subprocess.run", return_value=Mock(returncode=0)):
        setup_and_start(yes_to_all=True, noninteractive=True, minimal=True)
        with patch("sima_cli.sdk.install._rescue_agent_sessions", return_value=True) as rescue, \
             patch("sima_cli.sdk.install.is_container_running", return_value=False):
            confirm.call_args.kwargs["before_remove"]("sdk-latest")

    default_store = os.path.join(os.path.expanduser("~"), "sima-sdk-extensions")
    rescue.assert_called_once_with("sdk-latest", "devuser", default_store)


def test_setup_links_sessions_in_a_reused_container():
    _, _, link, _, _ = _setup_existing("sdk-latest")

    link.assert_called_once_with("sdk-latest", "devuser", 1000, 1000)


def test_a_linked_container_whose_store_moved_brings_its_sessions_along(old_container, tmp_path):
    home, root, store = old_container
    previous = tmp_path / "previous-extensions"
    (previous / ".agent-sessions/claude/projects/p").mkdir(parents=True)
    (previous / ".agent-sessions/claude/projects/p/s.jsonl").write_text("from the old store\n")
    (home / ".claude").mkdir(parents=True)
    os.symlink("/sdk-extensions/.agent-sessions/claude/projects", home / ".claude/projects")

    with patch("sima_cli.sdk.utils._mount_source", return_value=str(previous)):
        assert _rescue_agent_sessions("old", "dev", store) is True

    assert (root / "claude/projects/p/s.jsonl").read_text() == "from the old store\n"
    assert (previous / ".agent-sessions/claude/projects/p/s.jsonl").exists()


@pytest.mark.parametrize("mount", ["same", ""])
def test_a_linked_container_on_the_same_or_an_unknown_store_needs_nothing(old_container, mount):
    home, root, store = old_container
    (home / ".claude").mkdir(parents=True)
    os.symlink("/sdk-extensions/.agent-sessions/claude/projects", home / ".claude/projects")

    with patch("sima_cli.sdk.utils._mount_source", return_value=store if mount == "same" else ""):
        assert _rescue_agent_sessions("old", "dev", store) is True

    assert not root.exists()


def test_a_foreign_link_is_never_followed_into_the_old_store(old_container, tmp_path):
    home, root, store = old_container
    previous = tmp_path / "previous-extensions"
    (previous / "elsewhere").mkdir(parents=True)
    (previous / "elsewhere/secret.jsonl").write_text("not a session\n")
    (home / ".claude").mkdir(parents=True)
    os.symlink("/sdk-extensions/../elsewhere", home / ".claude/projects")

    with patch("sima_cli.sdk.utils._mount_source", return_value=str(previous)):
        assert _rescue_agent_sessions("old", "dev", store) is True

    assert not root.exists()


def test_an_already_stopped_container_is_not_stopped_again():
    _, _, _, order, _ = _setup_existing("sdk-latest", running=False)

    assert order == ["rescue"]


def test_a_kept_stopped_container_is_started_again():
    # After a failed rescue the user may keep the container; setup then reuses and starts it.
    _, _, _, _, started = _setup_existing("sdk-latest", running=False)

    assert started == [["docker", "start", "sdk-latest"]]
