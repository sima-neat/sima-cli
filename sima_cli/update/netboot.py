from sima_cli.update.updater import (
    _extract_required_files,
    _resolve_firmware_url,
    download_image,
    resolve_image_reference,
)
from sima_cli.utils.net import get_local_ip_candidates
from sima_cli.update.remote import wait_for_ssh, copy_file_to_remote_board, DEFAULT_PASSWORD, run_remote_command, init_ssh_session, get_remote_board_info
from sima_cli.utils.env import get_environment_type
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import hashlib
import ipaddress
import inspect
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import socket
import select
import time
import logging
import uuid
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

NETBOOT_CACHE_SCHEMA = 2
NETBOOT_CACHE_MANIFEST = "manifest.json"


@dataclass
class NetbootAssets:
    """Prepared paths used by the TFTP server and optional eMMC flashing."""

    tftp_root: str
    emmc_image_paths: List[str]
    troot_image_path: Optional[str]
    cache_dir: str
    reused_cache: bool = False


def _netboot_cache_root() -> Path:
    return Path(tempfile.gettempdir()) / "sima-cli" / "netboot"


def _is_archive(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".tar", ".tar.gz", ".tgz"))


def _describe_candidates(paths: List[Path]) -> str:
    return ", ".join(str(path) for path in paths) if paths else "none"


def _require_one_candidate(role: str, preferred: List[Path], fallback: List[Path]) -> Path:
    candidates = preferred or fallback
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one {role}; found {len(candidates)}: "
            f"{_describe_candidates(candidates)}"
        )
    return candidates[0]


def _discover_local_netboot_sources(
    images: str,
    board: str,
    swtype: str,
    archive_path: Optional[str] = None,
):
    images_dir = Path(images).expanduser().resolve()
    if archive_path:
        selected_archive = Path(archive_path).expanduser().resolve()
        if not selected_archive.is_file() or not _is_archive(selected_archive):
            raise RuntimeError(f"Local netboot source is not an archive: {selected_archive}")
        files = sorted(path for path in selected_archive.parent.iterdir() if path.is_file())
    else:
        selected_archive = None
        files = sorted(path for path in images_dir.rglob("*") if path.is_file())
    archives = [path for path in files if _is_archive(path)]

    if swtype == "elxr":
        if selected_archive:
            archive = selected_archive
        else:
            tftp_archives = [path for path in archives if "tftp-boot" in path.name.lower()]
            board_archives = [
                path for path in tftp_archives if board.lower() in path.name.lower()
            ]
            archive = _require_one_candidate(
                "eLxr minimal TFTP archive", board_archives or tftp_archives, archives
            )
        emmc_candidates = [
            path for path in files
            if path.name.lower().endswith(".img.gz")
            and "recovery" not in path.name.lower()
        ]
        board_emmc = [
            path for path in emmc_candidates if board.lower() in path.name.lower()
        ]
        emmc = _require_one_candidate(
            "eLxr eMMC .img.gz image", board_emmc, emmc_candidates
        )
        return archive, [emmc]

    if selected_archive:
        archive = selected_archive
    else:
        preferred_archives = [
            path for path in archives
            if path.name.lower() in {"release.tar.gz", "graphics.tar.gz"}
        ]
        archive = _require_one_candidate(
            "Yocto release archive",
            preferred_archives,
            archives if len(archives) == 1 else [],
        )
    return archive, []


def _source_file_identity(path: Path):
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _tftp_root_from_files(paths) -> Path:
    boot_scripts = [
        Path(path).resolve()
        for path in paths
        if Path(path).name == "netboot.scr.uimg"
    ]
    if len(boot_scripts) > 1:
        raise RuntimeError(
            "Expected exactly one netboot.scr.uimg; found "
            f"{len(boot_scripts)}: {_describe_candidates(boot_scripts)}"
        )
    if boot_scripts:
        return boot_scripts[0].parent
    return Path(os.path.dirname(paths[0])).resolve()


def _cache_key(identity) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _cache_lock_path(cache_dir: Path) -> Path:
    return cache_dir.parent / f"{cache_dir.name}.lock"


def _acquire_cache_lease(cache_dir: str):
    import fcntl

    lock_file = _cache_lock_path(Path(cache_dir)).open("a+")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
    return lock_file


