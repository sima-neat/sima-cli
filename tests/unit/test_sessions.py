"""`sima-cli sessions`: Claude transcripts, the Codex app-server client, and the command itself.

Codex is exercised through a fake `codex` executable that speaks the same newline-delimited JSON-RPC
over stdio, so the client is tested end to end without a real Codex install.
"""

import json
import os
import stat
import sys
import textwrap

import pytest
from click.testing import CliRunner

from sima_cli.sessions import claude, codex, commands
from sima_cli.sessions.model import Session, SessionError, pick, relative_time

FAKE_CODEX = textwrap.dedent(
    """
    import json, os, sys

    state = json.load(open(os.environ["FAKE_CODEX_STATE"]))
    verb = sys.argv[1]
    if verb in ("archive", "unarchive"):
        with open(os.environ["FAKE_CODEX_LOG"], "a") as log:
            log.write(verb + " " + sys.argv[2] + "\\n")
        if state.get("refuse"):
            print("WARNING: proceeding anyway", file=sys.stderr)
            print("Error: No active session found matching '" + sys.argv[2] + "'.", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    if verb == "resume":
        sys.exit(0)
    assert verb == "app-server"
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        if state.get("silent"):
            continue
        if message["method"] == "initialize":
            result = {"userAgent": "fake"}
        elif state.get("error"):
            print(json.dumps({"id": message["id"], "error": {"message": state["error"]}}), flush=True)
            continue
        else:
            params = message["params"]
            threads = state["archived" if params.get("archived") else "active"]
            start = int(params.get("cursor") or 0)
            page = threads[start:start + 1]
            more = start + 1 < len(threads)
            result = {"data": page, "nextCursor": str(start + 1) if more else None}
        print("not json", flush=True)
        print(json.dumps({"method": "noise"}), flush=True)
        print(json.dumps({"id": message["id"], "result": result}), flush=True)
    """
)


def runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def write_transcript(path, records, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def user(text, cwd="/workspace/demo", **extra):
    record = {"type": "user", "cwd": cwd, "gitBranch": "main", "message": {"role": "user", "content": text}}
    record.update(extra)
    return record


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    return home


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / "codex"
    executable.write_text(f"#!{sys.executable}\n{FAKE_CODEX}")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    state_file = tmp_path / "codex-state.json"
    log_file = tmp_path / "codex-calls.log"
    monkeypatch.setenv("FAKE_CODEX_STATE", str(state_file))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_file))

    def configure(active=(), archived=(), **flags):
        state_file.write_text(json.dumps({"active": list(active), "archived": list(archived), **flags}))
        return str(executable), log_file

    return configure


def thread(thread_id, cwd="/workspace/demo", **extra):
    record = {"id": thread_id, "cwd": cwd, "preview": f"prompt of {thread_id}", "name": None,
              "gitInfo": {"branch": "dev"}, "updatedAt": 1_700_000_000}
    record.update(extra)
    return record


# --- Claude transcripts ----------------------------------------------------------------------------

def test_claude_title_prefers_custom_then_ai_then_first_prompt(home):
    projects = home / ".claude/projects/-workspace-demo"
    custom = write_transcript(projects / "a.jsonl", [user("first"), {"type": "ai-title", "aiTitle": "AI"},
                                                     {"type": "custom-title", "customTitle": "Mine"}])
    ai = write_transcript(projects / "b.jsonl", [user("first"), {"type": "ai-title", "aiTitle": "AI"}])
    prompt = write_transcript(projects / "c.jsonl", [user("  fix the\n  pipeline  ")])

    assert claude.read_session(str(custom)).title == "Mine"
    assert claude.read_session(str(ai)).title == "AI"
    assert claude.read_session(str(prompt)).title == "fix the pipeline"


def test_claude_reads_id_folder_and_branch(home):
    path = write_transcript(home / ".claude/projects/-x/1234-abcd.jsonl", [user("hi", cwd="/w/x")], mtime=1000)

    session = claude.read_session(str(path))

    assert (session.agent, session.id, session.cwd, session.branch, session.updated) == \
        ("claude", "1234-abcd", "/w/x", "main", 1000)


