import sys
import platform
import click
import json
import tarfile
import os
import subprocess
from pathlib import Path
from sima_cli.download import download_file_from_url
from sima_cli.utils.env import is_sima_board
from sima_cli.utils.config_loader import load_resource_config

def build_internal_url(download_url_base, latest_filename):
    """Build the URL for internal Palette package."""
    return f"{download_url_base}palette/{latest_filename}"

def build_external_url(download_url_base, latest_filename, version):
    """Build the URL for external Palette package from SDKx.y.z folder."""
    return f"{download_url_base}palette/SDK{version}/{latest_filename}"

def install_palette(internal=False):
    try:
        # Check for unsupported platforms
        system = platform.system().lower()
        if is_sima_board():
            click.echo("❌ Palette installation is not supported on SiMa DevKit, please install on Ubuntu 22.04 or Windows 10/11.")
            sys.exit(1)
        if system == "darwin":
            click.echo("❌ Palette installation is not supported on macOS, please install on Ubuntu 22.04 or Windows 10/11.")
            sys.exit(1)

        # Load configuration for resources
        cfg = load_resource_config()
        download_url_base = cfg.get('internal' if internal else 'public').get('download').get('download_url')

        # Fetch Palette version info
        click.echo(f"📥 Fetching Palette version info ({'internal' if internal else 'external'})...")
        version_url = f"{download_url_base}palette/metadata.json"
        downloads_dir = Path.home() / "Downloads" / "palette-installer"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = download_file_from_url(version_url, dest_folder=downloads_dir, internal=internal)

        # Read version data
        with open(metadata_path, "r") as f:
            version_data = json.load(f)
            latest_filename = version_data.get("latest")

        if not latest_filename:
            raise Exception("Unable to retrieve latest filename info.")

        # Extract version from filename (assuming format like palette-installation-<arch>-x.y.z.tar.gz)
        version = latest_filename.split('-')[-1].replace('.tar.gz', '')

        # Build package URL based on internal or external
        pkg_url = build_internal_url(download_url_base, latest_filename) if internal else build_external_url(download_url_base, latest_filename, version)
        click.echo(f"🌐 Downloading Palette ({'internal' if internal else 'external'}: {latest_filename})...")

        # Download the package
        archive_path = download_file_from_url(pkg_url, dest_folder=downloads_dir, internal=internal)

        # Extract the package
        click.echo(f"📦 Extracting to {downloads_dir}...")
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=downloads_dir)

        # Determine installation script based on platform
        install_script = "install.bat" if system == "windows" else "install.sh"
        script_path = os.path.join(downloads_dir, install_script)

        if not os.path.isfile(script_path):
            raise Exception(f"Installation script not found: {script_path}")

        # Run the installer
        click.echo(f"🚀 Running installer: {install_script}")
        if system == "windows":
            subprocess.run(["cmd", "/c", os.path.basename(script_path)], check=True, cwd=downloads_dir)
        else:
            subprocess.run(["bash", os.path.basename(script_path)], check=True, cwd=downloads_dir)

        click.echo(f"✅ Palette installed successfully ({'internal' if internal else 'external'}). Run {downloads_dir}/run.sh to start palette")

    except Exception as e:
        click.echo(f"❌ {'Internal' if internal else 'External'} installation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    install_palette(internal=False)