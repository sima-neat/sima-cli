"""Claude Code sessions, read from the transcripts Claude writes under ~/.claude/projects.

Claude offers no listing interface, so the transcripts are read directly. Their format is internal
and unversioned: every record is optional, unknown records are skipped, and a file that cannot be
read is left out rather than failing the listing. Nothing here writes into a transcript; archiving
moves the file, which is the only change this module makes.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
from typing import Iterable, List, Optional

from .model import Session, SessionError, one_line

AGENT = "claude"


def config_dir() -> str:
    # CLAUDE_CONFIG_DIR is what Claude itself honours; CLAUDE_HOME is the name the playbooks
    # manager uses for the same folder, so both are respected.
    for variable in ("CLAUDE_CONFIG_DIR", "CLAUDE_HOME"):
        value = os.environ.get(variable)
        if value:
            return value
    return os.path.join(os.path.expanduser("~"), ".claude")


def projects_dir() -> str:
    return os.path.join(config_dir(), "projects")


def archive_dir() -> str:
    # Beside the real projects folder, so an archived session lives on the host whenever the
    # projects folder is a symlink to it and survives an SDK upgrade with the rest.
    return os.path.join(os.path.dirname(os.path.realpath(projects_dir())), "archived_projects")


def _prompt_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def read_session(path: str, archived: bool = False) -> Optional[Session]:
    custom_title = ai_title = first_prompt = cwd = branch = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                wanted = '"custom-title"' in line or '"ai-title"' in line
                if not first_prompt and '"user"' in line:
                    wanted = True
                if not cwd and '"cwd"' in line:
                    wanted = True
                if not wanted:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                kind = record.get("type")
                if kind == "custom-title" and isinstance(record.get("customTitle"), str):
                    custom_title = record["customTitle"]
                elif kind == "ai-title" and isinstance(record.get("aiTitle"), str):
                    ai_title = record["aiTitle"]
                if not cwd and isinstance(record.get("cwd"), str):
                    cwd = record["cwd"]
                    branch = record.get("gitBranch") if isinstance(record.get("gitBranch"), str) else ""
                if (not first_prompt and kind == "user" and not record.get("isMeta")
                        and not record.get("isSidechain")):
                    text = _prompt_text(record.get("message")).strip()
                    if text and not text.startswith("<"):
                        first_prompt = text
        updated = os.path.getmtime(path)
    except OSError:
        return None

    title = custom_title or ai_title or first_prompt
    if not title:
        # Claude hides sessions nobody typed into, and so does this listing.
        return None
    session_id = os.path.splitext(os.path.basename(path))[0]
    return Session(AGENT, session_id, one_line(title), cwd, branch, updated, archived, path)


def _transcripts(root: str) -> Iterable[str]:
    return sorted(glob.glob(os.path.join(glob.escape(root), "*", "*.jsonl")))


def list_sessions(archived: bool = False) -> List[Session]:
    root = archive_dir() if archived else projects_dir()
    sessions = (read_session(path, archived) for path in _transcripts(root))
    return [session for session in sessions if session is not None]


def _move(session: Session, target_root: str) -> str:
    """Move exactly the picked transcript, and its subagent folder, under target_root."""
    transcript = session.path
    if not transcript or not os.path.isfile(transcript):
        raise SessionError(f"The transcript of Claude session {session.id} is gone.")
    project = os.path.basename(os.path.dirname(transcript))
    target_project = os.path.join(target_root, project)
    target = os.path.join(target_project, os.path.basename(transcript))
    # Claude keeps a session's subagent transcripts in a folder named after it.
    companion = os.path.splitext(transcript)[0]
    target_companion = os.path.join(target_project, os.path.basename(companion))
    has_companion = os.path.isdir(companion)
    for path in (target, target_companion) if has_companion else (target,):
        if os.path.exists(path):
            raise SessionError(f"{path} already exists; nothing was moved.")

    os.makedirs(target_project, exist_ok=True)
    if has_companion:
        shutil.move(companion, target_companion)
    try:
        shutil.move(transcript, target)
    except OSError:
        if has_companion:
            shutil.move(target_companion, companion)
        raise
    return target


def archive(session: Session) -> str:
    return _move(session, archive_dir())


def unarchive(session: Session) -> str:
    return _move(session, projects_dir())


def _extension_dirs() -> List[str]:
    home = os.path.expanduser("~")
    candidates = [
        os.environ.get("OPENVSCODE_SERVER_EXTENSIONS_DIR", ""),
        os.environ.get("CODE_SERVER_EXTENSIONS_DIR", ""),
        os.path.join(home, ".openvscode-server", "extensions"),
        os.path.join(home, ".local", "share", "code-server", "extensions"),
        "/opt/openvscode-server/extensions",
        os.path.join(home, ".vscode-server", "extensions"),
        os.path.join(home, ".vscode", "extensions"),
    ]
    seen, dirs = set(), []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            dirs.append(candidate)
    return dirs


def find_cli() -> Optional[str]:
    """The `claude` command, or the one bundled with the Claude VS Code extension.

    The Neat SDK image does not ship the Claude CLI; `sima-cli sdk setup` installs the VS Code
    extension instead, and that extension carries its own native binary.
    """
    on_path = shutil.which("claude")
    if on_path:
        return os.path.abspath(on_path)
    bundled = []
    for directory in _extension_dirs():
        pattern = os.path.join(
            glob.escape(directory), "anthropic.claude-code-*", "resources", "native-binary", "claude"
        )
        bundled.extend(path for path in glob.glob(pattern) if os.access(path, os.X_OK))
    if not bundled:
        return None
    return max(bundled, key=os.path.getmtime)


def resume_command(session: Session) -> List[str]:
    cli = find_cli()
    if cli is None:
        raise SessionError(
            "The Claude CLI was not found, neither on PATH nor in the Claude VS Code extension. "
            "Open the session from the Claude extension, or install the CLI."
        )
    return [cli, "--resume", session.id]