def test_claude_skips_meta_and_command_prompts(home):
    path = write_transcript(home / ".claude/projects/-x/s.jsonl", [
        user("caveat", isMeta=True),
        user("<command-name>/clear</command-name>"),
        user([{"type": "text", "text": "real question"}]),
    ])

    assert claude.read_session(str(path)).title == "real question"


def test_claude_hides_sessions_nobody_typed_into_and_survives_garbage(home):
    projects = home / ".claude/projects/-x"
    write_transcript(projects / "empty.jsonl", [{"type": "permission-mode", "permissionMode": "default"}])
    (projects / "broken.jsonl").write_text('{"type": "user", "cwd": \nnot json\n')
    write_transcript(projects / "ok.jsonl", [user("hello")])

    assert [session.id for session in claude.list_sessions()] == ["ok"]


def test_claude_long_titles_are_cut(home):
    path = write_transcript(home / ".claude/projects/-x/s.jsonl", [user("word " * 50)])

    title = claude.read_session(str(path)).title

    assert len(title) == claude.TITLE_LIMIT and title.endswith("…")


def test_claude_honours_claude_config_dir(home, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "custom"))
    write_transcript(tmp_path / "custom/projects/-x/s.jsonl", [user("hello")])

    assert [session.id for session in claude.list_sessions()] == ["s"]


def test_claude_archive_moves_transcript_and_subagents_and_back(home):
    projects = home / ".claude/projects/-x"
    write_transcript(projects / "s.jsonl", [user("hello")])
    (projects / "s/subagents").mkdir(parents=True)
    (projects / "s/subagents/agent-1.jsonl").write_text("{}\n")

    claude.archive("s")

    archived = home / ".claude/archived_projects/-x"
    assert (archived / "s.jsonl").is_file() and (archived / "s/subagents/agent-1.jsonl").is_file()
    assert not (projects / "s.jsonl").exists()
    assert [session.id for session in claude.list_sessions(archived=True)] == ["s"]
    assert claude.list_sessions() == []

    claude.unarchive("s")

    assert (projects / "s.jsonl").is_file() and (projects / "s/subagents/agent-1.jsonl").is_file()


def test_claude_archive_lands_next_to_the_real_projects_folder(home, tmp_path):
    host = tmp_path / "sdk-extensions/.agent-sessions/claude/projects"
    write_transcript(host / "-x/s.jsonl", [user("hello")])
    (home / ".claude").mkdir()
    os.symlink(host, home / ".claude/projects")

    claude.archive("s")

    assert (tmp_path / "sdk-extensions/.agent-sessions/claude/archived_projects/-x/s.jsonl").is_file()


def test_claude_archive_never_overwrites(home):
    write_transcript(home / ".claude/projects/-x/s.jsonl", [user("active")])
    write_transcript(home / ".claude/archived_projects/-x/s.jsonl", [user("older")])

    with pytest.raises(SessionError, match="already exists"):
        claude.archive("s")
    assert "active" in (home / ".claude/projects/-x/s.jsonl").read_text()


def test_claude_cli_found_on_path_before_the_extension(home, tmp_path):
    bundled = tmp_path / "home/.openvscode-server/extensions/anthropic.claude-code-2.1.266-linux-x64/resources/native-binary/claude"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)

    assert claude.find_cli() == str(bundled)

    on_path = tmp_path / "bin/claude"
    on_path.parent.mkdir(exist_ok=True)
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(0o755)

    assert claude.find_cli() == str(on_path)


def test_claude_resume_without_any_cli_explains_what_to_do(home):
    with pytest.raises(SessionError, match="Claude VS Code extension"):
        claude.resume_command(Session("claude", "s", "t", "/w", "", 0))


# --- Codex app-server client -----------------------------------------------------------------------

