"""Hidden, experimental CloudEx command group."""

import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .client import (
    CloudExError,
    SessionStore,
    authorize_admin,
    connect,
    decode_key,
    disconnect_session,
    find_forwarder,
    inspect_session,
    run_benchmark,
)
from .installer import DEFAULT_BRANCH, install_forwarder


console = Console()


def _authorize_supported_host():
    """Prompt only where the native forwarder package can run."""
    if os.name != "nt":
        authorize_admin()


class StageProgress:
    """A compact spinner for terminals and readable milestones for logs."""

    def __init__(self, label, verbose=False):
        self.label = label
        self.verbose = verbose
        self._progress = None
        self._task = None
        self._last = None
        self._finished = False

    def __enter__(self):
        if console.is_interactive and not self.verbose:
            self._progress = Progress(
                SpinnerColumn(style="bold cyan"),
                TextColumn("[cyan]{task.description}[/cyan]"),
                TimeElapsedColumn(),
                console=console,
                transient=True,
                refresh_per_second=8,
            )
            self._progress.start()
            self._task = self._progress.add_task(self.label, total=None)
        else:
            console.print("[cyan]→[/cyan] " + self.label)
            self._last = self.label
        return self

    def update(self, label):
        if label == self._last:
            return
        self._last = label
        if self._progress:
            self._progress.update(self._task, description=label)
        else:
            console.print("[cyan]→[/cyan] " + label)

    def success(self, label):
        self._finish()
        self._finished = True
        console.print("[bold green]✓[/bold green] " + label)

    def failure(self, label):
        self._finish()
        self._finished = True
        console.print("[bold red]✗[/bold red] " + label, style="red")

    def warning(self, label):
        self._finish()
        self._finished = True
        console.print("[bold yellow]![/bold yellow] " + label)

    def _finish(self):
        if self._progress:
            self._progress.stop()
            self._progress = None

    def __exit__(self, exc_type, exc, traceback):
        self._finish()
        if exc_type is not None and not self._finished:
            console.print("[bold red]✗[/bold red] Operation failed", style="red")


def _store():
    return SessionStore()


def _short(value):
    return value[:8] if isinstance(value, str) else "—"


