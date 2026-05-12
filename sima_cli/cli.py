import os
import shutil
import click
from sima_cli.utils.env import get_environment_type
from sima_cli.update.updater import perform_update
from sima_cli.model_zoo.model import list_models, download_model, describe_model
from sima_cli.app_zoo.app import list_apps, download_app, describe_app
from sima_cli.utils.config_loader import internal_resource_exists
from sima_cli.mla.meminfo import monitor_simaai_mem_chart
from sima_cli.__version__ import __version__ 
from sima_cli.utils.config import CONFIG_PATH
from sima_cli.install.optiview import install_optiview
from sima_cli.install.hostdriver import install_hostdriver
from sima_cli.install.metadata_installer import install_from_metadata, metadata_resolver
from sima_cli.serial.serial import connect_serial
from sima_cli.storage.nvme import nvme_format, nvme_remount
from sima_cli.storage.sdcard import sdcard_format
from sima_cli.network.network import network_menu
from sima_cli.utils.pkg_update_check import check_for_update
from sima_cli.utils.container_registries import docker_logout_from_registry, install_from_cr
from sima_cli.utils.env import is_devkit_running_elxr, is_modalix_devkit
from sima_cli.sdk.commands import register_sdk_commands
from sima_cli.deploy_only.mpk.commands import register_mpk_commands
from sima_cli.deploy_only.device.commands import register_device_commands
from sima_cli.utils.tag import resolve_version
from sima_cli.utils.artifactory import check_artifactory_reachability
from sima_cli.app_zoo.commands import register_appzoo_commands
from sima_cli.install.registry import register_packages_commands
from sima_cli.upgrade.selfupdate import register_selfupdate_command
from sima_cli.playbooks import register_playbook_commands

# Entry point for the CLI tool using Click's command group decorator
@click.group(context_settings=dict(help_option_names=["-h", "--help", "-?"], max_content_width=120))
@click.option('-i', '--internal', is_flag=True, help="Use internal Artifactory resources, Authorized Sima employees only")
@click.pass_context
def main(ctx, internal):
    """
    sima-cli – SiMa Developer Portal CLI Tool

    Global Options:
      --internal  Use internal Artifactory resources (can also be set via env variable SIMA_CLI_INTERNAL=1)
    """
    check_for_update('sima-cli')
    ctx.ensure_object(dict)

    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)

    # Allow env override if --internal not explicitly passed
    if not internal:
        internal = os.getenv("SIMA_CLI_INTERNAL", "0") in ("1", "true", "yes")

    if internal and not internal_resource_exists():
        click.echo("❌ You have specified -i or --internal argument to access internal resources, but you do not have an internal resource map configured.")        
        click.echo("Refer to the confluence page to find out how to configure internal resource map.")
        exit(0)        

    if internal and not check_artifactory_reachability():
        click.secho("❌ You have specified -i or --internal argument to access internal resources, but you can't connect to Artifactory.", fg='red')
        click.secho("Please make sure you are connected to VPN or are on the corporate network.", fg='red')
        exit(0)
        

    ctx.obj["internal"] = internal

    env_type, env_subtype = get_environment_type()

    if internal:
        click.echo(f"🔧 Environment: {env_type} ({env_subtype}) | Internal: {internal}")
    else:
        click.echo(f"🔧 Environment: {env_type} ({env_subtype})")


# ----------------------
# SDK Command
# ----------------------
register_sdk_commands(main)
register_playbook_commands(main)


# ----------------------
# Authentication Command
# ----------------------
@main.command()
@click.pass_context
def login(ctx):
    """Authenticate with the SiMa Developer Portal."""

    from sima_cli.auth import login as perform_login

    internal = ctx.obj.get("internal", False)
    perform_login.login("internal" if internal else "external")

# ----------------------
# Version Command
# ----------------------
@main.command(name="version")
def version_cmd():
    """Show the version of the CLI tool."""
    click.echo(f"SiMa CLI version: {__version__}")

