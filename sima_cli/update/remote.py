import paramiko
import os
import click
import time
import socket
import itertools
import threading
import sys
import select
from typing import Tuple, Optional

from sima_cli.update.cleanlog import LineSquelcher

DEFAULT_USER = "sima"
DEFAULT_PASSWORD = "edgeai"

def wait_for_ssh(ip: str, timeout: int = 120):
    """
    Show an animated spinner while waiting for SSH on the target IP to become available.

    Args:
        ip (str): IP address of the target board.
        timeout (int): Maximum seconds to wait.
    """
    spinner = itertools.cycle(['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏'])
    stop_event = threading.Event()

    def animate():
        while not stop_event.is_set():
            sys.stdout.write(f"\r🔁 Waiting for board to reboot {next(spinner)} ")
            sys.stdout.flush()
            time.sleep(0.1)

    thread = threading.Thread(target=animate)
    thread.start()

    start_time = time.time()
    success = False
    while time.time() - start_time < timeout:
        try:
            sock = socket.create_connection((ip, 22), timeout=3)
            sock.close()
            success = True
            break
        except (socket.error, paramiko.ssh_exception.SSHException):
            time.sleep(2)  # wait and retry

    stop_event.set()
    thread.join()

    if not success:
        print(f"❌ Timeout: SSH did not become available on {ip} within {timeout} seconds.")
    else:
        print("\r✅ Board is online!           \n")


def get_remote_board_info(ip: str, passwd: str = DEFAULT_PASSWORD) -> Tuple[str, str, str, bool, str]:
    """
    Connect to the remote board and retrieve board type, build version,
    devkit model, full_image flag, and fwtype (from DISTRO).

    Args:
        ip (str): IP address of the board.
        passwd (str): SSH password.

    Returns:
        (board_type, build_version, devkit_model, full_image, fwtype):
            Tuple of strings + bool, or ('', '', '', False, '') on failure.
    """
    board_type = ""
    build_version = ""
    devkit_model = ""
    full_image = False
    fwtype = ""

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=DEFAULT_USER, password=passwd, timeout=10)

        # Retrieve build info
        _, stdout, _ = ssh.exec_command("cat /etc/build 2>/dev/null || cat /etc/buildinfo 2>/dev/null")
        build_output = stdout.read().decode()

        # Retrieve model from /proc/device-tree/model
        _, stdout, _ = ssh.exec_command("cat /proc/device-tree/model 2>/dev/null || echo ''")
        model_output = stdout.read().decode().strip()
        if model_output:
            # Normalize string: remove "SiMa.ai " prefix and trailing "Board"
            if model_output.startswith("SiMa.ai "):
                model_output = model_output[len("SiMa.ai "):]
            model_output = model_output.replace(" Board", "")
            devkit_model = model_output.lower().replace(" ", "-")

        # Check for NVMe tool presence (determine if full image is flashed)
        nvme_check_cmd = r'PATH="$PATH:/usr/sbin:/sbin"; command -v nvme >/dev/null 2>&1 || which nvme >/dev/null 2>&1; echo $?'
        _, stdout, _ = ssh.exec_command(nvme_check_cmd)
        nvme_rc = stdout.read().decode().strip()
        full_image = (nvme_rc == "0")

        ssh.close()

        # Parse build info
        for line in build_output.splitlines():
            line = line.strip()
            if line.startswith("MACHINE"):
                board_type = line.split("=", 1)[-1].strip()
            elif line.startswith("SIMA_BUILD_VERSION"):
                build_version = line.split("=", 1)[-1].strip()
            elif line.startswith("DISTRO "):
                fwtype = line.split("=", 1)[-1].strip()

        return board_type, build_version, devkit_model, full_image, fwtype

    except Exception as e:
        click.echo(f"Unable to retrieve board info with error: {e}, board may be still booting.")
        return "", "", "", False, ""

def _scp_file(sftp, local_path: str, remote_path: str):
    """Upload file via SFTP with tqdm block progress bar (Windows-safe)."""

    from tqdm import tqdm

    filename = os.path.basename(local_path)
    total_size = os.path.getsize(local_path)

    # Normalize paths
    local_path = os.path.abspath(local_path)
    remote_path = remote_path.replace("\\", "/")

    # Use Unicode blocks instead of hashes
    with tqdm(
        total=total_size,
        unit="B",
        unit_scale=True,
        desc=f"📤 {filename}",
        bar_format="{l_bar}{bar} | {n_fmt}/{total_fmt}",
    ) as pbar:
        def progress(transferred, total):
            pbar.update(transferred - pbar.n)

        # Open explicitly to avoid Windows file lock issues
        with open(local_path, "rb") as f:
            sftp.putfo(f, remote_path, callback=progress)

    click.echo("✅ Upload complete")


