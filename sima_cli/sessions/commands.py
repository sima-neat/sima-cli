"""`sima-cli sessions`: list, resume and archive Claude and Codex sessions from one place.

Each agent keeps its own session list, reachable only from its own CLI. This command puts both in
one list for the current folder and hands off to the agent's native command to resume, so no
session format of our own exists and nothing is converted between agents.
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

AGENTS = (claude.AGENT, codex.AGENT)


def _collect(all_folders: bool, archived: bool, agent: Optional[str]) -> Tuple[List[Session], List[str]]:
    sessions: List[Session] = []
    warnings: List[str] = []
    if agent in (None, claude.AGENT):
        sessions.extend(claude.list_sessions(archived))
    if agent in (None, codex.AGENT):
        cli = codex.find_cli()
        if cli is None:
            warnings.append("The Codex CLI was not found, so Codex sessions are not listed.")
        else:
            try:
                sessions.extend(codex.list_sessions(cli, archived))
            except SessionError as error:
                warnings.append(str(error))
    if not all_folders:
        here = os.getcwd()
        sessions = [session for session in sessions if same_folder(session.cwd, here)]
    return newest_first(sessions), warnings


def _fail(message: str) -> None:
    click.echo(f"Error: {message}", err=True)
    sys.exit(1)


def _select(selector: str, all_folders: bool, archived: bool, agent: Optional[str]) -> Session:
    sessions, warnings = _collect(all_folders, archived, agent)
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)
    try:
        return pick(sessions, selector)
    except SessionError as error:
        _fail(str(error))
        raise AssertionError("unreachable")


def _common(func):
    func = click.option("--agent", type=click.Choice(AGENTS), default=None,
                        help="Only sessions of this agent.")(func)
    func = click.option("--all", "all_folders", is_flag=True,
                        help="Sessions from every folder, not just the current one.")(func)
    return func


def _show(all_folders: bool, archived: bool, agent: Optional[str], as_json: bool) -> None:
    sessions, warnings = _collect(all_folders, archived, agent)
    if as_json:
        click.echo(json.dumps({"sessions": [s.as_json() for s in sessions], "warnings": warnings}, indent=2))
        return
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)
    if not sessions:
        where = "anywhere" if all_folders else "in this folder"
        hint = "" if all_folders else " Use --all to see sessions from every folder."
        kind = "archived sessions" if archived else "sessions"
        click.echo(f"No {kind} {where}.{hint}")
        return
    headers = ["#", "AGENT", "TITLE", "BRANCH", "LAST ACTIVE"]
    if all_folders:
        headers.append("FOLDER")
    rows = []
    for index, session in enumerate(sessions, 1):
        row = [index, session.agent, session.title, session.branch or "-", relative_time(session.updated)]
        if all_folders:
            row.append(session.cwd or "-")
        rows.append(row)
    click.echo(tabulate(rows, headers=headers, tablefmt="plain"))


@click.group("sessions", invoke_without_command=True)
@click.pass_context
def sessions(ctx: click.Context) -> None:
    """List, resume and archive Claude and Codex sessions.

    Shows the sessions of both agents for the current folder in one list. Resuming hands off to the
    agent's own command, so a session always continues in the agent that started it.
    """
    if ctx.invoked_subcommand is None:
        _show(all_folders=False, archived=False, agent=None, as_json=False)


@sessions.command("list")
@_common
@click.option("--archived", is_flag=True, help="Archived sessions instead of active ones.")
@click.option("--json", "as_json", is_flag=True, help="Print the sessions as JSON.")
def list_command(all_folders: bool, agent: Optional[str], archived: bool, as_json: bool) -> None:
    """List sessions, newest first.

    The # column is what `resume`, `archive` and `unarchive` accept, along with a session id, an id
    prefix or a title.
    """
    _show(all_folders, archived, agent, as_json)


@sessions.command("resume")
@click.argument("session")
@_common
def resume_command(session: str, all_folders: bool, agent: Optional[str]) -> None:
    """Continue SESSION in the agent that started it, in the folder it was started in."""
    chosen = _select(session, all_folders, False, agent)
    try:
        if chosen.agent == claude.AGENT:
            command = claude.resume_command(chosen)
        else:
            cli = codex.find_cli()
            if cli is None:
                raise SessionError("The Codex CLI was not found.")
            command = codex.resume_command(cli, chosen)
    except SessionError as error:
        _fail(str(error))
        return
    if chosen.cwd and os.path.isdir(chosen.cwd):
        os.chdir(chosen.cwd)
    elif chosen.cwd:
        click.echo(f"Warning: {chosen.cwd} no longer exists; resuming from the current folder.", err=True)
    os.execvp(command[0], command)


@sessions.command("archive")
@click.argument("session")
@_common
def archive_command(session: str, all_folders: bool, agent: Optional[str]) -> None:
    """Hide SESSION from the session lists without deleting it."""
    chosen = _select(session, all_folders, False, agent)
    _move(chosen, archive=True)


@sessions.command("unarchive")
@click.argument("session")
@_common
def unarchive_command(session: str, all_folders: bool, agent: Optional[str]) -> None:
    """Bring an archived SESSION back (pick it from `list --archived`)."""
    chosen = _select(session, all_folders, True, agent)
    _move(chosen, archive=False)


def _move(chosen: Session, archive: bool) -> None:
    verb = "Archived" if archive else "Restored"
    try:
        if chosen.agent == claude.AGENT:
            (claude.archive if archive else claude.unarchive)(chosen.id)
        else:
            cli = codex.find_cli()
            if cli is None:
                raise SessionError("The Codex CLI was not found.")
            (codex.archive if archive else codex.unarchive)(cli, chosen.id)
    except (SessionError, OSError) as error:
        _fail(str(error))
        return
    click.echo(f"{verb} {chosen.agent} session {chosen.id} ({chosen.title}).")


def register_sessions_commands(cli: click.Group) -> None:
    cli.add_command(sessions)
