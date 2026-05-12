import os
from typing import Tuple
import pty
import click
import re

from typing import Optional
from sima_cli.utils.env import is_board_running_full_image, get_exact_devkit_type, is_devkit_running_elxr
from sima_cli.update.cleanlog import LineSquelcher


def _run_local_cmd(
    command: str,
    passwd: str,
    squelcher: Optional[LineSquelcher] = None,
    show_summary: bool = True,
) -> bool:
    """
    Run a local command using a PTY for live output.
    Filters out noisy lines using LineSquelcher (if provided or default).
    """
    click.echo(f"🖥️  Running: {command}")

    needs_sudo = command.strip().startswith("sudo")
    if needs_sudo:
        command = f"sudo -S {command[len('sudo '):]}"

    squelcher = squelcher or LineSquelcher()
    suppressed = 0
    buf = ""  # carry partial lines between reads

    try:
        pid, fd = pty.fork()

        if pid == 0:
            # Child process: execute the shell command
            os.execvp("sh", ["sh", "-c", command])
        else:
            if needs_sudo:
                # Send the sudo password immediately (stdin is the PTY)
                os.write(fd, (passwd + "\n").encode())

            while True:
                try:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break

                    # Decode & normalize progress lines: turn lone \r into \n
                    text = chunk.decode("utf-8", errors="replace")
                    text = text.replace("\r\n", "\n").replace("\r", "\n")

                    buf += text
                    *lines, buf = buf.split("\n") 

                    for line in lines:
                        if squelcher.allow(line):
                            click.echo(line)
                        else:
                            suppressed += 1

                except OSError:
                    break

            # Flush any remaining partial line
            if buf:
                if squelcher.allow(buf):
                    click.echo(buf)
                else:
                    suppressed += 1

            _, status = os.waitpid(pid, 0)

            if show_summary and suppressed:
                click.echo(f"🔇 suppressed {suppressed} noisy line(s)")

            return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    except Exception as e:
        click.echo(f"❌ Command execution error: {e}")
        return False


def get_local_board_info() -> Tuple[str, str, bool]:
    """
    Retrieve the local board type and build version by reading /etc/build or /etc/buildinfo.

    Returns:
        (board_type, build_version, devkit_name, full_image, fwtype): Tuple of strings, or ('', '') on failure.
    """
    board_type = ""
    build_version = ""
    build_file_paths = ["/etc/build", "/etc/buildinfo"]

    for path in build_file_paths:
        try:
            if os.path.isfile(path):
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("MACHINE"):
                            board_type = line.split("=", 1)[-1].strip()
                        elif line.startswith("SIMA_BUILD_VERSION"):
                            build_version = line.split("=", 1)[-1].strip()
                if board_type or build_version:
                    break  # Exit early if data found
        except Exception:
            continue

    devkit_name = get_exact_devkit_type()
    fwtype = 'ELXR' if is_devkit_running_elxr() else 'Yocto'

    return board_type, build_version, devkit_name, is_board_running_full_image(), fwtype


def get_boot_mmc(mounts_path="/proc/mounts", cmdline_path="/proc/cmdline"):
    """
    Figure out which eMMC the device was booted from - local version
    """    
    try:
        with open(mounts_path) as f:
            for line in f:
                dev, mnt = line.split()[:2]
                if mnt == "/":
                    m = re.search(r'(mmcblk\d+)', dev)
                    if m:
                        return m.group(1)
    except OSError:
        pass

    try:
        with open(cmdline_path) as f:
            m = re.search(r'\broot=(?:/dev/)?(mmcblk\d+)', f.read())
            if m:
                return m.group(1)
    except OSError:
        pass

    return None

def push_and_update_local_board(troot_path: str, palette_path: str, passwd: str, flavor: str, troot_only: bool):
    """
    Perform local firmware update using swupdate commands.
    Calls swupdate directly on the provided file paths.
    """
    click.echo("📦 Starting local firmware update...")

    try:
        blk = get_boot_mmc()
        if blk != None:
            fix_gpt_cmd = f'sudo printf "fix\n" | sudo parted ---pretend-input-tty /dev/{blk} print'
            _run_local_cmd(fix_gpt_cmd, passwd)

        # Run tRoot update
        if troot_path != None:
            click.echo("⚙️  Flashing tRoot image...")
            if not _run_local_cmd(f"sudo swupdate -H simaai-image-troot:1.0 -i {troot_path}", passwd):
                click.echo("❌ tRoot update failed.")
                return
            click.echo("✅ tRoot update completed.")
            
            if troot_only:
                click.echo("✅ tRoot only option specified, skipping the rest of the update.")
                return

        # Run Palette update
        click.echo("⚙️  Flashing System image...")
        _flavor = 'palette' if flavor == 'headless' else 'graphics'
        if not _run_local_cmd(f"sudo swupdate -H simaai-image-{_flavor}:1.0 -i {palette_path}", passwd):
            click.echo("❌ System image update failed.")
            return
        click.echo("✅ System image update completed. Please powercycle the device")

    except Exception as e:
        click.echo(f"❌ Local update failed: {e}")