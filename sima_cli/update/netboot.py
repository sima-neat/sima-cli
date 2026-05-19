from sima_cli.update.updater import download_image
from sima_cli.utils.net import get_local_ip_candidates
from sima_cli.update.remote import wait_for_ssh, copy_file_to_remote_board, DEFAULT_PASSWORD, run_remote_command, init_ssh_session
from sima_cli.utils.env import get_environment_type
import ipaddress
import os
import platform
import subprocess
import threading
import socket
import select
import time
import logging
import click
from errno import EINTR
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from tftpy import TftpServer, TftpException, TftpTimeout, TftpTimeoutExpectACK, DEF_TFTP_PORT, DEF_TIMEOUT_RETRIES
from tftpy.TftpContexts import TftpContextServer
from tftpy.TftpPacketFactory import TftpPacketFactory

# Configuration constants
MAX_BLKSIZE = 1468  # Block size for MTU compatibility
SOCK_TIMEOUT = 2    # Timeout for faster retransmits

log = logging.getLogger("tftpy.InteractiveTftpServer")
emmc_image_paths = []
troot_image_path = None
custom_rootfs = ''
console = Console()


def _ping_host(ip, timeout_seconds=3):
    """Return whether a host responds to one ping within the timeout."""
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_seconds * 1000), ip]
    elif system == "Darwin":
        cmd = ["ping", "-c", "1", "-W", str(timeout_seconds * 1000), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout_seconds), ip]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _validate_override_ip(ip):
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        click.echo(f"❌ Invalid IP address: {ip}")
        return False

    click.echo(f"🏓 Pinging override IP: {ip}")
    if not _ping_host(ip):
        click.echo(f"❌ {ip} did not respond to ping. Aborting flash.")
        return False
    return True


