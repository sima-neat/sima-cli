import subprocess
import click
import re
import os

from sima_cli.utils.env import is_modalix_devkit

def scan_nvme():
    try:
        nvme_list = subprocess.check_output("sudo nvme list", shell=True, text=True).strip()
        if "/dev/nvme0n1" in nvme_list:
            return nvme_list
    except subprocess.CalledProcessError:
        pass
    return None

def get_lba_format_index():
    try:
        lba_output = subprocess.check_output("sudo nvme id-ns -H /dev/nvme0n1 | grep 'Relative Performance'", shell=True, text=True)
        lbaf_line = lba_output.strip().split(":")[0]
        lbaf_index = lbaf_line.split()[-1]
        return lbaf_index
    except Exception:
        return None

def format_nvme(lbaf_index):
    cmds = [
        f"sudo nvme format /dev/nvme0n1 --lbaf={lbaf_index}",
        "sudo parted -a optimal /dev/nvme0n1 mklabel gpt",
        "sudo parted -a optimal /dev/nvme0n1 mkpart primary ext4 0% 100%",
        "sudo mkfs.ext4 /dev/nvme0n1p1",
        "sudo nvme smart-log -H /dev/nvme0n1"
    ]
    for cmd in cmds:
        subprocess.run(cmd, shell=True, check=True)

def add_nvme_to_fstab():
    """
    Safely update /etc/fstab with the correct UUID entry for NVMe.

    - Reads existing fstab into memory
    - Removes old /media/nvme entries
    - Appends correct UUID entry
    - Writes new fstab to a temporary file
    - Atomically renames over the original file (crash-safe)
    - Calls sync() twice to flush to eMMC
    - Reloads systemd to apply changes
    """

    device = "/dev/nvme0n1p1"
    fstab_path = "/etc/fstab"
    tmp_path = "/tmp/fstab.new"

    try:
        # ---------------------------------------------------------
        # 1. Extract UUID + filesystem type
        # ---------------------------------------------------------
        blkid_output = subprocess.check_output(["sudo", "blkid", device], text=True)
        uuid_match = re.search(r'UUID="([^"]+)"', blkid_output)
        type_match = re.search(r'TYPE="([^"]+)"', blkid_output)

        if not uuid_match or not type_match:
            click.echo("❌ Could not extract UUID or TYPE from blkid output.")
            return

        uuid = uuid_match.group(1)
        fs_type = type_match.group(1)
        new_entry = f"UUID={uuid}  /media/nvme  {fs_type}  defaults,noatime  0 0"

        # ---------------------------------------------------------
        # 2. Read original fstab safely
        # ---------------------------------------------------------
        with open(fstab_path, "r") as f:
            lines = f.readlines()

        # ---------------------------------------------------------
        # 3. Filter out old /media/nvme lines
        # ---------------------------------------------------------
        filtered = [line for line in lines if "/media/nvme" not in line]

        # ---------------------------------------------------------
        # 4. Write a new fstab via a temporary file
        # ---------------------------------------------------------
        with open(tmp_path, "w") as f:
            for line in filtered:
                f.write(line.rstrip() + "\n")

            # Append our new entry
            f.write(new_entry + "\n")

        # ---------------------------------------------------------
        # 5. Atomic overwrite of original fstab
        # ---------------------------------------------------------
        subprocess.run(f"sudo mv {tmp_path} {fstab_path}", shell=True, check=True)

        # ---------------------------------------------------------
        # 6. Flush filesystem + write cache to eMMC
        # ---------------------------------------------------------
        subprocess.run("sync", shell=True, check=True)
        subprocess.run("sync", shell=True, check=True)

        click.echo(f"✅ Updated /etc/fstab with new NVMe UUID entry:\n{new_entry}")

        # ---------------------------------------------------------
        # 7. Reload systemd daemon to apply fresh fstab
        # ---------------------------------------------------------
        subprocess.run("sudo systemctl daemon-reload", shell=True, check=True)
        click.echo("🔄 Reloaded systemd daemon to apply fstab changes.")

    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Failed to update fstab or reload systemd: {e}")

    except Exception as e:
        click.echo(f"❌ Unexpected error: {e}")

def mount_nvme():
    try:
        # Unmount if already mounted
        mount_check = subprocess.run("mountpoint -q /media/nvme", shell=True)
        if mount_check.returncode == 0:
            print("⚙️  /media/nvme is already mounted, unmounting first...")
            subprocess.run("sudo umount -l /media/nvme", shell=True, check=True)

        # Create mount point
        subprocess.run("sudo mkdir -p /media/nvme", shell=True, check=True)

        # Mount the NVMe partition
        subprocess.run("sudo mount /dev/nvme0n1p1 /media/nvme", shell=True, check=True)

        # Add to fstab and remount all
        add_nvme_to_fstab()
        subprocess.run("sudo systemctl daemon-reload", shell=True, check=True)
        subprocess.run("sudo mount -a", shell=True, check=True)
        
        # Change ownership to 'sima'
        subprocess.run("sudo chown sima:sima /media/nvme", shell=True, check=True)
        subprocess.run("sudo chmod 755 /media/nvme", shell=True, check=True)

        # Create /media/nvme/applications folder
        subprocess.run("sudo mkdir -p /media/nvme/applications", shell=True, check=True)
        subprocess.run("sudo chown sima:sima /media/nvme/applications", shell=True, check=True)

        # Ensure /data/simaai/applications symlink points correctly
        subprocess.run("sudo mkdir -p /data/simaai", shell=True, check=True)
        current_target = None
        if os.path.islink("/data/simaai/applications"):
            current_target = os.readlink("/data/simaai/applications")
        
        desired_target = "/media/nvme/applications"
        if current_target != desired_target:
            print(f"🔗 Creating symlink: /data/simaai/applications → {desired_target}")
            subprocess.run("sudo ln -sfn /media/nvme/applications /data/simaai/applications", shell=True, check=True)
        else:
            print("✅ Symlink /data/simaai/applications already points to the correct target in NVMe.")

        # Set symlink ownership to sima:sima
        subprocess.run("sudo chown -h sima:sima /data/simaai/applications", shell=True, check=True)

        print("✅ NVMe mounted successfully, symlink verified, and ownership set to sima:sima.")

    except subprocess.CalledProcessError as e:
        print(f"❌ Error during NVMe mount: {e}")

def nvme_format():
    nvme_info = scan_nvme()
    if not nvme_info:
        click.echo("❌  No NVMe drive detected.")
        return
    click.echo(nvme_info)

    lbaf_index = get_lba_format_index()
    if lbaf_index is None:
        click.echo("❌  Failed to detect LBA format index.")
        return
    click.echo(f"ℹ️  Detected LBA format index: {lbaf_index}")

    if not click.confirm("⚠️  Are you sure you want to format /dev/nvme0n1? This will erase all data."):
        click.echo("❌ Aborted by user.")
        return

    try:
        # Unmount before formatting, ignore error if not mounted
        subprocess.run("sudo umount -l /media/nvme", shell=True, check=False)

        # Format and mount
        format_nvme(lbaf_index)
        mount_nvme()
        click.echo("✅ NVMe drive formatted and mounted at /media/nvme.")
    except subprocess.CalledProcessError:
        click.echo("❌ Formatting process failed.")


def nvme_remount():
    if not is_modalix_devkit():
        click.echo("❌ This command can only be run on the Modalix DevKit.")
        return

    try:
        mount_nvme()

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to remount NVMe: {e}")