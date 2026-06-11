
import click
from rich.console import Console
from rich.panel import Panel

from sima_cli.app_zoo.app import list_apps, download_app, describe_app, clone_apps
from sima_cli.utils.deprecation import should_show_post_neat_ga_deprecation_notice
from sima_cli.utils.tag import resolve_version

console = Console()


def show_appzoo_deprecation_notice() -> None:
    if not should_show_post_neat_ga_deprecation_notice():
        return

    console.print(
        Panel(
            "[yellow]App Zoo is compatible with legacy Palette SDKs and will be deprecated soon.[/yellow]\n\n"
            "Use https://developer.sima.ai/examples to access current example applications.",
            title="App Zoo Deprecation Notice",
            border_style="yellow",
            style="yellow",
            expand=False,
        )
    )


@click.group()
@click.option(
    "-v", "--ver", "--version",
    "ver",
    required=False,
    help="SDK version (e.g. 1.7.0, 2.0.0). If not provided, you can select from available versions.",
)
@click.pass_context
def appzoo(ctx, ver):
    """Access sample apps from the App Zoo."""
    ctx.ensure_object(dict)
    show_appzoo_deprecation_notice()
    internal = ctx.obj.get("internal", False)
    if not internal:
        ver = resolve_version(ver)
    ctx.obj['ver'] = ver
    pass

@appzoo.command("list")
@click.pass_context
def list_apps_cmd(ctx):
    """List available models."""
    internal = ctx.obj.get("internal", False)
    version = ctx.obj.get("ver")
    click.echo(f"Listing apps for version: {version}")
    list_apps(internal, version)

@appzoo.command("get")
@click.argument('app_name') 
@click.pass_context
def get_app(ctx, app_name):
    """Download a specific model."""
    ver = ctx.obj.get("ver")
    internal = ctx.obj.get("internal", False)
    click.echo(f"Getting app '{app_name}' for version: {ver}")
    download_app(internal, ver, app_name)

@appzoo.command("clone")
@click.pass_context
def clone_apps_cmd(ctx):
    """Clone the version specific appzoo."""
    internal = ctx.obj.get("internal", False)
    version = ctx.obj.get("ver")
    click.echo(f"Listing apps for version: {version}")
    clone_apps(internal, version)

@appzoo.command("describe")
@click.argument('app_name') 
@click.pass_context
def get_model(ctx, app_name):
    """Download a specific model."""
    ver = ctx.obj.get("ver")
    internal = ctx.obj.get("internal", False)
    click.echo(f"Getting model '{app_name}' for version: {ver}")
    describe_app(internal, ver, app_name)

def register_appzoo_commands(main):
    """
    Register the App Zoo command group with the main CLI entry point.

    This allows the main `sima-cli` to include all App Zoo–related subcommands
    (e.g., `list`, `get`, `describe`) under the `appzoo` group.

    Example:
        from .app import register_appzoo_commands
        register_appzoo_commands(main)
    """
    main.add_command(appzoo)