def _select_flash_target(client_manager, override_ip=None):
    if override_ip:
        if _validate_override_ip(override_ip):
            return override_ip
        return None

    clients = [
        (ip, info) for ip, info in client_manager.get_client_info()
        if info.get("state") == "Connected"
    ]

    # must comment out when checking in, this is for testing only
    # clients = [("192.168.1.20", {"type": "devkit", "state": "Connected"})]

    if not clients:
        click.echo("📭 No connected clients available to flash.")
        return None

    if len(clients) == 1:
        return clients[0][0]

    click.echo("👥 Multiple connected clients found. Select one to flash:")
    for idx, (ip, info) in enumerate(clients, 1):
        board_info = info.get("board_info") or "Unknown"
        click.echo(f"   {idx}. {ip} - {board_info}")
    while True:
        choice = input("Enter the number of the client to flash: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(clients):
            return clients[int(choice) - 1][0]
        click.echo("❌ Invalid choice. Try again.")


def _print_troot_programming_warning():
    console.print(
        Panel(
            Text(
                "tRoot programming is about to start.\n"
                "Do not power off or disconnect the device while programming is in progress.",
                style="yellow",
            ),
            title="[yellow]Do Not Power Off Device[/yellow]",
            border_style="yellow",
            expand=False,
        )
    )


def flash_emmc(client_manager, emmc_image_paths, override_ip=None, troot_image_path=None):
    """Flash eMMC on a selected client device."""
    selected_ip = _select_flash_target(client_manager, override_ip=override_ip)
    if not selected_ip:
        return

    click.echo(f"📡 Selected client: {selected_ip}")
    remote_dir = "/tmp"

    if troot_image_path:
        click.echo(f"📤 Copying tRoot image {troot_image_path} to {selected_ip}:{remote_dir}")
        success = copy_file_to_remote_board(
            selected_ip, troot_image_path, remote_dir, passwd=DEFAULT_PASSWORD
        )
        if not success:
            click.echo(f"❌ Failed to copy {troot_image_path} to {selected_ip}. Aborting.")
            return
    else:
        click.echo("⚠️  tRoot image troot_blob.be was not found; continuing with eMMC image transfer.")

    for path in emmc_image_paths:
        click.echo(f"📤 Copying {path} to {selected_ip}:{remote_dir}")
        success = copy_file_to_remote_board(
            selected_ip, path, remote_dir, passwd=DEFAULT_PASSWORD
        )
        if not success:
            click.echo(f"❌ Failed to copy {path} to {selected_ip}. Aborting.")
            return

    try:
        ssh = init_ssh_session(selected_ip, password=DEFAULT_PASSWORD)

        if troot_image_path:
            _print_troot_programming_warning()
            troot_remote_path = f"/tmp/{os.path.basename(troot_image_path)}"
            run_remote_command(ssh, f"sudo troot_upgrade {troot_remote_path}")

        # Step a: Check if eMMC exists
        check_cmd = "[ -e /dev/mmcblk0 ] || (echo '❌ /dev/mmcblk0 not found'; exit 1)"
        run_remote_command(ssh, check_cmd)

        # Step b: umount eMMC
        pre_unmount_cmd = (
            "sudo mount | grep mmcblk0 | awk '{print $3}' | while read mnt; do "
            "sudo umount \"$mnt\"; done"
        )
        run_remote_command(ssh, pre_unmount_cmd)

        # Step c: Decide flashing method
        wic_path = next((p for p in emmc_image_paths if p.endswith(".wic.gz")), None)
        img_path = next((p for p in emmc_image_paths if p.endswith(".img.gz")), None)

        if wic_path:
            filename = os.path.basename(wic_path)
            remote_path = f"/tmp/{filename}"
            flash_cmd = f"sudo bmaptool copy {remote_path} /dev/mmcblk0"
            run_remote_command(ssh, flash_cmd)

            # Step d: Fix GPT for Yocto
            fix_cmd = 'sudo printf "fix\n" | sudo parted ---pretend-input-tty /dev/mmcblk0 print'
            run_remote_command(ssh, fix_cmd)

        elif img_path:
            filename = os.path.basename(img_path)
            remote_path = f"/tmp/{filename}"
            flash_cmd = f"sudo gzip -dc {remote_path} | sudo dd of=/dev/mmcblk0 bs=16M status=progress"
            run_remote_command(ssh, flash_cmd)
        else:
            click.echo("❌ No .wic.gz or .img image found in emmc_image_paths.")
            return

        click.echo("✅ Flash completed. Please reboot the board to boot from eMMC.")
    except Exception as e:
        click.echo(f"❌ Flashing failed: {e}")


class ClientManager:
    """Manages TFTP client state and monitoring."""
    def __init__(self):
        self.clients = {}
        self.lock = threading.Lock()
        self.shutdown_event = threading.Event()

    def add_client(self, ip, filename):
        """Add a new client with initial state."""
        with self.lock:
            if ip not in self.clients:
                start_time = time.time()
                self.clients[ip] = {
                    'state': 'Booting',
                    'filename': filename,
                    'timestamp': start_time,
                    'board_info': None
                }
                click.echo(f"📥 New client connected: {ip}")
                if filename:
                    click.echo(f"📄 Client {ip} requested file: {filename}")
                # Start monitoring thread
                threading.Thread(
                    target=self.monitor_client,
                    args=(ip, start_time),
                    daemon=True
                ).start()

    def monitor_client(self, ip, start_time):
        """
        Monitor client connectivity by waiting for SSH availability on the target IP.
        Uses `wait_for_ssh()` instead of retrieving board info.
        Retries until success or shutdown_event is set.
        """
        try:
            # Wait up to 1 minute after the start before first attempt
            if self.shutdown_event.wait(timeout=max(0, 60 - (time.time() - start_time))):
                return

            while not self.shutdown_event.is_set():
                click.echo(f"🔍 Checking SSH availability for {ip}...")
                try:
                    wait_for_ssh(ip, timeout=120)
                    with self.lock:
                        self.clients[ip]['state'] = 'Connected'
                        self.clients[ip]['board_info'] = "SSH available"
                    click.echo(f"✅ SSH is available on {ip}")
                    break

                except Exception as e:
                    log.info(f"SSH not yet available for {ip}, retrying in 10s: {e}")

                # Wait before retrying
                if self.shutdown_event.wait(timeout=10):
                    break

        except Exception as e:
            log.error(f"Unexpected error while monitoring {ip}: {e}")

    def get_client_info(self):
        """Return sorted client information for display."""
        with self.lock:
            return sorted(self.clients.items(), key=lambda x: x[0])

    def shutdown(self):
        """Signal monitoring threads to exit."""
        self.shutdown_event.set()

class InteractiveTftpServer(TftpServer):
    """Custom TFTP server with client logging and monitoring."""
    def __init__(self, tftproot, client_manager):
        super().__init__(tftproot)
        self.client_manager = client_manager

    def listen(self, listenip="", listenport=DEF_TFTP_PORT, timeout=SOCK_TIMEOUT, retries=DEF_TIMEOUT_RETRIES):
        """Override listen to log client IPs and filenames."""
        tftp_factory = TftpPacketFactory()
        if not listenip:
            listenip = "0.0.0.0"
        log.info(f"Server requested on ip {listenip}, port {listenport}")
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((listenip, listenport))
            self.sock.setblocking(0)
            _, self.listenport = self.sock.getsockname()
        except OSError as err:
            raise err

        self.is_running.set()
        log.info("Starting receive loop...")
        while True:
            log.debug("shutdown_immediately is %s" % self.shutdown_immediately)
            log.debug("shutdown_gracefully is %s" % self.shutdown_gracefully)
            if self.shutdown_immediately:
                log.info("Shutting down now. Session count: %d" % len(self.sessions))
                self.sock.close()
                for key in self.sessions:
                    log.warning("Forcefully closed session with %s" % self.sessions[key].host)
                    self.sessions[key].end()
                self.sessions = []
                self.is_running.clear()
                self.shutdown_gracefully = self.shutdown_immediately = False
                self.client_manager.shutdown()
                break
            elif self.shutdown_gracefully:
                if not self.sessions:
                    log.info("In graceful shutdown mode and all sessions complete.")
                    self.sock.close()
                    self.is_running.clear()
                    self.shutdown_gracefully = self.shutdown_immediately = False
                    self.client_manager.shutdown()
                    break

            inputlist = [self.sock]
            for key in self.sessions:
                inputlist.append(self.sessions[key].sock)

            try:
                readyinput, _, _ = select.select(inputlist, [], [], timeout)
            except OSError as err:
                if err.errno == EINTR:
                    log.debug("Interrupted syscall, retrying")
                    continue
                else:
                    raise

            deletion_list = []
            for readysock in readyinput:
                if readysock == self.sock:
                    log.debug("Data ready on our main socket")
                    buffer, (raddress, rport) = self.sock.recvfrom(MAX_BLKSIZE)
                    log.debug("Read %d bytes", len(buffer))

                    if self.shutdown_gracefully:
                        log.warning("Discarding data on main port, in graceful shutdown mode")
                        continue

                    key = f"{raddress}:{rport}"
                    if key not in self.sessions:
                        log.debug("Creating new server context for session key = %s" % key)
                        filename = None
                        if buffer[:2] == b'\x00\x01':  # RRQ packet
                            filename = buffer[2:].split(b'\x00')[0].decode()
                        self.client_manager.add_client(raddress, filename)
                        self.sessions[key] = TftpContextServer(
                            raddress,
                            rport,
                            timeout,
                            self.root,
                            self.dyn_file_func,
                            self.upload_open,
                            retries=retries
                        )
                        try:
                            self.sessions[key].start(buffer)
                        except TftpTimeoutExpectACK:
                            self.sessions[key].timeout_expectACK = True
                        except TftpException as err:
                            deletion_list.append(key)
                            log.error("Fatal exception thrown from session %s: %s" % (key, str(err)))
                    else:
                        log.warning("received traffic on main socket for existing session??")
                    log.info("Currently handling these sessions:")
                    for session_key, session in list(self.sessions.items()):
                        log.info("    %s" % session)
                else:
                    for key in self.sessions:
                        if readysock == self.sessions[key].sock:
                            log.debug("Matched input to session key %s" % key)
                            self.sessions[key].timeout_expectACK = False
                            try:
                                self.sessions[key].cycle()
                                if self.sessions[key].state is None:
                                    log.info("Successful transfer.")
                                    deletion_list.append(key)
                            except TftpTimeoutExpectACK:
                                self.sessions[key].timeout_expectACK = True
                            except TftpException as err:
                                deletion_list.append(key)
                                log.error("Fatal exception thrown from session %s: %s" % (key, str(err)))
                            break
                    else:
                        log.error("Can't find the owner for this packet. Discarding.")

            now = time.time()
            for key in self.sessions:
                try:
                    self.sessions[key].checkTimeout(now)
                except TftpTimeout as err:
                    log.error(str(err))
                    self.sessions[key].retry_count += 1
                    if self.sessions[key].retry_count >= self.sessions[key].retries:
                        log.debug("hit max retries on %s, giving up" % self.sessions[key])
                        deletion_list.append(key)
                    else:
                        log.debug("resending on session %s" % self.sessions[key])
                        self.sessions[key].state.resendLast()

            for key in deletion_list:
                log.info("Session %s complete" % key)
                if key in self.sessions:
                    log.debug("Gathering up metrics from session before deleting")
                    self.sessions[key].end()
                    metrics = self.sessions[key].metrics
                    if metrics.duration == 0:
                        log.info("Duration too short, rate undetermined")
                    else:
                        log.info("Transferred %d bytes in %.2f seconds" % (metrics.bytes, metrics.duration))
                        log.info("Average rate: %.2f kbps" % metrics.kbps)
                        click.echo(f"✅ Transfer to {self.sessions[key].host} complete: "
                                   f"{metrics.bytes} bytes in {metrics.duration:.2f} s ({metrics.kbps:.2f} kbps)")
                    log.info("%.2f bytes in resent data" % metrics.resent_bytes)
                    log.info("%d duplicate packets" % metrics.dupcount)
                    log.debug("Deleting session %s" % key)
                    del self.sessions[key]
                    log.debug("Session list is now %s" % self.sessions)
                else:
                    log.warning("Strange, session %s is not on the deletion list" % key)

        self.is_running.clear()
        self.shutdown_gracefully = self.shutdown_immediately = False
        self.client_manager.shutdown()

def run_cli(client_manager):
    """Run the interactive CLI for netboot commands."""
    click.echo("\n🛠  Type 'c' to see connected IPs and board info, 'f [ip]' to flash eMMC, or 'q' to quit.\n")
    while True:
        try:
            user_input = input("netboot> ").strip()
            parts = user_input.split()
            command = parts[0].lower() if parts else ""
            args = parts[1:]

            if command in {"q", "quit", "exit"}:
                click.echo("🛑 Shutting down TFTP server.")
                return True
            elif command == "c":
                client_info = client_manager.get_client_info()
                if client_info:
                    click.echo("🧾 TFTP client IPs and status:")
                    for ip, info in client_info:
                        state = info['state']
                        filename = info['filename'] or "Unknown"
                        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info['timestamp']))
                        board_info = info['board_info']
                        click.echo(f"   • {ip}: {state}, Initial File: {filename}, First seen: {timestamp}")
                        if board_info:
                            click.echo(f"     Board Info: {board_info}")
                else:
                    click.echo("📭 No TFTP client requests received yet.")
            elif command == "f":
                if len(args) > 1:
                    click.echo("❌ Usage: f [ip]")
                    continue
                override_ip = args[0] if args else None
                click.echo(f"🔧 Initiating eMMC flash {emmc_image_paths}.")
                flash_emmc(
                    client_manager,
                    emmc_image_paths,
                    override_ip=override_ip,
                    troot_image_path=troot_image_path,
                )
            elif command == "":
                continue
            else:
                click.echo("❓ Unknown command. Try 'c' to print client list, 'f [ip]' to flash emmc, or 'q'.")
        except (KeyboardInterrupt, EOFError):
            click.echo("\n🛑 Exiting netboot session.")
            return True