def test_codex_lists_every_page_and_prefers_the_name(fake_codex):
    cli, _ = fake_codex(active=[thread("a"), thread("b", name="Named"), thread("c", gitInfo=None)])

    sessions = codex.list_sessions(cli)

    assert [s.id for s in sessions] == ["a", "b", "c"]
    assert [s.title for s in sessions] == ["prompt of a", "Named", "prompt of c"]
    assert [s.branch for s in sessions] == ["dev", "dev", ""]


def test_codex_lists_archived_sessions(fake_codex):
    cli, _ = fake_codex(active=[thread("a")], archived=[thread("z")])

    sessions = codex.list_sessions(cli, archived=True)

    assert [(s.id, s.archived) for s in sessions] == [("z", True)]


def test_codex_refusal_is_reported(fake_codex):
    cli, _ = fake_codex(error="boom")

    with pytest.raises(SessionError, match="Codex refused thread/list: boom"):
        codex.list_sessions(cli)


def test_codex_silence_times_out(fake_codex):
    cli, _ = fake_codex(silent=True)
    server = codex._AppServer(cli, timeout=0.5)
    try:
        with pytest.raises(SessionError, match="did not answer"):
            server.call("initialize", {})
    finally:
        server.close()


def test_codex_archive_uses_codex_and_reports_its_error(fake_codex):
    cli, log = fake_codex()
    codex.archive(cli, "a")
    codex.unarchive(cli, "a")
    assert log.read_text() == "archive a\nunarchive a\n"

    cli, _ = fake_codex(refuse=True)
    with pytest.raises(SessionError, match="No active session found"):
        codex.archive(cli, "a")


# --- picking and formatting ------------------------------------------------------------------------

SESSIONS = [
    Session("claude", "1111-aaaa", "Fix pipeline", "/w", "", 3),
    Session("codex", "1111-bbbb", "Add RTSP", "/w", "", 2),
    Session("codex", "2222-cccc", "Add RTSP", "/w", "", 1),
]


@pytest.mark.parametrize("selector,expected", [("1", "1111-aaaa"), ("3", "2222-cccc"),
                                               ("1111-bbbb", "1111-bbbb"), ("2222", "2222-cccc"),
                                               ("Fix pipeline", "1111-aaaa")])
def test_pick_accepts_row_id_prefix_and_title(selector, expected):
    assert pick(SESSIONS, selector).id == expected


@pytest.mark.parametrize("selector,message", [("1111", "matches 2 sessions"), ("Add RTSP", "2 sessions are titled"),
                                              ("9", "No session matches"), ("nope", "No session matches")])
def test_pick_refuses_ambiguous_or_unknown(selector, message):
    with pytest.raises(SessionError, match=message):
        pick(SESSIONS, selector)


def test_relative_time():
    from datetime import datetime

    now = datetime(2026, 10, 22, 15, 0)
    assert relative_time(datetime(2026, 10, 22, 14, 2).timestamp(), now) == "today 14:02"
    assert relative_time(datetime(2026, 10, 21, 18, 40).timestamp(), now) == "yesterday 18:40"
    assert relative_time(datetime(2026, 10, 1, 9, 5).timestamp(), now) == "2026-10-01 09:05"


# --- the command ------------------------------------------------------------------------------------

@pytest.fixture
def workspace(home, tmp_path, monkeypatch):
    here = tmp_path / "demo"
    here.mkdir()
    monkeypatch.chdir(here)
    write_transcript(home / ".claude/projects/-demo/here.jsonl", [user("in this folder", cwd=str(here))], mtime=2000)
    write_transcript(home / ".claude/projects/-other/there.jsonl", [user("elsewhere", cwd="/other")], mtime=1000)
    return here


def test_list_shows_only_the_current_folder_and_warns_without_codex(workspace):
    result = runner().invoke(commands.sessions, ["list"])

    assert result.exit_code == 0, result.output
    assert "in this folder" in result.stdout and "elsewhere" not in result.stdout
    assert "Codex CLI was not found" in result.stderr


def test_bare_sessions_lists(workspace):
    result = runner().invoke(commands.sessions, [])

    assert result.exit_code == 0 and "in this folder" in result.stdout