# ----------------------
# Logout Command
# ----------------------
@main.command(name="logout")
@click.pass_context
def logout_cmd(ctx):
    """Log out by deleting cached credentials and config files."""
    sima_cli_dir = os.path.expanduser("~/.sima-cli")
    internal = ctx.obj.get("internal", False)

    if not os.path.isdir(sima_cli_dir):
        click.echo("⚠️ No ~/.sima-cli directory found.")
        return

    if internal:
        target_files = ["config.json"]
        docker_logout_from_registry()
    else:
        target_files = [".sima-cli-cookies.txt", ".sima-cli-csrf.json", ".tokens.json"]

    for filename in target_files:
        full_path = os.path.join(sima_cli_dir, filename)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception as e:
                click.echo(f"⚠️ Failed to delete {full_path}: {e}", err=True)

    # Remove sima-cli-specific Docker credential profile as part of logout.
    docker_cfg_dir = os.path.join(sima_cli_dir, "docker-config")
    if os.path.isdir(docker_cfg_dir):
        try:
            shutil.rmtree(docker_cfg_dir)
        except Exception as e:
            click.echo(f"⚠️ Failed to delete {docker_cfg_dir}: {e}", err=True)

    click.echo("✅ Logged out successfully.")

# ----------------------
# Download Command
# ----------------------
@main.command(name="download")
@click.argument('url')  # Accept both file and folder URLs
@click.option('-d', '--dest', type=click.Path(), default='.', help="Target download directory")
@click.pass_context
def download(ctx, url, dest):
    """Download a file or a whole folder from a given URL."""
    from sima_cli.download.downloader import download_file_from_url, download_folder_from_url

    internal = ctx.obj.get("internal", False)

    # First, try to download as a file
    try:
        click.echo("🔍 Checking if URL is a direct file...")
        path = download_file_from_url(url, dest, internal)
        click.echo(f"\n✅ File downloaded successfully to: {path}")
        return
    except Exception as e:
        click.echo(f"❌ Failed to download as file {e}")
        pass

    # If that fails, try to treat as a folder and download all files
    try:
        click.echo("🔍 Attempting folder download...")
        paths = download_folder_from_url(url, dest, internal)
        if not paths:
            raise RuntimeError("No files were downloaded.")
        click.echo(f"\n✅ Folder download completed. {len(paths)} files saved to: {dest}")
    except Exception as e:
        click.echo(f"\n❌ Failed to download as folder: {e}", err=True)

