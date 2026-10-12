"""`sima-cli peripherals`: what is attached to this DevKit, and which camera modes Neat can use.

Runs on the DevKit only, where SiMa Sentinel serves the catalog on a local socket. Discovery is
read-only: Sentinel never opens, configures or streams from a device, and neither does this command.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

import click
from tabulate import tabulate

from sima_cli.__version__ import __version__
from sima_cli.utils.env import get_sima_build_version, is_sima_board

from .model import (
    ExitCode,
    TYPES,
    catalog_warnings,
    describe,
    envelope,
    exit_code_for,
    filter_devices,
    find_device,
    list_rows,
    summarize,
    support_label,
    verdict_warnings,
)
from .sentinel import SOCKET_PATH, SentinelClient, SentinelError

NOT_DEVKIT_MESSAGE = ("`sima-cli peripherals` runs on the DevKit, where SiMa Sentinel reads the hardware. "
                      "Connect to the DevKit (for example `ssh sima@<devkit-ip>`) and run it there.")


class _Output:
    """Where results and errors go: JSON on stdout only, everything else on stderr."""

    def __init__(self, as_json: bool, command: str, client: Optional[SentinelClient]):
        self.as_json = as_json
        self.command = command
        self.client = client

    def devkit(self) -> Dict[str, Any]:
        _, build = get_sima_build_version()
        sentinel_version = None
        if self.client is not None:
            try:
                sentinel_version = self.client.health().get("version")
            except SentinelError:
                sentinel_version = None
        return {"build_version": build, "sentinel_version": sentinel_version, "sima_cli_version": __version__}

    def fail(self, code: int, error: Dict[str, Any], catalog: Optional[Dict[str, Any]] = None) -> None:
        if self.as_json:
            document = envelope(self.command, self.devkit(), catalog, {"error": error}, result="error")
            click.echo(json.dumps(document, indent=2))
        else:
            click.echo(f"Error: {error['message']}", err=True)
            if error.get("hint"):
                click.echo(error["hint"], err=True)
        sys.exit(code)


def _common_options(function):
    function = click.option("--json", "as_json", is_flag=True, default=False,
                            help="Print a versioned JSON document on stdout; implies --non-interactive.")(function)
    function = click.option("--refresh", is_flag=True, default=False,
                            help="Rescan the board before reading (otherwise the catalog Sentinel holds is returned).")(function)
    function = click.option("--non-interactive", is_flag=True, default=False,
                            help="Never prompt; fail with a result code instead. Accepted for scripts: these commands do not prompt.")(function)
    function = click.option("--socket", "socket_path", default=SOCKET_PATH, show_default=True, hidden=True,
                            help="Sentinel's API socket.")(function)
    return function


def _require_devkit(output: _Output, socket_path: str) -> SentinelClient:
    """The gate: off the DevKit nothing is opened, not even the socket for a version in the error document."""
    if not is_sima_board():
        output.fail(ExitCode.NOT_DEVKIT, {"code": "not_devkit", "message": NOT_DEVKIT_MESSAGE})
    client = SentinelClient(socket_path)
    output.client = client
    return client


def _read_catalog(output: _Output, client: SentinelClient, refresh: bool) -> Dict[str, Any]:
    try:
        return client.catalog(refresh=refresh)
    except SentinelError as exc:
        output.fail(exit_code_for(exc.code), exc.as_dict())
        raise AssertionError("unreachable")


@click.group("peripherals")
def peripherals() -> None:
    """Discover the peripherals attached to this DevKit, read from SiMa Sentinel.

    Cameras and microphones are listed with a stable ID and, for cameras, Sentinel's verdict on
    whether any of their modes can be used by Neat's CameraInput on this board. Discovery is
    read-only and safe to run beside a running application.
    """


@peripherals.command("list")
@click.option("--type", "kind", default=None, metavar="TYPE",
              help=f"Only devices of this type, as Sentinel names it: {', '.join(TYPES)}, or a type a newer Sentinel adds.")
@_common_options
def list_command(kind: Optional[str], as_json: bool, refresh: bool, non_interactive: bool, socket_path: str) -> None:
    """List connected peripherals with their type, connection, status and support."""
    output = _Output(as_json, "peripherals.list", None)
    client = _require_devkit(output, socket_path)
    catalog = _read_catalog(output, client, refresh)
    devices = filter_devices(catalog.get("devices") or [], kind)
    summaries = [summarize(device) for device in devices]
    warnings = catalog_warnings(catalog) + verdict_warnings(devices)
    if as_json:
        document = envelope("peripherals.list", output.devkit(), catalog, {"peripherals": summaries}, warnings=warnings)
        click.echo(json.dumps(document, indent=2))
        return
    if not summaries:
        what = f"{kind}s" if kind else "peripherals"
        click.echo(f"No {what} detected.")
    else:
        click.echo(tabulate(list_rows(summaries), headers=["ID", "TYPE", "CONNECTION", "DEVICE", "STATUS", "SUPPORT"],
                            tablefmt="plain"))
        unsupported = [s for s in summaries if s["support"] == "unsupported"]
        for summary in unsupported:
            click.echo(f"  {summary['id']}: {summary['support_reason']}")
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)


@peripherals.command("describe")
@click.argument("device_id")
@_common_options
def describe_command(device_id: str, as_json: bool, refresh: bool, non_interactive: bool, socket_path: str) -> None:
    """Show one peripheral: identity, device nodes, every mode and Sentinel's verdict on each."""
    output = _Output(as_json, "peripherals.describe", None)
    client = _require_devkit(output, socket_path)
    catalog = _read_catalog(output, client, refresh)
    devices = catalog.get("devices") or []
    device = find_device(devices, device_id)
    if device is None:
        known = ", ".join(str(d.get("id")) for d in devices if isinstance(d, dict)) or "none detected"
        output.fail(ExitCode.INVALID_INPUT, {"code": "unknown_device",
                                             "message": f"No peripheral with id or name {device_id!r}.",
                                             "hint": f"Known ids: {known}. Run `sima-cli peripherals list`."}, catalog)
        return
    description = describe(device, catalog.get("board"))
    warnings = catalog_warnings(catalog) + verdict_warnings([device])
    if as_json:
        document = envelope("peripherals.describe", output.devkit(), catalog, description, warnings=warnings)
        click.echo(json.dumps(document, indent=2))
        return
    _print_description(description)
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)


