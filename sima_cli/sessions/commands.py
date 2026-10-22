"""`sima-cli sessions`: list, resume and archive Claude and Codex sessions from one place.

Each agent keeps its own session list, reachable only from its own CLI. This command puts both in
one list for the current folder and hands off to the agent's native command to resume, so no
session format of our own exists and nothing is converted between agents.

Row numbers are always counted over both agents, so `--agent` filters what is shown without
renumbering it: the number a user reads in `list` stays the number `archive` acts on.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional, Tuple

import click
from tabulate import tabulate

from . import claude, codex
from .model import Session, SessionError, newest_first, pick, relative_time, same_folder

AGENT_MODULES = {claude.AGENT: claude, codex.AGENT: codex}
JSON_SCHEMA_VERSION = "1.0"

Row = Tuple[int, Session]


def _collect(all_folders: bool, archived: bool, agent: Optional[str]) -> Tuple[List[Session], List[str]]:
    sessions: List[Session] = list(claude.list_sessions(archived))
    warnings: List[str] = []
    try:
        sessions.extend(codex.list_sessions(archived))
    except SessionError as error:
        # Codex is still listed for numbering; its absence only matters if Codex was asked for.
        if agent in (None, codex.AGENT):
            warnings.append(f"{error} Codex sessions are not listed.")
    if not all_folders:
        here = os.getcwd()
        sessions = [session for session in sessions if same_folder(session.cwd, here)]
    return newest_first(sessions), warnings


def _rows(sessions: List[Session], agent: Optional[str]) -> List[Row]:
    return [(number, session) for number, session in enumerate(sessions, 1)
            if agent in (None, session.agent)]


def _fail(message: str) -> None:
    click.echo(f"Error: {message}", err=True)
    sys.exit(1)


def _select(selector: str, all_folders: bool, archived: bool, agent: Optional[str]) -> Session:
    sessions, warnings = _collect(all_folders, archived, agent)
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)
    try:
        if selector.strip().isdigit():
            if not 1 <= int(selector) <= len(sessions):
                raise SessionError(f"There is no row {selector.strip()}. Run `sima-cli sessions list` to see them.")
            chosen = pick(sessions, selector)
            if agent not in (None, chosen.agent):
                raise SessionError(f"Row {selector.strip()} is a {chosen.agent} session, not {agent}.")
            return chosen
        return pick([session for _, session in _rows(sessions, agent)], selector)
    except SessionError as error:
        _fail(str(error))
        raise AssertionError("unreachable")


def _common(func):
    func = click.option("--agent", type=click.Choice(sorted(AGENT_MODULES)), default=None,
                        help="Only sessions of this agent (row numbers stay the same).")(func)
    func = click.option("--all", "all_folders", is_flag=True,
                        help="Sessions from every folder, not just the current one.")(func)
    return func


def _show(all_folders: bool, archived: bool, agent: Optional[str], as_json: bool) -> None:
    sessions, warnings = _collect(all_folders, archived, agent)
    rows = _rows(sessions, agent)
    if as_json:
        document = {
            "schema_version": JSON_SCHEMA_VERSION,
            "command": "sessions.list",
            "result": "ok",
            "sessions": [dict(session.as_json(), number=number) for number, session in rows],
            "warnings": warnings,
        }
        click.echo(json.dumps(document, indent=2))
        return
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)
    if not rows:
        where = "anywhere" if all_folders else "in this folder"
        hint = "" if all_folders else " Use --all to see sessions from every folder."
        kind = "archived sessions" if archived else "sessions"
        click.echo(f"No {kind} {where}.{hint}")
        return
    headers = ["#", "AGENT", "TITLE", "BRANCH", "LAST ACTIVE"]
    if all_folders:
        headers.append("FOLDER")
    table = []
    for number, session in rows:
        row = [number, session.agent, session.title, session.branch or "-", relative_time(session.updated)]
        if all_folders:
            row.append(session.cwd or "-")
        table.append(row)
    click.echo(tabulate(table, headers=headers, tablefmt="plain"))


@click.group("sessions", invoke_without_command=True, options_metavar="", subcommand_metavar="[<command>]")
@click.pass_context
def sessions(ctx: click.Context) -> None:
    """List, resume and archive Claude and Codex sessions.

    Shows the sessions of both agents for the current folder in one list. Resuming hands off to the
    agent's own command, so a session always continues in the agent that started it.
    """
    if ctx.invoked_subcommand is None:
        _show(all_folders=False, archived=False, agent=None, as_json=False)


@sessions.command("list", short_help="List sessions, newest first.")
@_common
@click.option("--archived", is_flag=True, help="Archived sessions instead of active ones.")
@click.option("--json", "as_json", is_flag=True,
              help="Print a JSON document (schema_version, command, result, sessions, warnings).")
def list_command(all_folders: bool, agent: Optional[str], archived: bool, as_json: bool) -> None:
    """List sessions, newest first.

    The # column is what `resume`, `archive` and `unarchive` accept when given the same --all and
    --archived flags, along with a session id, an id prefix or a title. Rows are counted over every
    agent that could be listed, so they shift if the Codex CLI appears or disappears in between.
    """
    _show(all_folders, archived, agent, as_json)


@sessions.command("resume", short_help="Continue a session in its own agent and folder.")
@click.argument("session")
@_common
def resume_command(session: str, all_folders: bool, agent: Optional[str]) -> None:
    """Continue SESSION in the agent that started it, in the folder it was started in."""
    chosen = _select(session, all_folders, False, agent)
    try:
        command = AGENT_MODULES[chosen.agent].resume_command(chosen)
    except SessionError as error:
        _fail(str(error))
        raise AssertionError("unreachable")
    if chosen.cwd and os.path.isdir(chosen.cwd):
        os.chdir(chosen.cwd)
    elif chosen.cwd:
        click.echo(f"Warning: {chosen.cwd} no longer exists; resuming from the current folder.", err=True)
    os.execvp(command[0], command)


@sessions.command("archive", short_help="Hide a session from the lists without deleting it.")
@click.argument("session")
@_common
def archive_command(session: str, all_folders: bool, agent: Optional[str]) -> None:
    """Hide SESSION from the session lists without deleting it."""
    _move(_select(session, all_folders, False, agent), archive=True)


@sessions.command("unarchive", short_help="Bring back a session from `list --archived`.")
@click.argument("session")
@_common
def unarchive_command(session: str, all_folders: bool, agent: Optional[str]) -> None:
    """Bring back SESSION, picked from `sima-cli sessions list --archived`."""
    _move(_select(session, all_folders, True, agent), archive=False)


def _move(chosen: Session, archive: bool) -> None:
    module = AGENT_MODULES[chosen.agent]
    try:
        (module.archive if archive else module.unarchive)(chosen)
    except (SessionError, OSError) as error:
        _fail(str(error))
        raise AssertionError("unreachable")
    verb = "Archived" if archive else "Restored"
    click.echo(f"{verb} {chosen.agent} session {chosen.id} ({chosen.title}).")


def register_sessions_commands(cli: click.Group) -> None:
    cli.add_command(sessions)
