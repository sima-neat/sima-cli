"""Codex sessions, asked of Codex itself through `codex app-server`.

Codex keeps its session index in a private SQLite database next to its rollout files, so the
listing goes through the JSON-RPC interface Codex exposes for clients instead of reading either.
Archiving goes through Codex's own commands for the same reason.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from sima_cli.__version__ import __version__

from .model import Session, SessionError

AGENT = "codex"
# The interactive sources, which is what `codex resume` offers by default.
SOURCE_KINDS = ["cli", "vscode"]
PAGE_SIZE = 100
TIMEOUT_SECONDS = 20.0
TITLE_LIMIT = 80


def find_cli() -> Optional[str]:
    return shutil.which("codex")


class _AppServer:
    """One `codex app-server` process, spoken to over newline-delimited JSON-RPC on stdio."""

    def __init__(self, cli: str, timeout: float = TIMEOUT_SECONDS):
        self._timeout = timeout
        self._next_id = 0
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        try:
            self._proc = subprocess.Popen(
                [cli, "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise SessionError(f"Could not start `codex app-server`: {error}") from error
        # A reader thread rather than select(): select() cannot wait on pipes on Windows.
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, message: Dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except OSError as error:
            raise SessionError(f"`codex app-server` stopped accepting requests: {error}") from error

    def notify(self, method: str) -> None:
        self._send({"method": method})

    def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SessionError(f"`codex app-server` did not answer {method} in {self._timeout:.0f}s.")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise SessionError(f"`codex app-server` exited before answering {method}.")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if message.get("error"):
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise SessionError(f"Codex refused {method}: {detail}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def _one_line(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LIMIT else line[: TITLE_LIMIT - 1] + "…"


def _session(thread: Dict[str, Any], archived: bool) -> Optional[Session]:
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or not thread_id:
        return None
    title = thread.get("name") or thread.get("preview") or ""
    git = thread.get("gitInfo") if isinstance(thread.get("gitInfo"), dict) else {}
    branch = git.get("branch") or ""
    updated = thread.get("updatedAt") or thread.get("createdAt") or 0
    return Session(
        AGENT,
        thread_id,
        _one_line(title if isinstance(title, str) else ""),
        thread.get("cwd") if isinstance(thread.get("cwd"), str) else "",
        branch if isinstance(branch, str) else "",
        float(updated) if isinstance(updated, (int, float)) else 0.0,
        archived,
    )


def list_sessions(cli: str, archived: bool = False) -> List[Session]:
    server = _AppServer(cli)
    try:
        server.call("initialize", {"clientInfo": {"name": "sima-cli", "version": __version__}})
        server.notify("initialized")
        sessions: List[Session] = []
        cursor = None
        while True:
            params: Dict[str, Any] = {"sourceKinds": SOURCE_KINDS, "limit": PAGE_SIZE}
            if archived:
                params["archived"] = True
            if cursor:
                params["cursor"] = cursor
            page = server.call("thread/list", params)
            for thread in page.get("data") or []:
                if isinstance(thread, dict):
                    session = _session(thread, archived)
                    if session is not None:
                        sessions.append(session)
            cursor = page.get("nextCursor")
            if not cursor:
                return sessions
    finally:
        server.close()


def _run(cli: str, verb: str, session_id: str) -> None:
    result = subprocess.run([cli, verb, session_id], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        lines = [line for line in (result.stderr or result.stdout or "").splitlines()
                 if line.strip() and not line.startswith("WARNING:")]
        detail = lines[-1] if lines else f"exit code {result.returncode}"
        raise SessionError(f"`codex {verb}` failed: {detail}")


def archive(cli: str, session_id: str) -> None:
    _run(cli, "archive", session_id)


def unarchive(cli: str, session_id: str) -> None:
    _run(cli, "unarchive", session_id)


def resume_command(cli: str, session: Session) -> List[str]:
    return [cli, "resume", session.id]
