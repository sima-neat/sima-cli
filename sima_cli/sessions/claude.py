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

from .model import Session, SessionError

AGENT = "claude"
TITLE_LIMIT = 80


def config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


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


def _one_line(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LIMIT else line[: TITLE_LIMIT - 1] + "…"


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
    return Session(AGENT, session_id, _one_line(title), cwd, branch, updated, archived)


def _transcripts(root: str) -> Iterable[str]:
    return sorted(glob.glob(os.path.join(glob.escape(root), "*", "*.jsonl")))


def list_sessions(archived: bool = False) -> List[Session]:
    root = archive_dir() if archived else projects_dir()
    sessions = (read_session(path, archived) for path in _transcripts(root))
    return [session for session in sessions if session is not None]


def _find_transcript(root: str, session_id: str) -> Optional[str]:
    for path in _transcripts(root):
        if os.path.splitext(os.path.basename(path))[0] == session_id:
            return path
    return None


def _move(session_id: str, source_root: str, target_root: str) -> str:
    transcript = _find_transcript(source_root, session_id)
    if transcript is None:
        raise SessionError(f"Claude session {session_id} was not found in {source_root}.")
    project = os.path.basename(os.path.dirname(transcript))
    target_project = os.path.join(target_root, project)
    target = os.path.join(target_project, os.path.basename(transcript))
    if os.path.exists(target):
        raise SessionError(f"{target} already exists; nothing was moved.")
    os.makedirs(target_project, exist_ok=True)
    shutil.move(transcript, target)
    # Claude keeps a session's subagent transcripts in a folder named after it.
    companion = os.path.splitext(transcript)[0]
    if os.path.isdir(companion) and not os.path.exists(os.path.join(target_project, session_id)):
        shutil.move(companion, os.path.join(target_project, session_id))
    return target


def archive(session_id: str) -> str:
    return _move(session_id, projects_dir(), archive_dir())


def unarchive(session_id: str) -> str:
    return _move(session_id, archive_dir(), projects_dir())


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
        return on_path
    bundled = []
    for directory in _extension_dirs():
        pattern = os.path.join(glob.escape(directory), "anthropic.claude-code-*", "resources", "native-binary", "claude")
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
