import click
import os
import re
import time
import tempfile
import tarfile
import gzip
import subprocess
import shutil
from urllib.parse import urlparse
from typing import List
from sima_cli.utils.env import get_environment_type
from sima_cli.download import download_file_from_url
from sima_cli.utils.config_loader import load_resource_config
from sima_cli.update.remote import push_and_update_remote_board, get_remote_board_info, reboot_remote_board
from sima_cli.update.query import list_available_firmware_versions
from sima_cli.utils.env import is_sima_board, is_devkit_running_elxr
from sima_cli.update.elxr import update_elxr

if is_sima_board():
    from sima_cli.update import local
    get_local_board_info = local.get_local_board_info
    push_and_update_local_board = local.push_and_update_local_board
else:
    get_local_board_info = None
    push_and_update_local_board = None


def convert_flavor(flavor: str = 'headless'):
    return 'palette' if flavor == 'headless' else 'graphics'

def _resolve_firmware_url(version_or_url: str, board: str, internal: bool = False, flavor: str = 'headless', swtype: str = 'yocto') -> str:
    """
    Resolve the final firmware download URL based on board, version, and environment.

    Args:
        version_or_url (str): Either a version string (e.g. 1.6.0_master_B1611) or a full URL.
        board (str): Board type ('davinci' or 'modalix').
        internal (bool): Whether to use internal config for URL construction.
        flavor (str): firmware image flavor, can be headless or full.
        swtype (str): firmware image type, can be yocto or elxr

    Returns:
        str: Full download URL.
    """
    # If it's already a full URL, return it as-is
    if re.match(r'^https?://', version_or_url):
        return version_or_url

    # Load internal or public config
    cfg = load_resource_config()

    repo_cfg = cfg.get("internal" if internal else "public", {}).get("download")
    artifactory_cfg = cfg.get("internal" if internal else "public", {}).get("artifactory")
    base_url = artifactory_cfg.get("url", {})
    download_url = repo_cfg.get("download_url")
    url = f"{base_url}/{download_url}"
    if not url:
        raise RuntimeError("⚠️ 'url' is not defined in resource config.")

    # Davinci only supports headless images
    if board == 'davinci':
        flavor = 'headless'

    if swtype == 'yocto':
        image_file = 'release.tar.gz' if flavor == 'headless' else 'graphics.tar.gz'
        download_url = url.rstrip("/") + f"/soc-images/{board}/{version_or_url}/artifacts/{image_file}"
    elif swtype == 'elxr':
        image_file = f'{board}-tftp-boot-minimal.tar.gz' 
        download_url = url.rstrip("/") + f"/soc-images/elxr/{board}/{version_or_url}/artifacts/minimal/{image_file}"

    return download_url

def _confirm_flavor_switching(full_image: bool, flavor: str) -> str:
    """
    Check if the system is running a different flavor from the board and prompt user to confirm switching.
    
    Args:
        full_image (bool): Indicates if the current image is full
        flavor (str): The desired flavor of the image ('full' or 'headless')
    
    Returns:
        str: The flavor to use ('full' or 'headless')
    """
    # when the flavor is set to auto use the detected flavor instead
    if flavor == 'auto':
        flavor = 'full' if full_image else 'headless'
        click.echo(f"✅ Automatically detected the flavor of the running image: [{flavor}], Proceeding to update")

    if (full_image and flavor != 'full') or (not full_image and flavor == 'full'):
        click.echo(f"🔄 The current image running on the board has a different flavor from what you specified ({flavor}).")
        click.echo("Please choose an option:")
        choice = click.prompt(
            f"  a) Switch to the specified {flavor} flavor\n  b) Keep the existing flavor\n",
            type=click.Choice(['a', 'b'], case_sensitive=False),
            default='a',
            show_choices=False  # Choices are already shown in the prompt
        )
        
        if choice.lower() == 'b':
            flavor = 'full' if full_image else 'headless'
            click.echo(f"🔄 Keeping the existing flavor: {flavor}")
        else:
            click.echo(f"🔄 Switching to the specified flavor: {flavor}")
    
    return flavor

