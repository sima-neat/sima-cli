"""The agent-neutral view of a session and how a user picks one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence


class SessionError(Exception):
    """A failure the user can act on; the message is shown as is."""


@dataclass
class Session:
    agent: str
    id: str
    title: str
    cwd: str
    branch: str
    updated: float
    archived: bool = False

    def as_json(self) -> dict:
        return {
            "agent": self.agent,
            "id": self.id,
            "title": self.title,
            "cwd": self.cwd,
            "branch": self.branch,
            "updated_at": datetime.fromtimestamp(self.updated, tz=timezone.utc).isoformat(),
            "archived": self.archived,
        }


def same_folder(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a.rstrip("/") == b.rstrip("/")


def newest_first(sessions: Sequence[Session]) -> List[Session]:
    return sorted(sessions, key=lambda session: session.updated, reverse=True)


def pick(sessions: Sequence[Session], selector: str) -> Session:
    """Resolve what the user typed: a row number from `list`, an id, an id prefix or a title."""
    selector = selector.strip()
    if selector.isdigit() and 1 <= int(selector) <= len(sessions):
        return sessions[int(selector) - 1]

    exact = [session for session in sessions if session.id == selector]
    if exact:
        return exact[0]
    if len(selector) >= 4:
        prefixed = [session for session in sessions if session.id.startswith(selector)]
        if len(prefixed) == 1:
            return prefixed[0]
        if len(prefixed) > 1:
            raise SessionError(f"'{selector}' matches {len(prefixed)} sessions; use more of the id.")
    titled = [session for session in sessions if session.title == selector]
    if len(titled) == 1:
        return titled[0]
    if len(titled) > 1:
        raise SessionError(f"{len(titled)} sessions are titled '{selector}'; use the row number or the id.")
    raise SessionError(f"No session matches '{selector}'. Run `sima-cli sessions list` to see them.")


def relative_time(updated: float, now: Optional[datetime] = None) -> str:
    moment = datetime.fromtimestamp(updated)
    now = now or datetime.now()
    days = (now.date() - moment.date()).days
    if days == 0:
        return f"today {moment:%H:%M}"
    if days == 1:
        return f"yesterday {moment:%H:%M}"
    return f"{moment:%Y-%m-%d %H:%M}"
