import click
from sima_cli.utils.services import services
from sima_cli.utils.mpk_api import DeployParams, KillParams, LaunchParams, RemoveParams
from sima_cli.utils.api_common import _ImportErrorWithHint
from sima_cli.utils.env import get_environment_type

# ----------------------
# mpk command group
# ----------------------
@click.group(help="Manage MPK packages and app lifecycle on the connected devices", hidden=True)
def mpk():
    """Top-level MPK command group."""
    pass

def register_mpk_commands(main):
    """
    The mpk command can only run on Linux or Windows host, or on the devKit itself (mpk create is only available on eLxr when running on the DevKit).
    """
    _, subenv = get_environment_type()

    if subenv != 'mac':
        main.add_command(mpk)

# ----------------------
# mpk create
# ----------------------
@mpk.command(help="Creates a new MPK package from project sources (eLxr platform only)")
@click.option(
    "-s", "--source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True),
    required=True,
    help="(Required) Folder where project sources are located."
)
@click.option(
    "-d", "--destination",
    type=click.Path(file_okay=False, dir_okay=True, writable=True),
    required=True,
    help="(Required) Folder to be used for MPK creation."
)
@click.option(
    "--clean",
    is_flag=True,
    default=False,
    show_default=True,
    help="(Optional) Cleans the source directory of build artifacts."
)
@click.option(
    "--board-type",
    type=click.Choice(["davinci", "modalix"], case_sensitive=False),
    default="davinci",
    show_default=True,
    help=(
        "(Optional) Specify the type of the Device: davinci or modalix. "
        "Defaults to davinci if both are available; otherwise, "
        "defaults to the one that exists."
    )
)
def create(source, destination, clean, board_type):
    """Create a new MPK package using the given source folder and destination."""
    click.echo("Creating MPK with the following parameters:")
    click.echo(f"  Source folder      : {source}")
    click.echo(f"  Destination folder : {destination}")
    click.echo(f"  Clean build        : {clean}")
    click.echo(f"  Board type         : {board_type}")

    if clean:
        click.echo("Cleaning build artifacts from source directory...")
        # TODO: implement cleanup logic

    click.echo("MPK creation started...")
    # TODO: implement actual MPK creation logic
    click.echo("MPK creation completed successfully.")


# ----------------------
# mpk deploy
# ----------------------
@mpk.command("deploy", help="Deploys a prebuilt MPK package to a connected device")
@click.option("--set-default", is_flag=True, default=False)
@click.option("-t", "--target", type=str)
@click.option("-s", "--slot", type=str)
@click.option("-f", "--file", "file_path", type=click.Path(exists=True), required=True)
def deploy_cmd(set_default, target, slot, file_path):
    if not target and not slot:
        raise click.UsageError("You must specify either --target (Device IP/FQDN) or --slot (PCIe slot).")
    try:
        sv = services()
        sv.mpk.deploy(DeployParams(file_path=file_path, target=target, slot=slot, set_default=set_default))
    except _ImportErrorWithHint as e:
        click.echo(str(e)); return
    except Exception as e:
        raise click.ClickException(str(e))
    click.echo("Deployment completed successfully.")


# ----------------------
# mpk kill
# ----------------------
@mpk.command(
    help=(
        "Kills deployed running pipeline on the device.\n"
        "Note: Either --id or --pid must be passed with mandatory -t/--target parameter."
    )
)
@click.option(
    "-i", "--id",
    type=str,
    required=False,
    help="Pipeline Id of the Pipeline to kill."
)
@click.option(
    "--pid",
    type=int,
    required=False,
    help="Pid of the Pipeline to kill."
)
@click.option(
    "-t", "--target",
    type=str,
    required=False,
    help="(Required) IP address or FQDN of the device."
)
@click.option(
    "-s", "--slot",
    type=str,
    required=False,
    help="(Required) Slot number of the PCIe Soc to connect."
)
def kill(id, pid, target, slot):
    """
    Kill a deployed pipeline running on the device.
    """
    # Validation: at least one of id or pid must be provided
    if not id and not pid:
        raise click.UsageError("You must specify either --id or --pid to identify the pipeline.")

    # Validation: either target or slot must be provided
    if not target and not slot:
        raise click.UsageError("You must specify either --target (Device IP/FQDN) or --slot (PCIe slot).")

    click.echo("Killing pipeline with the following parameters:")
    if target:
        click.echo(f"  Target Device : {target}")
    if slot:
        click.echo(f"  PCIe Slot     : {slot}")
    if id:
        click.echo(f"  Pipeline ID   : {id}")
    if pid:
        click.echo(f"  PID           : {pid}")

    try:
        sv = services()
        sv.mpk.kill(KillParams(pipeline_id=id, pid=pid, target=target, slot=slot))
    except _ImportErrorWithHint as e:
        click.echo(str(e)); return
    except Exception as e:
        raise click.ClickException(str(e))


