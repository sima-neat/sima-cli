import click
from tabulate import tabulate
from sima_cli.utils.services import services
from sima_cli.utils.device_api import DeviceCreateParams, FirmwareUpgradeParams
from sima_cli.utils.api_common import _ImportErrorWithHint
from sima_cli.utils.env import get_environment_type
from sima_cli.discover.discover import discover_and_probe, discover_and_render_pcie_devices

# ----------------------
# top-level device group
# ----------------------

# Dynamically set help based on platform
_env, _subenv = get_environment_type()
if _subenv == "mac":
    _device_help = "Discover nearby SiMa.ai devices on the local network."
else:
    _device_help = (
        "Discover and manage device(s) for MPK deployment and app lifecycle management "
        "purposes, compatible with both PCIe and Ethernet deployment models. Host side only."
    )

@click.group(
    name="device",
    help=_device_help,
    hidden=False,
    context_settings=dict(help_option_names=["-h", "--help"])
)
def device():
    """Device management subcommands."""
    pass

# ----------------------
# register commands
# ----------------------
def register_device_commands(main):
    """
    Register the device command group dynamically depending on platform.
    - On macOS: only `device discover` is available.
    - On Linux/Windows: all subcommands are available.
    """
    _, subenv = get_environment_type()

    # always add the device group (for discover)
    main.add_command(device)

    # If running on macOS, limit to discover only
    if subenv == "mac":
        # remove all hidden commands except discover
        for cmd_name in list(device.commands.keys()):
            if cmd_name != "discover":
                device.commands[cmd_name].hidden = True
        return

# ----------------------
# device discover
# ----------------------
@device.command(
    name="discover",
    help="Discover nearby SiMa.ai DevKits via ARP or multicast."
)
@click.option(
    "--ignore-cache",
    is_flag=True,
    default=False,
    help="Ignore ARP cache and run multicast/mDNS discovery directly."
)
def discover(ignore_cache):
    """
    Discover connected or nearby SiMa.ai devices across Ethernet networks.
    This works across macOS, Linux, and Windows.
    """
    click.echo("🔍 Running device discovery...\n")
    try:
        discover_and_probe(mdns_only=ignore_cache)
        discover_and_render_pcie_devices()
    except Exception as e:
        raise click.ClickException(f"Discovery failed: {e}")

# ----------------------
# device connect
# ----------------------
@device.command(name="connect", help="Connect to a device over Ethernet or PCIe", hidden=True)
@click.option("-t", "--target", type=str, help="Ethernet device IP or FQDN.")
@click.option("-u", "--user", type=str, help="Username (defaults to 'sima').")
@click.option("-p", "--password", type=str, help="Password (defaults to 'edgeai').")
@click.option("-s", "--slot", type=str, help="PCIe slot number e.g. 1")
def connect(target, user, password, slot):
    """
    Create and connect to a device. You must specify either --target (Device IP/FQDN) or --slot (PCIe slot).
    """
    if not target and not slot:
        raise click.UsageError("You must specify either --target (Device IP/FQDN) or --slot (PCIe slot).")
    try:
        sv = services()
        handle = sv.device.create_and_connect_device(
            DeviceCreateParams(target=target, user=user, password=password, slot=slot)
        )
    except _ImportErrorWithHint as e:
        click.echo(str(e))
        return
    except Exception as e:
        raise click.ClickException(str(e))

    click.echo("\n------------- Device Created & Connected ---------------")

    if handle.target:
        click.echo(f"Target : {handle.target}")

    click.echo("✅ Device created and successfully connected.")


# ----------------------
# device list
# ----------------------
@device.command(name="list", help="List the connected devices", hidden=True)
def list_devices_cmd():
    try:
        sv = services()
        devices = sv.device.list_devices()
    except _ImportErrorWithHint as e:
        click.echo(str(e))
        return
    except Exception as e:
        raise click.ClickException(str(e))

    if not devices:
        click.echo("No devices are currently connected.")
        return

    # Display connected devices
    headers = ["Target", "Type", "User", "Status"]
    tableData = [[device.target, device.kind, device.raw.getUserName(), device.raw.getCurrentConnectionStatusStr()] for device in devices]
    click.echo("\nConnected Devices:")
    print(tabulate(tableData, headers=headers, tablefmt="grid"))
    click.echo("✅ Devices listed successfully.")

