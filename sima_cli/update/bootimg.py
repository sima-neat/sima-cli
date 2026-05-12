import click
import platform
import subprocess
import sys
import os
import select
import re

from sima_cli.update.updater import download_image

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def list_removable_devices():
    system = platform.system()

    if system == "Linux":
        return get_linux_removable()
    elif system == "Darwin":
        return get_macos_removable()
    elif system == "Windows":
        return get_windows_removable()
    else:
        click.echo(f"❌ Unsupported platform: {system}")
        return []

# Linux: Use lsblk to find removable drives
def get_linux_removable():
    try:
        output = subprocess.check_output(["lsblk", "-o", "NAME,RM,SIZE,MOUNTPOINT", "-J"]).decode()
        import json
        data = json.loads(output)
        devices = []
        for block in data['blockdevices']:
            if block.get('rm') == True and block.get('mountpoint') is None:
                devices.append({
                    "name": block['name'],
                    "size": block['size'],
                    "path": f"/dev/{block['name']}"
                })
        return devices
    except Exception:
        return []

# macOS: Use diskutil
def get_macos_removable():
    try:
        output = subprocess.check_output(["diskutil", "list"], text=True)
        devices = []

        candidate_disks = [
            line.split()[0]
            for line in output.splitlines()
            if line.startswith("/dev/disk")
        ]

        for disk in candidate_disks:
            info = subprocess.check_output(["diskutil", "info", disk], text=True)
            is_removable = False
            is_disk_image = False
            size = "Unknown"
            device_name = ''

            for info_line in info.splitlines():
                if "Secure Digital" in info_line or "USB" in info_line:
                    is_removable = True
                elif "Disk Size" in info_line and "(" in info_line:
                    size = info_line.split("(")[1].split()[0] 
                elif "Volume Name" in info_line:
                    volume_name = info_line.split(":")[-1].strip() or "Unknown"
                elif "Device / Media Name" in info_line:
                    is_disk_image = ('Disk Image' in info_line)
                    device_name = info_line.split(":")[-1].strip() or "Unknown"

            # switch to raw device to speed up dd performance
            if is_removable and not is_disk_image:
                devices.append({
                    "name": device_name,
                    "size": round(int(size) / (1024 ** 3), 0),
                    "path": disk.replace('/disk', '/rdisk')
                })

        return devices
    except Exception as e:
        click.echo(f"Failed to detect removable devices on macOS: {e}")
        return []

# Windows: Use wmic or powershell
def get_windows_removable():
    try:
        output = subprocess.check_output(
            ['powershell', '-Command',
             'Get-WmiObject Win32_DiskDrive | Where { $_.MediaType -match "Removable" } | '
             'Select-Object DeviceID,Model,Size | ConvertTo-Json']
        ).decode()
        import json
        parsed = json.loads(output)
        if not isinstance(parsed, list):
            parsed = [parsed]
        devices = []
        for d in parsed:
            size_gb = int(d.get("Size", 0)) // (1024 ** 3)
            devices.append({
                "name": d.get("Model", "Removable Drive"),
                "size": f"{size_gb} GB",
                "path": d["DeviceID"]
            })
        return devices
    except Exception:
        return []