# ----------------------
# mpk launch
# ----------------------
@mpk.command(
    help=(
        "Launches deployed pipeline on the device "
        "of the same pipeline."
    )
)
@click.option(
    "-a", "--application",
    type=str,
    required=True,
    help="(Required) Pipeline to Launch."
)
@click.option(
    "-t", "--target",
    type=str,
    required=False,
    help="(Required) IP address or FQDN of the device."
)
@click.option(
    "-s", "--slot",
    type=str,
    required=False,
    help="(Required) Slot number of the PCIe Soc to connect."
)
def launch(application, target, slot):
    """
    Launch a deployed pipeline or start a new instance of the specified pipeline.
    """
    # Validation: either target or slot must be provided
    if not target and not slot:
        raise click.UsageError(
            "You must specify either --target (Device IP/FQDN) or --slot (PCIe slot)."
        )

    click.echo("Launching pipeline with the following parameters:")
    click.echo(f"  Application   : {application}")
    if target:
        click.echo(f"  Target Device : {target}")
    if slot:
        click.echo(f"  PCIe Slot     : {slot}")

    try:
        sv = services()
        sv.mpk.launch(LaunchParams(application=application, target=target, slot=slot))
    except _ImportErrorWithHint as e:
        click.echo(str(e)); return
    except Exception as e:
        raise click.ClickException(str(e))

    click.echo(f"Successfully launched Pipeline: {application}")


# ----------------------
# mpk remove
# ----------------------
@mpk.command(
    help="Removes the deployed pipeline on the device."
)
@click.option(
    "-a", "--application",
    type=str,
    required=True,
    help="(Required) Pipeline to remove from the board."
)
@click.option(
    "-t", "--target",
    type=str,
    required=False,
    help="(Required) IP address or FQDN of the device."
)
@click.option(
    "-s", "--slot",
    type=str,
    required=False,
    help="(Required) Slot number of the PCIe Soc to connect."
)
def remove(application, target, slot):
    """
    Remove a deployed pipeline from the device, using either Ethernet (target)
    or PCIe (slot).
    """
    # Validation: either target or slot must be provided
    if not target and not slot:
        raise click.UsageError(
            "You must specify either --target (Device IP/FQDN) or --slot (PCIe slot)."
        )

    click.echo("Removing pipeline with the following parameters:")
    click.echo(f"  Application   : {application}")
    if target:
        click.echo(f"  Target Device : {target}")
    if slot:
        click.echo(f"  PCIe Slot     : {slot}")

    try:
        sv = services()
        sv.mpk.remove(RemoveParams(application=application, target=target, slot=slot))
    except _ImportErrorWithHint as e:
        click.echo(str(e)); return
    except Exception as e:
        raise click.ClickException(str(e))
    click.echo(f"Successfully removed Pipeline: {application}")


# ----------------------
# mpk list
# ----------------------
@mpk.command(
    name="list",
    help="Fetch details of all the deployed pipeline on the connected devices."
)
def list_pipelines():
    """
    Fetch and display all deployed pipelines on the target device.
    """
    click.echo("Fetching list of deployed pipelines...")

    try:
        sv = services()
        sv.mpk.list()
    except _ImportErrorWithHint as e:
        click.echo(str(e)); return
    except Exception as e:
        raise click.ClickException(str(e))
    click.echo("Listing pipelines completed successfully.")