def _release_cache_lease(lock_file):
    import fcntl

    if lock_file is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def _cached_tftp_root(cache_dir: Path, manifest) -> Optional[Path]:
    relative = manifest.get("tftp_root")
    if not isinstance(relative, str):
        return None
    root = (cache_dir / relative).resolve()
    try:
        root.relative_to(cache_dir.resolve())
    except ValueError:
        return None
    return root if root.is_dir() else None


def _read_valid_cache_manifest(cache_dir: Path, identity):
    def file_matches(item) -> bool:
        path = (cache_dir / item["path"]).resolve()
        try:
            path.relative_to(cache_dir.resolve())
        except ValueError:
            return False
        return path.is_file() and path.stat().st_size == item["size"]

    try:
        manifest = json.loads(
            (cache_dir / NETBOOT_CACHE_MANIFEST).read_text(encoding="utf-8")
        )
        if manifest.get("schema") != NETBOOT_CACHE_SCHEMA:
            return None
        if manifest.get("identity") != identity:
            return None
        if _cached_tftp_root(cache_dir, manifest) is None:
            return None
        prepared_files = manifest.get("files", [])
        if not prepared_files or not all(file_matches(item) for item in prepared_files):
            return None
        return manifest
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _prepare_cache_entry(identity, builder):
    import fcntl

    cache_root = _netboot_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_dir = cache_root / _cache_key(identity)

    with _cache_lock_path(cache_dir).open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        manifest = _read_valid_cache_manifest(cache_dir, identity)
        if manifest is not None:
            click.echo(f"♻️  Reusing prepared netboot cache: {cache_dir}")
            return cache_dir, _cached_tftp_root(cache_dir, manifest), True

        staging_dir = cache_root / f".{cache_dir.name}.{uuid.uuid4().hex}"
        content_root = staging_dir / "content"
        content_root.mkdir(parents=True)
        try:
            staged_tftp_root = Path(builder(content_root)).resolve()
            staged_tftp_root.relative_to(staging_dir.resolve())
            if not staged_tftp_root.is_dir():
                raise RuntimeError(f"Prepared TFTP root does not exist: {staged_tftp_root}")
            prepared_files = sorted(
                ({
                    "path": str(path.relative_to(staging_dir)),
                    "size": path.stat().st_size,
                } for path in content_root.rglob("*") if path.is_file()),
                key=lambda item: item["path"],
            )
            if not prepared_files:
                raise RuntimeError("Netboot preparation produced no TFTP files.")
            manifest = {
                "schema": NETBOOT_CACHE_SCHEMA,
                "identity": identity,
                "files": prepared_files,
                "tftp_root": str(staged_tftp_root.relative_to(staging_dir.resolve())),
            }
            (staging_dir / NETBOOT_CACHE_MANIFEST).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            os.replace(staging_dir, cache_dir)
        except BaseException:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        click.echo(f"✅ Prepared netboot cache: {cache_dir}")
        return cache_dir, cache_dir / manifest["tftp_root"], False


def _classify_prepared_assets(
    cache_dir: Path,
    board: str,
    swtype: str,
    external_emmc_paths: Optional[List[Path]] = None,
    require_emmc: bool = True,
    tftp_root: Optional[Path] = None,
) -> NetbootAssets:
    tftp_root = tftp_root or cache_dir / "content"
    files = sorted(path for path in (cache_dir / "content").rglob("*") if path.is_file())
    troot_candidates = [path for path in files if path.name == "troot_blob.be"]
    troot = str(troot_candidates[0]) if troot_candidates else None

    if external_emmc_paths:
        emmc_paths = [str(path) for path in external_emmc_paths]
    elif swtype == "elxr":
        candidates = [path for path in files if path.name.lower().endswith(".img.gz")]
        preferred = [path for path in candidates if board.lower() in path.name.lower()]
        if require_emmc:
            emmc_paths = [str(_require_one_candidate(
                "eLxr eMMC .img.gz image", preferred, candidates
            ))]
        else:
            selected = preferred or candidates
            emmc_paths = [str(selected[0])] if len(selected) == 1 else []
    else:
        wic_candidates = [path for path in files if path.name.lower().endswith(".wic.gz")]
        bmap_candidates = [path for path in files if path.name.lower().endswith(".wic.bmap")]
        preferred_wic = [path for path in wic_candidates if board.lower() in path.name.lower()]
        preferred_bmap = [path for path in bmap_candidates if board.lower() in path.name.lower()]
        if require_emmc:
            emmc_paths = [
                str(_require_one_candidate("Yocto eMMC .wic.gz image", preferred_wic, wic_candidates)),
                str(_require_one_candidate("Yocto eMMC .wic.bmap file", preferred_bmap, bmap_candidates)),
            ]
        else:
            selected_wic = preferred_wic or wic_candidates
            selected_bmap = preferred_bmap or bmap_candidates
            emmc_paths = []
            if len(selected_wic) == 1:
                emmc_paths.append(str(selected_wic[0]))
            if len(selected_bmap) == 1:
                emmc_paths.append(str(selected_bmap[0]))

    return NetbootAssets(
        tftp_root=str(tftp_root),
        emmc_image_paths=emmc_paths,
        troot_image_path=troot,
        cache_dir=str(cache_dir),
    )


