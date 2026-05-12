import sys
import click
import os
import subprocess
from pathlib import Path

from sima_cli.update.updater import download_image
from sima_cli.utils.env import is_pcie_host


def install_hostdriver(version: str, internal: bool = False):
    """
    Install PCIe host driver on supported platforms.

    This function is only valid on PCIe host machines. It downloads the appropriate image
    package and installs the host driver script if present.

    Args:
        version (str): Firmware version string (e.g., "1.6.0").
        internal (bool): Whether to use internal sources for the download.

    Raises:
        RuntimeError: If the platform is not supported or the driver script is missing.
    """
    if not is_pcie_host():
        click.echo("❌ This command is only supported on PCIe host Linux machines.")
        sys.exit(1)

    try:
        click.secho(
            "\n⚠️  DEPRECATION NOTICE\n"
            "────────────────────────────────────────────────────────────────────────────────────────\n"
            "This command is deprecated and has been removed from this version of sima-cli.\n\n"
            "Please use the new unified installer instead:\n\n"
            "    sima-cli install drivers/linux\n\n"
            "That command provides improved validation, logging, and support.\n"
            "─────────----------------───────────────────────────────────────────────────────────────\n",
            fg="red",
            bold=True,
        )
        exit(0)

    except Exception as e:
        raise RuntimeError(f"❌ Failed to install host driver: {e}")

