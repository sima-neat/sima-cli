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
    adapter_for_type,
    catalog_warnings,
    describe,
    envelope,
    exit_code_for,
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


class _JsonUsageCommand(click.Command):
    """A command whose usage errors (missing <ID>, unknown option, extra argument) are also JSON documents
    when --json is on the command line. Click raises them before the callback runs, so the callback's
    _Output never sees them."""

    def parse_args(self, ctx: click.Context, args: List[str]) -> List[str]:
        as_json = "--json" in args  # Click consumes `args` in place while parsing; read it first.
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as error:
            if not as_json:
                raise
            _Output(True, f"peripherals.{self.name}", None).fail(ExitCode.INVALID_INPUT, {
                "code": "invalid_input",
                "message": error.format_message(),
                "hint": f"Run `sima-cli peripherals {self.name} --help`.",
            })
            raise  # unreachable: fail() exits


def _common_options(function):
    function = click.option("--json", "as_json", is_flag=True, default=False,
                            help="Print JSON instead of text.")(function)
    function = click.option("--refresh", is_flag=True, default=False,
                            help="Look for devices again first (after plugging one in).")(function)
    function = click.option("--socket", "socket_path", default=SOCKET_PATH, show_default=True, hidden=True,
                            help="Development only: the local socket of the SiMa Sentinel service to read from.")(function)
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
        if exc.code == "timeout":
            output.client = None  # Sentinel is not answering; asking it for a version would wait out another timeout.
        output.fail(exit_code_for(exc.code), exc.as_dict())
        raise AssertionError("unreachable")


@click.group("peripherals", options_metavar="", subcommand_metavar="<command>")
def peripherals() -> None:
    """Show the cameras and microphones plugged into this DevKit."""


@peripherals.command("list", cls=_JsonUsageCommand, short_help="Show the connected cameras and microphones.",
                     options_metavar="")
@_common_options
def list_command(as_json: bool, refresh: bool, socket_path: str) -> None:
    """Show the connected cameras and microphones.

    SUPPORT is "Supported" when a Neat application can capture from the camera. `describe` says why
    when it is "Unsupported".
    """
    output = _Output(as_json, "peripherals.list", None)
    client = _require_devkit(output, socket_path)
    catalog = _read_catalog(output, client, refresh)
    devices = [device for device in catalog.get("devices") or [] if isinstance(device, dict)]
    summaries = [summarize(device) for device in devices]
    warnings = catalog_warnings(catalog) + verdict_warnings(devices)
    if as_json:
        document = envelope("peripherals.list", output.devkit(), catalog, {"peripherals": summaries}, warnings=warnings)
        click.echo(json.dumps(document, indent=2))
        return
    if not summaries:
        click.echo("No cameras or microphones detected.")
    else:
        click.echo(tabulate(list_rows(summaries), headers=["ID", "TYPE", "CONNECTION", "DEVICE", "STATUS", "SUPPORT"],
                            tablefmt="plain"))
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)


@peripherals.command("describe", cls=_JsonUsageCommand, short_help="Show one device in detail.", options_metavar="")
@click.argument("device_id", metavar="<ID>")
@_common_options
def describe_command(device_id: str, as_json: bool, refresh: bool, socket_path: str) -> None:
    """Show one device in detail: every resolution and frame rate, and whether Neat can use it.

    ID is from `sima-cli peripherals list`; the device's name works too, for example:

    \b
      sima-cli peripherals describe imx477
    """
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
    adapter = adapter_for_type(summary["type"])
    lines: List[List[str]] = [
        ["ID", str(summary["id"])],
        ["Type", summary["type_label"]],
        ["Connection", summary["connection"]],
        ["Device", summary["name"]],
        ["Status", summary["status"]],
        ["Support", support_label(summary) + (f" ({summary['support_reason']})" if summary["support_reason"] else "")],
    ]
    lines.extend([label, text] for label, text in adapter.fact_lines(description["facts"]))
    board = description.get("board") or {}
    if board.get("firmware"):
        lines.append(["Platform", str(board["firmware"])])
    if board.get("overlays"):
        lines.append(["Overlays", ", ".join(str(o) for o in board["overlays"])])
    click.echo(tabulate(lines, tablefmt="plain"))
    modes = description["modes"]
    if modes:
        click.echo("")
        click.echo(tabulate([adapter.mode_cells(row) for row in modes], headers=list(adapter.mode_headers),
                            tablefmt="plain"))


def register_peripherals_commands(cli: click.Group) -> None:
    cli.add_command(peripherals)