def _prepare_local_netboot_assets(
    images: str,
    board: str,
    swtype: str,
    flavor: str,
    archive_path: Optional[str] = None,
) -> NetbootAssets:
    archive, external_emmc = _discover_local_netboot_sources(
        images, board, swtype, archive_path=archive_path
    )
    identity = {
        "mode": "local",
        "board": board,
        "swtype": swtype,
        "flavor": flavor,
        "sources": [_source_file_identity(path) for path in [archive] + external_emmc],
    }

    def build(content_root: Path):
        staged_archive = content_root / archive.name
        shutil.copy2(archive, staged_archive)
        extracted = _extract_required_files(
            str(staged_archive), board, update_type="netboot", flavor=flavor
        )
        if not extracted:
            raise RuntimeError(f"No netboot files could be extracted from {archive}.")
        legacy_tftp_root = _tftp_root_from_files(extracted)
        try:
            legacy_tftp_root.relative_to(content_root.resolve())
        except ValueError as error:
            raise RuntimeError(
                f"Extracted TFTP root escaped the managed cache: {legacy_tftp_root}"
            ) from error
        _classify_prepared_assets(
            content_root.parent, board, swtype, external_emmc,
            tftp_root=legacy_tftp_root,
        )
        return legacy_tftp_root

    cache_dir, tftp_root, reused = _prepare_cache_entry(identity, build)
    assets = _classify_prepared_assets(
        cache_dir, board, swtype, external_emmc, tftp_root=tftp_root
    )
    assets.reused_cache = reused
    return assets


def _prepare_downloaded_netboot_assets(
    version: str,
    board: str,
    swtype: str,
    internal: bool,
    flavor: str,
    allow_daily_fallback: bool = False,
) -> NetbootAssets:
    uses_complete_internal_set = (
        internal and swtype == "elxr" and board == "modalix"
        and not version.startswith(("http://", "https://"))
        and not os.path.exists(version)
    )
    internal_selection = None
    if uses_complete_internal_set:
        from sima_cli.update.netboot_artifacts import resolve_netboot_image_selection

        internal_selection = resolve_netboot_image_selection(
            version,
            board,
            flavor,
            allow_daily_fallback=allow_daily_fallback,
        )
        resolved_reference = internal_selection.version
        source_identity = {
            "internal_complete_set": internal_selection.cache_identity(),
        }
    else:
        resolved_reference = resolve_image_reference(
            version, board, swtype, internal=internal,
            update_type="netboot", flavor=flavor,
        )
        source_identity = _resolve_firmware_url(
            resolved_reference, board, internal=internal,
            flavor=flavor, swtype=swtype, update_type="netboot",
        )
    identity = {
        "mode": "download",
        "source": source_identity,
        "board": board,
        "swtype": swtype,
        "flavor": flavor,
        "internal": internal,
        "allow_daily_fallback": allow_daily_fallback,
    }

    def build(content_root: Path):
        click.echo(
            f"⬇️  Downloading netboot image for version: {resolved_reference}, "
            f"board: {board}, swtype: {swtype}"
        )
        if internal_selection is not None:
            from sima_cli.update.netboot_artifacts import download_selected_netboot_image

            file_list = download_selected_netboot_image(
                internal_selection,
                board,
                flavor,
                allow_daily_fallback=allow_daily_fallback,
                destination_dir=str(content_root),
            )
        else:
            file_list = download_image(
                resolved_reference, board, swtype=swtype, internal=internal,
                update_type="netboot", flavor=flavor,
                allow_daily_fallback=allow_daily_fallback,
                destination_dir=str(content_root),
                reference_is_resolved=True,
            )
        if not isinstance(file_list, list) or not file_list:
            raise RuntimeError("Netboot download did not produce any files.")
        legacy_tftp_root = _tftp_root_from_files(file_list)
        try:
            legacy_tftp_root.relative_to(content_root.resolve())
        except ValueError:
            # Keep compatibility with download implementations (and test doubles)
            # that ignore destination_dir: copy only their returned files into
            # the managed entry before serving them.
            for returned in map(Path, file_list):
                if not returned.is_file():
                    continue
                try:
                    relative = returned.resolve().relative_to(legacy_tftp_root)
                except ValueError:
                    relative = Path(returned.name)
                destination = content_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(returned, destination)
            legacy_tftp_root = content_root.resolve()
        _classify_prepared_assets(
            content_root.parent, board, swtype,
            require_emmc=False, tftp_root=legacy_tftp_root,
        )
        return legacy_tftp_root

    cache_dir, tftp_root, reused = _prepare_cache_entry(identity, build)
    assets = _classify_prepared_assets(
        cache_dir, board, swtype, require_emmc=False, tftp_root=tftp_root
    )
    assets.reused_cache = reused
    return assets