def setup_netboot(version: str, board: str, internal: bool = False, autoflash: bool = False, flavor: str = 'headless', rootfs: str = '', swtype: str = 'yocto'):
    """
    Download and serve a bootable image for network boot over TFTP with client monitoring.

    Parameters:
        version (str): Firmware version to download (e.g., "1.6.0").
        board (str): Target board type, e.g., "modalix" or "davinci".
        internal (bool): Whether to use internal download sources. Defaults to False.
        autoflash (bool): Whether to automatically flash the devkit when networked booted. Defaults to False.
        flavor (str): The software flavor, can be either headless or full.
        rootfs (str): The root fs folder, which contains the .wic.gz file and the .bmap file, for custom image writing.
        swtype (str): The software type, either yocto or elxr.

    Raises:
        RuntimeError: If the download or TFTP setup fails.
    """
    global emmc_image_paths
    global troot_image_path
    global custom_rootfs

    if platform.system() == "Windows":
        click.secho("❌ Netboot with built-in TFTP is not supported on Windows. Use macOS or Linux.", fg="red")
        exit(1)

    env_type, _ = get_environment_type()
    if env_type == 'board':
        click.secho("❌ Netboot is not supported on the DevKit, use macOS or Linux host instead.", fg="red")
        exit(1)

    try:
        click.echo(f"⬇️  Downloading netboot image for version: {version}, board: {board}, swtype: {swtype}")
        file_list = download_image(version, board, swtype=swtype, internal=internal, update_type='netboot', flavor=flavor)
        if not isinstance(file_list, list):
            raise ValueError("Expected list of extracted files, got something else.")
        extract_dir = os.path.dirname(file_list[0])
        click.echo(f"📁 Image extracted to: {extract_dir}")
        
        # Extract specific image paths
        wic_gz_file = next((f for f in file_list if f.endswith(".wic.gz")), None)
        bmap_file = next((f for f in file_list if f.endswith(".wic.bmap")), None)
        elxr_img_file = next((f for f in file_list if f.endswith(".img.gz")), None)
        troot_image_path = next((f for f in file_list if os.path.basename(f) == "troot_blob.be"), None)
        emmc_image_paths = [p for p in [wic_gz_file, bmap_file, elxr_img_file] if p]

        # Check global custom_rootfs before doing anything else
        custom_rootfs = rootfs
        if custom_rootfs:
            if not os.path.isdir(custom_rootfs):
                raise RuntimeError(f"❌ custom_rootfs path is not a directory: {custom_rootfs}")

            import glob
            wic_gz_file = next(iter(glob.glob(os.path.join(custom_rootfs, "*.wic.gz"))), None)
            bmap_file   = next(iter(glob.glob(os.path.join(custom_rootfs, "*.wic.bmap"))), None)
            exlr_file   = next(iter(glob.glob(os.path.join(custom_rootfs, "*.img.gz"))), None)
            troot_image_path = next(iter(glob.glob(os.path.join(custom_rootfs, "troot_blob.be"))), None)

            if not (wic_gz_file and bmap_file):
                raise RuntimeError(
                    f"❌ custom_rootfs '{custom_rootfs}' must contain both .wic.gz and .wic.bmap files."
                )

            emmc_image_paths = [wic_gz_file, bmap_file, exlr_file]
            click.echo(f"📁 Using custom_rootfs: {custom_rootfs}")

        click.echo(f"📁 eMMC image paths are: {emmc_image_paths}")
        click.echo(f"📁 tRoot image path is: {troot_image_path}")

    except Exception as e:
        raise RuntimeError(f"❌ Failed to download and extract netboot image: {e}")

    try:
        click.echo(f"🚀 Starting TFTP server in: {extract_dir}")
        ip_candidates = get_local_ip_candidates()
        if not ip_candidates:
            click.echo("❌ No suitable local IP addresses found.")
            exit(1)

        click.echo("🌐 TFTP server is listening on these interfaces (UDP port 69):")
        for iface, ip in ip_candidates:
            click.echo(f"   🔹 {iface}: {ip}")

        client_manager = ClientManager()

        server = InteractiveTftpServer(tftproot=extract_dir, client_manager=client_manager)
        server_thread = threading.Thread(target=server.listen, args=('0.0.0.0', 69), daemon=True)
        server_thread.start()

        if run_cli(client_manager):
            server.stop(now=True)
            client_manager.shutdown()

    except PermissionError:
        raise RuntimeError("❌ Permission denied. You must run this command with sudo to bind to port 69.")
    except OSError as e:
        raise RuntimeError(f"❌ Failed to start TFTP server: {e}")