def check_dd_installed():
    """Check if dd is installed on the system."""
    try:
        subprocess.run(["which", "dd"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def unmount_device(device_path):
    """Unmount the device using platform-specific commands."""
    system = platform.system()
    try:
        if system == "Darwin":  # macOS
            subprocess.run(["diskutil", "unmountDisk", device_path], check=True, capture_output=True, text=True)
            click.echo(f"✅ Unmounted {device_path} on macOS")
        elif system == "Linux":
            result = subprocess.run(["umount", device_path], capture_output=True, text=True)
            if result.returncode == 0:
                click.echo(f"✅ Unmounted {device_path} on Linux")
            elif "not mounted" in result.stderr.lower():
                click.echo(f"ℹ️  {device_path} was not mounted. Continuing.")
            else:
                click.echo(f"❌ Failed to unmount {device_path}: {result.stderr.strip()}")
                sys.exit(1)
        else:
            click.echo(f"❌ Unsupported platform: {system}. Cannot unmount {device_path}.")
            sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Unexpected error while unmounting {device_path}: {e}")
        sys.exit(1)

def _require_sudo():
    try:
        # This will prompt for password if necessary and cache it for a few minutes
        click.echo("✅ Running this command requires sudo access.")
        subprocess.run(["sudo", "-v"], check=True)
    except subprocess.CalledProcessError:
        click.echo("❌ Sudo authentication failed.")
        sys.exit(1)

def copy_image_to_device(image_path, device_path):
    """Copy the image file to the device using dd with 16M block size."""
    # Get file size for progress calculation
    file_size = os.path.getsize(image_path)
    click.echo(f"ℹ️  Running 'sudo dd' to copy {image_path} to {device_path}")

    # Debug: Log raw dd output to a file for diagnosis
    debug_log = "dd_output.log"
    click.echo(f"ℹ️  Logging raw dd output to {debug_log} for debugging.")
    _require_sudo()

    dd_command = ["sudo", "dd", f"if={image_path}", f"of={device_path}", "bs=16M", "status=progress"]
    try:
        # Start dd process with unbuffered output
        process = subprocess.Popen(
            dd_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            errors="replace" 
        )

        # Regex to parse dd progress (more robust to handle variations)
        pattern = re.compile(
            r"(?:dd:\s*)?(?P<bytes>\d+)\s+bytes(?:\s+\(.*?\))?\s+(?:transferred|copied),?\s+[\d\.]+\s*s?,?\s+[\d\.]+\s+MB/s",
            re.IGNORECASE
        )

        # Initialize tqdm progress bar
        with tqdm(total=file_size, unit="B", unit_scale=True, desc="Copying", ncols=100) as pbar:
            with open(debug_log, "w") as log_file:
                while process.poll() is None:
                    rlist, _, _ = select.select([process.stdout, process.stderr], [], [], 0.1)
                    for stream in rlist:
                        line = stream.readline().strip()
                        if line:
                            log_file.write(f"{line}\n")
                            log_file.flush()
                            match = pattern.search(line)
                            if match:
                                bytes_transferred = int(match.group(1))
                                pbar.n = min(bytes_transferred, file_size)
                                pbar.refresh()
                            elif line:
                                click.echo(f"⚠️  dd: {line}")  # Show other messages (e.g., errors)

        # Capture remaining output and check for errors
        stdout, stderr = process.communicate()
        with open(debug_log, "a") as log_file:
            if stdout.strip():
                log_file.write(f"Final stdout: {stdout.strip()}\n")
            if stderr.strip():
                log_file.write(f"Final stderr: {stderr.strip()}\n")

        if process.returncode != 0:
            click.echo(f"❌ Failed to copy {image_path} to {device_path}: {stderr}")
            click.echo(f"ℹ️  Check {debug_log} for raw dd output.")
            sys.exit(1)

        click.echo(f"✅ Successfully copied {image_path} to {device_path}")
        subprocess.run(["sync"], check=True)
        click.echo("✅ Synced data to device")
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Failed to copy {image_path} to {device_path}: {e.stderr}")
        click.echo(f"ℹ️  Check {debug_log} for raw dd output.")
        sys.exit(1)
    except FileNotFoundError:
        click.echo("❌ 'dd' not found. Ensure both are installed and accessible.")
        sys.exit(1)

def write_bootimg(image_path):
    """Write a boot image to a removable device."""
    # Step 1: Validate image file
    if not os.path.isfile(image_path):
        click.echo(f"❌ Image file {image_path} does not exist.")
        sys.exit(1)

    click.echo(f"✅ Valid image file: {image_path}")

    # Step 2: Check if dd is installed
    if not check_dd_installed():
        click.echo("⚠️  'dd' is not installed on this system.")
        if platform.system() == "Darwin":
            click.echo("ℹ️  On macOS, 'dd' is included by default. Check your PATH or system configuration.")
        elif platform.system() == "Linux":
            click.echo("ℹ️  On Linux, install 'dd' using your package manager (e.g., 'sudo apt install coreutils' on Debian/Ubuntu).")
        else:
            click.echo("ℹ️  Please install 'dd' for your system.")
        sys.exit(1)
    click.echo("✅ 'dd' is installed")

    # Step 3: Detect removable devices
    devices = list_removable_devices()  # Assumes this function exists
    if not devices:
        click.echo("⚠️  No removable devices detected. Please plug in your USB drive or SD card.")
        sys.exit(1)

    # Step 4: Display devices
    click.echo("\n🔍 Detected removable devices:")
    for i, dev in enumerate(devices):
        click.echo(f"  [{i}] Name: {dev['name']}, Size: {dev['size']} GB, Path: {dev['path']}")

    # Step 5: Select device
    selected_device = None
    if len(devices) == 1:
        confirm = input(f"\n✅ Do you want to use device {devices[0]['path']}? (y/N): ").strip().lower()
        if confirm == 'y':
            selected_device = devices[0]
        else:
            click.echo("❌ Operation cancelled by user.")
            sys.exit(0)
    else:
        try:
            selection = int(input("\n🔢 Multiple devices found. Enter the number of the device to use: ").strip())
            if 0 <= selection < len(devices):
                selected_device = devices[selection]
            else:
                click.echo("❌ Invalid selection.")
                sys.exit(1)
        except ValueError:
            click.echo("❌ Invalid input. Please enter a number.")
            sys.exit(1)

    click.echo(f"\n🚀 Proceeding with device: {selected_device['path']}")

    # Step 6: Unmount the selected device
    unmount_device(selected_device['path'])

    # Step 7: Copy the image to the device
    copy_image_to_device(image_path, selected_device['path'])

    # Step 8: Eject the device (platform-dependent)
    try:
        if platform.system() == "Darwin":
            subprocess.run(["diskutil", "eject", selected_device['path']], check=True, capture_output=True, text=True)
            click.echo(f"✅ Ejected {selected_device['path']} on macOS")
        elif platform.system() == "Linux":
            click.echo("ℹ️  Linux does not require explicit eject. Device is ready.")
        else:
            click.echo("ℹ️  Please manually eject the device if required.")
    except subprocess.CalledProcessError as e:
        click.echo(f"⚠️  Failed to eject {selected_device['path']}: {e.stderr}")
        click.echo("ℹ️  Please manually eject the device.")


def write_image(version: str, board: str, swtype: str, internal: bool = False, flavor: str = 'headless'):
    """
    Download and write a bootable firmware image to a removable storage device.

    Parameters:
        version (str): Firmware version to download (e.g., "1.6.0").
        board (str): Target board type, e.g., "modalix" or "mlsoc".
        swtype (str): Software image type, e.g., "yocto" or "elxr".
        internal (bool): Whether to use internal download sources. Defaults to False.
        flavor (str): Flavor of the software package - can be either headless or full.

    Raises:
        RuntimeError: If the download or write process fails.
    """
    try:
        click.echo(f"⬇️  Downloading boot image for version: {version}, board: {board}, swtype: {swtype}")
        file_list = download_image(version, board, swtype, internal, update_type='bootimg', flavor=flavor)
        if not isinstance(file_list, list):
            raise ValueError("Expected list of extracted files, got something else.")
        
        image_file = next(
            (f for f in file_list if f.endswith(".wic") or f.endswith(".img")),
            None
        )

        if not image_file:
            raise FileNotFoundError("No .wic or .img image file found after extraction.")

    except Exception as e:
        raise RuntimeError(f"❌ Failed to download image: {e}")

    try:
        click.echo(f"📝 Writing image to removable media: {image_file}")
        write_bootimg(image_file)
        
    except Exception as e:
        raise RuntimeError(f"❌ Failed to write image: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        click.echo("❌ Usage: python write_bootimg.py <image_file>")
        sys.exit(1)
    
    write_image('1.7.0', 'modalix', 'davinci', True)