def _prepare_netboot_assets(
    version: Optional[str],
    board: str,
    swtype: str,
    internal: bool,
    flavor: str,
    images: Optional[str] = None,
    allow_daily_fallback: bool = False,
) -> NetbootAssets:
    if images:
        return _prepare_local_netboot_assets(images, board, swtype, flavor)
    if not version:
        raise RuntimeError("A firmware version or --images directory is required for netboot.")
    if os.path.isfile(version):
        return _prepare_local_netboot_assets(
            os.path.dirname(os.path.abspath(version)), board, swtype, flavor,
            archive_path=version,
        )
    return _prepare_downloaded_netboot_assets(
        version, board, swtype, internal, flavor,
        allow_daily_fallback=allow_daily_fallback,
    )


def _delete_netboot_cache(cache_dir: str):
    import fcntl

    cache_root = _netboot_cache_root().resolve()
    target = Path(cache_dir).resolve()
    if target.parent != cache_root:
        raise RuntimeError(f"Refusing to delete unmanaged cache path: {target}")
    with _cache_lock_path(target).open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OSError("cache is in use by another netboot session") from error
        shutil.rmtree(target)
    click.echo(f"🧹 Deleted netboot cache: {target}")


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


def flash_emmc(
    client_manager,
    emmc_image_paths,
    override_ip=None,
    troot_image_path=None,
    configuration=None,
):
    """Flash eMMC on a selected client device."""
    if not emmc_image_paths:
        click.echo(
            "⚠️  No eMMC image was prepared. TFTP netboot is available, "
            "but eMMC flashing is disabled for this session."
        )
        return
    selected_ip = _select_flash_target(client_manager, override_ip=override_ip)
    if not selected_ip:
        return
    if configuration is not None and configuration.changed:
        configuration.devkit = selected_ip

    click.echo(f"📡 Selected client: {selected_ip}")
    remote_dir = "/tmp"

    troot_command = None
    if troot_image_path:
        _, running_version, _, _, _ = get_remote_board_info(
            selected_ip, passwd=DEFAULT_PASSWORD
        )
        match = re.match(r"^(\d+)\.(\d+)(?=$|[._-])", running_version.strip().strip('"\''))
        if not match:
            click.echo("❌ Cannot determine the running firmware version. Aborting before tRoot programming.")
            return
        if tuple(map(int, match.groups())) >= (3, 0):
            troot_command = "sudo sh -c 'cd /tmp && exec simaai-trootctl full-flash'"
        else:
            remote_blob = shlex.quote(f"/tmp/{os.path.basename(troot_image_path)}")
            troot_command = f"sudo troot_upgrade {remote_blob}"
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
            run_remote_command(ssh, troot_command, check=True)

        # Match mounts through the entire block-device tree, including LVM.
        from sima_cli.update.emmc import prepare_emmc
        preparation = inspect.getsource(prepare_emmc) + '\nprepare_emmc()\n'
        run_remote_command(ssh, 'sudo python3 -c ' + shlex.quote(preparation), check=True,
                           command_label='Preparing eMMC for flashing')

        # Step c: Decide flashing method
        wic_path = next((p for p in emmc_image_paths if p.endswith(".wic.gz")), None)
        img_path = next((p for p in emmc_image_paths if p.endswith(".img.gz")), None)

        if wic_path:
            filename = os.path.basename(wic_path)
            remote_path = shlex.quote(f"/tmp/{filename}")
            flash_cmd = f"sudo bmaptool copy {remote_path} /dev/mmcblk0"
            run_remote_command(ssh, flash_cmd, check=True)

            # Step d: Fix GPT for Yocto
            fix_cmd = 'sudo printf "fix\n" | sudo parted ---pretend-input-tty /dev/mmcblk0 print'
            run_remote_command(ssh, fix_cmd, check=True)

        elif img_path:
            filename = os.path.basename(img_path)
            remote_path = shlex.quote(f"/tmp/{filename}")
            flash_cmd = "sudo bash -o pipefail -c " + shlex.quote(
                f"gzip -dc {remote_path} | dd of=/dev/mmcblk0 bs=16M conv=fsync status=progress"
            )
            run_remote_command(ssh, flash_cmd, check=True)
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
        self.monitor_threads = []

    def add_client(self, ip, filename):
        """Add a new client with initial state."""
        with self.lock:
            if ip not in self.clients:
                start_time = time.time()
                self.clients[ip] = {
                    'state': 'SSH check stopped' if self.shutdown_event.is_set() else 'Booting',
                    'filename': filename,
                    'timestamp': start_time,
                    'board_info': None
                }
                click.echo(f"📥 New client connected: {ip}")
                if filename:
                    click.echo(f"📄 Client {ip} requested file: {filename}")
                if self.shutdown_event.is_set():
                    return
                # Start monitoring thread
                thread = threading.Thread(
                    target=self.monitor_client,
                    args=(ip, start_time),
                    daemon=True
                )
                self.monitor_threads.append(thread)
                thread.start()

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
                    if not wait_for_ssh(ip, timeout=120, cancel_event=self.shutdown_event):
                        if not self.shutdown_event.is_set():
                            with self.lock:
                                self.clients[ip]['state'] = 'SSH unavailable'
                            _print_ip_recovery_help()
                        if self.shutdown_event.wait(timeout=10):
                            break
                        continue
                    with self.lock:
                        if self.shutdown_event.is_set():
                            break
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

    def stop_monitoring(self):
        """Stop automatic SSH checks while leaving the TFTP server running."""
        self.shutdown_event.set()
        with self.lock:
            threads = list(self.monitor_threads)
            for info in self.clients.values():
                if info['state'] in {'Booting', 'SSH unavailable'}:
                    info['state'] = 'SSH check stopped'
        for thread in threads:
            thread.join()

    def shutdown(self):
        """Signal monitoring threads to exit."""
        self.stop_monitoring()