def _pick_from_available_versions(board: str, version_or_url: str, internal: bool, flavor: str, swtype: str) -> str:
    """
    Presents an interactive menu (with search) for selecting a firmware version.
    """

    if "http" in version_or_url:
        return version_or_url

    available_versions = list_available_firmware_versions(board, version_or_url, internal, flavor, swtype)

    try:
        if len(available_versions) > 1:
            click.echo("Multiple firmware versions found matching your input:")
            
            from InquirerPy import inquirer
            
            selected_version = inquirer.fuzzy(
                message="Select a version:",
                choices=available_versions,
                max_height="70%",  # scrollable
                instruction="(Use ↑↓ to navigate, / to search, Enter to select)"
            ).execute()

            if not selected_version:
                click.echo("No selection made. Exiting.", err=True)
                raise SystemExit(1)

            return selected_version

        elif len(available_versions) == 1:
            return available_versions[0]

        else:
            click.echo(
                f"No firmware versions found matching keyword '{version_or_url}' for board '{board}'.",
                err=True
            )
            raise SystemExit(1)
    except:
        click.echo("❌ Unable to determine available versions")
        exit(0)

def _sanitize_url_to_filename(url: str) -> str:
    """
    Convert a URL to a safe filename by replacing slashes and removing protocol.

    Args:
        url (str): Original URL.

    Returns:
        str: Safe, descriptive filename (e.g., soc-images__modalix__1.6.0__release.tar.gz)
    """
    parsed = urlparse(url)
    path = parsed.netloc + parsed.path
    safe_name = re.sub(r'[^\w.-]', '__', path)
    return safe_name