def _expiry_text(value, now=None):
    """Render the allocation deadline as both relative and unambiguous UTC time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    current = time.time() if now is None else now
    remaining = int(value - current)
    elapsed = remaining < 0
    duration = abs(remaining)
    if duration < 60:
        relative = "{}s".format(duration)
    elif duration < 3600:
        relative = "{}m".format(duration // 60)
    elif duration < 86400:
        relative = "{}h {}m".format(duration // 3600, duration % 3600 // 60)
    else:
        relative = "{}d {}h".format(duration // 86400, duration % 86400 // 3600)
    label = "expired {} ago".format(relative) if elapsed else "in {}".format(relative)
    absolute = datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return label + "\n" + absolute


def _read_key_file(path):
    """Read a protected regular file without following a replaced symlink."""
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise CloudExError("allocation key path must be a regular file")
        if os.name != "nt" and mode & 0o077:
            raise CloudExError("allocation key file must have mode 0600 (chmod 600)")
        stream = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = None
        with stream:
            return stream.read().strip()
    except CloudExError:
        raise
    except OSError as exc:
        raise CloudExError("allocation key file could not be read securely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _session_table(rows, show_benchmark=False):
    stream_counts = {
        measurement.get("parallel_streams") for _session, _status, measurement in rows
        if measurement.get("parallel_streams")
    }
    title = "CloudEx connections"
    if show_benchmark and len(stream_counts) == 1:
        title += " · {} parallel streams/direction".format(next(iter(stream_counts)))
    table = Table(title=title, header_style="bold cyan", border_style="bright_black")
    table.add_column("State")
    table.add_column("Device", style="bold")
    if not show_benchmark:
        table.add_column("Session")
    table.add_column("Path")
    table.add_column("Endpoint")
    if not show_benchmark:
        table.add_column("Expires")
    if show_benchmark:
        table.add_column("RTT", justify="right")
        table.add_column("H→D", justify="right")
        table.add_column("D→H", justify="right")
    for session, status, _measurement in rows:
        state = "[green]● connected[/green]" if status["connected"] else "[red]● {}[/red]".format(status["status"])
        path = status["path"]
        if path == "direct":
            path = "[green]direct[/green]"
        elif path == "relay":
            path = "[yellow]relayed[/yellow]"
        else:
            path = "[dim]{}[/dim]".format(path)
        values = [
            state,
            session.get("device", "DevKit " + _short(session.get("allocation_id"))),
        ]
        if not show_benchmark:
            values.append(_short(session.get("session_id")))
        values.extend([path, session.get("target", "—")])
        if not show_benchmark:
            values.append(_expiry_text(session.get("expires_at")))
        if show_benchmark:
            if not status["connected"]:
                values.extend(["—", "—", "—"])
            elif _measurement.get("error"):
                values.extend(["[yellow]n/a[/yellow]", "—", "—"])
            else:
                values.extend([
                    "{:.1f} ms".format(_measurement["latency_ms"]),
                    "{:.1f} Mbps".format(_measurement["host_to_device_throughput_mbps"]),
                    "{:.1f} Mbps".format(_measurement["device_to_host_throughput_mbps"]),
                ])
        table.add_row(*values)
    return table


@click.group(name="cloudex", hidden=True)
def cloudex_group():
    """Connect to allocated DevKits through CloudEx."""


@cloudex_group.command("connect")
@click.option(
    "--key-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read the allocation key from a mode-0600 file.",
)
@click.option(
    "--key-stdin",
    is_flag=True,
    help="Read the allocation key from standard input for automation.",
)
@click.option("--transport", type=click.Choice(["auto", "p2p", "turn"]), default="auto", hidden=True)
@click.option("--attempts", type=click.IntRange(1, 5), default=3, hidden=True)
@click.option(
    "--verbose",
    is_flag=True,
    help="Print each sanitized connection stage for troubleshooting.",
)
def connect_command(key_file, key_stdin, transport, attempts, verbose):
    """Connect to an allocated DevKit using a securely supplied key."""
    if key_file and key_stdin:
        raise click.UsageError("Use either --key-file or --key-stdin, not both.")
    try:
        if key_file:
            key = _read_key_file(key_file)
        elif key_stdin:
            key = sys.stdin.readline().strip()
            if not key:
                raise CloudExError("standard input did not contain a CloudEx allocation key")
        else:
            key = click.prompt("CloudEx allocation key", hide_input=True).strip()
        profile = decode_key(key)
        console.print("[cyan]→[/cyan] Authorizing encrypted tunnel access")
        _authorize_supported_host()
        with StageProgress("Preparing CloudEx connection", verbose=verbose) as progress:
            session = connect(profile, _store(), progress, attempts=attempts, transport=transport)
            progress.success("Connected to {}".format(session["device"]))
        path = session.get("ice_path", "unknown")
        path_text = "direct" if path == "direct" else "relayed" if path == "relay" else path
        console.print("  [dim]Session[/dim]  {}".format(_short(session["session_id"])))
        console.print("  [dim]Path[/dim]     {}".format(path_text))
        console.print("  [dim]SSH[/dim]      [bold]ssh sima@{}[/bold]".format(session["target"]))
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc


@cloudex_group.command("update")
@click.option(
    "--branch",
    default=DEFAULT_BRANCH,
    show_default=True,
    help="Kerrigan branch whose latest forwarder should be installed.",
)
def update_command(branch):
    """Install or update the native CloudEx forwarder."""
    try:
        console.print("[cyan]→[/cyan] Authorizing CloudEx forwarder installation")
        _authorize_supported_host()
        with StageProgress("Updating CloudEx forwarder from {}".format(branch)) as progress:
            result = install_forwarder(branch, progress=progress)
            progress.success("CloudEx forwarder updated from {}".format(result["branch"]))
        console.print("  [dim]Version[/dim]  {}".format(result["version"]))
        console.print("  [dim]Path[/dim]     {}".format(result["path"]))
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc


def _resolve_session(sessions, selector):
    matches = [
        session for session in sessions
        if session["session_id"] == selector or session["session_id"].startswith(selector)
    ]
    if not matches:
        raise click.ClickException("No CloudEx session matches '{}'".format(selector))
    if len(matches) > 1:
        raise click.ClickException("Session prefix '{}' is ambiguous".format(selector))
    return matches[0]


def _choose_sessions(sessions):
    console.print(_session_table([(session, inspect_session(session, _store()), {}) for session in sessions]))
    choices = [str(index) for index in range(1, len(sessions) + 1)] + ["all"]
    choice = click.prompt(
        "Disconnect which connection",
        type=click.Choice(choices, case_sensitive=False),
        show_choices=True,
    )
    return sessions if choice.lower() == "all" else [sessions[int(choice) - 1]]


@cloudex_group.command("disconnect")
@click.option("--session", "session_id", help="Session ID or unique prefix to disconnect.")
@click.option("--all", "disconnect_all", is_flag=True, help="Disconnect every recorded CloudEx session.")
def disconnect_command(session_id, disconnect_all):
    """Disconnect one or all CloudEx sessions."""
    if session_id and disconnect_all:
        raise click.UsageError("Use either --session or --all, not both.")
    store = _store()
    sessions = store.list()
    if not sessions:
        console.print("[dim]No CloudEx connections are recorded.[/dim]")
        return
    if disconnect_all:
        selected = sessions
    elif session_id:
        selected = [_resolve_session(sessions, session_id)]
    elif len(sessions) == 1:
        selected = sessions
    elif not sys.stdin.isatty():
        raise click.ClickException("Multiple sessions are recorded; use --session ID or --all.")
    else:
        selected = _choose_sessions(sessions)
    console.print("[cyan]→[/cyan] Authorizing encrypted tunnel cleanup")
    try:
        _authorize_supported_host()
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc
    failures = []
    for session in selected:
        label = session.get("device", "DevKit " + _short(session.get("allocation_id")))
        try:
            with StageProgress("Disconnecting {}".format(label)) as progress:
                result = disconnect_session(session, store, progress)
                if result == "stale":
                    progress.success("Removed stale record for {}; newer connection retained".format(label))
                else:
                    progress.success("Disconnected {}".format(label))
        except CloudExError as exc:
            failures.append("{}: {}".format(label, exc))
    if failures:
        raise click.ClickException("; ".join(failures))


@cloudex_group.command("list")
@click.option("--benchmark", is_flag=True, help="Stress-test RTT and maximum bidirectional throughput for each connection.")
@click.option("--duration", type=click.IntRange(1, 60), default=10, show_default=True,
              help="Throughput measurement window in seconds.")
def list_command(benchmark, duration):
    """List locally recorded CloudEx connections."""
    store = _store()
    sessions = store.list()
    if not sessions:
        console.print("[dim]No CloudEx connections are recorded.[/dim]")
        return
    forwarder = None
    if benchmark:
        try:
            forwarder = find_forwarder()
        except CloudExError:
            try:
                with StageProgress(
                    "CloudEx forwarder is missing; installing from {}".format(DEFAULT_BRANCH)
                ) as progress:
                    result = install_forwarder(DEFAULT_BRANCH, progress=progress)
                    forwarder = result["path"]
                    progress.success("CloudEx forwarder installed")
            except CloudExError as exc:
                raise click.ClickException(str(exc)) from exc
    rows = []
    for session in sessions:
        status = inspect_session(session, store)
        measurement = {}
        if benchmark and status["connected"]:
            device = session.get("device", _short(session["session_id"]))
            with StageProgress("Benchmarking {}".format(device)) as progress:
                try:
                    measurement = run_benchmark(forwarder, session["target"], duration)
                    progress.success("Benchmark complete")
                except CloudExError as exc:
                    measurement = {"error": str(exc)}
                    progress.warning("Benchmark unavailable for {}".format(device))
        rows.append((session, status, measurement))
    console.print(_session_table(rows, show_benchmark=benchmark))
    if benchmark:
        console.print("[bold cyan]Allocation expiry[/bold cyan]")
        for session in sessions:
            label = session.get("device", "DevKit " + _short(session.get("allocation_id")))
            console.print("  {}: {}".format(label, _expiry_text(session.get("expires_at")).replace("\n", " · ")))
    if benchmark and any(row[2].get("error") for row in rows):
        console.print("[yellow]Some benchmarks were unavailable; verify the DevKit is running a forwarder with parallel bidirectional benchmark support. Existing tunnels were left connected.[/yellow]")


def register_cloudex_commands(main):
    main.add_command(cloudex_group)