class InteractiveTftpServer(TftpServer):
    """Custom TFTP server with client logging and monitoring."""
    def __init__(self, tftproot, client_manager):
        super().__init__(tftproot)
        self.client_manager = client_manager

    def stop(self, now=False):
        """Request shutdown and wake the receive loop so no later RRQ is served."""
        super().stop(now=now)
        sock = getattr(self, 'sock', None)
        if sock is None:
            return
        try:
            address, port = sock.getsockname()
            if address in ('', '0.0.0.0'):
                address = '127.0.0.1'
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake:
                wake.sendto(b'\0', (address, port))
        except OSError:
            log.debug('Unable to wake TFTP receive loop during shutdown', exc_info=True)

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

            if self.shutdown_immediately:
                continue

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

def _print_ip_recovery_help():
    click.echo("The board may have received a different IP address during boot.")
    click.echo("Type 'd' to discover devices on the local network.")
    click.echo("Or connect the board's serial console, run 'sima-cli serial' in another terminal, "
               "log in, and run 'ip -4 addr' (or 'ifconfig') on the device.")
    click.echo("Identify your board's current IP, then type 'f <ip>' here to flash it "
               "(for example: f 192.168.2.3).")


def _discover_netboot_devices():
    from sima_cli.discover.discover import discover_and_probe

    try:
        discover_and_probe(mdns_only=True)
    except Exception as exc:
        click.echo(f"❌ Device discovery failed: {exc}")
    _print_ip_recovery_help()