def _print_description(description: Dict[str, Any]) -> None:
    summary = description["summary"]
    device = description["device"]
    lines: List[List[str]] = [
        ["ID", str(summary["id"])],
        ["Type", summary["type_label"]],
        ["Connection", summary["connection"]],
        ["Device", summary["name"]],
        ["Status", summary["status"]],
        ["Support", support_label(summary) + (f" ({summary['support_reason']})" if summary["support_reason"] else "")],
    ]
    for key in ("camera_name", "media_device", "bus_info", "csi_receiver", "device_path", "by_id_path"):
        if device.get(key):
            lines.append([key.replace("_", " ").capitalize(), str(device[key])])
    isp = device.get("isp")
    if isinstance(isp, dict):
        lines.append(["ISP", f"{isp.get('state')}" + (f", sizes set at run time" if isp.get("sizing") == "runtime" else "")])
    board = description.get("board") or {}
    if board.get("firmware"):
        lines.append(["Platform", str(board["firmware"])])
    if board.get("overlays"):
        lines.append(["Overlays", ", ".join(str(o) for o in board["overlays"])])
    click.echo(tabulate(lines, tablefmt="plain"))
    modes = description["modes"]
    if modes:
        click.echo("")
        rows = []
        for mode in modes:
            size = f"{mode['width']}x{mode['height']}" if mode.get("width") else "-"
            rates = ", ".join(f"{r:g}" for r in mode["rates"]) or "-"
            verdict = "Available" if mode["available"] is True else "Unsupported" if mode["available"] is False else "-"
            rows.append([mode["format"], size, rates, verdict, mode["reason"]])
        click.echo(tabulate(rows, headers=["FORMAT", "SIZE", "FPS", "VERDICT", "REASON"], tablefmt="plain"))


def register_peripherals_commands(cli: click.Group) -> None:
    cli.add_command(peripherals)
