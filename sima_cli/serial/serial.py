import platform
import subprocess
import shutil
import click
import os
import errno
import glob
from sima_cli.utils.env import is_sima_board

def connect_serial(ctx, baud):
    """
    Connect to the UART serial console of the DevKit.
    Automatically installs required tools if missing.
    """
    if is_sima_board():
        click.echo("🚫 This command is not supported on the DevKit. Please run it from your host machine.")
        ctx.exit(1)

    system = platform.system()
    internal = ctx.obj.get("internal", False)

    if system == "Darwin":
        _connect_mac(baud)
    elif system == "Linux":
        _connect_linux(baud)
    elif system == "Windows":
        _print_windows_instructions()
    else:
        click.echo(f"⚠️ Unsupported OS: {system}. Only macOS, Linux, and Windows are supported.")
        ctx.exit(1)

    click.echo("✅ Serial session ended.")


def _connect_mac(baud):
    terminal = "picocom"
    if not shutil.which(terminal):
        click.echo("⚙️ 'picocom' is not installed. Attempting to install with Homebrew...")
        if shutil.which("brew"):
            subprocess.run(["brew", "install", "picocom"], check=True)
        else:
            click.echo("❌ Homebrew not found. Please install Homebrew first: https://brew.sh/")
            raise SystemExit(1)

    ports = sorted(
        subprocess.getoutput("ls /dev/tty.usbserial-* /dev/cu.usbserial-* 2>/dev/null").splitlines()
    )
    if not ports:
        click.echo("❌ No USB serial device found.")
        raise SystemExit(1)

    click.echo(f"Connecting to device with picocom ({baud} baud)...")
    click.echo("🧷 To exit: Press Ctrl + A, then Ctrl + X")
    click.echo("📜 Scrollback will work in your terminal as expected.\n")
    
    if not click.confirm("Proceed to connect?", default=True):
        click.echo("❎ Connection aborted by user.")
        return

    port = ports[0]
    click.echo(f"🔌 Connecting to {port} with picocom (115200 8N1)...")
    try:
        subprocess.run([
            terminal,
            "-b", f"{baud}",
            "--databits", "8",
            "--parity", "n",
            "--stopbits", "1",
            port
        ])
    except KeyboardInterrupt:
        click.echo("\n❎ Serial connection interrupted by user.")