def run_cli(client_manager, configuration=None):
    """Run the interactive CLI for netboot commands."""
    click.echo("\n🛠  Type 'c' to see connected IPs and board info, 'd' to discover devices, 'f [ip]' to flash eMMC, or 'q' to quit.\n")
    click.echo("Press Ctrl+C at the netboot prompt to stop SSH reboot checks if the board's IP changed.")
    while True:
        try:
            try:
                user_input = input("netboot> ").strip()
            except KeyboardInterrupt:
                client_manager.stop_monitoring()
                click.echo("\nStopped SSH reboot checks. The TFTP server is still running.")
                _print_ip_recovery_help()
                continue
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
            elif command == "d":
                if args:
                    click.echo("❌ Usage: d")
                    continue
                client_manager.stop_monitoring()
                _discover_netboot_devices()
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
                    configuration=configuration,
                )
            elif command == "":
                continue
            else:
                click.echo("❓ Unknown command. Try 'c' to print client list, 'd' to discover devices, 'f [ip]' to flash emmc, or 'q'.")
        except (KeyboardInterrupt, EOFError):
            click.echo("\n🛑 Exiting netboot session.")
            return True

def auto_flash(client_manager, selected_ip, timeout=900, configuration=None):
    """Flash only the confirmed device, once, after its network boot is ready."""
    click.echo(f'Waiting for SSH on {selected_ip}; flashing will start automatically.')
    deadline = time.monotonic() + timeout
    while not client_manager.shutdown_event.is_set():
        connected = any(ip == selected_ip and info.get('state') == 'Connected'
                        for ip, info in client_manager.get_client_info())
        if connected:
            from sima_cli.update.remote import run_remote_command_capture
            ssh = init_ssh_session(selected_ip, password=DEFAULT_PASSWORD)
            try:
                code, cmdline, _ = run_remote_command_capture(ssh, 'cat /proc/cmdline')
            finally:
                ssh.close()
            if code != 0 or 'root=/dev/ram0' not in cmdline.split():
                raise click.ClickException('Automatic flashing stopped: the selected DevKit is not confirmed '
                                           'to be running the network boot image.')
            click.echo(f'Starting automatic flash on {selected_ip}.')
            flash_emmc(client_manager, emmc_image_paths, override_ip=selected_ip,
                       troot_image_path=troot_image_path, configuration=configuration)
            return
        if time.monotonic() >= deadline:
            raise click.ClickException(f'Timed out waiting for network boot on {selected_ip}; no automatic flash was started.')
        client_manager.shutdown_event.wait(0.5)