def run_remote_command(ssh, command: str, password: str = DEFAULT_PASSWORD,
                       squelcher: Optional[LineSquelcher] = None):
    """
    Run a remote command over SSH and stream its output live to the console.
    If the command starts with 'sudo', pipe in the password.

    Args:
        ssh (paramiko.SSHClient): Active SSH connection.
        command (str): The command to run on the remote host.
        password (str): Password to use if the command requires sudo.
    """
    squelcher = squelcher or LineSquelcher()  # use defaults unless you pass a custom one

    click.echo(f"🚀 Running on remote: {command}")
    needs_sudo = command.strip().startswith("sudo")
    if needs_sudo:
        command = f"sudo -S {command[len('sudo '):]}"

    stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)
    if needs_sudo:
        stdin.write(password + "\n")
        stdin.flush()

    suppressed = 0
    while not stdout.channel.exit_status_ready():
        rl, _, _ = select.select([stdout.channel], [], [], 0.5)
        if rl:
            if stdout.channel.recv_ready():
                output = stdout.channel.recv(4096).decode("utf-8", errors="replace")
                for line in output.splitlines():
                    if squelcher.allow(line):
                        click.echo(f"↦ {line}")
                    else:
                        suppressed += 1
            if stdout.channel.recv_stderr_ready():
                err_output = stdout.channel.recv_stderr(4096).decode("utf-8", errors="replace")
                for line in err_output.splitlines():
                    if squelcher.allow(line):
                        click.echo(f"⚠️ {line}")
                    else:
                        suppressed += 1

    # Final remaining output
    remaining = stdout.read().decode("utf-8", errors="replace")
    for line in remaining.splitlines():
        if squelcher.allow(line):
            click.echo(f"↦ {line}")
        else:
            suppressed += 1

    remaining_err = stderr.read().decode("utf-8", errors="replace")
    for line in remaining_err.splitlines():
        if squelcher.allow(line):
            click.echo(f"⚠️ {line}")
        else:
            suppressed += 1

    # Optional: surface how much noise we hid (comment out if you want it totally silent)
    if suppressed:
        click.echo(f"🔇 suppressed {suppressed} noisy line(s)")


def init_ssh_session(ip: str, password: str = DEFAULT_PASSWORD):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh.connect(ip, username=DEFAULT_USER, password=password, timeout=10)
    return ssh

def reboot_remote_board(ip: str, passwd: str):
    """
    Reboot remote board by sending SSH command
    """    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        ssh.connect(ip, username=DEFAULT_USER, password=passwd, timeout=10)

        run_remote_command(ssh, "sudo systemctl stop watchdog", password=passwd)
        run_remote_command(ssh, "sudo bash -c 'echo b > /proc/sysrq-trigger'", password=passwd)

    except Exception as reboot_err:
        click.echo(f"⚠️  Unable to connect to the remote board")


def run_remote_command_capture(ssh, command: str, password: str = DEFAULT_PASSWORD):
    """
    Run a remote command over SSH and return (exit_status, stdout_str, stderr_str).
    Does not stream output to the console.
    If the command starts with 'sudo', it will send the password via stdin.
    """
    needs_sudo = command.strip().startswith("sudo")
    if needs_sudo:
        command = f"sudo -S {command[len('sudo '):]}"

    stdin, stdout, stderr = ssh.exec_command(command, get_pty=needs_sudo)
    if needs_sudo:
        stdin.write(password + "\n")
        stdin.flush()

    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def get_remote_boot_mmc(ssh, password: str = DEFAULT_PASSWORD) -> Optional[str]:
    """
    Determine the remote boot device: 'mmcblk0', 'mmcblk1', or None.

    Strategy (remote):
      1) Look at the actual device mounted as '/' in /proc/mounts.
      2) Fallback to parsing /proc/cmdline (root=...).
    """
    # Minimal, BusyBox-friendly shell: grep/awk only, no fancy sed EREs.
    remote_script = r"""
mmc="$(awk '$2=="/"{print $1}' /proc/mounts | grep -oE 'mmcblk[0-9]+' | head -n1)"
if [ -z "$mmc" ]; then
  root_tok="$(sed -n 's/.*root=\([^ ]*\).*/\1/p' /proc/cmdline)"
  mmc="$(printf '%s\n' "$root_tok" | grep -oE 'mmcblk[0-9]+' | head -n1)"
fi
[ -n "$mmc" ] && printf '%s\n' "$mmc"
"""

    code, out, err = run_remote_command_capture(
        ssh, f"sh -c {quote_shell(remote_script)}", password=password
    )
    mmc = out.strip()
    return mmc if mmc else None

def quote_shell(s: str) -> str:
    """Safely single-quote a string for sh -c."""
    # Turn: abc'def -> 'abc'"'"'def'
    return "'" + s.replace("'", "'\"'\"'") + "'"

