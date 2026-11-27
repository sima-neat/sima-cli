"""Hidden, experimental CloudEx command group."""

import base64
import contextlib
import json
import os
import uuid
import stat
import sys
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import power
from .client import (
    APIError,
    CloudExAPI,
    CloudExError,
    LEASE_EXPIRED,
    SessionStore,
    authorize_admin,
    connect,
    decode_key,
    disconnect_requires_admin,
    disconnect_session,
    find_forwarder,
    inspect_session,
    run_benchmark,
    session_expired,
    stop_local_forwarder,
)
from .installer import DEFAULT_BRANCH, install_forwarder
from .neatops import NeatOps


console = Console()


def _authorize_supported_host():
    """Prompt only where the native forwarder package can run."""
    if os.name != "nt":
        authorize_admin()


def _authorize_tunnel_cleanup(session):
    if disconnect_requires_admin(session):
        console.print("[cyan]→[/cyan] Authorizing local tunnel cleanup")
        _authorize_supported_host()


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


def _read_allocation_profile(key_file=None, key_stdin=False):
    """Decode a key from a protected file, stdin, or a hidden prompt; never argv."""
    if key_file:
        key = _read_key_file(key_file)
    elif key_stdin:
        key = sys.stdin.readline().strip()
        if not key:
            raise CloudExError("standard input did not contain a CloudEx allocation key")
    else:
        key = click.prompt("CloudEx allocation key", hide_input=True).strip()
    return decode_key(key)


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
        color = "yellow" if status["status"] in {"reconnecting", "link unverified"} else "red"
        state = "[green]● connected[/green]" if status["connected"] else "[{0}]● {1}[/{0}]".format(color, status["status"])
        if status.get("reason"):
            state += "\n[dim]{}[/dim]".format(escape(status["reason"]))
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


def _internal_cloudex():
    ctx = click.get_current_context(silent=True)
    return bool(ctx and isinstance(ctx.obj, dict) and ctx.obj.get("internal"))


def _require_vulcan():
    if not _internal_cloudex():
        raise click.ClickException(
            "CloudEx is not available on Arthas yet. Use sima-cli -i cloudex for Vulcan."
        )