def setup_netboot(
    version: Optional[str],
    board: str,
    internal: bool = False,
    autoflash: bool = False,
    flavor: str = 'headless',
    rootfs: str = '',
    swtype: str = 'yocto',
    allow_daily_fallback: bool = False,
    devkit: str = None,
    images: Optional[str] = None,
    delete_cache: bool = False,
):
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
        images (str): Optional read-only directory containing local netboot source artifacts.
        delete_cache (bool): Delete the managed cache entry after the netboot session exits.

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

    cache_dir = None
    cache_lease = None
    try:
        assets = _prepare_netboot_assets(
            version, board, swtype, internal, flavor, images=images,
            allow_daily_fallback=allow_daily_fallback,
        )
        cache_dir = assets.cache_dir
        extract_dir = assets.tftp_root
        emmc_image_paths = assets.emmc_image_paths
        troot_image_path = assets.troot_image_path
        click.echo(f"📁 TFTP image prepared in: {extract_dir}")

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

            emmc_image_paths = [path for path in [wic_gz_file, bmap_file, exlr_file] if path]
            click.echo(f"📁 Using custom_rootfs: {custom_rootfs}")

        click.echo(f"📁 eMMC image paths are: {emmc_image_paths}")
        click.echo(f"📁 tRoot image path is: {troot_image_path}")

    except Exception as e:
        raise RuntimeError(f"❌ Failed to download and extract netboot image: {e}")

    from sima_cli.update.netboot_device import (
        NetbootConfiguration,
        configure_and_reboot,
        resolve_device,
        restore_environment,
        server_address,
    )
    if not os.path.isfile(os.path.join(extract_dir, 'netboot.scr.uimg')):
        raise RuntimeError('The netboot archive is missing netboot.scr.uimg; the DevKit was not changed.')
    selected_devkit = resolve_device(devkit)
    server_ip = server_address(selected_devkit) if selected_devkit else None
    cache_lease = _acquire_cache_lease(cache_dir)
    server = None
    client_manager = None
    server_thread = None
    tftp_ready = False
    cache_can_delete = True
    configuration = NetbootConfiguration(selected_devkit) if selected_devkit else None
    try:
        click.echo(f"🚀 Starting TFTP server in: {extract_dir}")
        ip_candidates = get_local_ip_candidates()
        if server_ip and not any(ip == server_ip for _, ip in ip_candidates):
            ip_candidates.append(('route to selected DevKit', server_ip))

        client_manager = ClientManager()

        server = InteractiveTftpServer(tftproot=extract_dir, client_manager=client_manager)
        startup_errors = []

        def serve():
            try:
                server.listen('0.0.0.0', 69)
            except Exception as exc:
                startup_errors.append(exc)

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()

        deadline = time.monotonic() + 5
        while not server.is_running.wait(0.05):
            if startup_errors:
                raise startup_errors[0]
            if not server_thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError('TFTP server did not become ready; the DevKit was not changed.')

        tftp_ready = True
        click.echo("🌐 TFTP server is listening on these interfaces (UDP port 69):")
        for iface, ip in ip_candidates:
            click.echo(f"   🔹 {iface}: {ip}")

        if selected_devkit:
            reboot_scheduled = configure_and_reboot(
                selected_devkit,
                server_ip,
                autoflash=autoflash,
                configuration=configuration,
            )
            if not reboot_scheduled:
                click.echo(
                    f'Skipped network boot setup and reboot for {selected_devkit}. '
                    'The TFTP server is still running; waiting for a device to connect. '
                    'Boot another device from the network using a reachable host IP listed above. '
                    'Once "✅ SSH is available on <IP>" appears, type "f" to flash the device.'
                )
                if autoflash:
                    click.echo('Automatic flashing is disabled because device setup was not confirmed.')
            elif autoflash:
                auto_flash(client_manager, selected_devkit, configuration=configuration)
        else:
            message = Text('No DevKit was discovered. This program is still serving the netboot images.\n\n'
                           'Configure the DevKit manually through its serial console to boot from the network, '
                           'using a reachable host IP listed above as its TFTP server.\n'
                           'Keep this program running. Once "✅ SSH is available on <IP>" appears, type "f" to flash the device.')
            if autoflash:
                message.append('\n\nAutomatic flashing is disabled because no DevKit was selected.', style='bold yellow')
            console.print(Panel(message, title='Manual netboot setup', border_style='yellow'))
        run_cli(client_manager, configuration=configuration)

    except OSError as e:
        if tftp_ready:
            raise RuntimeError(f"Netboot device setup or session failed after TFTP started: {e}") from e
        if isinstance(e, PermissionError):
            raise RuntimeError("❌ Permission denied. You must run this command with sudo to bind to port 69.") from e
        raise RuntimeError(f"❌ Failed to start TFTP server: {e}") from e

    finally:
        shutdown_error = None
        if configuration is not None and configuration.changed:
            try:
                restore_environment(configuration)
            except Exception as exc:
                recovery_backup = (
                    configuration.local_backup_dir or configuration.backup_dir
                )
                click.secho(
                    f'Could not restore the saved U-Boot environment on '
                    f'{configuration.devkit}: {exc}. Use serial recovery before rebooting again. '
                    f'Backup: {recovery_backup}',
                    fg='red',
                    err=True,
                )
                shutdown_error = RuntimeError(
                    f'Netboot ended, but U-Boot restoration failed on '
                    f'{configuration.devkit}.'
                )
        if server is not None:
            server.stop(now=True)
        if client_manager is not None:
            client_manager.shutdown()
        if server_thread is not None:
            server_thread.join(timeout=5)
            if tftp_ready and server_thread.is_alive():
                cache_can_delete = False
                click.echo(
                    "⚠️  TFTP server did not stop within 5 seconds; preserving its cache.",
                    err=True,
                )
                shutdown_error = RuntimeError(
                    'TFTP server did not stop; UDP port 69 may still be active.'
                )
        _release_cache_lease(cache_lease)
        if delete_cache and cache_dir and cache_can_delete:
            try:
                _delete_netboot_cache(cache_dir)
            except OSError as cleanup_error:
                click.echo(
                    f"⚠️  Failed to delete netboot cache {cache_dir}: {cleanup_error}",
                    err=True,
                )
        if configuration is not None and not configuration.changed:
            configuration.cleanup()
        if shutdown_error is not None:
            raise shutdown_error