def _extract_required_files(tar_path: str, board: str, update_type: str = 'standard', flavor: str = 'headless') -> list:
    """
    Extract required files from a .tar.gz or .tar archive into the same folder
    and return the full paths to the extracted files (with subfolder if present).
    Netboot archives are extracted in full.
    Skips files that already exist. If a .wic.gz file is extracted, it will be decompressed,
    unless the decompressed .wic file already exists.

    Args:
        tar_path (str): Path to the downloaded or provided firmware archive.
        board (str): Board type ('davinci' or 'modalix').
        update_type (str): Update type ('standard' or 'bootimg').
        flavor (str): flavor of the firmware ('full' or 'headless').

    Returns:
        list: List of full paths to extracted files.
    """    
    extract_dir = os.path.dirname(tar_path)
    _flavor = convert_flavor(flavor)

    target_filenames = {
        "troot-upgrade-simaai-ev.swu",
        f"simaai-image-{_flavor}-upgrade-{board}.swu"
    }

    env_type, _os = get_environment_type()
    if env_type == "host" and _os == "linux":
        target_filenames.add("sima_pcie_host_pkg.sh")

    extract_all = update_type == 'netboot'

    if update_type == 'bootimg':
        target_filenames = {
            f"simaai-image-{_flavor}-{board}.wic.gz",
            f"simaai-image-{_flavor}-{board}.wic.bmap"
        }

    extracted_paths = []

    # Handle .img.gz downloaded directly (e.g., ELXR palette image)
    if tar_path.endswith(".img.gz"):
        extract_dir = os.path.dirname(tar_path)
        uncompressed_path = tar_path[:-3]  # Remove .gz → .img

        if os.path.exists(uncompressed_path):
            click.echo(f"⚠️  Skipping decompression: {uncompressed_path} already exists")
            return [tar_path, uncompressed_path]

        try:
            with gzip.open(tar_path, 'rb') as f_in:
                with open(uncompressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            click.echo(f"📦 Decompressed .img: {uncompressed_path}")
            return [tar_path, uncompressed_path]
        except Exception as e:
            click.echo(f"❌ Failed to decompress {tar_path}: {e}")
            return []

    try:
        try:
            tar = tarfile.open(tar_path, mode="r:gz")
        except tarfile.ReadError:
            tar = tarfile.open(tar_path, mode="r:")

        with tar:
            for member in tar.getmembers():
                base_name = os.path.basename(member.name)

                if member.isdir():
                    continue

                if extract_all or base_name in target_filenames or base_name.endswith(".img.gz"):
                    full_dest_path = os.path.join(extract_dir, member.name)

                    if os.path.exists(full_dest_path):
                        click.echo(f"⚠️  Skipping existing file: {full_dest_path}")
                        extracted_paths.append(full_dest_path)
                    else:
                        os.makedirs(os.path.dirname(full_dest_path), exist_ok=True)
                        try:
                            tar.extract(member, path=extract_dir, filter="data")
                        except TypeError:
                            tar.extract(member, path=extract_dir)
                        click.echo(f"✅ Extracted: {full_dest_path}")
                        extracted_paths.append(full_dest_path)

                    # Handle .wic.gz decompression
                    if full_dest_path.endswith(".wic.gz"):
                        uncompressed_path = full_dest_path[:-3]

                        if os.path.exists(uncompressed_path):
                            click.echo(f"⚠️  Skipping decompression: {uncompressed_path} already exists")
                            extracted_paths.append(uncompressed_path)
                            continue

                        try:
                            with gzip.open(full_dest_path, 'rb') as f_in:
                                with open(uncompressed_path, 'wb') as f_out:
                                    shutil.copyfileobj(f_in, f_out)
                            click.echo(f"📦 Decompressed: {uncompressed_path}")
                            extracted_paths.append(uncompressed_path)
                        except Exception as decomp_err:
                            click.echo(f"❌ Failed to decompress {full_dest_path}: {decomp_err}")

        if not extracted_paths:
            click.echo("⚠️  No matching files were found or extracted.")
            exit()

        return extracted_paths

    except Exception as e:
        click.echo(f"❌ Failed to extract files from archive: {e}")
        return []


    
def _download_image(version_or_url: str, board: str, internal: bool = False, update_type: str = 'standard', flavor: str = 'headless', swtype: str = 'yocto'):
    """
    Download or use a firmware image for the specified board and version or file path.

    Args:
        version_or_url (str): Version string, HTTP(S) URL, or local file path.
        board (str): Target board type ('davinci' or 'modalix').
        internal (bool): Whether to use internal Artifactory resources.
        flavor (str): Flavor of the image, can be headless or full, supported for Modalix only.
        swtype (str): Type of the image, can be yocto or elxr, supported for all H/W platforms. 

    Notes:
        - If a local file is provided, it skips downloading.
        - Downloads the firmware into the system's temporary directory otherwise.
        - Target file name is uniquely derived from the URL or preserved from local path.
    """
    try:
        # Case 1: Local file provided
        if os.path.exists(version_or_url) and os.path.isfile(version_or_url):
            click.echo(f"📁 Using local firmware file: {version_or_url}")
            filelist = _extract_required_files(version_or_url, board, update_type, flavor)
            
            # In the case of eLxr conversion, add the root file system image that is locally available.
            if update_type == "netboot" and swtype == "elxr":
                # Normalize the file path so bare filenames work properly
                abs_path = os.path.abspath(version_or_url)
                local_dir = os.path.dirname(abs_path)

                # Pattern: elxr-palette*arm64.img.gz
                palette_candidates = [
                    os.path.join(local_dir, fname)
                    for fname in os.listdir(local_dir)
                    if fname.startswith("elxr-palette") and fname.endswith("arm64.img.gz")
                ]

                if palette_candidates:
                    for path in palette_candidates:
                        click.echo(f"📁 Found local eMMC root file system file: {path}")
                        filelist.append(path)
                else:
                    click.echo(
                        f"⚠️ No eMMC root file system found in {local_dir} matching "
                        "'elxr-palette*arm64.img.gz'"
                    )
                    exit(-1)

            return filelist
        
        # Case 2: Treat as custom full URL
        if version_or_url.startswith("http://") or version_or_url.startswith("https://"):
            image_url = version_or_url
        else:
            # Case 3: Resolve standard version string (Artifactory/AWS)
            image_url = _resolve_firmware_url(version_or_url, board, internal, flavor=flavor, swtype=swtype)

        # Determine platform-safe temp directory
        temp_dir = tempfile.gettempdir()
        os.makedirs(temp_dir, exist_ok=True)

        # Build safe filename based on the URL
        safe_filename = _sanitize_url_to_filename(image_url)
        dest_path = os.path.join(temp_dir, safe_filename)

        # Download the file
        click.echo(f"📦 Downloading from {image_url}")
        firmware_path = download_file_from_url(image_url, dest_path, internal=internal)
        extracted_files = _extract_required_files(firmware_path, board, update_type, flavor)

        # If internal, netboot and elxr, we need to download some additional files to prepare for eMMC flash.
        if update_type == "netboot" and swtype == "elxr":
            base_url = os.path.dirname(image_url)
            base_version = version_or_url.split('_')[0]

            if internal:
                extra_files = [f"../palette/elxr-palette-{board}-{base_version}-arm64.img.gz"]
            else:
                match = re.search(r"SDK(\d+\.\d+\.\d+)", image_url)
                version = match.group(1)
                extra_files = [f"elxr-palette-{board}-{version}-arm64.img.gz"]

            for fname in extra_files:
                extra_url = f"{base_url}/{fname}"
                try:
                    click.echo(f"📥 Downloading extra file: {fname} from {extra_url} saving into {dest_path}")
                    netboot_file_path = download_file_from_url(extra_url, dest_path, internal=internal)
                    print(netboot_file_path)
                    extracted_files.extend([netboot_file_path])
                    click.echo(f"✅ Saved {fname} to {dest_path}")
                except Exception as e:
                    click.echo(f"⚠️ Failed to download {fname}: {e}")

        click.echo(f"📦 Firmware downloaded to: {firmware_path}")
        return extracted_files

    except Exception as e:
        click.echo(f"❌ Host update failed: {e}")
        exit(0)

def _update_host(script_path: str, board: str, boardip: str, passwd: str):
    """
    Perform PCIe host update by running the sima_pcie_host_pkg.sh script.

    Args:
        script_path (str): Full path of the extracted host package script
        board (str): Board type (e.g., 'davinci' or 'modalix').
    """
    try:
        if not script_path or not os.path.isfile(script_path):
            click.echo("❌ sima_pcie_host_pkg.sh not found in extracted files.")
            return

        click.echo(f"🚀 Running PCIe host install script: {script_path}")

        # Start subprocess with live output streaming
        process = subprocess.Popen(
            ["sudo", "bash", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # Stream output line by line
        for line in process.stdout:
            click.echo(f"📄 {line.strip()}")

        process.stdout.close()
        returncode = process.wait()

        if returncode != 0:
            click.echo(f"❌ Host driver install script exited with code {returncode}.")
            return

        click.echo("✅ PCIe host update completed successfully.")

        # Ask for reboot
        if click.confirm("🔄 Do you want to reboot your system now?", default=True):
            click.echo("♻️ Rebooting system...")
            # This workaround reboots the PCIe card before we reboot the system
            reboot_remote_board(boardip, passwd)
            time.sleep(2)
            subprocess.run(["sudo", "reboot"])
        else:
            click.echo("🕒 Reboot skipped. Please powercycle to apply changes.")

    except Exception as e:
        click.echo(f"❌ Host update failed: {e}")
        exit(0)


def _update_sdk(version_or_url: str, board: str):
    click.echo(f"⚙️  Simulated SDK firmware update logic for board '{board}' (not implemented).")
    # TODO: Implement update via SDK-based communication or tools

def _update_board(extracted_paths: List[str], board: str, passwd: str, flavor: str, target_ver: str, troot_only: bool):
    """
    Perform local firmware update using extracted files.

    Args:
        extracted_paths (List[str]): Paths to the extracted .swu files.
        board (str): Board type expected (e.g. 'davinci', 'modalix').
        flavor (str): headless or full.
        troot_only (bool): whether to only update tRoot.
    """
    click.echo(f"⚙️  Starting local firmware update for board '{board}'...")

    # Locate the needed files
    _flavor = 'palette' if flavor == 'headless' else 'graphics'
    troot_path = next((p for p in extracted_paths if "troot-upgrade" in os.path.basename(p)), None)
    palette_path = next((p for p in extracted_paths if f"{_flavor}-upgrade-{board}" in os.path.basename(p)), None)

    if not troot_path:
        click.echo("⚠️  tRoot update skipped because the requested image doesn't contain troot image.")

    if not palette_path:
        click.echo(f"❌ Required firmware files not found in extracted paths. (_flavor = {_flavor}, board = {board})")
        return

    # Optionally verify the board type
    board_type, board_version, _, full_image, _ = get_local_board_info()
    if board_type.lower() != board.lower():
        click.echo(f"❌ Board mismatch: expected '{board}', but found '{board_type}'")
        return

    # flavor switching is only supported on 1.7.
    if '1.7' in board_version and '1.7' in target_ver:
        flavor = _confirm_flavor_switching(full_image=full_image, flavor=flavor)

    click.echo("✅ Board verified. Starting update...")
    push_and_update_local_board(troot_path, palette_path, passwd, flavor, troot_only)

def _update_remote(extracted_paths: List[str], ip: str, board: str, passwd: str, reboot_and_wait: bool = True, flavor: str = 'headless', troot_only: bool = False):
    """
    Perform remote firmware update to the specified board via SSH.

    Args:
        extracted_paths (List[str]): Paths to the extracted .swu files.
        ip (str): IP of the remote board.
        board (str): Expected board type ('davinci' or 'modalix').
        passwd (str): password to access the board, if it's not default
        flavor (str): flavor of the firmware - headless or full
        troot_only (bool): only update tRoot and skip the root file system
    """
    click.echo(f"⚙️  Starting remote update on '{ip}' for board type '{board}'...")

    # Locate files
    _flavor = convert_flavor(flavor)
    troot_path = next((p for p in extracted_paths if "troot-upgrade" in os.path.basename(p)), None)
    palette_path = next((p for p in extracted_paths if f"{_flavor}-upgrade-{board}" in os.path.basename(p)), None)
    script_path = next((p for p in extracted_paths if p.endswith("sima_pcie_host_pkg.sh")), None)

    if not troot_path:
        click.echo("⚠️  Required troot firmware files not found in extracted paths, skipping tRoot update...")
    if not palette_path:
        click.echo("❌ Required o/s files not found in extracted paths.")
        return

    # Get remote board info
    click.echo("🔍 Checking remote board type and version...")
    remote_board, remote_version, _, _, _ = get_remote_board_info(ip, passwd)

    if not remote_board:
        click.echo("❌ Could not determine remote board type.")
        return

    click.echo(f"🔍 Remote board: {remote_board} | Version: {remote_version}")

    if remote_board.lower() != board.lower():
        click.echo(f"❌ Board mismatch: expected '{board}', but got '{remote_board}' on device.")
        return

    # Proceed with update
    click.echo(f"✅ Board type verified. Proceeding with firmware update: troot : {troot_path}, os: {palette_path}...")
    push_and_update_remote_board(ip, troot_path, palette_path, passwd=passwd, reboot_and_wait=reboot_and_wait, flavor=flavor, troot_only=troot_only)

    return script_path

def download_image(version_or_url: str, board: str, swtype: str, internal: bool = False, update_type: str = 'standard', flavor: str = 'headless'):
    """
    Download and extract a firmware image for a specified board.

    Args:
        version_or_url (str): Either a version string (e.g., "1.6.0") or a direct URL or local file path to the image.
        board (str): The board type (e.g., "mlsoc", "modalix").
        swtype (str): The software type (default to 'yocto', possible values: `yocto`, `elxr`): not supported for now
        internal (bool): Whether to use internal download paths (e.g., Artifactory).
        update_type (str): Whether this is standard update or writing boot image.
        flavor (str): Flavor of the image, can be headless or full.

    Returns:
        List[str]: Paths to the extracted image files.
    """
    
    if 'http' not in version_or_url and not os.path.exists(version_or_url): 
        version_or_url = _pick_from_available_versions(board, version_or_url, internal, flavor, swtype)

    extracted_paths = _download_image(version_or_url, board, internal, update_type, flavor=flavor, swtype=swtype) 
    return extracted_paths


def perform_update(version_or_url: str, ip: str = None, internal: bool = False, passwd: str = "edgeai", auto_confirm: bool = False, flavor: str = 'auto', troot_only: bool = False):
    r"""
    Update the system based on environment and input.

    - On PCIe host: updates host driver and/or downloads firmware.
    - On SiMa board: applies firmware update.
    - In SDK: allows simulated or direct board update.
    - Unknown env: requires --ip to specify remote device.

    Args:
        version_or_url (str): Version string or direct URL.
        ip (str): Optional remote target IP.
        internal (bool): If True, enable internal-only behaviors (e.g., Artifactory access).
        passwd (str): Password for the board user (default: "edgeai").
        auto_confirm (bool): If True, auto-confirm firmware update without prompting.
        flavor (str): headless or full, or auto (detect the running image flavor and use it)
        troot_only (bool): whether to only update tRoot or not. Default is false and we always update both tRoot and root file system
    """
    try:
        board = ''
        env_type, env_subtype = get_environment_type()
        click.echo(f"🔄 Running update for environment: {env_type} ({env_subtype})")
        click.echo(f"🔧 Requested version or URL: {version_or_url}, with flavor {flavor}")

        if env_type == 'board':
            board, version, devkit_name, full_image, fwtype = get_local_board_info()
        else:
            if ip:
                board, version, devkit_name, full_image, fwtype = get_remote_board_info(ip, passwd)
            else:
                click.secho('❌ Trying to run update command from outside the board, you must specify the IP address of the device using the --ip argument', fg='red')
                exit(0)

        # Only when converting 1.7 full <-> headless we ask user to confirm
        # In all other scenarios we'd just update to headless and ignore the current flavor on the devkit, 
        # this enforces tRoot update and minimizes the chance of incompatible tRoots

        if version_or_url:
            if '1.7' in version_or_url and '1.7' in version:
                flavor = _confirm_flavor_switching(full_image=full_image, flavor=flavor)
            else:
                flavor = 'headless'

        if board in ['davinci', 'modalix']:
            click.echo(f"🔧 Target board: {board}, specific type: [{devkit_name}], board currently running: {version}, full_image: {full_image}, firmware: {fwtype}")
            
            if flavor == 'full' and 'modalix' not in devkit_name:
                click.echo(f"❌ You've requested updating {devkit_name} to full image, this is only supported for the Modalix DevKit")
                return

            if is_devkit_running_elxr():
                return update_elxr(version_or_url, internal=internal)
            
            elif fwtype.lower() == 'elxr':
                click.echo(
                    click.style(
                        "⚠️  ELXR does not support remote update.\n"
                        "   Please connect the DevKit to the Internet\n"
                        "   and run:  ", fg="yellow"
                    ) + click.style("sima-cli update", fg="cyan", bold=True)
                )
                return False                
                
            # Davinci only supports headless build, so ignore the full flavor
            if board == 'davinci' and flavor != 'headless':
                click.echo(f"MLSoC only supports headless image, ignoring {flavor} setting")
                flavor = 'headless'
            
            if 'http' not in version_or_url and not os.path.exists(version_or_url): 
                version_or_url = _pick_from_available_versions(board, version_or_url, internal, flavor=flavor, swtype='yocto')

            extracted_paths = _download_image(version_or_url, board, internal, flavor=flavor)

            if not auto_confirm:
                click.confirm(
                    "⚠️  Firmware image is ready. Do you want to proceed with the update?",
                    abort=True
                )

            click.echo("⚠️  DO NOT INTERRUPT THE UPDATE PROCESS...")

            if len(extracted_paths) > 0:
                if env_type == "host" and env_subtype == 'linux':
                    # Always update the remote device first then update the host driver, otherwise the host would 
                    # not be able to connect to the board
                    script_path = _update_remote(extracted_paths, ip, board, passwd, reboot_and_wait=False, flavor=flavor, troot_only=troot_only)
                    click.echo("👉 sima-cli detected you are updating the board on a Linux host...")        
                    if click.confirm("👉  Do you want to update the host PCIe driver now? If you do not intend to use a Sima PCIe card ever on this machine, enter N", default=False):
                        click.echo("👉  Updating PCIe host driver and downloading firmware...")
                        _update_host(script_path, board, ip, passwd)
                    else:
                        click.echo("⚠️  Skipping Linux host driver update.")
                elif env_type == "board":
                    _update_board(extracted_paths, board, passwd, flavor=flavor, target_ver=version_or_url, troot_only=troot_only)
                elif env_type == "sdk":
                    click.echo("👉 Updating firmware from within the Palette SDK...: Not implemented yet")
                elif ip:
                    click.echo(f"👉 Updating firmware on remote board at {ip}...")
                    _update_remote(extracted_paths, ip, board, passwd, reboot_and_wait=True, flavor=flavor, troot_only=troot_only)
                else:
                    click.echo("❌ Unknown environment. Use --ip to specify target device.")
        else:
            click.echo("❌ Unable to retrieve target board information")

    except Exception as e:
        click.echo(f"❌ Update failed: {e}")