def _vulcan_only(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        _require_vulcan()
        return function(*args, **kwargs)
    return wrapped


@click.group(name="cloudex", hidden=True)
@click.option("-i", "--internal", is_flag=True, help="Use internal Vulcan CloudEx and NeatOps authentication.")
@click.pass_context
def cloudex_group(ctx, internal):
    """Manage CloudEx devices; use -i for internal Vulcan access."""
    ctx.ensure_object(dict)
    ctx.obj["internal"] = internal or ctx.obj.get("internal", False) or os.getenv("SIMA_CLI_INTERNAL", "0") in ("1", "true", "yes")


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
@click.option("--allocation", help="Connect to your NeatOps allocation ID without copying a key.")
@click.option("--environment", type=click.Choice(["production", "staging"]), default="production", show_default=True, help="Vulcan environment (requires -i).")
@click.option("--transport", type=click.Choice(["auto", "p2p", "turn"]), default="auto", hidden=True)
@click.option("--attempts", type=click.IntRange(1, 5), hidden=True)
@click.option(
    "--verbose",
    is_flag=True,
    help="Print each sanitized connection stage for troubleshooting.",
)
@_vulcan_only
def connect_command(key_file, key_stdin, allocation, environment, transport, attempts, verbose):
    """Connect to an allocated DevKit using a securely supplied key."""
    if sum(bool(value) for value in (key_file, key_stdin, allocation)) > 1:
        raise click.UsageError("Use either --key-file or --key-stdin, not both; --allocation must be used alone.")
    try:
        if allocation:
            api = _neatops_api(environment)
            profile = _allocation_profile(api, _allocation_id(allocation))
            _connect_profile(profile, verbose=verbose, attempts=attempts, transport=transport, diagnostics_api=api)
            return
        profile = _read_allocation_profile(key_file, key_stdin)
        _connect_profile(profile, verbose=verbose, attempts=attempts, transport=transport)
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc


@cloudex_group.command("update")
@click.option(
    "--branch",
    default=DEFAULT_BRANCH,
    show_default=True,
    help="Kerrigan branch whose latest forwarder should be installed.",
)
@_vulcan_only
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


def _interactive_session_selection_available():
    return sys.stdin.isatty()


def _select_checkboxes(choices, *, message, instruction, mandatory=True):
    from InquirerPy import inquirer

    return set(inquirer.checkbox(
        message=message,
        choices=choices,
        instruction=instruction,
        mandatory=mandatory,
        enabled_symbol="[x]",
        disabled_symbol="[ ]",
        pointer="❯",
        transformer=lambda selected: (
            "{} selected".format(len(selected)) if selected else "None selected"
        ),
    ).execute() or [])


def _choose_sessions(sessions):
    """Select one or more local connection records without probing them."""
    choices = [{"name": _session_choice_label(s), "value": s["session_id"]} for s in sessions]
    selected = _select_checkboxes(
        choices,
        message="Select CloudEx connections to disconnect:",
        instruction="Space to select, Enter to disconnect selected connections",
    )
    return [session for session in sessions if session["session_id"] in selected]


def _session_choice_label(session):
    device = session.get("device", "DevKit " + _short(session.get("allocation_id")))
    expiry = _expiry_text(session.get("expires_at")).replace("\n", " · ")
    return "{} · {} · {} · {}".format(
        device, _short(session.get("session_id")), session.get("target", "unknown endpoint"), expiry
    )


def _choose_session(sessions):
    """Pick exactly one recorded connection."""
    from InquirerPy import inquirer

    selected = inquirer.select(
        message="Select the CloudEx DevKit to power-cycle:",
        choices=[{"name": _session_choice_label(s), "value": s["session_id"]} for s in sessions],
        pointer="❯",
    ).execute()
    return [session for session in sessions if session["session_id"] == selected]


def _select_recorded_sessions(sessions, selector, choose, multiple_hint):
    """Shared by disconnect and power-cycle: explicit, only, picker, or fail closed."""
    if selector:
        return [_resolve_session(sessions, selector)]
    if len(sessions) == 1:
        return list(sessions)
    if not _interactive_session_selection_available():
        raise click.ClickException(multiple_hint)
    return choose(sessions)


@cloudex_group.command("disconnect")
@click.option("--session", "session_id", help="Session ID or unique prefix to disconnect.")
@click.option("--all", "disconnect_all", is_flag=True, help="Disconnect every recorded CloudEx session.")
@_vulcan_only
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
    else:
        selected = _select_recorded_sessions(
            sessions, session_id, _choose_sessions,
            "Multiple sessions are recorded; use --session ID or --all.",
        )
    if not selected:
        console.print("[dim]No CloudEx connections selected.[/dim]")
        return
    failures = []
    for session in selected:
        label = session.get("device", "DevKit " + _short(session.get("allocation_id")))
        try:
            _authorize_tunnel_cleanup(session)
            with StageProgress("Disconnecting {}".format(label)) as progress:
                result = disconnect_session(session, store, progress, authorize=False)
                if result == "stale":
                    progress.success("Removed stale record for {}; newer connection retained".format(label))
                elif result == "expired":
                    progress.success("Removed expired local record for {}".format(label))
                else:
                    progress.success("Disconnected {}".format(label))
        except CloudExError as exc:
            failures.append("{}: {}".format(label, exc))
    if failures:
        raise click.ClickException("; ".join(failures))


POWER_INTERRUPTION = "Power will be interrupted for 5 seconds."
RESUME_COMMAND = "sima-cli -i cloudex power-cycle"
POWER_RESUME_HINT = "Rerun {} to resume.".format(RESUME_COMMAND)


def _session_profile(session):
    """Rebuild the allocation credential already held in a mode-0600 session record."""
    return {
        "environment": session.get("environment", ""),
        "api_url": session["api_url"],
        "requester": session["requester"],
        "allocation_id": session["allocation_id"],
        "allocation_secret": session["api_secret"],
    }


def _device_name(session=None, operation=None, allocation_id=None):
    for source in (session, operation):
        if source and isinstance(source.get("device"), str) and source["device"]:
            return source["device"]
    return "DevKit " + _short(allocation_id)


def _pending_operation(pending, sessions, selector, key_profile):
    if selector:
        allocation_id = _resolve_session(sessions, selector)["allocation_id"]
    elif key_profile:
        allocation_id = key_profile["allocation_id"]
    elif len(pending) > 1:
        names = ", ".join(_device_name(operation=o, allocation_id=o["allocation_id"]) for o in pending)
        raise click.ClickException(
            "Several power cycles are pending ({}); choose one with --session ID or --key-file.".format(names)
        )
    else:
        return pending[0] if pending else None
    return next((o for o in pending if o["allocation_id"] == allocation_id), None)


def _allocation_session(sessions, allocation_id, preferred=None):
    matches = [s for s in sessions if s.get("allocation_id") == allocation_id]
    return next((s for s in matches if s["session_id"] == preferred), matches[0] if matches else None)


def _require_key(key_profile, can_prompt, missing):
    if key_profile:
        return key_profile
    if not can_prompt:
        raise click.UsageError(missing + " Supply the allocation key with --key-file or --key-stdin.")
    return _read_allocation_profile()


def _confirm_power_cycle(device, yes, can_prompt):
    if yes:
        return True
    if not can_prompt:
        raise click.UsageError("--yes is required when standard input is not interactive.")
    return click.confirm("Power-cycle {}? {}".format(device, POWER_INTERRUPTION), default=False)


def _retain_credential(store, session):
    """Keep the stale record (and its credential) until a new connection replaces it.

    connect() removes records it reconciles; restoring this one after a failed
    attempt lets a later rerun resume without asking for the key again.
    """
    if session and not any(s.get("allocation_id") == session["allocation_id"] for s in store.list()):
        # Restore only the credential-bearing record, never the stopped
        # forwarder's PID or a connected status that is no longer true.
        retained = dict(session, status="disconnected")
        retained.pop("forwarder_pid", None)
        store.write(retained)


def _stop_obsolete_forwarders(store, allocation_id, cycle_completed_at, by_attempt=False):
    """Stop tunnels that predate the relay cycle; a newer connection is kept."""
    obsolete = [
        s for s in store.list()
        if s.get("allocation_id") == allocation_id and _created_before(s, cycle_completed_at, by_attempt)
    ]
    for session in obsolete:
        _authorize_tunnel_cleanup(session)
        stop_local_forwarder(session)
        session.pop("forwarder_pid", None)
        session["status"] = "disconnected"
        store.write(session)


def _created_before(session, moment, by_attempt=False):
    # Against a cutoff observed during the cycle, a tunnel is as new as its
    # connection: a connect() that started before may have come up after it.
    # Against the submission (by_attempt), its attempt start decides: the
    # service refuses new peers from submission until the cycle is final, and
    # disconnects the one it found, so an attempt begun before is dead.
    for key in (("created_at",) if by_attempt else ("connected_at", "created_at")):
        value = session.get(key)
        if not isinstance(value, bool) and isinstance(value, (int, float)):
            return value <= moment
    return True  # Older records lack both times; treat them as obsolete.


def _has_newer_connection(store, allocation_id, cycle_completed_at):
    return any(
        s.get("allocation_id") == allocation_id and not _created_before(s, cycle_completed_at)
        for s in store.list()
    )


def _still_unreadable(operations, path):
    allocation_id = os.path.basename(path)[:-len(".json")]
    try:
        if power.valid_allocation_id(allocation_id):
            operations.load(allocation_id)
        else:
            operations.list()
    except power.UnreadableOperation as exc:
        return exc.path == path
    return False


def _reconnect_after_cycle(profile, store, session, device):
    _authorize_supported_host()
    api = CloudExAPI.from_profile(profile)
    deadline = time.monotonic() + power.DEVICE_RETURN_TIMEOUT_SECONDS
    with StageProgress("Reconnecting CloudEx") as progress:
        while True:
            try:
                connected = connect(profile, store, progress)
            except CloudExError as exc:
                if power.device_not_ready(exc) and time.monotonic() < deadline:
                    progress.update("Waiting for {} to return".format(device))
                    power.sleep(power.DEVICE_RETURN_POLL_SECONDS)
                    # The failed attempt's ICE session holds the allocation until the
                    # appliance confirms its cleanup; a new one is refused before then.
                    remaining = deadline - time.monotonic()
                    try:
                        cleared = remaining > 0 and power.wait_for_peer_cleanup(api, progress, timeout=remaining)
                    except CloudExError as probe:
                        if power.allocation_ended(probe):
                            raise  # the caller drops the ended allocation's saved cycle
                        cleared = False  # report the reconnect failure, not the status probe
                    if cleared:
                        continue
                if power.allocation_ended(exc):
                    raise  # nothing left to resume; the caller reports it
                progress.warning("{} did not reconnect yet".format(device))
                reason = power.api_error_message(exc) if isinstance(exc, APIError) else str(exc)
                raise power.PowerCyclePending(power.PARTIAL_SUCCESS + "\nLast error: " + reason) from exc
            finally:
                _retain_credential(store, session)
            _drop_stopped_siblings(store, connected)
            progress.success("{} power-cycled and CloudEx reconnected".format(device))
            return connected


def _drop_stopped_siblings(store, connected):
    """Forget the allocation's stopped pre-cycle records once a tunnel is up again.

    connect() reuses a tunnel opened after the cycle without reconciling older
    records, so the one retained for resuming would otherwise stay listed and
    make the next run ask which session to use.
    """
    for recorded in store.list():
        if (recorded.get("allocation_id") == connected.get("allocation_id")
                and recorded.get("session_id") != connected.get("session_id")
                and recorded.get("status") == "disconnected" and "forwarder_pid" not in recorded):
            store.delete(recorded["session_id"])


ALLOCATION_ENDED = (
    "The relay power cycle of {} completed, but its CloudEx allocation has ended, so it cannot be "
    "reconnected. The saved power-cycle state was removed; request a new allocation in NeatOps."
)
ALLOCATION_ENDED_TUNNEL_KEPT = (
    " The ended connection's local tunnel could not be stopped; remove it with "
    "sima-cli -i cloudex disconnect --session {}."
)


def _drop_ended_allocation(store, operations, allocation_id, device):
    """Forget an ended allocation's saved cycle so it never hijacks a later run."""
    kept = []
    for recorded in store.list():
        if recorded.get("allocation_id") != allocation_id:
            continue
        try:
            _authorize_tunnel_cleanup(recorded)
            stop_local_forwarder(recorded)
        except CloudExError:
            kept.append(recorded["session_id"])  # its forwarder may still run
            continue
        store.delete(recorded["session_id"])
    operations.delete(allocation_id)
    message = ALLOCATION_ENDED.format(device)
    if kept:
        message += ALLOCATION_ENDED_TUNNEL_KEPT.format(kept[0][:8])
    raise power.AllocationEnded(message)


LOCK_UNSUPPORTED_WARNING = (
    "Warning: this filesystem does not support file locking, so concurrent sima-cli power-cycle "
    "runs for this DevKit are not serialized. Avoid running more than one at a time; a second "
    "power cycle is still never sent for a saved request."
)


def _lock_allocation(held, operations, allocation_id):
    if not held.enter_context(operations.lock(allocation_id)):
        console.print(LOCK_UNSUPPORTED_WARNING, style="yellow", markup=False, soft_wrap=True)


def _run_power_cycle(session_id, key_file, key_stdin, yes, no_reconnect, abandon):
    can_prompt = _interactive_session_selection_available() and not key_stdin
    store = _store()
    operations = power.OperationStore(store.root)
    sessions = store.list()
    key_profile = _read_allocation_profile(key_file, key_stdin) if (key_file or key_stdin) else None
    target = (_resolve_session(sessions, session_id)["allocation_id"] if session_id
              else key_profile["allocation_id"] if key_profile else None)
    try:
        if target:
            # Records are per allocation: a damaged one for another DevKit must
            # not block the DevKit the user selected.
            selected = operations.load(target)
            pending = [selected] if selected else []
        else:
            pending = operations.list()
    except power.UnreadableOperation as exc:
        if not abandon:
            raise
        # A targeted request reads only its own record, so it can never discard
        # another DevKit's unconfirmed cycle (whose next run could cycle again).
        if not yes:
            if not can_prompt:
                raise click.UsageError("--yes is required when standard input is not interactive.")
            if not click.confirm("Discard the unreadable power-cycle state {}? Nothing is sent to the DevKit.".format(exc.path), default=False):
                return
        with contextlib.ExitStack() as held:
            allocation_id = os.path.basename(exc.path)[:-len(".json")]
            if power.valid_allocation_id(allocation_id):
                _lock_allocation(held, operations, allocation_id)
            if not _still_unreadable(operations, exc.path):
                console.print("The power-cycle state {} changed in another sima-cli run; nothing was "
                              "discarded.".format(exc.path), markup=False, soft_wrap=True)
                return
            operations.discard(exc.path)
        console.print("Discarded unreadable power-cycle state {}.".format(exc.path), markup=False, soft_wrap=True)
        return
    operation = _pending_operation(pending, sessions, session_id, key_profile)

    # One run at a time per allocation drives the state machine; a concurrent
    # resume would otherwise advance a stale copy and redo cleanup.
    with contextlib.ExitStack() as held:
        if abandon:
            if operation is None:
                console.print("[dim]No pending CloudEx power cycle.[/dim]")
                return
            device = _device_name(operation=operation, allocation_id=operation["allocation_id"])
            if not yes:
                if not can_prompt:
                    raise click.UsageError("--yes is required when standard input is not interactive.")
                if not click.confirm("Discard the saved power-cycle state for {}? Nothing is sent to the DevKit.".format(device), default=False):
                    return
            _lock_allocation(held, operations, operation["allocation_id"])
            # Confirmation can take a while: delete only the request the user confirmed,
            # never a newer one another run recorded meanwhile.
            current = operations.load(operation["allocation_id"])
            if current is None or current["request_id"] != operation["request_id"]:
                console.print("The saved power-cycle state for {} changed in another sima-cli run; "
                              "nothing was discarded.".format(device), markup=False)
                return
            operations.delete(operation["allocation_id"])
            console.print("Discarded the saved power-cycle state for {}.".format(device), markup=False)
            return

        if operation is None:
            if key_profile:
                profile = key_profile
                session = _allocation_session(sessions, profile["allocation_id"])
            elif sessions or session_id:
                session = _select_recorded_sessions(
                    sessions, session_id, _choose_session,
                    "Multiple sessions are recorded; use --session ID.",
                )
                if not session:
                    console.print("[dim]No CloudEx DevKit selected.[/dim]")
                    return
                session = session[0]
                profile = _session_profile(session)
            else:
                profile = _require_key(None, can_prompt, "No CloudEx connection is recorded.")
                session = None
            allocation_id = profile["allocation_id"]
            device = _device_name(session=session, allocation_id=allocation_id)
            if not _confirm_power_cycle(device, yes, can_prompt):
                console.print("Power cycle cancelled.")
                return
            _lock_allocation(held, operations, allocation_id)
            operation = power.new_operation(allocation_id, session and session["session_id"], device)
            try:
                operations.create(operation)
            except power.OperationExists:
                raise click.ClickException(
                    "A power cycle for {} is already pending or in progress in another sima-cli run. "
                    "Rerun sima-cli -i cloudex power-cycle to resume it; no new cycle was sent.".format(device)
                ) from None
        else:
            allocation_id = operation["allocation_id"]
            _lock_allocation(held, operations, allocation_id)
            # Re-read under the lock: another run may have advanced or finished it.
            operation = operations.load(allocation_id)
            if operation is None:
                console.print("The saved power cycle was already finished by another sima-cli run.")
                return
            session = _allocation_session(sessions, allocation_id, operation.get("session_id"))
            if session and not key_profile:
                profile = _session_profile(session)
            else:
                profile = _require_key(key_profile, can_prompt,
                                       "The saved power cycle's CloudEx connection record is gone.")
            if profile["allocation_id"] != allocation_id:
                raise click.UsageError("That allocation key belongs to a different DevKit than the saved power cycle.")
            device = _device_name(session, operation, allocation_id)
            if operation["phase"] == power.PREPARED:
                # The request may never have left this host: confirm again, but reuse its ID.
                console.print("Resuming the saved power-cycle request for {}.".format(device), markup=False)
                if not _confirm_power_cycle(device, yes, can_prompt):
                    console.print("Power cycle cancelled; the saved request is kept.")
                    return
            else:
                console.print("Resuming the power cycle of {} (no new cycle will be sent).".format(device), markup=False)

        api = CloudExAPI.from_profile(profile)
        phase = operation["phase"]
        if phase in (power.PREPARED, power.CYCLE_SUBMITTED):
            with StageProgress("Requesting power cycle" if phase == power.PREPARED else "Checking power cycle status") as progress:
                try:
                    if phase == power.PREPARED:
                        power.submit_cycle(api, operations, operation)
                        progress.update("Power cycle in progress")
                    power.wait_for_cycle(api, operations, operation, progress)
                except power.PowerCycleRejected:
                    progress.failure("Power cycle did not complete")
                    raise
                except power.PowerCyclePending:
                    progress.warning("Power cycle status is not confirmed yet")
                    raise
                progress.success("Relay cycle completed")
            phase = operation["phase"]

        if no_reconnect:
            operations.delete(allocation_id)
            console.print("Relay power returned; {} may still be booting. CloudEx reconnection was skipped; "
                          "the previous tunnel may be stale. Reconnect with sima-cli -i cloudex connect "
                          "or remove it with sima-cli -i cloudex disconnect.".format(device), markup=False)
            return

        if phase != power.COMPLETED and session and session_expired(session):
            _drop_ended_allocation(store, operations, allocation_id, device)
        try:
            if phase == power.CYCLE_COMPLETED:
                console.print("[cyan]→[/cyan] Cleaning up the previous CloudEx connection")
                _stop_obsolete_forwarders(store, allocation_id, power.cycle_completed_at(operation),
                                          by_attempt=operation.get("completion_observed") is not True)
                power.advance(operations, operation, power.WAITING_FOR_DEVICE)
                phase = operation["phase"]
            if phase == power.WAITING_FOR_DEVICE:
                with StageProgress("Waiting for {} to return".format(device)) as progress:
                    if (operation.get("completion_observed") is True
                            and _has_newer_connection(store, allocation_id, power.cycle_completed_at(operation))):
                        # The allocation's live ICE session belongs to a tunnel opened
                        # after the cycle; it will never close, and connect() reuses it.
                        # Only a completion watched live gives a cutoff precise enough to
                        # tell such a tunnel from one the power cut itself killed.
                        progress.success("A CloudEx connection made after the power cycle is kept")
                    elif power.wait_for_peer_cleanup(api, progress):
                        progress.success("Previous CloudEx connection closed")
                    else:
                        progress.warning("Previous CloudEx connection is still closing; reconnect will reconcile it")
                power.advance(operations, operation, power.RECONNECTING)
                phase = operation["phase"]
            if phase == power.RECONNECTING:
                connected = _reconnect_after_cycle(profile, store, session, device)
                power.advance(operations, operation, power.COMPLETED)
                console.print("  [dim]SSH[/dim]      [bold]ssh sima@{}[/bold]".format(connected["target"]))
        except power.PowerCyclePending:
            raise
        except CloudExError as exc:
            if power.allocation_ended(exc):
                _drop_ended_allocation(store, operations, allocation_id, device)
            raise power.PowerCyclePending(power.PARTIAL_SUCCESS + "\nLast error: " + str(exc)) from exc
        if operation["phase"] == power.COMPLETED:
            operations.delete(allocation_id)


@cloudex_group.command("power-cycle")
@click.option("--session", "session_id", help="Session ID or unique prefix of the DevKit to power-cycle.")
@click.option(
    "--key-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read the allocation key from a mode-0600 file.",
)
@click.option("--key-stdin", is_flag=True, help="Read the allocation key from standard input (requires --yes).")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation; required without an interactive terminal.")
@click.option("--no-reconnect", is_flag=True, help="Stop after the relay power event instead of reconnecting CloudEx.")
@click.option("--abandon", is_flag=True, help="Discard a pending power-cycle record without sending anything.")
@_vulcan_only
def power_cycle_command(session_id, key_file, key_stdin, yes, no_reconnect, abandon):
    """Power-cycle your allocated DevKit and reconnect CloudEx.

    A rerun resumes an interrupted power cycle without cycling again.
    """
    if key_file and key_stdin:
        raise click.UsageError("Use either --key-file or --key-stdin, not both.")
    if session_id and (key_file or key_stdin):
        raise click.UsageError("--session uses the recorded credential; do not combine it with a key option.")
    if key_stdin and not yes:
        raise click.UsageError("--key-stdin requires --yes because standard input cannot confirm.")
    try:
        _run_power_cycle(session_id, key_file, key_stdin, yes, no_reconnect, abandon)
    except power.PowerCycleRejected as exc:
        raise click.ClickException(str(exc)) from exc
    except power.PowerCyclePending as exc:
        message = str(exc)
        if message.startswith(power.PARTIAL_SUCCESS):
            # The agreed partial-success sentence (vulcan#238) names the public
            # command; while CloudEx is internal-only, also give the one that works.
            message += "\nResume with: " + RESUME_COMMAND
        else:
            message += ". The power-cycle state is saved. " + POWER_RESUME_HINT
        console.print(message, style="yellow", markup=False, soft_wrap=True)
        raise click.exceptions.Exit(1)
    except KeyboardInterrupt:
        console.print("Interrupted. Any saved power-cycle state is kept; " + POWER_RESUME_HINT,
                      style="yellow", markup=False, soft_wrap=True)
        raise click.exceptions.Exit(130)
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc


@cloudex_group.command("list")
@click.option("--benchmark", is_flag=True, help="Stress-test RTT and maximum bidirectional throughput for each connection.")
@click.option("--duration", type=click.IntRange(1, 60), default=10, show_default=True,
              help="Throughput measurement window in seconds.")
@_vulcan_only
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
    if any(status.get("reason") == LEASE_EXPIRED for _session, status, _measurement in rows):
        console.print("Run sima-cli -i cloudex disconnect to remove expired records.", markup=False, soft_wrap=True)
    if benchmark:
        console.print("[bold cyan]Allocation expiry[/bold cyan]")
        for session in sessions:
            label = session.get("device", "DevKit " + _short(session.get("allocation_id")))
            console.print("  {}: {}".format(label, _expiry_text(session.get("expires_at")).replace("\n", " · ")))
    if benchmark and any(row[2].get("error") for row in rows):
        console.print("[yellow]Some benchmarks were unavailable; verify the DevKit is running a forwarder with parallel bidirectional benchmark support. Existing tunnels were left connected.[/yellow]")


def register_cloudex_commands(main):
    main.add_command(cloudex_group)


def _connect_profile(profile, verbose=False, attempts=None, transport="auto", *, diagnostics_api=None,
                     announce_authorization=True):
    if announce_authorization:
        console.print("[cyan]→[/cyan] Authorizing encrypted tunnel access")
    # When the caller already authorized, this only renews sudo's cached credentials.
    _authorize_supported_host()
    with StageProgress("Preparing CloudEx connection", verbose=verbose) as progress:
        options = {"report_diagnostics": diagnostics_api.report_cloudex_diagnostics} if diagnostics_api else {}
        session = connect(profile, _store(), progress, attempts=attempts, transport=transport, **options)
        progress.success("Connected to {}".format(session["device"]))
    path = session.get("ice_path", "unknown")
    path_text = "direct" if path == "direct" else "relayed" if path == "relay" else path
    console.print("  [dim]Session[/dim]  {}".format(_short(session["session_id"])))
    console.print("  [dim]Path[/dim]     {}".format(path_text))
    console.print("  [dim]SSH[/dim]      [bold]ssh sima@{}[/bold]".format(session["target"]))


DURATIONS = {"1h": 3600, "4h": 14400, "8h": 28800, "1d": 86400, "7d": 604800, "4w": 2419200}


def _allocation_id(value):
    if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise CloudExError("Use the full 32-character allocation ID from sima-cli -i cloudex devices")
    return value


def _allocation_profile(api, allocation_id, timeout=180):
    deadline = time.monotonic() + timeout
    with StageProgress("Waiting for device preparation") as progress:
        while True:
            inventory = api.call("GET", "/devkits")
            allocation = next((a for a in inventory.get("allocations", []) if a["session_id"] == allocation_id), None)
            if not allocation or allocation.get("status") in {"released", "releasing", "expired", "failed"}:
                raise CloudExError("Allocation is unavailable; check sima-cli -i cloudex devices")
            if allocation.get("status") in {"ready", "active"}:
                profile = api.call("POST", "/devkits/allocations/" + allocation_id + "/profile", {})
                # Reuse the same strict profile validation as manually supplied keys.
                profile = decode_key(base64.b64encode(json.dumps(profile).encode()).decode())
                progress.success("Device is ready")
                return profile
            if time.monotonic() >= deadline:
                raise CloudExError("Device preparation timed out. Allocation {} is retained; retry with sima-cli -i cloudex connect --allocation {} or release it".format(allocation_id, allocation_id))
            time.sleep(2)


def _neatops_login(api):
    api.login(lambda url: console.print("Open this URL in a browser on this computer:\n" + url, markup=False, soft_wrap=True))
    console.print("[green]Signed in to NeatOps ({})[/green]".format(api.environment))


def _neatops_api(environment):
    _require_vulcan()
    return NeatOps(environment, on_login_required=_neatops_login)


@cloudex_group.command("login")
@click.option("--environment", type=click.Choice(["production", "staging"]), default="production", show_default=True, help="Vulcan environment (requires -i).")
def login_command(environment):
    """Sign in to the Developer Portal, or NeatOps with -i."""
    if not _internal_cloudex():
        if environment != "production":
            raise click.UsageError("--environment selects a Vulcan environment and requires -i.")
        from sima_cli.auth.devportal import login_external
        if not login_external(loginDocker=False):
            raise click.ClickException("Developer Portal sign-in was not completed.")
        console.print("Signed in to the Developer Portal. CloudEx is not available on Arthas yet.")
        return
    try:
        _neatops_login(NeatOps(environment))
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc


@cloudex_group.command("logout")
@click.option("--environment", type=click.Choice(["production", "staging"]), default="production", show_default=True, help="Vulcan environment (requires -i).")
def logout_command(environment):
    """Sign out of the Developer Portal, or NeatOps with -i; keep leases."""
    if not _internal_cloudex():
        if environment != "production":
            raise click.UsageError("--environment selects a Vulcan environment and requires -i.")
        from sima_cli.auth.auth0 import clear_external_login_state
        clear_external_login_state()
        console.print("Signed out of the Developer Portal.")
        return
    try:
        NeatOps(environment).logout()
        console.print("Signed out of NeatOps. Existing device leases are unchanged.")
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc


@cloudex_group.command("devices")
@click.option("--environment", type=click.Choice(["production", "staging"]), default="production", show_default=True, help="Vulcan environment (requires -i).")
@_vulcan_only
def devices_command(environment):
    """Show NeatOps device availability and your allocation IDs."""
    try:
        inventory = _neatops_api(environment).call("GET", "/devkits")
        table = Table("Device type", "Firmware version", "ID", "Allocation ID", "State")
        allocations = {(a["cluster_id"], a["target_id"]): a for a in inventory.get("allocations", [])}
        visible_devices = set()
        for d in inventory.get("devices", []):
            device_key = (d["cluster_id"], d["id"])
            visible_devices.add(device_key)
            a = d.get("allocation") or allocations.get(device_key, {})
            table.add_row(d.get("type") or "Unknown", d.get("firmware") or "Unknown", d["id"],
                          a.get("session_id") or "—", "Available" if d["available"] else "Not available")
        # Allocations can survive an appliance disappearing from inventory.
        for a in inventory.get("allocations", []):
            if (a["cluster_id"], a["target_id"]) not in visible_devices:
                table.add_row("Unknown", "Unknown", a["target_id"], a["session_id"], "Not available")
        console.print(table)
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc


@cloudex_group.command("request")
@click.option("--device", help="Device ID or exact name; otherwise choose interactively.")
@click.option("--cluster", help="Appliance ID, to disambiguate devices.")
@click.option("--duration", type=click.Choice(list(DURATIONS)), default="1h", show_default=True)
@click.option("--environment", type=click.Choice(["production", "staging"]), default="production", show_default=True, help="Vulcan environment (requires -i).")
@click.option("--verbose", is_flag=True, help="Print sanitized connection stages.")
@_vulcan_only
def request_command(device, cluster, duration, environment, verbose):
    """Request a NeatOps device, wait for preparation, and connect automatically."""
    allocation_id = None
    try:
        api = _neatops_api(environment)
        entitlement = api.call("GET", "/users/me/entitlement")["entitlement"]
        if DURATIONS[duration] > entitlement["max_lease_duration_seconds"]:
            raise CloudExError("Requested duration exceeds your NeatOps lease limit; choose a shorter duration")
        inventory = api.call("GET", "/devkits")
        if len(inventory.get("allocations", [])) >= entitlement["max_active_leases"]:
            raise CloudExError("Your device lease limit is reached; connect to an existing allocation or release one")
        candidates = [d for d in inventory.get("devices", []) if d.get("available")
                      and (not cluster or d["cluster_id"] == cluster)
                      and (not device or device in {d["id"], d["name"]})]
        if not candidates:
            raise CloudExError("No matching devices are available; check sima-cli -i cloudex devices")
        if device and len(candidates) > 1:
            raise CloudExError("Device name or ID is ambiguous; specify --cluster")
        if not device:
            table = Table("#", "Device type", "Software version", "ID")
            for index, d in enumerate(candidates, 1):
                table.add_row(str(index), d.get("type") or "Unknown", d.get("firmware") or "Unknown", d["id"])
            console.print(table)
            choice = click.prompt("Choose a device", type=click.IntRange(1, len(candidates)))
            selected = candidates[choice - 1]
        else:
            selected = candidates[0]
        # Get local administrator authorization before reserving scarce hardware.
        console.print("[cyan]→[/cyan] Authorizing encrypted tunnel access")
        _authorize_supported_host()
        allocation_id = uuid.uuid4().hex
        console.print("Allocation {} · {} · {}".format(allocation_id, selected["name"], duration), markup=False)
        api.call("POST", "/devkits/allocations", {"request_id": allocation_id,
                 "cluster_id": selected["cluster_id"], "target_id": selected["id"], "duration_seconds": DURATIONS[duration]})
        profile = _allocation_profile(api, allocation_id)
        _connect_profile(profile, verbose=verbose, diagnostics_api=api, announce_authorization=False)
    except CloudExError as exc:
        if allocation_id:
            console.print("Inspect: sima-cli -i cloudex devices --environment {}\nResume: sima-cli -i cloudex connect --allocation {} --environment {}\nRelease: sima-cli -i cloudex release {} --environment {}".format(environment, allocation_id, environment, allocation_id, environment), markup=False, soft_wrap=True)
        raise click.ClickException(str(exc)) from exc


def _choose_allocations_to_release(api):
    inventory = api.call("GET", "/devkits")
    allocations = inventory.get("allocations", [])
    if not allocations:
        console.print("You have no CloudEx allocations to release.")
        return []

    devices = {(d["cluster_id"], d["id"]): d.get("name") for d in inventory.get("devices", [])}
    choices = []
    for allocation in allocations:
        target = allocation["target_id"]
        name = devices.get((allocation["cluster_id"], target)) or target
        choices.append({
            "name": "{} · {} · {}".format(name, _short(allocation["session_id"]), allocation["status"]),
            "value": allocation["session_id"],
        })
    selected = _select_checkboxes(
        choices,
        message="Select CloudEx allocations to release:",
        instruction="Space to select, Enter to release selected allocations",
        mandatory=False,
    )
    if not selected:
        console.print("[dim]No CloudEx allocations selected.[/dim]")
    return [allocation["session_id"] for allocation in allocations if allocation["session_id"] in selected]


def _release_allocation(allocation_id, store, get_api):
    failures = []
    local_cleanup_blocked = False
    for session in store.list():
        if session.get("allocation_id") == allocation_id:
            try:
                # Rich redirects terminal output during progress rendering.
                # Ask for sudo before starting the spinner, as disconnect does.
                _authorize_tunnel_cleanup(session)
                with StageProgress("Disconnecting CloudEx") as progress:
                    disconnect_session(session, store, progress, authorize=False)
            except CloudExError as exc:
                failures.append("Tunnel cleanup failed: {}. Local retry state retained; retry sima-cli -i cloudex disconnect.".format(exc))
                local_cleanup_blocked |= disconnect_requires_admin(session)
    if local_cleanup_blocked:
        failures.append("Lease release skipped because its local CloudEx tunnel is still running.")
        return failures
    try:
        get_api().call("DELETE", "/devkits/allocations/" + allocation_id)
    except CloudExError as exc:
        failures.append("Lease release failed: {}".format(exc))
    else:
        console.print("Lease released; device availability returns after cleanup is confirmed.")
    return failures


@cloudex_group.command("release")
@click.argument("allocation_id", required=False)
@click.option("--environment", type=click.Choice(["production", "staging"]), default="production", show_default=True, help="Vulcan environment (requires -i).")
@_vulcan_only
def release_command(allocation_id, environment):
    """Disconnect local CloudEx sessions and release their NeatOps allocations.

    Omit ALLOCATION_ID in an interactive terminal to select allocations.
    """
    try:
        api = None
        if allocation_id is None:
            if not _interactive_session_selection_available():
                raise click.UsageError("Provide an allocation ID when running without an interactive terminal. "
                                       "Use sima-cli -i cloudex devices to find your allocation IDs.")
            api = _neatops_api(environment)
            allocation_ids = _choose_allocations_to_release(api)
            if not allocation_ids:
                return
        else:
            allocation_ids = [allocation_id]
        allocation_ids = [_allocation_id(value) for value in allocation_ids]
        store = _store()
        failures = []
        batch = len(allocation_ids) > 1
        for allocation_id in allocation_ids:
            if batch:
                console.print("Releasing allocation {}".format(allocation_id))
            errors = _release_allocation(allocation_id, store, lambda: api or _neatops_api(environment))
            failures.extend("{}: {}".format(allocation_id, error) if batch else error for error in errors)
        if failures:
            raise CloudExError("; ".join(failures))
    except CloudExError as exc:
        raise click.ClickException(str(exc)) from exc