# ----------------------
# device disconnect
# ----------------------
@device.command(name="disconnect", help="Disconnects from a device", hidden=True)
@click.option(
    "-t", "--target",
    required=False,
    help="IP address or the FQDN of the device to disconnect (Ethernet)."
)
@click.option(
    "-u", "--user",
    type=str,
    default="sima",
    show_default=True,
    help="Username of the device to disconnect."
)
@click.option(
    "-s", "--slot",
    required=False,
    help="Slot number of the PCIe SoC to disconnect."
)
def disconnect_cmd(target, user, slot):
    """
    Disconnects from a device using either its IP/FQDN (Ethernet)
    or PCIe slot number.
    """
    if not target and not slot:
        raise click.UsageError(
            "You must specify either --target (Device IP/FQDN) or --slot (PCIe slot)."
        )

    try:
        sv = services()
        # Create a device handle dynamically
        device_handle = sv.device.create_device(
            DeviceCreateParams(target=target, slot=slot, user=user)
        )

        # Perform actual disconnect via pybind API
        sv.device.disconnect_device(device_handle)

    except _ImportErrorWithHint as e:
        click.echo(str(e))
        return
    except Exception as e:
        raise click.ClickException(str(e))


# ----------------------
# device reboot
# ----------------------
@device.command(name="reboot", help="Reboot a specific device", hidden=True)
@click.option("-t", "--target", type=str, help="Ethernet device IP or FQDN.")
@click.option("-s", "--slot", type=str, help="PCIe slot number, e.g. 1")
def reboot(target, slot):
    """
    Reboot a device using either --target (Device IP/FQDN) or --slot (PCIe slot).
    """
    if not target and not slot:
        raise click.UsageError("You must specify either --target (Device IP/FQDN) or --slot (PCIe slot).")

    try:
        sv = services()

        # Create device handle
        device = sv.device.create_device(DeviceCreateParams(target=target, slot=slot))

        # Connect device
        status = sv.device.connect_device(device)

        # Reboot device
        sv.device.reboot_device(device)

    except _ImportErrorWithHint as e:
        click.echo(str(e))
        return
    except Exception as e:
        raise click.ClickException(str(e))


# ----------------------
# device firmware-upgrade
# ----------------------
@device.command(
    name="firmware-upgrade",
    help="Upgrade firmware for PCIe device",
    hidden=True
)
@click.option(
    "-f", "--file",
    "file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
    required=True,
    help="Software Update file."
)
@click.option(
    "-t", "--target",
    type=str,
    help="FQDN or IP address of the DevKit to upgrade."
)
@click.option(
    "-s", "--slot-number",
    type=str,
    help="Slot number of the PCIe SoC to upgrade."
)
@click.option(
    "--reboot-on-upgrade",
    is_flag=True,
    default=False,
    show_default=True,
    help="Reboot the device automatically after upgrade."
)
def firmware_upgrade(file, target, slot_number, reboot_on_upgrade):
    """
    Upgrade firmware on a specified Device.

    You can target the upgrade by specifying either:
      --target (Device IP/FQDN), or --slot-number (PCIe slot)
    """
    click.echo("Starting firmware upgrade with the following parameters:")
    click.echo(f"  Software Update File : {file}")
    if target:
        click.echo(f"  Target DevKit        : {target}")
    if slot_number:
        click.echo(f"  PCIe Slot Number     : {slot_number}")

    click.echo(f"  Reboot on Upgrade    : {reboot_on_upgrade}")

    if not (target or slot_number):
        raise click.UsageError("You must provide either --target, --slot-number.")

    click.echo("Performing firmware upgrade...")
    try:
        sv = services()

        # Create device handle
        deviceHandle = sv.device.create_device(DeviceCreateParams(target=target, slot=slot_number))

        # Connect device
        status = sv.device.connect_device(deviceHandle)

        # Do firmware upgrade
        sv.device.device_firmware_upgrade(deviceHandle, file, reboot_on_upgrade)
    except _ImportErrorWithHint as e:
        click.echo(str(e))
        return
    except Exception as e:
        raise click.ClickException(str(e))

    click.echo("Firmware upgrade completed successfully!")

    if reboot_on_upgrade:
        if deviceHandle.kind == "DeviceConnectionMode.PCIE":
            print(f"Please perform Host reboot to ensure proper PCIe connection with the device")
        else:
            click.echo("Rebooting device as requested...")