def test_list_all_merges_agents_newest_first(workspace, fake_codex):
    fake_codex(active=[thread("cx", cwd="/elsewhere", updatedAt=3000)])

    result = runner().invoke(commands.sessions, ["list", "--all", "--json"])

    document = json.loads(result.stdout)
    assert [s["id"] for s in document["sessions"]] == ["cx", "here", "there"]
    assert document["sessions"][0]["agent"] == "codex" and document["warnings"] == []


def test_list_agent_filter(workspace, fake_codex):
    fake_codex(active=[thread("cx", cwd=str(workspace))])

    document = json.loads(runner().invoke(commands.sessions, ["list", "--agent", "codex", "--json"]).stdout)

    assert [s["id"] for s in document["sessions"]] == ["cx"]


def test_list_empty_folder_suggests_all(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner().invoke(commands.sessions, ["list", "--agent", "claude"])

    assert "No sessions in this folder. Use --all" in result.stdout


def test_resume_claude_runs_in_the_session_folder(workspace, tmp_path, monkeypatch):
    cli = tmp_path / "bin/claude"
    cli.parent.mkdir(exist_ok=True)
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    calls = []
    monkeypatch.setattr(commands.os, "execvp", lambda file, args: calls.append((os.getcwd(), args)))

    result = runner().invoke(commands.sessions, ["resume", "1", "--agent", "claude"])

    assert result.exit_code == 0, result.output
    assert calls == [(str(workspace), [str(cli), "--resume", "here"])]


def test_resume_codex(workspace, fake_codex, monkeypatch):
    cli, _ = fake_codex(active=[thread("cx", cwd=str(workspace), updatedAt=9999)])
    calls = []
    monkeypatch.setattr(commands.os, "execvp", lambda file, args: calls.append(args))

    result = runner().invoke(commands.sessions, ["resume", "cx"])

    assert result.exit_code == 0, result.output
    assert calls == [[cli, "resume", "cx"]]


def test_resume_unknown_session_fails(workspace):
    result = runner().invoke(commands.sessions, ["resume", "nope", "--agent", "claude"])

    assert result.exit_code == 1 and "No session matches 'nope'" in result.stderr


def test_archive_and_unarchive_a_claude_session(workspace, home):
    archived = runner().invoke(commands.sessions, ["archive", "1", "--agent", "claude"])
    assert archived.exit_code == 0 and "Archived claude session here" in archived.stdout
    assert "No sessions" in runner().invoke(commands.sessions, ["list", "--agent", "claude"]).stdout

    restored = runner().invoke(commands.sessions, ["unarchive", "1", "--agent", "claude"])
    assert restored.exit_code == 0 and "Restored claude session here" in restored.stdout
    assert (home / ".claude/projects/-demo/here.jsonl").is_file()


def test_archive_a_codex_session_goes_through_codex(workspace, fake_codex):
    _, log = fake_codex(active=[thread("cx", cwd=str(workspace))])

    result = runner().invoke(commands.sessions, ["archive", "cx"])

    assert result.exit_code == 0, result.output
    assert log.read_text() == "archive cx\n"


def test_through_the_root_cli_json_stdout_carries_only_the_document(workspace, monkeypatch):
    """The root banner goes to stderr and no update check runs: the output feeds scripts and skills."""
    from sima_cli import cli as root_cli

    monkeypatch.setattr(root_cli, "get_environment_type", lambda: ("sdk", "neat"))

    def no_update_check(package_name):
        raise AssertionError("the update check ran before a sessions command")

    monkeypatch.setattr(root_cli, "check_for_update", no_update_check)
    monkeypatch.setenv("SIMA_CLI_INTERNAL", "0")

    result = runner().invoke(root_cli.main, ["sessions", "list", "--agent", "claude", "--json"])

    assert result.exit_code == 0, result.output
    assert [s["id"] for s in json.loads(result.stdout)["sessions"]] == ["here"]
    assert "Environment: sdk (neat)" in result.stderr