# ----------------------
# Update Command
# ----------------------
@main.command(name="update")
@click.argument("version_or_url", required=False)
@click.option(
    "-v", "--version",
    "version_option",
    help="Specify version string (e.g., '1.7.0', 'ga', 'beta', or a direct firmware URL). Default is GA if not specified"
         "Overrides positional argument if both are given."
)
@click.option("--ip", help="Target device IP address for remote firmware update.")
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation after firmware file is downloaded."
)
@click.option(
    "-p", "--passwd",
    default="edgeai",
    show_default=True,
    help="Optional SSH password for remote board (default is 'edgeai')."
)
@click.option(
    "-f", "--flavor",
    type=click.Choice(["headless", "full", "auto"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Firmware flavor: 'full' image supports NVMe and GUI on Modalix DevKit. This option is deprecated for 2.0 and above"
)
@click.option(
    "-t", "--troot_only",
    is_flag=True,
    default=False,
    help="Only update tRoot and not the root file system, compatible with Yocto system only, used for Yocto to eLxr conversion."
)
@click.pass_context
def update(ctx, version_or_url, version_option, ip, yes, passwd, flavor, troot_only):
    """
    Update the software on a SiMa DevKit or remote SiMa device.

    This command downloads and applies system software updates across
    different SiMa environments (Modalix, MLSoC/Davinci, headless images,
    or remote devices accessible over the network). Updates may be
    installed directly on the device or pushed from a development host.

    How Version Resolution Works:

      • If a version string is provided (e.g., ``1.7.0``), sima-cli automatically resolves it to the correct downloadable firmware asset based on channel, flavor, and board type.

      • If a URL or local bundle path is provided, sima-cli will use the specified file directly.

      • The ``--version`` option overrides the positional argument (``VERSION_OR_URL``).

    Requirements:
    
      • A valid SiMa Developer Portal account

      • You must run ``sima-cli login`` before performing updates

      • Remote updates require an accessible IP address (``--ip``)

    Typical Use Cases:

      • Updating a SiMa DevKit to the latest GA release

      • Pushing a test build to a remote Modalix device

      • Applying a specific firmware version during bring-up

      • Running updates from both the device itself or a host PC

    \b
    Examples:

        # Update the device you're currently logged into

        sima-cli update

        # Update a remote device by IP address

        sima-cli update --ip 192.168.6.5

        # Update to a specific version

        sima-cli update -v 1.7.0

        # Update using a direct firmware bundle URL

        sima-cli update https://example.com/fw/sima-1.8.0.tar.gz

        # Silent/auto-confirm mode

        sima-cli update -v 1.7.0 -y

        # Provide root password for remote updates

        sima-cli update --ip 192.168.6.5 --passwd root

    """
    # Prioritize explicit --version option over positional argument
    version_or_url = version_option or version_or_url

    # Resolve the version or tag (GA/Beta/Alpha/QA) if not on eLxr
    # On eLxr always use latest (no version) or explicit version string

    if not is_devkit_running_elxr():
        version_or_url = resolve_version(version_or_url)
    
    if is_devkit_running_elxr() and troot_only:
        click.secho("⚠️  Running update on eLxr with troot_only flag is not supported, this setting is ignored..", fg='yellow')

    click.echo(f"➡️  Updating with {version_or_url}")

    # Extract context and run update logic
    internal = ctx.obj.get("internal", False)
    perform_update(
        version_or_url,
        ip,
        internal,
        passwd=passwd,
        auto_confirm=yes,
        flavor=flavor,
        troot_only=troot_only
    )

# ----------------------
# Model Zoo Subcommands
# ----------------------
@main.group()
@click.option(
    "-v", "--ver", "--version",
    "ver",
    required=False,
    help="SDK version (e.g. 1.7.0, 2.0.0). If not provided, the current GA version will be used.",
)
@click.option(
    "--boardtype",
    type=click.Choice(["mlsoc", "modalix"], case_sensitive=False),
    default='modalix',
    required=False,
    help="Target board type (mlsoc or modalix).",
)
@click.pass_context
def modelzoo(ctx, ver, boardtype):
    """Access models from the Model Zoo."""
    ctx.ensure_object(dict)
    ctx.obj['ver'] = resolve_version(ver)
    ctx.obj["boardtype"] = boardtype

    pass

@modelzoo.command("list")
@click.pass_context
def list_models_cmd(ctx):
    """List available models."""
    internal = ctx.obj.get("internal", False)
    version = ctx.obj.get("ver")
    boardtype = ctx.obj.get('boardtype')
    click.echo(f"Listing models for version: {version}")
    list_models(internal, version, boardtype)

@modelzoo.command("get")
@click.argument('model_name') 
@click.pass_context
def get_model(ctx, model_name):
    """Download a specific model."""
    ver = ctx.obj.get("ver")
    internal = ctx.obj.get("internal", False)
    boardtype = ctx.obj.get('boardtype')
    click.echo(f"Getting model '{model_name}' for version: {ver}")
    download_model(internal, ver, boardtype, model_name)

@modelzoo.command("describe")
@click.argument('model_name') 
@click.pass_context
def get_model(ctx, model_name):
    """Download a specific model."""
    ver = ctx.obj.get("ver")
    internal = ctx.obj.get("internal", False)
    boardtype = ctx.obj.get('boardtype')
    click.echo(f"Getting model '{model_name}' for version: {ver}")
    describe_model(internal, ver, boardtype, model_name)

# ----------------------
# App Zoo Subcommands
# ----------------------
register_appzoo_commands(main)

# ----------------------
# MLA Command
# ----------------------
@main.group()
@click.pass_context
def mla(ctx):
    """Machine Learning Accelerator Utilities."""
    env_type, _ = get_environment_type()
    if env_type != 'board':
        click.echo("❌ This command can only be executed on the SiMa board.")
        raise click.Abort()
    pass

@mla.command("meminfo")
@click.pass_context
def show_mla_memory_usage(ctx):
    """Show MLA Memory usage overtime."""
    monitor_simaai_mem_chart()
    pass


# ----------------------
# bootimg Command
# ----------------------
@main.command(name="bootimg")
@click.option("-v", "--version", required=True, help="Firmware version to download and write (e.g., 1.6.0)")
@click.option("-b", "--boardtype", type=click.Choice(["modalix",  "mlsoc"], case_sensitive=False), default="mlsoc", show_default=True, help="Target board type.")
@click.option("-t", "--fwtype", type=click.Choice(["yocto",  "elxr"], case_sensitive=False), default="yocto", show_default=True, help="Target firmware type.")
@click.option("-n", "--netboot", is_flag=True, default=False, show_default=True, help="Prepare image for network boot and launch TFTP server.")
@click.option("--devkit-ip", required=False, help="Optional DevKit IP address for pre-netboot version probing.")
@click.option("-r", "--rootfs", required=False, help="Custom root fs folders (internal use only)")
@click.option("-a", "--autoflash", is_flag=True, default=False, show_default=True, help="Net boot the DevKit and automatically flash the internal storage - TBD")
@click.pass_context
def bootimg_cmd(ctx, version, boardtype, netboot, devkit_ip, autoflash, fwtype, rootfs):
    """
    Prepare a bootable image for the SiMa DevKit.

    This command downloads the specified firmware version and prepares a
    removable boot medium (SD card or USB) or configures a TFTP-based
    network boot environment. It supports both MLSoC- and Modalix-based
    DevKits, as well as Yocto and eLxr firmware types.

    Operations Performed:

      • Download the correct firmware bundle for the selected version
    
      • Build a bootable disk image (SD/USB) OR configure TFTP netboot

      • (Optional) Boot the DevKit over the network and flash internal eMMC storage (use `f` command)

      • Support for internal/testing rootfs overrides (`--rootfs`)

    Typical Use Cases:

        • Flashing a new firmware version to an SD card

        • Setting up a fast development loop using TFTP netboot

        • Preparing an eLxr-based bring-up image for Modalix DevKits

        • Automating eMMC flashing over the network

    \b
    Examples:

        # Write an SD card image for an MLSoC DevKit

        sima-cli bootimg -v 1.6.0 --boardtype mlsoc

        # Set up netboot for a Modalix DevKit

        sima-cli bootimg -v 1.6.0 --boardtype modalix --netboot

        # Set up netboot and probe an existing DevKit first

        sima-cli bootimg -v 2.1.0 --boardtype modalix --netboot --devkit-ip 192.168.1.20

        # Prepare an eLxr netboot image for Modalix

        sima-cli bootimg -v 2.0.0 --boardtype modalix --fwtype elxr --netboot

    """

    from sima_cli.update.bootimg import write_image
    from sima_cli.update.netboot import setup_netboot
    from sima_cli.update.remote import get_remote_board_info

    internal = ctx.obj.get("internal", False)

    click.echo(f"📦 Preparing boot image:")
    click.echo(f"   🔹 Version   : {version}")
    click.echo(f"   🔹 Board Type: {boardtype}")
    click.echo(f"   🔹 F/W Type  : {fwtype}")
    click.echo(f"   🔹 F/W Flavor: headless")
    click.echo(f"   🔹 Custom RootFS: {rootfs}")
    click.echo(f"   🔹 DevKit IP : {devkit_ip}")
    
    try:
        if devkit_ip and (netboot or autoflash):
            click.echo(f"🔎 Probing DevKit version at {devkit_ip} ...")
            _, remote_version, _, _, _ = get_remote_board_info(devkit_ip)
            if not remote_version:
                raise click.ClickException(
                    f"Unable to retrieve remote version from DevKit at {devkit_ip}."
                )
            click.echo(f"✅ DevKit current version: {remote_version}")

        boardtype = boardtype if boardtype != 'mlsoc' else 'davinci'
        if netboot or autoflash:
            setup_netboot(version, boardtype, internal, autoflash, flavor='headless', rootfs=rootfs, swtype=fwtype)
            click.echo("✅ Netboot image prepared and TFTP server is running.")
        else:
            write_image(version, boardtype, fwtype, internal, flavor='headless')
            click.echo("✅ Boot image successfully written.")
        click.echo("✅ Boot image successfully written.")
    except Exception as e:
        click.echo(f"❌ Failed to write boot image: {e}", err=True)
        ctx.exit(1)

# ----------------------
# install Command
# ----------------------
SDK_DEPENDENT_COMPONENTS = {"palette", "hostdriver"}
SDK_INDEPENDENT_COMPONENTS = {"optiview"}
ALL_COMPONENTS = SDK_DEPENDENT_COMPONENTS | SDK_INDEPENDENT_COMPONENTS

@main.command(name="install")
@click.argument("component", required=False)
@click.option("-v", "--version", help="SDK version (required for SDK-dependent components unless --metadata is provided)")
@click.option("-m", "--mirror", help="URL to a metadata.json file for generic installation")
@click.option("-t", "--tag", help="Tag of the package (optional)")
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    help="Force installation even if compatibility checks fail.",
)
@click.pass_context
def install_cmd(ctx, component, version, mirror, tag, force):
    """
    Install SiMa packages.

    This command is the unified installer for all SiMa-defined packages—
    regardless of package type, platform, or deployment location. Packages
    follow the standard metadata specification (metadata.json), with optional
    extended formats such as metadata-v2.json.

    Key Features:

      • Automatically resolves the package URL, allowing simplified command syntax.

      • Handles all supported asset retrieval flows: Developer Portal authenticated downloads, Hugging Face artifacts, GitHub releases/assets, Docker Hub images, and raw file URLs.

      • Performs prerequisite checks (e.g., OS compatibility, disk space, platform model).

      • Supports interactive multi-selection when a package defines optional components.

      • Automatically prompts for authentication when required.

      • Works uniformly across all SiMa package types—including SDK releases, OptiView, LLiMa demos, 16-channel vision demos, and internal tools.

    \b
    Examples:
        sima-cli install hostdriver -v 1.6.0

        sima-cli install optiview

        sima-cli install -m https://custom-server/packages/foo/metadata.json

        sima-cli install samples/llima -v 1.7.0
    """
    internal = ctx.obj.get("internal", False)

    # Metadata-based installation path
    if mirror:
        if component:
            click.echo(f"⚠️ Component '{component}' is ignored when using --metadata. Proceeding with metadata-based installation.")
        click.echo(f"🔧 Installing generic component from metadata URL: {mirror}")
        return install_from_metadata(metadata_url=mirror, internal=internal, force=force)

    # No component and no metadata: error
    if not component:
        click.echo("❌ You must specify either a component name or provide --metadata.")
        ctx.exit(1)

    # if user specified gh: as component, treat it the same as -m
    if component.startswith("gh:"):
        return install_from_metadata(metadata_url=component, internal=False, force=force)
    
    # if the user specified cr: or ghcr: as component, install from container registry
    if component.startswith("cr:") or component.startswith("ghcr:"):
        return install_from_cr(resource_spec=component, internal=internal)

    version = resolve_version(version)

    # Validate version requirement
    if component in SDK_DEPENDENT_COMPONENTS and not version:
        click.echo(f"❌ The component '{component}' requires a specific SDK version. Please provide one using -v.")
        ctx.exit(1)

    component = component.lower()

    if component in SDK_INDEPENDENT_COMPONENTS and version:
        click.echo(f"ℹ️  The component '{component}' does not require an SDK version. Ignoring -v {version}.")

    # Hardcoded component installation
    if component == "palette":
        click.echo(f"🔧 Installing SDK component 'palette' for version {version} is not implemented yet...")
    elif component == "hostdriver":
        click.echo(f"🔧 Installing SDK component 'hostdriver' for version {version}...")
        install_hostdriver(version=version, internal=internal)
    elif component == "optiview":
        click.echo("🔧 Installing tool 'optiview'...")
        install_optiview()
    else:
        # Case 4: Try to resolve metadata URL from version + tag
        try:
            metadata_url = metadata_resolver(component, version, tag)
            click.echo(f"🔧 Installing '{component}' from resolved metadata: {metadata_url}")
            if install_from_metadata(metadata_url=metadata_url, internal=internal, force=force):
                click.echo("✅ Installation complete.")
        except Exception as e:
            click.echo(f"❌ Failed to resolve metadata for component '{component}': {e}")
            ctx.exit(1)

    click.echo("✅ Installation complete.")


# ----------------------
# Serial Subcommands
# ----------------------
@main.command(name="serial")
@click.option("-b", "--baud", default=115200, show_default=True, help="Baud rate for the serial connection")
@click.pass_context
def serial_cmd(ctx, baud):
    """
    Connect to the UART serial console of the DevKit.

    Automatically detects the serial port and launches a terminal emulator:

    - macOS: uses 'picocom'
    
    - Linux: uses 'picocom'
    
    - Windows: shows PuTTY/Tera Term setup instructions
    """
    connect_serial(ctx, baud)

# ----------------------
# Network Subcommands
# ----------------------
@main.command(name="network")
@click.pass_context
def network_cmd(ctx):
    """
    Setup Network IP address on the DevKit

    This command only works on the DevKit. It allows user to switch between DHCP and Static (Default addresses) IP.

    """
    network_menu()

# ----------------------
# NVME Subcommands
# ----------------------
NVME_OPERATIONS = {"format", "remount"}
@main.command(name="nvme")
@click.argument("operation", type=click.Choice(NVME_OPERATIONS, case_sensitive=False))
@click.pass_context
def nvme_cmd(ctx, operation):
    """
    Perform NVMe operations on the Modalix DevKit.

    Available operations:

      format   - Format the NVMe drive and mount it to /media/nvme

      remount  - Remount the existing NVMe partition to /media/nvme

    Example:
      sima-cli nvme format

      sima-cli nvme remount
    """
    # Abort when not on nvme to avoid the risk of accidentally formatting
    if not is_modalix_devkit():
        raise click.ClickException("❌ This command can only be executed on the Modalix DevKit.")

    operation = operation.lower()

    if operation == "format":
        nvme_format()

    elif operation == "remount":
        try:
            nvme_remount()
        except Exception as e:
            click.echo(f"❌ Failed to remount NVMe drive: {e}")
            ctx.exit(1)

    else:
        click.echo(f"❌ Unsupported NVMe operation: {operation}")
        ctx.exit(1)

# ----------------------
# NVME Subcommands
# ----------------------
NVME_OPERATIONS = {"format"}
@main.command(name="sdcard")
@click.argument("operation", type=click.Choice(NVME_OPERATIONS, case_sensitive=False))
@click.pass_context
def sdcard_cmd(ctx, operation):
    """
    Prepare the SD Card as a data storage device for MLSoc DevKit or Modalix Early Access Unit

    Available operations:

      format   - Format the SD Card

    Example:
      sima-cli sdcard format
    """
    operation = operation.lower()

    if operation == "format":
        sdcard_format()


# ------------------------------
# Deploy-only mode subcommands
# ------------------------------
register_device_commands(main)
register_mpk_commands(main)

# ------------------------------
# packages commands
# ------------------------------
register_packages_commands(main)

# ------------------------------
# selfupdate commands
# ------------------------------
register_selfupdate_command(main)

# ----------------------
# App Zoo Subcommands
# ----------------------
# @main.group()
# @click.pass_context
# def app_zoo(ctx):
#     """Access apps from the App Zoo."""
#     pass

# @app_zoo.command("list")
# @click.option('--ver', help="SDK version")
# @click.pass_context
# def list_apps(ctx, ver):
#     """List available apps."""
#     # Placeholder: Call API to list apps
#     click.echo(f"Listing apps for version: {ver or 'latest'}")

# @app_zoo.command("get")
# @click.argument('app_name')  # Required: app name
# @click.option('--ver', help="SDK version")
# @click.pass_context
# def get_app(ctx, app_name, ver):
#     """Download a specific app."""
#     # Placeholder: Download and validate app
#     click.echo(f"Getting app '{app_name}' for version: {ver or 'latest'}")

# ----------------------
# Entry point for direct execution
# ----------------------
if __name__ == "__main__":
    main()