def copy_file_to_remote_board(ip: str, file_path: str, remote_dir: str, passwd: str):
    """
    Copy a file to the remote board over SSH with tqdm progress bar.
    Assumes default credentials: sima / edgeai.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    from tqdm import tqdm

    try:
        ssh.connect(ip, username=DEFAULT_USER, password=passwd, timeout=10)
        sftp = ssh.open_sftp()

        base_file_path = os.path.basename(file_path)
        remote_path = os.path.join(remote_dir, base_file_path)
        file_size = os.path.getsize(file_path)

        click.echo(f"📤 Uploading {file_path} → {remote_path}")

        with tqdm(total=file_size, unit="B", unit_scale=True,
                  desc=f"📤 {base_file_path}", ncols=100) as pbar:

            def progress(transferred, total):
                pbar.update(transferred - pbar.n)

            sftp.put(file_path, remote_path, callback=progress)

        click.echo("✅ Upload complete")

        sftp.close()
        ssh.close()
        return True

    except Exception as e:
        click.echo(f"❌ Remote file copy failed: {e}")

    return False

def push_and_update_remote_board(ip: str, troot_path: str, palette_path: str, passwd: str, reboot_and_wait: bool, flavor: str = 'headless', troot_only: bool = False):
    """
    Upload and install firmware images to remote board over SSH.
    Assumes default credentials: sima / edgeai.
    Includes reboot and SSH wait after each step.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(ip, username=DEFAULT_USER, password=passwd, timeout=10)
        sftp = ssh.open_sftp()
        remote_dir = "/tmp"
        palette_name = os.path.basename(palette_path)

        boot_mmc = get_remote_boot_mmc(ssh, passwd)
        if boot_mmc != None:
            click.echo(f'✅ Checking partition table GPT record for {boot_mmc}...')
            fix_gpt_cmd = f'sudo printf "fix\n" | sudo parted ---pretend-input-tty /dev/{boot_mmc} print'
            run_remote_command(ssh, fix_gpt_cmd)

        # Upload tRoot image
        if troot_path is not None:
            troot_name = os.path.basename(troot_path)
            _scp_file(sftp, troot_path, os.path.join(remote_dir, troot_name))
            click.echo("🚀 Uploaded tRoot image.")

            # Run tRoot update
            run_remote_command(
                ssh,
                f"sudo swupdate -H simaai-image-troot:1.0 -i /tmp/{troot_name}", password=passwd
            )

            if troot_only:
                click.echo("✅  tRoot only option specified, exiting update process now, please reboot your DevKit")
                exit(0)

            # Disabled the following code per agreement with QA, this reboot step is not required for tRoot update
            # click.echo("✅ tRoot update complete, the board needs to be rebooted to proceed to the next phase of update.")
            # click.confirm("⚠️  Have you rebooted the board?", default=True, abort=True)
            # _wait_for_ssh(ip, timeout=120)
        else:
            click.echo("⚠️  tRoot update skipped because the requested image doesn't contain troot image.")

        # Upload Palette image
        ssh.connect(ip, username=DEFAULT_USER, password=passwd, timeout=10)
        sftp = ssh.open_sftp()        
        _scp_file(sftp, palette_path, os.path.join(remote_dir, palette_name))
        click.echo("🚀 Uploaded system image.")

        # Run Palette update
        _flavor = 'palette' if flavor == 'headless' else 'graphics'

        # Set necessary env first to make sure it can access NVMe device
        if _flavor == 'graphics':
            click.echo(f"⚠️  With full image, setting U-Boot environment variable to support NVMe and GPU.")
            run_remote_command(
                ssh,
                f"sudo fw_setenv dtbos pcie-4rc-2rc-2rc.dtbo",
                password=passwd
            )

        run_remote_command(
            ssh,
            f"sudo swupdate -H simaai-image-{_flavor}:1.0 -i /tmp/{palette_name}",
            password=passwd
        )
        click.echo("✅ Board image update complete.")

        # In the case of PCIe system, we don't need to reboot the card, instead, we will let it finish then update the PCIe driver in the host
        # After that we can reboot the whole system.
        if reboot_and_wait:
            # Reboot and expect disconnect
            click.echo("🔁 Rebooting board after update. Waiting for reconnection...")

            try:
                run_remote_command(ssh, "sudo reboot", password=passwd)

            except Exception as reboot_err:
                click.echo(f"⚠️  SSH connection lost due to reboot (expected): {reboot_err}, please powercycle the board...")
                click.confirm("⚠️  Have you powercycled the board?", default=True, abort=True)

            try:
                ssh.close()
            except Exception:
                pass

            # Wait for board to come back
            time.sleep(5)
            wait_for_ssh(ip, timeout=120)

            # Reconnect and verify version
            try:
                click.echo("🔍 Reconnecting to verify build version...")
                time.sleep(10)
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=DEFAULT_USER, password=passwd, timeout=10)

                run_remote_command(ssh, "grep SIMA_BUILD_VERSION /etc/build 2>/dev/null || grep SIMA_BUILD_VERSION /etc/buildinfo 2>/dev/null", password=passwd)
                ssh.close()
            except Exception as e:
                click.echo(f"❌ Unable to validate the version: {e}")

        click.echo("✅ Firmware update process complete.")

    except Exception as e:
        click.echo(f"❌ Remote update failed: {e}")


if __name__ == "__main__":
    wait_for_ssh("192.168.2.20", timeout=60)
    print(get_remote_board_info("192.168.2.20"))