def _is_busy(port: str) -> bool:
    # Try fuser
    try:
        r = subprocess.run(["fuser", "-s", port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
    except FileNotFoundError:
        pass

    try:
        r = subprocess.run(["lsof", "-Fn", port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except FileNotFoundError:
        pass
    return False

def _has_rw_permission(port: str) -> bool:
    # Quick check
    if not os.access(port, os.R_OK | os.W_OK):
        # Double-check by attempting to open; some perms lie with access()
        try:
            fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            os.close(fd)
            return True
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EPERM):
                return False
            # Other errors (e.g., EBUSY) are handled elsewhere
    return True

def _connect_linux(baud):
    terminal = "picocom"
    if not shutil.which(terminal):
        click.echo("⚙️ 'picocom' is not installed. Attempting to install via apt...")
        if shutil.which("apt-get"):
            try:
                subprocess.run(["sudo", "apt-get", "update"], check=True)
                subprocess.run(["sudo", "apt-get", "install", "-y", "picocom"], check=True)
            except subprocess.CalledProcessError:
                click.echo("❌ Failed to install picocom (possibly due to lack of sudo privileges). Checking for minicom...")
                terminal = "minicom"
                if not shutil.which(terminal):
                    click.echo("❌ Neither 'picocom' nor 'minicom' is available. Please request the admin to install one manually.")
                    raise SystemExit(1)
                # Create minicom config file to match working settings
                config_file = os.path.expanduser("~/.minirc.custom")
                with open(config_file, "w") as f:
                    f.write("pu port /dev/ttyUSB0\n")
                    f.write(f"pu baudrate {baud}\n")
                    f.write("pu bits 8\n")
                    f.write("pu parity N\n")
                    f.write("pu stopbits 1\n")
                    f.write("pu rtscts No\n")
                    f.write("pu xonxoff No\n")
        else:
            click.echo("❌ 'apt-get' not found. Checking for minicom...")
            terminal = "minicom"
            if not shutil.which(terminal):
                click.echo("❌ Neither 'picocom' nor 'minicom' is available. Please request the admin to install one manually.")
                raise SystemExit(1)
            # Create minicom config file to match working settings
            config_file = os.path.expanduser("~/.minirc.custom")
            with open(config_file, "w") as f:
                f.write("pu port /dev/ttyUSB0\n")
                f.write(f"pu baudrate {baud}\n")
                f.write("pu bits 8\n")
                f.write("pu parity N\n")
                f.write("pu stopbits 1\n")
                f.write("pu rtscts No\n")
                f.write("pu xonxoff No\n")

    ports = sorted(
        subprocess.getoutput("ls /dev/ttyUSB* 2>/dev/null").splitlines()
    )
    if not ports:
        click.echo("❌ No USB serial device found.")
        raise SystemExit(1)

    # Discover ports
    ports = sorted(glob.glob("/dev/ttyUSB*"))
    if not ports:
        click.echo("❌ No USB serial device found.")
        raise SystemExit(1)

    # Classify ports
    busy, no_perm, free_ok = [], [], []
    for p in ports:
        if _is_busy(p):
            busy.append(p)
        elif not _has_rw_permission(p):
            no_perm.append(p)
        else:
            free_ok.append(p)

    if busy:
        click.echo("⚠ Busy (in use): " + ", ".join(busy))
    if no_perm:
        click.echo("⛔ No permission: " + ", ".join(no_perm))
        click.echo(
            "\nTo fix permissions on Ubuntu/Debian:\n"
            "  sudo usermod -aG dialout $USER\n"
            "  # then log out and log back in (or reboot)\n"
            "Temporary (until reboot):\n"
            "  sudo chmod a+rw /dev/ttyUSBX   # not recommended long-term\n"
        )

    if not free_ok:
        click.echo("❌ No accessible, free USB serial ports available.")
        raise SystemExit(1)

    # Choose port
    if len(free_ok) == 1:
        port = free_ok[0]
        click.echo(f"✅ Using the only free port: {port}")
    else:
        click.echo("🔍 Multiple free ports found:")
        for i, p in enumerate(free_ok, 1):
            click.echo(f"  {i}. {p}")
        idx = click.prompt(f"Select a port [1-{len(free_ok)}]", type=int, default=1)
        if not (1 <= idx <= len(free_ok)):
            click.echo("❌ Invalid selection.")
            raise SystemExit(1)
        port = free_ok[idx - 1]

    # Connect
    click.echo(f"🔌 Connecting to {port} with {terminal} ({baud} 8N1)...")
    try:
        if terminal == "picocom":
            click.echo("🧷 To exit: Ctrl+A, then Ctrl+X")
            subprocess.run([terminal, "-b", f"{baud}", "--databits", "8", "--parity", "n", "--stopbits", "1", port])
        else:  # minicom
            cfg = os.path.expanduser("~/.minirc.custom")
            click.echo("🧷 To exit: Ctrl+A, then Q")
            subprocess.run([terminal, "-C", cfg, "-D", port])
    except KeyboardInterrupt:
        click.echo("\n❎ Serial connection interrupted by user.")


def _print_windows_instructions():
    click.echo("📘 To connect to the DevKit via a serial terminal on Windows, follow these steps:\n")

    click.echo("1. Identify the COM Port:")
    click.echo("   • Open **Device Manager** → Expand **Ports (COM & LPT)**.")
    click.echo("   • Look for an entry like **USB Serial Port (COMx)**.\n")

    click.echo("2. Install and Open a Serial Terminal:")
    click.echo("   • Use **PuTTY** (Download from https://www.putty.org/) or **Tera Term**.")
    click.echo("   • Set the **Connection Type** to **Serial**.")
    click.echo("   • Enter the correct **COM Port** (e.g., COM3).")
    click.echo("   • Set **Baud Rate** to **115200**.")
    click.echo("   • Click **Open** to start the connection.\n")

    click.echo("🔌 You are now ready to connect to the DevKit over serial.")
