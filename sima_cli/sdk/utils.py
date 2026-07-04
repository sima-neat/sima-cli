# utils.py
from typing import Dict, List, Set
import hashlib
import locale
import os
import sys
import time
import getpass
import shutil
import socket
import subprocess
import json
import re
import shlex
import platform
import shutil
import tempfile
from collections import defaultdict
from rich.console import Console
from rich.panel import Panel

from sima_cli.sdk.config import (
    IMAGE_NAMES,
    IMAGE_CONFIG,
    BASELINE_IMAGE,
    IMAGE_ALIASES,
)


FILTER_KEYWORDS = ["elxr", "yocto", "mpk", "modelsdk", "neat-sdk", "sima-neat/sdk", "sima-neat/elxr"]
SDK_KEYWORD_ALIASES = {
    "model": "modelsdk",
    "mpk": "mpk_cli_toolset",
}
console = Console()
SIMA_CLI_AUTH_CACHE_FILES = (
    ".tokens.json",
    ".sima-cli-cookies.txt",
    ".sima-cli-csrf.json",
)
OPENVSCODE_SERVER_BIN = "/opt/openvscode-server/bin/openvscode-server"
OPENVSCODE_LEGACY_EXTENSIONS_DIR = "/opt/openvscode-server/extensions"
CODEX_EXTENSION_DEFAULT_ID = "openai.chatgpt"
CODEX_EXTENSION_ID_ENV = "SIMA_CLI_CODEX_EXTENSION_ID"
CODEX_EXTENSION_INSTALL_ENV = "SIMA_CLI_INSTALL_CODEX_EXTENSION"

def _devcontainer_metadata_label(remote_user: str, workspace_folder: str = "/workspace") -> str:
    """
    VS Code Dev Containers reads this label when attaching to an existing
    container. Neat SDK containers keep Docker's default user as root so
    supervised services can start, but editor sessions should use the mapped
    host user created by configure_container().
    """
    return json.dumps(
        [
            {
                "remoteUser": remote_user,
                "workspaceFolder": workspace_folder,
            }
        ],
        separators=(",", ":"),
    )

def check_os() -> str:
    """Detect and return the current operating system."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin"):
        return "windows"
    return "not_supported"


def _decode_subprocess_output(output) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output

    encoding = locale.getpreferredencoding(False) or "utf-8"
    try:
        return output.decode(encoding, errors="replace")
    except LookupError:
        return output.decode("utf-8", errors="replace")


def is_docker_user_mapping_error(output: str) -> bool:
    output = (output or "").lower()
    return any(
        pattern in output
        for pattern in (
            "unable to find user",
            "no matching entries in passwd file",
            "user lookup failed",
        )
    )


def container_user_mapping_unavailable(container_name: str, exec_user: str) -> bool:
    result = subprocess.run(
        ["docker", "exec", "-u", exec_user, container_name, "true"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return False
    return is_docker_user_mapping_error(f"{result.stdout}\n{result.stderr}")


def check_and_start_docker(platform_os):
    """
    Check if Docker daemon is running and start it if necessary.

    - Linux: optionally uses `sudo systemctl restart docker.service`
    - macOS: uses `open -a Docker` (may require sudo if permission denied)
    - Windows: launches Docker Desktop executable
    """

    # Step 1: Check if Docker is already running
    if is_docker_running():
        print("✅ Docker daemon is running.")
        return

    # Step 2: Docker not running → ask user if they want to start it
    response = input("⚠️  Docker daemon is not running. Do you want to start it? [Y/n]: ").strip().lower()
    if response in {"n", "no"}:
        print("❌ Please start Docker manually and re-run the script.")
        print_manual_start_instructions(platform_os)
        sys.exit(1)

    # Step 3: Ask if sudo is allowed (for Linux/macOS)
    use_sudo = False
    if platform_os in {"linux", "macos"}:
        sudo_resp = input("🔒 Starting Docker may require sudo privileges. Grant sudo access? [Y/n]: ").strip().lower()
        use_sudo = sudo_resp not in {"n", "no"}  # Default is YES

    print("⏳ Attempting to start Docker...")

    # Step 4: Platform-specific handling
    if platform_os == "linux":
        docker_start_cmd = ["systemctl", "restart", "docker.service"]
        if use_sudo:
            docker_start_cmd.insert(0, "sudo")

        for attempt in range(1, 4):
            result = subprocess.run(docker_start_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0 and is_docker_running():
                print("✅ Docker daemon started successfully.")
                return
            print(f"Retrying Docker start... Attempt {attempt}/3")
            time.sleep(5)

        print("❌ Failed to start Docker service after 3 attempts.")
        print_manual_start_instructions(platform_os)
        sys.exit(1)

    elif platform_os == "windows":
        docker_exe = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
        if not os.path.exists(docker_exe):
            print(f"❌ Docker Desktop executable not found at:\n   {docker_exe}")
            print("Please install Docker Desktop and try again.")
            sys.exit(1)

        print("ℹ️ Launching Docker Desktop on Windows...")
        subprocess.Popen(["cmd.exe", "/c", "start", "", docker_exe])
        print("⏳ Waiting for Docker Desktop to initialize...")

        for i in range(6):
            time.sleep(10)
            if is_docker_running():
                print("✅ Docker daemon started successfully.")
                return
            print(f"⌛ Checking again ({i+1}/6)...")

        print("❌ Docker daemon did not start after waiting.")
        print_manual_start_instructions(platform_os)
        sys.exit(1)

    elif platform_os == "macos":
        docker_app_path = "/Applications/Docker.app"
        if not os.path.exists(docker_app_path):
            print(f"❌ Docker Desktop not found at {docker_app_path}")
            print("Please install Docker Desktop for macOS and try again.")
            sys.exit(1)

        start_cmd = ["open", "-a", "Docker"]
        if use_sudo:
            start_cmd.insert(0, "sudo")

        print("ℹ️ Starting Docker Desktop for macOS...")
        subprocess.Popen(start_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for i in range(10):
            time.sleep(10)
            if is_docker_running():
                print("✅ Docker daemon started successfully.")
                return
            print(f"⌛ Checking again ({i+1}/10)...")

        print("❌ Docker daemon did not start after waiting.")
        print_manual_start_instructions(platform_os)
        sys.exit(1)

    else:
        print(f"❌ Unsupported platform: {platform_os}")
        sys.exit(1)


def print_manual_start_instructions(platform_os):
    """Print platform-specific manual instructions for starting Docker."""
    print("\n🧭 Manual Start Instructions:")
    if platform_os == "linux":
        print("   ➤ Run:  sudo systemctl start docker.service")
        print("   ➤ Verify: docker info")
    elif platform_os == "macos":
        print("   ➤ Open Docker Desktop manually from Applications folder.")
        print("   ➤ Or run:  open -a Docker")
    elif platform_os == "windows":
        print("   ➤ Launch Docker Desktop from the Start menu.")
    print("   Then re-run this installer once Docker is active.\n")
        
def docker_login_jfrog(host: str, user: str, password: str) -> None:
    print(f"\n🔑 Logging in to JFrog Docker registry: {host}")
    # Use --password-stdin to avoid exposing the password in the process list
    p = subprocess.Popen(
        ["docker", "login", host, "-u", user, "--password-stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = p.communicate(input=password or "")
    if p.returncode != 0:
        print("❌ Docker login failed.")
        if err:
            print(err.strip())
        sys.exit(1)
    print("✅ Docker login successful")

def baseline_is_present(
    installed_refs: Set[str],
    version: str,
    images_user_will_remove: Set[str],
) -> bool:
    # True if mpk_cli_toolset:<version> exists AND the user did not choose to remove it
    installed = any(ref.endswith(f"{BASELINE_IMAGE}:{version}") for ref in installed_refs)
    not_removed = BASELINE_IMAGE not in images_user_will_remove
    return installed and not_removed

def prompt_multi_select(baseline_present: bool = False) -> List[str]:
    """
    Prompts the user to select one or more items by number or name (comma-separated).

    Rules:
      - If '0' or 'All' is selected, all items are included automatically.
      - Enforce baseline (mpk_cli_toolset) unless `baseline_present` is True.
      - Displays detailed information (name, description, sizes) for each container.
    """
    while True:
        print("\nSelect one or more options (numbers or names, comma-separated):\n")
        # Calculate total sizes for "All"
        total_size = sum(cfg.get("size", 0) for cfg in IMAGE_CONFIG.values())
        total_pull_space = sum(cfg.get("pull_space", 0) for cfg in IMAGE_CONFIG.values())

        print("0. All")
        print(
            f"    📦 Description      : Select all available components\n"
            f"    💾 Final image size : {total_size} GB (runtime requirement)\n"
            f"    📂 Pull space need  : {total_pull_space} GB (temporary during pull)\n"
        )

        # Display image information dynamically from IMAGE_CONFIG
        for idx, (img, cfg) in enumerate(IMAGE_CONFIG.items(), start=1):
            print(
                f"{idx}. {cfg['display']}\n"
                f"    📦 Description      : {cfg.get('description', 'N/A')}\n"
                f"    💾 Final image size : {cfg.get('size', 'N/A')} GB (runtime requirement)\n"
                f"    📂 Pull space need  : {cfg.get('pull_space', 'N/A')} GB (temporary during pull)\n"
            )

        raw = input(
            "\nYour selection (e.g., 1,3 or yocto,ModelSDK, or 0 for All):\n"
            "⚠️  **Important Note:**\n"
            "   1. If there are any existing containers associated with your selection, "
            "they will be stopped, removed, and relaunched automatically.\n"
            "   2. If you need to save any data from an existing container, "
            "please save it **now** and run this setup again.\n"
            "   3. If you need to exit the setup at any point, press **CTRL+C**.\n"
            "Enter your choice: "
        ).strip()

        if not raw:
            retry = input("⚠️  No selection provided. Try again? (Y/n): ").strip().lower()
            if retry in {"n", "no"}:
                print("Exiting without making any selections.")
                sys.exit(0)
            continue

        # ---- Handle "All" selection early ----
        if raw.lower() in {"0", "all"}:
            print("\nℹ️  'All' selected → All components will be included.")
            return list(IMAGE_CONFIG.keys())

        # ---- Process comma-separated custom selections ----
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        selected_images: List[str] = []
        invalid_entries = []

        # Build lookups
        display_to_internal = {cfg["display"].lower(): img for img, cfg in IMAGE_CONFIG.items()}
        name_lookup = {img.lower(): img for img in IMAGE_CONFIG.keys()}

        for part in parts:
            if part.isdigit():
                idx = int(part)
                if 1 <= idx <= len(IMAGE_CONFIG):
                    selected_images.append(list(IMAGE_CONFIG.keys())[idx - 1])
                else:
                    invalid_entries.append(part)
            else:
                # Try display name match
                disp_match = display_to_internal.get(part.lower())
                if disp_match:
                    selected_images.append(disp_match)
                else:
                    # Try internal name match
                    name_match = name_lookup.get(part.lower())
                    if name_match:
                        selected_images.append(name_match)
                    else:
                        invalid_entries.append(part)

        if invalid_entries:
            print(f"\n⚠️  Invalid selection(s): {', '.join(invalid_entries)}")
            retry = input("Do you want to retry? (Y/n): ").strip().lower()
            if retry in {"n", "no"}:
                print("Exiting due to invalid selections.")
                sys.exit(0)
            continue

        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for img in selected_images:
            if img not in seen:
                seen.add(img)
                deduped.append(img)

        # ---- Baseline enforcement ----
        baseline_image = next((k for k, v in IMAGE_CONFIG.items() if v.get("baseline")), None)
        if not baseline_present and baseline_image and baseline_image not in deduped:
            baseline_display = IMAGE_CONFIG[baseline_image]["display"]
            print(f"\n⚠️  You did not select the required baseline component: '{baseline_display}'.")
            add_baseline = input(f"Would you like to add '{baseline_display}' now? (Y/n): ").strip().lower()
            if add_baseline in {"y", "yes", ""}:
                deduped.insert(0, baseline_image)
            else:
                confirm_exit = input("Do you want to exit instead? (Y/n): ").strip().lower()
                if confirm_exit in {"y", "yes", ""}:
                    print("Exiting without making selections.")
                    sys.exit(0)
                else:
                    print("Let's try the selection again.")
                    continue

        return deduped

def run_command(cmd, capture_output=False, fatal=True):
    """Run a shell command and return output if requested."""
    try:
        if capture_output:
            return subprocess.check_output(cmd, text=True).strip()
        else:
            subprocess.run(cmd, check=True)
            return True if not fatal else None
    except subprocess.CalledProcessError as e:
        prefix = "❌ Command failed" if fatal else "⚠️  Optional command failed"
        print(f"{prefix}: {' '.join(cmd)}")
        if e.stderr:
            print(f"Error: {e.stderr.strip()}")
        if fatal:
            sys.exit(1)
        return False


### Dynamic port allocation
def is_port_in_use(port):
    platform_os = check_os()
    if platform_os in ["linux", "macos"]:
        # Using lsof to check if the port is being used
        result = subprocess.run(
            ["lsof", "-i", f":{port}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        return result.returncode == 0  # If return code is 0, the port is in use
    elif platform_os == "windows":
        # Using netstat to check if the port is being used
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.stdout:
            lines = _decode_subprocess_output(result.stdout).splitlines()
            for line in lines:
                if str(port) in line:
                    print(f"Port {port} is in use. Line: {line}")
                    return True
        return False
    else:
        print(f"Unsupported OS: {platform_os}")
        return False

def find_available_ports(count=1, start_port=49152, end_port=65535):
    """
    Find a given number of available ports within the specified range.

    Args:
        count (int): Number of free ports to return.
        start_port (int): Starting port number to check.
        end_port (int): Ending port number to check.

    Returns:
        list[int]: A list of free ports.

    Raises:
        SystemExit: If not enough free ports are found.
    """
    free_ports = []

    for port in range(start_port, end_port + 1):
        if not is_port_in_use(port):
            try:
                # Try to bind to confirm the port is truly free
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
                    free_ports.append(port)
                    if len(free_ports) == count:
                        return free_ports
            except OSError:
                continue

    print(f"❌ Could not find {count} free ports in the range {start_port}-{end_port}.")
    sys.exit(1)

def check_and_install_netstat():
    platform_os = check_os()
    
    # Check if netstat is available using shutil.which
    if shutil.which("netstat") is not None:
        print("netstat is already installed.")
        return True
    else:
        print("netstat is not installed. Attempting to install...")
        
        try:
            if platform_os == "linux":
                # For most Linux distributions, netstat is in the net-tools package
                print("Installing net-tools package...")
                subprocess.run(["sudo", "apt-get", "update"], check=True)
                subprocess.run(["sudo", "apt-get", "install", "-y", "net-tools"], check=True)
            elif platform_os == "macos":
                # For macOS, check if Homebrew is installed first
                try:
                    subprocess.run(["brew", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                except (subprocess.SubprocessError, FileNotFoundError):
                    print("Homebrew is not installed. Please install Homebrew first: https://brew.sh/")
                    return False
                
                print("Installing net-tools via Homebrew...")
                subprocess.run(["brew", "install", "net-tools"], check=True)
            elif platform_os == "windows":
                print("netstat should be pre-installed on Windows. Please check your Windows installation.")
                return False
            
            print("netstat has been installed successfully.")
            return True
        except subprocess.SubprocessError as e:
            print(f"Failed to install netstat: {e}")
            return False

def get_installed_images():
    """Get all SDK-related images installed locally."""
    output = run_command(["docker", "images", "--format", "{{.Repository}}"], capture_output=True)
    images = []
    for line in output.splitlines():
        image_name = extract_short_name(line)
        if image_name in IMAGE_NAMES:
            images.append(image_name)
    return sorted(set(images))

def get_container_status():
    """
    Return dict of container_name -> status, filtered by IMAGE_NAMES.
    Matches are partial (substring) matches instead of exact matches.
    """
    output = run_command(
        ["docker", "ps", "-a", "--format", "{{json .}}"],
        capture_output=True
    )
    containers = {}
    for line in output.splitlines():
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue

        name = info.get("Names") or info.get("Name") or info.get("name") or ""
        image = info.get("Image") or info.get("image") or ""
        status = info.get("Status") or info.get("status") or ""
        if _container_sdk_key(name, image):
            containers[name] = status.lower()
    return containers


def get_running_containers():
    """
    Return list of currently running container names, reusing get_container_status().
    """
    all_containers = get_container_status()
    running = [
        cname for cname, status in all_containers.items()
        if "up" in status  # "up" substring covers "up x minutes/hours/days"
    ]
    return running


def get_workspace(yes_to_all=False, noninteractive=False, workspace_override=None):
    """
    Determine the workspace:
    - If at least one container is running, read from ~/.simaai/.mount
    - If a workspace is found, confirm with the user (unless yes_to_all=True)
    - Otherwise, prompt the user for a path
    """
    home = os.path.expanduser("~")
    simaai_dir = os.path.join(home, ".simaai")
    mount_file = os.path.join(simaai_dir, ".mount")

    if workspace_override:
        workspace = os.path.realpath(os.path.expanduser(workspace_override))
        if not os.path.isdir(workspace):
            os.makedirs(workspace, exist_ok=True)
            print(f"📂 Created workspace: {workspace}")
        print(f"✅ Workspace set to: {workspace}")
        os.makedirs(simaai_dir, exist_ok=True)
        with open(mount_file, "w") as f:
            f.write(workspace)
        return workspace

    running_containers = get_running_containers()

    # Case 1: At least one container running → read workspace
    if running_containers:
        if os.path.isfile(mount_file):
            with open(mount_file) as f:
                workspace = f.read().strip()

            if yes_to_all or noninteractive:
                print(f"\n📂 Detected running container. Using workspace: {workspace}")
                return workspace

            # Ask user for confirmation
            print(f"\n📂 Detected running container. Found workspace: {workspace}")
            confirm = input("Use this workspace? [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                print(f"✅ Using detected workspace: {workspace}")
                return workspace
            else:
                print("➡️  Skipping detected workspace. Proceeding to collect new path...")

        else:
            print("⚠️  Running SDK containers detected but no saved workspace mount was found.")
            print("➡️  Recreating workspace mount state before continuing.")
    
    # Case 2: No container running → ask user
    default_workspace = os.path.join(home, "workspace")
    if os.path.isfile(mount_file):
        with open(mount_file) as f:
            default_workspace = f.read().strip()

    if noninteractive:
        workspace = os.path.realpath(os.path.expanduser(default_workspace))
        if not os.path.isdir(workspace):
            os.makedirs(workspace, exist_ok=True)
            print(f"📂 Created workspace: {workspace}")
        print(f"✅ Workspace set to: {workspace}")
        os.makedirs(simaai_dir, exist_ok=True)
        with open(mount_file, "w") as f:
            f.write(workspace)
        return workspace

    while True:
        user_input = input(f"Enter workspace directory [{default_workspace}]: ").strip()
        workspace = user_input or default_workspace
        workspace = os.path.realpath(os.path.expanduser(workspace))

        if os.path.isdir(workspace):
            print(f"✅ Workspace set to: {workspace}")
            break
        else:
            print(f"❌ Directory '{workspace}' does not exist.")
            create_choice = input("Create it automatically? (Y/n): ").strip().lower()
            if create_choice in {"", "y", "yes"}:
                os.makedirs(workspace, exist_ok=True)
                print(f"📂 Created: {workspace}")
                break
            retry = input("Retry entering location? (Y/n): ").strip().lower()
            if retry in {"n", "no"}:
                sys.exit("❌ Exiting as per user request.")

    # Save the workspace for future use
    os.makedirs(simaai_dir, exist_ok=True)
    with open(mount_file, "w") as f:
        f.write(workspace)
    return workspace


def ensure_sima_cli_installed(sdk_container_name: str, login_name: str):
    """
    Ensure sima-cli is installed for the default user inside the container.
    """
    check_cmd = [
        "docker",
        "exec",
        "-u",
        login_name,
        sdk_container_name,
        "bash",
        "-lc",
        "command -v sima-cli >/dev/null 2>&1",
    ]
    installed = (
        subprocess.run(
            check_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )

    if installed:
        print(f"✅ sima-cli already installed for user '{login_name}' in '{sdk_container_name}'.")
        return

    print(f"ℹ️  sima-cli not found for user '{login_name}' in '{sdk_container_name}'. Installing...")
    run_command(
        [
            "docker",
            "exec",
            "-u",
            login_name,
            sdk_container_name,
            "bash",
            "-lc",
            "curl https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh | bash",
        ]
    )
    print(f"✅ sima-cli installed for user '{login_name}' in '{sdk_container_name}'.")


def _get_container_image_ref(container_name: str) -> str:
    try:
        return subprocess.check_output(
            ["docker", "inspect", "-f", "{{.Config.Image}}", container_name],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def is_snap_docker_cli() -> bool:
    docker_bin = shutil.which("docker") or ""
    resolved = os.path.realpath(docker_bin) if docker_bin else ""
    return (
        "/snap/" in docker_bin
        or "/snap/" in resolved
        or resolved.endswith("/usr/bin/snap")
    )


def _copy_sima_cli_auth_cache_to_container(sdk_container_name: str, login_name: str, uid: int, gid: int) -> None:
    image_ref = _get_container_image_ref(sdk_container_name)
    if not image_ref or not is_neat_sdk_image(image_ref):
        return

    host_sima_cli_dir = os.path.realpath(os.path.expanduser("~/.sima-cli"))
    existing_files = [
        filename
        for filename in SIMA_CLI_AUTH_CACHE_FILES
        if os.path.isfile(os.path.join(host_sima_cli_dir, filename))
    ]
    if not existing_files:
        print("ℹ️  No host sima-cli auth cache files found to copy into Neat SDK container.")
        return

    container_auth_dir = f"/home/{login_name}/.sima-cli"
    if not run_command([
        "docker",
        "exec",
        "-u",
        "root",
        sdk_container_name,
        "mkdir",
        "-p",
        container_auth_dir,
    ], fatal=False):
        print("⚠️  Could not create sima-cli auth cache directory in Neat SDK container; continuing setup.")
        return
    if not run_command([
        "docker",
        "exec",
        "-u",
        "root",
        sdk_container_name,
        "chown",
        f"{uid}:{gid}",
        container_auth_dir,
    ], fatal=False):
        print("⚠️  Could not update ownership for sima-cli auth cache directory; continuing setup.")

    copied = []
    with _docker_cp_staging_dir() as tmpdir:
        for filename in existing_files:
            host_path = os.path.join(host_sima_cli_dir, filename)
            staged_path = os.path.join(tmpdir, filename)
            container_path = f"{container_auth_dir}/{filename}"
            cleanup_target = [
                "docker",
                "exec",
                "-u",
                "root",
                sdk_container_name,
                "rm",
                "-f",
                container_path,
            ]

            try:
                shutil.copy2(host_path, staged_path)
                os.chmod(staged_path, 0o600)
            except OSError as e:
                print(f"⚠️  Could not stage host sima-cli auth cache file '{filename}': {e}; continuing setup.")
                continue

            run_command(cleanup_target, fatal=False)
            if not run_command(["docker", "cp", staged_path, f"{sdk_container_name}:{container_path}"], fatal=False):
                run_command(cleanup_target, fatal=False)
                print(f"⚠️  Could not copy host sima-cli auth cache file '{filename}' into Neat SDK container; continuing setup.")
                continue
            if not run_command([
                "docker",
                "exec",
                "-u",
                "root",
                sdk_container_name,
                "chown",
                f"{uid}:{gid}",
                container_path,
            ], fatal=False):
                run_command(cleanup_target, fatal=False)
                print(f"⚠️  Could not update ownership for copied sima-cli auth cache file '{filename}'; continuing setup.")
                continue
            copied.append(filename)

    if not copied:
        print("ℹ️  No sima-cli auth cache files were copied into Neat SDK container; continuing setup.")
        return

    print(f"🔐 Copied sima-cli auth cache file(s) into Neat SDK container: {', '.join(copied)}")


def _extract_sdk_base_version(sdk_release: str) -> str:
    match = re.search(r"^SDK Version\s*=\s*([0-9]+(?:\.[0-9]+){1,2})", sdk_release, re.MULTILINE)
    return match.group(1) if match else ""


def _docker_exec_interactive_prefix() -> List[str]:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return ["docker", "exec", "-it"]
    return ["docker", "exec", "-i"]


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_x86_platform() -> bool:
    machine = platform.machine().lower()
    return machine in {"x86_64", "amd64", "i386", "i686", "x86"}


def _is_arm64_platform() -> bool:
    machine = platform.machine().lower()
    return machine in {"aarch64", "arm64"}


def _version_at_least(version: str, minimum: str) -> bool:
    def parts(value: str) -> List[int]:
        match = re.match(r"^\s*(\d+(?:\.\d+)*)", value or "")
        if not match:
            return []
        return [int(part) for part in match.group(1).split(".")]

    current_parts = parts(version)
    minimum_parts = parts(minimum)
    if not current_parts or not minimum_parts:
        return False

    length = max(len(current_parts), len(minimum_parts))
    current_parts.extend([0] * (length - len(current_parts)))
    minimum_parts.extend([0] * (length - len(minimum_parts)))
    return current_parts >= minimum_parts


def _model_sdk_extension_component(base_version: str) -> str:
    if _is_x86_platform():
        return "tools/model-compiler/amd64"
    if _is_arm64_platform() and _version_at_least(base_version, "2.1.1"):
        return "tools/model-compiler/arm64"
    return ""


def ensure_model_sdk_extension_installed(
    sdk_container_name: str,
    login_name: str,
    auto_install: bool = False,
    uid: int = None,
    gid: int = None,
):
    """
    Install the Model Compiler extension for Neat SDK containers.
    """
    image_ref = _get_container_image_ref(sdk_container_name)
    if not image_ref or not is_neat_sdk_image(image_ref):
        return

    sdk_release = subprocess.run(
        ["docker", "exec", sdk_container_name, "cat", "/etc/sdk-release"],
        text=True,
        capture_output=True,
        check=False,
    )
    if sdk_release.returncode != 0:
        print(f"⚠️  Could not read /etc/sdk-release in '{sdk_container_name}'. Skipping Model Compiler extension install.")
        return

    base_version = _extract_sdk_base_version(sdk_release.stdout or "")
    if not base_version:
        print(f"⚠️  Could not determine SDK base version in '{sdk_container_name}'. Skipping Model Compiler extension install.")
        return

    extension_component = _model_sdk_extension_component(base_version)
    if not extension_component:
        print("ℹ️  Model Compiler extension install is not available on this host platform for SDK versions older than 2.1.1; skipping.")
        return

    console.print(
        Panel(
            "[yellow]This SDK supports Model Compiler as an extension.[/yellow]\n\n"
            "The Model Compiler extension lets you quantize and compile models "
            "so they can run on SiMa hardware accelerated.\n"
            "It will be installed on your host in the SDK extensions directory "
            "mounted into this container at /sdk-extensions.\n"
            "Depending on network conditions, installation may take up to 15 minutes.\n"
            "\n\n"
            "If you decide to install it later, run this from within the SDK container shell:\n"
            f"sima-cli install -v {base_version} {extension_component}",
            title="Model Compiler Extension",
            border_style="green",
            style="green",
            expand=False,
        )
    )
    if auto_install:
        print("ℹ️  Auto-installing Model Compiler extension.")
    else:
        if not yes_no_prompt("Install the Model Compiler extension now?"):
            print("ℹ️  Skipping Model Compiler extension install.")
            return

    internal_sima_cli_env = "export SIMA_CLI_AUTO_ACCEPT_UPDATE=1; "
    print("ℹ️  Logging in to sima-cli before installing the Model Compiler extension...")
    run_command(
        _docker_exec_interactive_prefix() + [
            "-u",
            login_name,
            sdk_container_name,
            "bash",
            "-lc",
            f"{internal_sima_cli_env}sima-cli login",
        ]
    )

    home_directory = f"/home/{login_name}"
    owner = f"{uid}:{gid}" if uid is not None and gid is not None else f"{login_name}:{login_name}"
    user_install_script = (
        "set -e; "
        f"export HOME={shlex.quote(home_directory)}; "
        f"export USER={shlex.quote(login_name)}; "
        f"export LOGNAME={shlex.quote(login_name)}; "
        f"{internal_sima_cli_env}"
        "export PATH=\"$HOME/.sima-cli/.venv/bin:$HOME/.local/bin:$PATH\"; "
        "mkdir -p \"$HOME/extension-installation\"; "
        "cd \"$HOME/extension-installation\"; "
        "if command -v sima-cli >/dev/null 2>&1; then "
        "SIMA_CLI_BIN=\"$(command -v sima-cli)\"; "
        "elif [ -x \"$HOME/.sima-cli/.venv/bin/sima-cli\" ]; then "
        "SIMA_CLI_BIN=\"$HOME/.sima-cli/.venv/bin/sima-cli\"; "
        "else "
        "echo \"sima-cli was not found for user $USER. Expected $HOME/.sima-cli/.venv/bin/sima-cli.\" >&2; "
        "exit 127; "
        "fi; "
        f"\"$SIMA_CLI_BIN\" install -v {shlex.quote(base_version)} {shlex.quote(extension_component)}"
    )
    install_script = (
        "set -e; "
        f"export HOME={shlex.quote(home_directory)}; "
        f"{_sudoers_drop_in_script(login_name)}; "
        "cleanup_model_sdk_install() { "
        f"chown -R {shlex.quote(owner)} \"$HOME/extension-installation\" \"$HOME/.sima-cli\" 2>/dev/null || true; "
        f"if [ -d /sdk-extensions ]; then chown -R {shlex.quote(owner)} /sdk-extensions || true; fi; "
        f"if [ -d \"$HOME/sdk-extensions\" ]; then chown -R {shlex.quote(owner)} \"$HOME/sdk-extensions\" || true; fi; "
        "}; "
        "trap cleanup_model_sdk_install EXIT; "
        "mkdir -p \"$HOME/extension-installation\"; "
        "cleanup_model_sdk_install; "
        f"su -s /bin/bash {shlex.quote(login_name)} -c 'sudo -n true'; "
        f"su -s /bin/bash {shlex.quote(login_name)} -c {shlex.quote(user_install_script)}"
    )
    print(f"ℹ️  Installing Model Compiler extension for SDK base version {base_version}...")
    run_command(
        [
            "docker",
            "exec",
            "-u",
            "root",
            sdk_container_name,
            "bash",
            "-lc",
            install_script,
        ]
    )
    print(f"✅ Model Compiler extension installed for SDK base version {base_version}.")


def ensure_codex_vscode_extension_installed(
    sdk_container_name: str,
    login_name: str,
    auto_install: bool = False,
    allow_prompt: bool = True,
    uid: int = None,
    gid: int = None,
) -> None:
    """
    Optionally install the Codex extension into browser VS Code.

    Older SDK images do not include OpenVSCode. In that case this function is a
    no-op so SDK setup remains backward compatible.
    """
    image_ref = _get_container_image_ref(sdk_container_name)
    if not image_ref or not is_neat_sdk_image(image_ref):
        return

    server_check = subprocess.run(
        ["docker", "exec", sdk_container_name, "test", "-x", OPENVSCODE_SERVER_BIN],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if server_check.returncode != 0:
        if auto_install:
            print("ℹ️  Browser VS Code is not available in this SDK image; skipping Codex extension install.")
        return

    extension_id = os.environ.get(CODEX_EXTENSION_ID_ENV, CODEX_EXTENSION_DEFAULT_ID).strip()
    if not extension_id:
        print(f"ℹ️  {CODEX_EXTENSION_ID_ENV} is empty; skipping Codex extension install.")
        return

    if auto_install:
        print("ℹ️  Auto-installing Codex extension for browser VS Code.")
    elif allow_prompt:
        if not yes_no_prompt("Install the Codex extension for browser VS Code now?", default_yes=False):
            print("ℹ️  Skipping Codex extension install.")
            return
    else:
        return

    home_directory = f"/home/{login_name}"
    extensions_dir = f"{home_directory}/.openvscode-server/extensions"
    owner = f"{uid}:{gid}" if uid is not None and gid is not None else f"{login_name}:{login_name}"
    install_script = (
        "set -e; "
        f"export HOME={shlex.quote(home_directory)}; "
        f"export USER={shlex.quote(login_name)}; "
        f"export LOGNAME={shlex.quote(login_name)}; "
        f"find {shlex.quote(OPENVSCODE_LEGACY_EXTENSIONS_DIR)} -maxdepth 1 -type d "
        f"-name {shlex.quote(extension_id + '-*')} -exec rm -rf {{}} + 2>/dev/null || true; "
        f"mkdir -p {shlex.quote(extensions_dir)}; "
        f"chown -R {shlex.quote(owner)} {shlex.quote(extensions_dir)} 2>/dev/null || true; "
        f"su -s /bin/bash {shlex.quote(login_name)} -c "
        + shlex.quote(
            "set -e; "
            f"export HOME={shlex.quote(home_directory)}; "
            f"export USER={shlex.quote(login_name)}; "
            f"export LOGNAME={shlex.quote(login_name)}; "
            f"mkdir -p {shlex.quote(extensions_dir)}; "
            f"if {shlex.quote(OPENVSCODE_SERVER_BIN)} --extensions-dir {shlex.quote(extensions_dir)} "
            f"--list-extensions 2>/dev/null | grep -Fxq {shlex.quote(extension_id)}; then "
            f"echo 'Codex extension already installed: {shlex.quote(extension_id)}'; "
            "else "
            f"{shlex.quote(OPENVSCODE_SERVER_BIN)} --extensions-dir {shlex.quote(extensions_dir)} "
            f"--install-extension {shlex.quote(extension_id)} --force --accept-server-license-terms; "
            "fi"
        )
    )

    print(f"ℹ️  Installing Codex extension for browser VS Code: {extension_id}")
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-u",
            "root",
            sdk_container_name,
            "bash",
            "-lc",
            install_script,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print("⚠️  Could not install Codex extension for browser VS Code; continuing SDK setup.")
        details = (result.stderr or result.stdout or "").strip()
        if details:
            print(details)
        return

    if result.stdout:
        print(result.stdout.strip())
    print("✅ Codex extension installed for browser VS Code.")


def _is_skills_enabled_image(image_ref: str) -> bool:
    return is_neat_sdk_image(image_ref)


def _sync_codex_skills(sdk_container_name: str, login_name: str, uid: int, gid: int) -> None:
    image_ref = _get_container_image_ref(sdk_container_name)
    if not image_ref or not _is_skills_enabled_image(image_ref):
        return

    home_directory = f"/home/{login_name}"
    script = f"""set -eu
src="${{SYSROOT:-/}}/usr/share/sima-neat/skills"
dest={shlex.quote(home_directory + "/.codex/skills")}
if [ ! -d "$src" ]; then
  echo "__SIMA_CODEX_SKILLS_STATUS=missing_source"
  exit 0
fi
mkdir -p "$dest"
cp -a "$src"/. "$dest"/
chown -R {uid}:{gid} {shlex.quote(home_directory + "/.codex")}
echo "__SIMA_CODEX_SKILLS_STATUS=copied"
"""

    proc = subprocess.run(
        ["docker", "exec", "-u", "root", sdk_container_name, "bash", "-lc", script],
        text=True,
        capture_output=True,
        check=False,
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if "__SIMA_CODEX_SKILLS_STATUS=copied" in output:
        print(f"✅ Codex skills synced for user '{login_name}' in '{sdk_container_name}'.")
        return
    if "__SIMA_CODEX_SKILLS_STATUS=missing_source" in output:
        print(
            f"ℹ️  Codex skills source not found in '{sdk_container_name}' "
            f"($SYSROOT/usr/share/sima-neat/skills)."
        )
        return
    print(f"⚠️ Failed to sync Codex skills in '{sdk_container_name}' (exit={proc.returncode}).")
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip())


def _sudoers_drop_in_script(login_name: str) -> str:
    sudoers_line = f"{login_name} ALL=(ALL:ALL) NOPASSWD:ALL"
    return (
        "set -eu; "
        "mkdir -p /etc/sudoers.d; "
        "if ! grep -Eq '^[[:space:]]*([#@]includedir)[[:space:]]+/etc/sudoers\\.d([[:space:]]+.*)?$' /etc/sudoers; then "
        "printf '\\n#includedir /etc/sudoers.d\\n' >> /etc/sudoers; "
        "fi; "
        f"printf '%s\\n' {shlex.quote(sudoers_line)} > /etc/sudoers.d/sima-cli-user; "
        "chmod 0440 /etc/sudoers.d/sima-cli-user"
    )


def _bash_profile_sources_bashrc_script(login_name: str, uid: int, gid: int) -> str:
    home = f"/home/{login_name}"
    profile = f"{home}/.bash_profile"
    bashrc = f"{home}/.bashrc"
    return (
        "set -eu; "
        f"home={shlex.quote(home)}; "
        f"profile={shlex.quote(profile)}; "
        f"bashrc={shlex.quote(bashrc)}; "
        "mkdir -p \"$home\"; "
        "touch \"$bashrc\"; "
        "touch \"$profile\"; "
        "if ! grep -Eq '(^|[[:space:]])(\\.|source)[[:space:]]+(\"?\\$HOME\"?/|~/)?\\.bashrc' \"$profile\" 2>/dev/null; then "
        "cat >> \"$profile\" <<'SIMA_CLI_BASHRC_SOURCE'\n\n"
        "if [ -f \"$HOME/.bashrc\" ]; then\n"
        "    . \"$HOME/.bashrc\"\n"
        "fi\n"
        "SIMA_CLI_BASHRC_SOURCE\n"
        "fi; "
        f"chown {uid}:{gid} \"$home\" \"$profile\" \"$bashrc\""
    )


def _append_unique_line(path: str, line: str) -> None:
    with open(path, "r+", encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines()
        if line in lines:
            return
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")


def _ensure_passwd_user(path: str, login_name: str, uid: int, gid: int) -> None:
    user_line = f"{login_name}:x:{uid}:{gid}::/home/{login_name}:/bin/bash"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    filtered = []
    insert_index = None
    for line in lines:
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == login_name:
            continue
        if insert_index is None and len(parts) >= 3 and parts[2] == str(uid):
            insert_index = len(filtered)
        filtered.append(line)

    if insert_index is None:
        insert_index = len(filtered)
    filtered.insert(insert_index, user_line)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(filtered) + "\n")


def _ensure_shadow_user(path: str, login_name: str) -> None:
    shadow_line = f"{login_name}:$6$hash$placeholder:::::::"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    filtered = [
        line
        for line in lines
        if not (line.split(":", 1)[0] == login_name)
    ]
    filtered.append(shadow_line)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(filtered) + "\n")


def _configure_group_file(path: str, login_name: str, gid: int) -> None:
    primary_group_line = f"{login_name}:x:{gid}:"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    updated_lines = []
    insert_index = None
    for line in lines:
        parts = line.split(":")
        if len(parts) < 4:
            updated_lines.append(line)
            continue
        name, password, group_id, members = parts[:4]
        if name == login_name:
            continue
        if insert_index is None and group_id == str(gid):
            insert_index = len(updated_lines)
        if name in {"docker", "sudo"}:
            member_list = [member for member in members.split(",") if member]
            if login_name not in member_list:
                member_list.append(login_name)
            line = ":".join([name, password, group_id, ",".join(member_list), *parts[4:]])
        updated_lines.append(line)

    if insert_index is None:
        insert_index = len(updated_lines)
    updated_lines.insert(insert_index, primary_group_line)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated_lines) + "\n")


def _prepare_log_host_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o777)
    except OSError as e:
        print(f"⚠️ Could not make log folder writable for container services: {path} ({e})")


def _docker_cp_staging_dir():
    """
    Docker installed through Snap may not see host /tmp paths. Stage files under
    a non-hidden user home directory so docker cp can access them across Docker
    variants, including Snap confinement.
    """
    home = os.path.expanduser("~")
    if home and os.path.isdir(home) and os.access(home, os.W_OK):
        staging = tempfile.TemporaryDirectory(prefix="sima-cli-sdk-", dir=home)
        try:
            os.chmod(staging.name, 0o755)
        except OSError:
            staging.cleanup()
            raise
        return staging
    return tempfile.TemporaryDirectory(prefix="sima-cli-sdk-")


def configure_container_user(
    sdk_container_name: str,
    login_name: str,
    uid: int,
    gid: int,
    platform_os: str = None,
) -> None:
    platform_os = platform_os or check_os()
    home_directory = f"/home/{login_name}"

    if platform_os in ["linux", "macos"]:
        with _docker_cp_staging_dir() as tmpdir:
            passwd_path = os.path.join(tmpdir, "passwd.txt")
            shadow_path = os.path.join(tmpdir, "shadow.txt")
            group_path = os.path.join(tmpdir, "group.txt")

            run_command(["docker", "cp", f"{sdk_container_name}:/etc/passwd", passwd_path])
            _ensure_passwd_user(passwd_path, login_name, uid, gid)
            run_command(["docker", "cp", passwd_path, f"{sdk_container_name}:/etc/passwd"])

            run_command(["docker", "cp", f"{sdk_container_name}:/etc/shadow", shadow_path])
            _ensure_shadow_user(shadow_path, login_name)
            run_command(["docker", "cp", shadow_path, f"{sdk_container_name}:/etc/shadow"])

            run_command(["docker", "cp", f"{sdk_container_name}:/etc/group", group_path])
            _configure_group_file(group_path, login_name, gid)
            run_command(["docker", "cp", group_path, f"{sdk_container_name}:/etc/group"])

        run_command([
            "docker",
            "exec",
            "-u",
            "0",
            sdk_container_name,
            "bash",
            "-lc",
            _sudoers_drop_in_script(login_name),
        ])

        run_command([
            "docker",
            "exec",
            "-u",
            login_name,
            sdk_container_name,
            "bash",
            "-lc",
            "sudo -n true",
        ])

        run_command(["docker", "exec", "-u", "root", sdk_container_name, "mkdir", "-p", home_directory])
        run_command(["docker", "exec", "-u", "root", sdk_container_name, "chown", f"{uid}:{gid}", home_directory])
        run_command([
            "docker",
            "exec",
            "-u",
            "root",
            sdk_container_name,
            "bash",
            "-lc",
            _bash_profile_sources_bashrc_script(login_name, uid, gid),
        ])
    else:
        command = f"echo '{login_name} ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers"
        run_command(["docker", "exec", "-u", "root", sdk_container_name, "sh", "-c", command])


def install_neat_playbooks(sdk_container_name: str, login_name: str) -> None:
    image_ref = _get_container_image_ref(sdk_container_name)
    if not image_ref or not is_neat_sdk_image(image_ref):
        return

    print(f"ℹ️  Installing Neat coding agent playbooks for user '{login_name}' in '{sdk_container_name}'...")
    run_command(
        [
            "docker",
            "exec",
            "-u",
            login_name,
            "-e",
            "SIMA_CLI_AUTO_ACCEPT_UPDATE=1",
            "-e",
            "GITHUB_TOKEN",
            sdk_container_name,
            "bash",
            "-lc",
            f"cd {shlex.quote('/home/' + login_name)} && sima-cli install gh:sima-neat/playbooks",
        ]
    )
    print(f"✅ Neat coding agent playbooks installed for user '{login_name}' in '{sdk_container_name}'.")


def configure_container(
    sdk_container_name,
    port=None,
    configure_network=False,
    noninteractive=False,
    yes_to_all=False,
    no_model_sdk=False,
    minimal=False,
):
    """
    Configure container user mappings and permissions:
      - Detects current host user (uid, gid, login_name)
      - Updates passwd, shadow, group, sudoers
      - Creates home directory inside container
      - Optionally saves port to container and updates rsyslog (if configure_network=True)
      - If configure_network=True, computes and stores .hash for /usr/local/simaai/plugins
    """
    platform_os = check_os()
    no_model_sdk = no_model_sdk or minimal

    # Detect current host user
    if platform_os in ["linux", "macos"]:
        login_name, uid, gid = detect_current_user()
    else:
        login_name, uid, gid = "docker", 900, 900

    print(f"⚙️  Configuring container '{sdk_container_name}' for user '{login_name}' (UID={uid}, GID={gid})")
    home_directory = f"/home/{login_name}"

    configure_container_user(sdk_container_name, login_name, uid, gid, platform_os=platform_os)

    run_command(
        [
            "docker",
            "exec",
            "-u",
            "root",
            sdk_container_name,
            "bash",
            "-lc",
            (
                "if [ -d /home/docker/.insight-config ]; then "
                f"mkdir -p {shlex.quote(home_directory)}; "
                f"if [ ! -e {shlex.quote(home_directory)}/.insight-config ] && "
                f"[ ! -L {shlex.quote(home_directory)}/.insight-config ]; then "
                f"ln -s /home/docker/.insight-config {shlex.quote(home_directory)}/.insight-config; "
                f"chown -h {uid}:{gid} {shlex.quote(home_directory)}/.insight-config; "
                "fi; "
                "fi"
            ),
        ]
    )

    # Ensure /workspace points to the mounted workspace path inside the container.
    run_command(
        [
            "docker",
            "exec",
            "-u",
            "root",
            sdk_container_name,
            "bash",
            "-lc",
            (
                "if [ ! -e /workspace ] && [ ! -L /workspace ]; then "
                "ln -s /home/docker/sima-cli /workspace; "
                "fi"
            ),
        ]
    )

    _copy_sima_cli_auth_cache_to_container(sdk_container_name, login_name, uid, gid)

    # Ensure sima-cli is available for the configured default user unless this
    # setup is only preparing a lightweight CI compilation container.
    if minimal:
        print("ℹ️  Skipping sima-cli installation because --minimal was specified.")
    else:
        ensure_sima_cli_installed(sdk_container_name, login_name)
    if no_model_sdk:
        reason = "--minimal" if minimal else "--no-model-compiler"
        print(f"ℹ️  Skipping Model Compiler extension installation because {reason} was specified.")
    else:
        ensure_model_sdk_extension_installed(
            sdk_container_name,
            login_name,
            auto_install=(noninteractive or yes_to_all),
            uid=uid,
            gid=gid,
        )
    _sync_codex_skills(sdk_container_name, login_name, uid, gid)
    if minimal:
        print("ℹ️  Skipping Neat coding agent playbook installation because --minimal was specified.")
    else:
        install_neat_playbooks(sdk_container_name, login_name)
        ensure_codex_vscode_extension_installed(
            sdk_container_name,
            login_name,
            auto_install=_env_truthy(CODEX_EXTENSION_INSTALL_ENV),
            allow_prompt=not noninteractive,
            uid=uid,
            gid=gid,
        )

    # ---- Optional Network & Syslog Configuration ----
    if configure_network:
        if port is None:
            raise ValueError("Port must be provided when configure_network=True")

        run_command(["docker", "cp", "./config.json", f"{sdk_container_name}:/home/docker/.simaai/config.json"])
        #os.remove("./config.json")

        run_command(["docker", "exec", "-u", "root", sdk_container_name,
                     "sed", "-i.bk", f"s@docker@{login_name}@g", "/etc/rsyslog.conf"])
        print(f"🌐 Network and syslog configuration applied (Port={port}).")

        run_command(["docker", "exec", "-u", "root", sdk_container_name,
                     "chown", "-R", f"{uid}:{gid}", "/usr/local/simaai/"])

        # ---- Compute plugin hash and store it ----
        plugin_dir = "/usr/local/simaai/plugin_zoo/a65-apps"
        print(f"🧮 Computing C++ source hash inside {plugin_dir} ...")

        hash_command = f"""
import hashlib, os
def get_cpp_plugins_source_code_hash(directory):
    hasher = hashlib.md5()
    cpp_extensions = {{'.cpp','.cc','.cxx','.hpp','.h','.c'}}
    excluded_dirs = {{'build'}}
    file_list = []
    for root, dirs, files in os.walk(directory, topdown=True):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        dirs.sort(); files.sort()
        for f in files:
            if f == 'CMakeLists.txt' or any(f.endswith(ext) for ext in cpp_extensions):
                rel = os.path.relpath(os.path.join(root,f),directory).replace('\\\\','/')
                file_list.append(rel)
    for f in sorted(file_list):
        hasher.update(f.encode('utf-8'))
        try:
            with open(os.path.join(directory,f),'rb') as fd:
                while chunk := fd.read(4096):
                    chunk = chunk.replace(b'\\r\\n',b'\\n')
                    hasher.update(chunk)
        except (FileNotFoundError,PermissionError): pass
    return hasher.hexdigest()

path='{plugin_dir}'
h = get_cpp_plugins_source_code_hash(path)
with open(os.path.join(path,'.hash'),'w') as f: f.write(h)
print(f'✅ Hash written to {{path}}/.hash → {{h}}')
"""
        run_command(["docker", "exec", sdk_container_name, "python3", "-c", hash_command])

    else:
        print("ℹ️  Skipping network and syslog configuration as requested.")

    print(f"✅ Container '{sdk_container_name}' configured successfully.")

def ensure_simasdkbridge_network():
    """
    Ensure that the 'simasdkbridge' Docker network exists.

    This method:
      - Lists all available Docker networks.
      - If the network 'simasdkbridge' exists, it prints a confirmation.
      - If not found, it creates the network silently.

    Raises:
        RuntimeError: If Docker command fails to run.
    """
    print("🔍 Checking SiMa SDK Bridge Network...")

    try:
        # List existing Docker networks
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )

        networks = result.stdout.splitlines()

        # Check for simasdkbridge
        if "simasdkbridge" in networks:
            print("✅ SiMa SDK Bridge Network found.")
        else:
            print("⚙️ 'simasdkbridge' network not found. Creating it now...")
            subprocess.run(
                ["docker", "network", "create", "simasdkbridge"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            print("✅ 'simasdkbridge' network created successfully.")

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"❌ Failed to check or create Docker network: {e.stderr.strip() if e.stderr else str(e)}")


def start_docker_container(
    uid,
    gid,
    port,
    workspace,
    image,
    privileged=False,
    port_mapping_required=False,
    devkit_env=None,
    sdk_extensions_dir=None,
    noninteractive=False,
    yes_to_all=False,
    no_insight=False,
    no_model_sdk=False,
    minimal=False,
):
    """
    Start a Docker container using an image pulled from either JFrog or AWS ECR.

    Automatically mounts log folders from the container's /var/log to
    the host workspace/<container_name>/logs/.
    """

    # ─────────────────────────────────────────────
    # Generate container name
    # ─────────────────────────────────────────────
    no_insight = no_insight or minimal
    container_name = sanitize_container_name(image)
    hostname = sanitize_container_hostname(container_name)
    print(f"🚀 Starting container '{container_name}' using image '{image}'")
    image_tag = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else "latest"

    # Detect macOS with Apple Silicon
    system_name = platform.system()
    machine_arch = platform.machine().lower()
    is_macos_arm = (system_name == "Darwin" and "arm" in machine_arch)

    # Base Docker command
    docker_cmd = [
        "docker", "run", "-t", "-d",
        "--name", container_name,
        "--hostname", hostname,
        "--network", "simasdkbridge",
        "-v", f"{workspace}:/home/docker/sima-cli/",
        "-e", f"SDK_IMAGE_TAG={image_tag}",
    ]

    neat_sdk_image = is_neat_sdk_image(image)
    if not neat_sdk_image:
        docker_cmd.insert(4, f"--user={uid}:{gid}")

    if neat_sdk_image:
        remote_user = detect_current_user()[0] if check_os() in ["linux", "macos"] else "docker"
        docker_cmd.extend([
            "--label",
            f"devcontainer.metadata={_devcontainer_metadata_label(remote_user)}",
        ])
        docker_cmd.extend(["-e", f"OPENVSCODE_SERVER_USER={remote_user}"])
        docker_cmd.extend(["-v", f"{workspace}:/workspace"])

    # ─────────────────────────────────────────────
    # Add --platform=linux/amd64 for macOS ARM
    # ─────────────────────────────────────────────
    if is_macos_arm and "modelsdk" in image.lower():
        print("💻 Detected macOS with Apple Silicon → forcing amd64 emulation for ModelSDK.")
        docker_cmd.extend(["--platform", "linux/amd64"])

    # ─────────────────────────────────────────────
    # Add privileged and port mappings
    # ─────────────────────────────────────────────
    if privileged:
        docker_cmd.extend(["--privileged", "--cap-add=NET_RAW", "--cap-add=NET_ADMIN"])

    if port_mapping_required:
        docker_cmd.extend(["-p", f"{port}:8084"])

    # ─────────────────────────────────────────────
    # Mount /var/log subfolders based on IMAGE_CONFIG
    # ─────────────────────────────────────────────
    short_name = extract_short_name(image)
    if short_name in IMAGE_CONFIG:
        var_log_folders = IMAGE_CONFIG[short_name].get("var-log-folders", [])
        if var_log_folders:
            for folder in var_log_folders:
                log_host_dir = os.path.join(workspace, '.' + container_name, "logs", folder.strip("/"))
                _prepare_log_host_dir(log_host_dir)
                if folder == "/" or folder == "":
                    mount_target = "/var/log"
                else:
                    mount_target = f"/var/log/{folder.strip('/')}"
                docker_cmd.extend(["-v", f"{log_host_dir}:{mount_target}"])
            print(f"🪵 Mapped log folders: {', '.join(var_log_folders)} → host logs/")
        else:
            print("ℹ️  No /var/log mappings configured for this SDK.")
    else:
        print("⚠️ Could not determine SDK short name for log mapping.")

    if sdk_extensions_dir and neat_sdk_image:
        os.makedirs(sdk_extensions_dir, exist_ok=True)
        docker_cmd.extend(["-v", f"{sdk_extensions_dir}:/sdk-extensions"])
        print(f"🧩 Mapped SDK extensions: {sdk_extensions_dir} → /sdk-extensions")

    # Inject DevKit context only for the supported Neat SDK image family.
    if devkit_env and neat_sdk_image:
        host_ip = devkit_env.get("host_ip", "")
        host_export = devkit_env.get("workspace", "")
        host_platform = devkit_env.get("host_platform", "")
        devkit_ip = devkit_env.get("devkit_ip", "")
        docker_cmd.extend([
            "-e", f"SIMA_DEVKIT_IP={devkit_ip}",
            "-e", f"DEVKIT_SYNC_DEVKIT_IP={devkit_ip}",
            "-e", f"NFS_SERVER_HOST_IP={host_ip}",
            "-e", f"DEVKIT_HOST_EXPORT_PATH={host_export}",
            "-e", f"DEVKIT_HOST_PLATFORM={host_platform}",
        ])

    # ─────────────────────────────────────────────
    # Launch container
    # ─────────────────────────────────────────────
    if neat_sdk_image:
        from sima_cli.sdk.neat import (
            NEAT_DOCKER_RETRY_LIMIT,
            append_neat_docker_args,
            is_docker_port_collision_error,
            prepare_neat_container_run,
            print_neat_setup_summary,
            reserved_ports_from_neat_port_map,
        )

        base_docker_cmd = list(docker_cmd)
        neat_run_config = None
        last_result = None
        reserved_ports = set()
        for attempt in range(1, NEAT_DOCKER_RETRY_LIMIT + 1):
            neat_run_config = prepare_neat_container_run(
                workspace=workspace,
                container_name=container_name,
                devkit_env=devkit_env,
                yes_to_all=yes_to_all,
                noninteractive=noninteractive,
                no_insight=no_insight,
                minimal=minimal,
                reserved_ports=reserved_ports,
            )
            launch_cmd = list(base_docker_cmd)
            append_neat_docker_args(launch_cmd, neat_run_config)
            launch_cmd.append(image)
            result = subprocess.run(launch_cmd, text=True, capture_output=True, check=False)
            last_result = result
            if result.returncode == 0:
                if result.stdout:
                    print(result.stdout.strip())
                break

            error_text = "\n".join(part for part in [result.stdout, result.stderr] if part)
            if attempt < NEAT_DOCKER_RETRY_LIMIT and is_docker_port_collision_error(error_text):
                _remove_failed_neat_container(container_name)
                reserved_ports.update(reserved_ports_from_neat_port_map(neat_run_config.port_map))
                print(
                    f"⚠️  Docker reported a Neat SDK port collision. Regenerating port map and retrying "
                    f"({attempt}/{NEAT_DOCKER_RETRY_LIMIT})..."
                )
                continue

            print(f"❌ Command failed: {' '.join(launch_cmd)}")
            if error_text:
                print(f"Error: {error_text.strip()}")
            sys.exit(1)

        if last_result is None or last_result.returncode != 0:
            sys.exit(1)
    else:
        docker_cmd.append(image)
        run_command(docker_cmd)

    # ─────────────────────────────────────────────
    # Post-launch configuration
    # ─────────────────────────────────────────────
    if port_mapping_required:
        print(f"✅ Container '{container_name}' started successfully on port {port}.")
    elif neat_sdk_image:
        print(f"✅ Container '{container_name}' started successfully with Neat SDK port mappings.")
    else:
        print(f"✅ Container '{container_name}' started successfully (no external port mapping).")

    if neat_sdk_image and devkit_env:
        from sima_cli.sdk.network_doctor import validate_running_neat_container_network

        validate_running_neat_container_network(
            container_name,
            devkit_ip=(devkit_env or {}).get("devkit_ip", ""),
        )

    configure_container(
        container_name,
        port,
        port_mapping_required,
        noninteractive=noninteractive,
        yes_to_all=yes_to_all,
        no_model_sdk=no_model_sdk,
        minimal=minimal,
    )

    if devkit_env and neat_sdk_image:
        bootstrap_devkit_container(container_name, devkit_env)

    if neat_sdk_image and neat_run_config is not None:
        print_neat_setup_summary(neat_run_config)

    return container_name


def _remove_failed_neat_container(container_name: str) -> None:
    try:
        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
            text=True,
            capture_output=True,
            check=False,
        )
        if inspect.returncode != 0:
            return
        if inspect.stdout.strip() == "running":
            return
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return


def bootstrap_devkit_container(container_name: str, devkit_env: dict):
    """
    One-time best-effort bootstrap:
      - ensure /workspace symlink exists
      - source devkit.sh with target IP
    This only runs when sdk setup --devkit was provided.
    """
    devkit_ip = devkit_env.get("devkit_ip", "")
    if not devkit_ip:
        return
    bootstrap_interactive = bool(devkit_env.get("bootstrap_interactive", False))
    noninteractive = bool(devkit_env.get("noninteractive", False))
    host_ip = devkit_env.get("host_ip", "")
    host_export = devkit_env.get("workspace", "")
    host_platform = devkit_env.get("host_platform", "")

    script = f"""set +e
BOOTSTRAP_STATUS=unknown
export SIMA_DEVKIT_IP={shlex.quote(devkit_ip)}
export DEVKIT_SYNC_DEVKIT_IP={shlex.quote(devkit_ip)}
export NFS_SERVER_HOST_IP={shlex.quote(host_ip)}
export DEVKIT_HOST_EXPORT_PATH={shlex.quote(host_export)}
export DEVKIT_HOST_PLATFORM={shlex.quote(host_platform)}
export DEVKIT_SYNC_NONINTERACTIVE={shlex.quote("1" if noninteractive else "0")}
if [ ! -e /workspace ] && [ ! -L /workspace ]; then
  ln -s /home/docker/sima-cli /workspace 2>/dev/null || true
fi
if [ ! -f /usr/local/bin/devkit.sh ]; then
  BOOTSTRAP_STATUS=missing_script
else
  source /usr/local/bin/devkit.sh {shlex.quote(devkit_ip)}
  SRC_RC=$?
  if [ "$SRC_RC" -ne 0 ]; then
    BOOTSTRAP_STATUS=source_failed
  elif command -v dk >/dev/null 2>&1; then
    BOOTSTRAP_STATUS=sourced_with_dk
  else
    BOOTSTRAP_STATUS=sourced_no_dk
  fi
fi
echo "__SIMA_DEVKIT_BOOTSTRAP_STATUS=$BOOTSTRAP_STATUS"
"""

    if bootstrap_interactive and not noninteractive and sys.stdin.isatty() and sys.stdout.isatty():
        proc = subprocess.run(
            ["docker", "exec", "-it", container_name, "bash", "-lc", script],
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            print(f"✅ DevKit bootstrap completed in container '{container_name}' (interactive).")
            return
        print(f"⚠️ DevKit bootstrap failed in container '{container_name}' (interactive, exit={proc.returncode}).")
        return

    proc = subprocess.run(
        ["docker", "exec", "-i", container_name, "bash", "-lc", script],
        input=("\n" * 32),
        text=True,
        capture_output=True,
        check=False,
    )

    status = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith("__SIMA_DEVKIT_BOOTSTRAP_STATUS="):
            status = line.split("=", 1)[1].strip()
            break

    if proc.returncode == 0:
        if status == "sourced_with_dk":
            print(f"✅ DevKit bootstrap completed in container '{container_name}' (devkit.sh sourced, dk found).")
        elif status == "sourced_no_dk":
            print(
                f"⚠️ DevKit bootstrap in container '{container_name}': "
                "devkit.sh sourced, but dk is not available in this shell."
            )
        elif status == "missing_script":
            print(
                f"⚠️ DevKit bootstrap in container '{container_name}': "
                "/usr/local/bin/devkit.sh not found."
            )
        elif status == "source_failed":
            print(
                f"⚠️ DevKit bootstrap in container '{container_name}': "
                "devkit.sh source command failed."
            )
            if proc.stdout.strip():
                print(proc.stdout.strip())
            if proc.stderr.strip():
                print(proc.stderr.strip())
        else:
            print(f"✅ DevKit bootstrap completed in container '{container_name}'.")
        return

    # First-time setup often needs interactive password entry for ssh-copy-id.
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if (
        status == "source_failed"
        and ("permission denied" in combined or "password" in combined)
        and not noninteractive
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        print(
            "ℹ️  DevKit bootstrap needs interactive authentication. "
            "Retrying in interactive mode..."
        )
        interactive_proc = subprocess.run(
            ["docker", "exec", "-it", container_name, "bash", "-lc", script],
            text=True,
            check=False,
        )
        if interactive_proc.returncode == 0:
            print(
                f"✅ DevKit bootstrap completed in container '{container_name}' after interactive authentication."
            )
            return
        print(
            f"⚠️ DevKit bootstrap interactive retry failed in container '{container_name}' "
            f"(exit={interactive_proc.returncode})."
        )

    print(f"⚠️ DevKit bootstrap failed in container '{container_name}' (exit={proc.returncode}).")
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip())


def print_section(title):
    print("\n" + "=" * 20 + f"[ {title} ]" + "=" * 20 + "\n")

def create_config_json(file_path="config.json", port=8084, selected_images=None):
    """
    Create a JSON configuration file containing port, selected SDK images,
    and their associated sanitized container names.

    The image keys come from IMAGE_CONFIG, not raw image URLs.

    Example output:
    {
        "port": 8084,
        "images": {
            "elxr": { "container_name": "ecr-sima-elxr-1.8" },
            "neat": { "container_name": "ghcr.io-sima-neat-sdk-latest" },
            "yocto": { "container_name": "jfrog-yocto-latest" },
            "modelsdk": { "container_name": "modelsdk-latest" }
        }
    }
    """

    if not selected_images:
        print("⚠️ No selected images provided; nothing to write.")
        return None

    try:
        images_data = {}

        for image in selected_images:
            key = extract_short_name(image)
            if key not in IMAGE_CONFIG or key in images_data:
                continue
            container_name = sanitize_container_name(image)
            images_data[key] = {"container_name": container_name}

        if not images_data:
            print("⚠️ No matching images found for IMAGE_CONFIG keys.")
            return None

        config_data = {
            "port": port,
            "images": images_data,
        }

        # Write JSON
        with open(file_path, "w") as f:
            json.dump(config_data, f, indent=4)

        abs_path = os.path.abspath(file_path)
        print(f"✅ Configuration file created successfully at: {abs_path}")
        return abs_path

    except Exception as e:
        print(f"❌ Failed to create configuration file: {e}")
        return None

def is_docker_running():
    """Check if the Docker daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False

def yes_no_prompt(prompt: str, default_yes=True) -> bool:
    """
    Prompt user for a yes/no response.
    Defaults to YES if Enter is pressed.
    """
    default_choice = "Y/n" if default_yes else "y/N"
    while True:
        choice = input(f"{prompt} ({default_choice}): ").strip().lower()
        if choice == "" and default_yes:
            return True
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no"}:
            return False
        print("❌ Invalid choice. Please enter Y or N.")


def get_all_containers(running_containers_only: bool = False):
    """
    Return a filtered list of Docker containers whose names contain:
        - neat
        - elxr
        - yocto
        - model
        - mpk

    Args:
        running_containers_only (bool): If True, return only running containers.

    Returns:
        list[dict]: Each entry is a parsed JSON object from docker ps output.
    """
    try:
        cmd = ["docker", "ps", "--format", "{{json .}}"]
        if not running_containers_only:
            cmd.insert(2, "-a")

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        containers = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue

            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue

            name = info.get("Names") or info.get("Name") or info.get("name") or ""
            image = info.get("Image") or info.get("image") or ""
            if _container_sdk_key(name, image):
                containers.append(info)

        return containers

    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ Failed to list containers: {e}[/red]")
        sys.exit(1)

def get_container_info(container_id):
    """Return container name, image, and status by container ID."""
    name = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Name}}", container_id],
        text=True
    ).strip().lstrip('/')

    image = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container_id],
        text=True
    ).strip()

    status = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.State.Status}}", container_id],
        text=True
    ).strip()  # running, exited, etc.

    return name, image, status

def is_target_container(container_name, container_image):
    """Check if container name or image matches our list of IMAGE_NAMES."""
    return bool(_container_sdk_key(container_name, container_image))

def extract_tag_from_image(image_name):
    """Extract the tag/version from a Docker image string."""
    return image_name.split(":")[-1] if ":" in image_name else "unknown"

def stop_and_remove_container(container_id, container_name, container_status):
    """Stop and optionally remove a single container."""
    if container_status == "running":
        if yes_no_prompt(f"Do you want to stop '{container_name}'?"):
            subprocess.run(["docker", "stop", container_id], check=True)
            print(f"✅ Container '{container_name}' stopped.")
    else:
        print(f"ℹ️ Container '{container_name}' is already stopped.")

    if yes_no_prompt(f"Do you want to remove container '{container_name}'?"):
        subprocess.run(["docker", "rm", container_id], check=True)
        print(f"🗑️  Container '{container_name}' removed.")

def stop_and_remove_group(group_items, selected_tag):
    """Stop and remove all containers in a group with a single prompt."""
    container_names = [cname for _, cname, _, _ in group_items]
    print(f"\n⚠️ You have chosen to manage ALL containers in group '{selected_tag}'.")
    print(f"Containers: {', '.join(container_names)}")

    if yes_no_prompt("Do you want to stop ALL containers in this group?"):
        for cid, cname, cstatus, _ in group_items:
            if cstatus == "running":
                subprocess.run(["docker", "stop", cid], check=True)
                print(f"✅ Stopped: {cname}")
            else:
                print(f"ℹ️ Already stopped: {cname}")

    if yes_no_prompt("Do you want to remove ALL containers in this group?"):
        for cid, cname, _, _ in group_items:
            subprocess.run(["docker", "rm", cid], check=True)
            print(f"🗑️ Removed: {cname}")

def get_valid_input(prompt, valid_range, allow_a=False):
    """
    Prompt user for input and handle invalid choices.
    - valid_range: range of valid numbers
    - allow_a: allow 'a' as a valid option
    """
    while True:
        choice = input(prompt).strip().lower()
        if allow_a and choice == 'a':
            return 'a'
        if choice.isdigit():
            choice_num = int(choice)
            if choice_num in valid_range:
                return choice_num

        # Invalid input handling
        if yes_no_prompt("❌ Invalid input. Would you like to retry?"):
            continue
        else:
            print("Exiting as per user request.")
            sys.exit(0)

def group_images_by_tag(images):
    """Group images by their version tag."""
    grouped = defaultdict(list)
    for image in images:
        tag = extract_tag_from_image(image)
        grouped[tag].append(image)
    return grouped

def remove_image(image_name):
    """Remove a single Docker image."""
    try:
        run_command(["docker", "rmi", image_name])
        print(f"🗑️  Image '{image_name}' removed.")
    except subprocess.CalledProcessError:
        print(f"❌ Failed to remove image '{image_name}'.")

def remove_images_in_group(group_images, tag):
    """
    Provide user with options to remove all images or selected ones.
    Includes validation and retry logic.
    """
    while True:
        print(f"\n⚙️ Managing image group: Version {tag}")
        print("Images in this group:")
        for idx, img in enumerate(group_images, start=1):
            print(f"    {idx}. {extract_image_name(img)}")
        print("    a. Remove ALL images in this group")

        selection = input("\nEnter image numbers (comma-separated) or 'a' to remove ALL: ").strip().lower()

        if selection == 'a':
            for img in group_images:
                remove_image(img)
            return

        # Validate numeric input
        parts = [p.strip() for p in selection.split(",") if p.strip()]
        if not parts or not all(p.isdigit() for p in parts):
            if yes_no_prompt("❌ Invalid input. Do you want to retry?", default_yes=True):
                continue
            print("Exiting as per user request.")
            sys.exit(0)

        # Convert to set of indices
        selected_indices = {int(i) for i in parts}
        invalid_indices = [i for i in selected_indices if i < 1 or i > len(group_images)]
        if invalid_indices:
            print(f"❌ Invalid selection: {invalid_indices}")
            if yes_no_prompt("Do you want to retry?", default_yes=True):
                continue
            print("Exiting as per user request.")
            sys.exit(0)

        for idx, img in enumerate(group_images, start=1):
            if idx in selected_indices:
                remove_image(img)
        return

def get_all_images():
    """
    Return list of all docker images filtered by IMAGE_NAMES with full repository:tag format.
    """
    output = subprocess.check_output(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        text=True
    ).strip()

    if not output:
        return []

    images = []
    for line in output.splitlines():

        if extract_short_name(line) in IMAGE_NAMES:
            images.append(line.strip())

    return images

def extract_image_name(full_repo_path):
    """
    Extract just the image name (e.g., 'elxr') from a full repository string.
    Example:
        'artifacts.eng.sima.ai:443/sima-docker/elxr:latest_VP-10555'
        -> 'elxr'
    """
    # Split by '/' then take the last part (e.g., 'elxr:latest_VP-10555')
    last_part = full_repo_path.split("/")[-1]
    # Remove the tag
    return last_part.split(":")[0]

#--------------------------------------------
# Added when integrating into sima-cli
#--------------------------------------------

from InquirerPy import inquirer


def _image_repository(image: str) -> str:
    image_ref = (image or "").strip().lower().split("@", 1)[0]
    if image_ref.startswith("ghcr:"):
        image_ref = "ghcr.io/" + image_ref[len("ghcr:"):]
    if not image_ref:
        return ""

    last_part = image_ref.rsplit("/", 1)[-1]
    if ":" in last_part:
        return image_ref.rsplit(":", 1)[0]
    return image_ref


def _is_sima_neat_repo(repo: str) -> bool:
    return repo.startswith("ghcr.io/sima-neat/")


def _is_local_neat_sdk_repo(repo: str) -> bool:
    return "/" not in repo and (
        repo == "sdk"
        or repo.startswith("sdk-")
        or _is_neat_sdk_alias_repo_name(repo)
    )


def _is_neat_sdk_alias_repo_name(repo_name: str) -> bool:
    return repo_name == "neat-sdk" or repo_name.startswith("neat-sdk-")


def _is_neat_repo_name(repo_name: str, include_neat_sdk_alias: bool = False) -> bool:
    return (
        repo_name == "sdk"
        or repo_name.startswith("sdk-")
        or repo_name == "elxr"
        or repo_name.startswith("elxr-")
        or (include_neat_sdk_alias and _is_neat_sdk_alias_repo_name(repo_name))
    )


def is_neat_sdk_image(image: str) -> bool:
    """
    Return True for Neat SDK images.

    Current Neat SDK images are published as ghcr.io/sima-neat/sdk*. Legacy
    ghcr.io/sima-neat/elxr* images are kept compatible and classified as Neat.
    Local development builds may also be tagged as bare sdk* or neat-sdk*
    repositories.
    """
    repo = _image_repository(image)
    if _is_local_neat_sdk_repo(repo):
        return True

    if not _is_sima_neat_repo(repo):
        return False

    repo_name = repo.rsplit("/", 1)[-1]
    return _is_neat_repo_name(repo_name, include_neat_sdk_alias=True)


def _canonical_sdk_image_name(image: str) -> str:
    if is_neat_sdk_image(image):
        return "neat"

    repo = _image_repository(image)
    if not repo:
        return ""

    base_name = repo.rsplit("/", 1)[-1]
    return IMAGE_ALIASES.get(base_name, base_name)


def _is_sanitized_neat_sdk_container_name(container_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "-", (container_name or "").lower()).strip("-")
    return (
        normalized == "ghcr-io-sima-neat-sdk"
        or normalized.startswith("ghcr-io-sima-neat-sdk-")
        or normalized == "ghcr-io-sima-neat-elxr"
        or normalized.startswith("ghcr-io-sima-neat-elxr-")
        or normalized == "neat-sdk"
        or normalized.startswith("neat-sdk-")
    )


def _container_sdk_key(container_name: str, container_image: str) -> str:
    image_key = _canonical_sdk_image_name(container_image)
    if image_key in IMAGE_CONFIG:
        return image_key

    name_ref = (container_name or "").lower()
    if _is_sanitized_neat_sdk_container_name(name_ref):
        return "neat"

    for key in IMAGE_NAMES:
        if key != "neat" and key in name_ref:
            return key
    return ""


def container_matches_sdk_keyword(container, keyword: str) -> bool:
    if not keyword:
        return True

    if isinstance(container, dict):
        name = container.get("Names") or container.get("Name") or container.get("name") or ""
        image = container.get("Image") or container.get("image") or ""
    else:
        name = str(container)
        image = ""

    normalized_keyword = SDK_KEYWORD_ALIASES.get(keyword.lower(), keyword.lower())
    normalized_keyword = IMAGE_ALIASES.get(normalized_keyword, normalized_keyword)
    sdk_key = _container_sdk_key(name, image)
    if sdk_key == normalized_keyword:
        return True
    if sdk_key:
        return False

    if normalized_keyword == "neat":
        return _is_sanitized_neat_sdk_container_name(name)

    return normalized_keyword in name.lower() or normalized_keyword in image.lower()


def get_local_sima_images():
    """Return a list of local SDK images across supported registries (JFrog/ECR/GHCR/local)."""
    try:
        output = subprocess.check_output(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            text=True
        ).strip()
    except subprocess.CalledProcessError:
        return []

    keywords = ("sima-docker", "vdp-cli")
    images = []
    for line in output.splitlines():
        image_ref = line.strip()
        if not image_ref:
            continue

        # Keep legacy behavior for known registry prefixes.
        if any(key in image_ref for key in keywords):
            images.append(image_ref)
            continue

        # Also allow images from other registries (e.g., GHCR) as long as the
        # final repository segment maps to a known SDK image name, or contains
        # an SDK token (e.g., "elxr-sdk").
        canonical = _canonical_sdk_image_name(image_ref)
        if canonical in IMAGE_NAMES or any(name in canonical for name in IMAGE_NAMES):
            images.append(image_ref)

    return sorted(set(images))

def _print_help_box():
    """Display a styled help box using Rich Panel."""
    message = (
        "[bold cyan]How to use this menu:[/bold cyan]\n\n"
        "• Use [green]↑[/green]/[green]↓[/green] arrows then [green]Space[/green] to select one or more images.\n"
        "• Press [bold]Enter[/bold] to confirm your selection.\n"
        "• These are local Docker SDK images detected across supported registries.\n"
        "• Containers based on these images will be started automatically.\n"
        "• Press [yellow]CTRL+C[/yellow] to cancel anytime."
    )

    console.print(
        Panel(
            message,
            title="📘 SiMa.ai SDK Image Selection",
            border_style="green",
            expand=False,
        )
    )

def prompt_image_selection(images, noninteractive=False):
    """Prompt the user to select one or more SDK images to start, supporting multi-version."""
    if not images:
        console.print("[red]❌ No SiMa.ai SDK images found locally.[/red]")
        sys.exit(1)

    if not noninteractive:
        _print_help_box()

    # ─────────────────────────────────────────────
    # 1. Detect SDK versions by image tag
    # ─────────────────────────────────────────────
    version_map = defaultdict(list)
    for img in images:
        parts = img.split(":")
        version = parts[-1] if len(parts) > 1 else "unknown"
        version_map[version].append(img)

    versions = sorted(version_map.keys())

    # ─────────────────────────────────────────────
    # 2. Version selection
    # ─────────────────────────────────────────────
    if len(versions) > 1:
        if noninteractive:
            # Non-interactive mode: select all versions
            console.print(
                "[dim]Non-interactive mode: multiple SDK versions detected — selecting all.[/dim]"
            )
            images = [img for imgs in version_map.values() for img in imgs]
        else:
            version_choices = [{"name": "All versions", "value": "__all_versions__"}] + [
                {"name": v, "value": v} for v in versions
            ]
            selected_version = (
                inquirer.fuzzy(
                    message="Multiple SDK versions detected — select one to start:",
                    choices=version_choices,
                    qmark="🔢",
                ).execute()
            )
            if selected_version == "__all_versions__":
                images = [img for imgs in version_map.values() for img in imgs]
                console.print("[cyan]ℹ️  Showing images across all detected versions.[/cyan]")
            else:
                images = version_map[selected_version]
                console.print(
                    f"[cyan]ℹ️  Showing images for version [bold]{selected_version}[/bold][/cyan]"
                )
    else:
        console.print(f"[dim]Single SDK version detected: {versions[0]}[/dim]")

    # ─────────────────────────────────────────────
    # 3. Image selection
    # ─────────────────────────────────────────────
    if noninteractive:
        console.print("[dim]Non-interactive mode: selecting all SDK images.[/dim]")
        return images

    choices = (
        [{"name": "✅ Select All", "value": "__all__", "enabled": True}]
        + [{"name": sanitize_container_name(img), "value": img} for img in images]
        + [{"name": "🚫 Cancel", "value": "__cancel__"}]
    )

    selected = (
        inquirer.checkbox(
            message="Select SDK images to start:",
            choices=choices,
            instruction="(Space to toggle, Enter to confirm)",
            qmark="📦",
            enabled_symbol="[x]",
            disabled_symbol="[ ]",
            pointer="❯",
            transformer=lambda res: (
                f"[bold green]{len(res)} selected[/bold green]"
                if res else "[dim]None selected[/dim]"
            ),
        ).execute()
    )

    # ─────────────────────────────────────────────
    # 4. Handle user actions
    # ─────────────────────────────────────────────
    if "__cancel__" in selected or not selected:
        console.print("[yellow]Exiting — no images selected.[/yellow]")
        sys.exit(-1)

    if "__all__" in selected:
        return images

    return [s for s in selected if s not in {"__all__", "__cancel__"}]

def confirm_to_remove_exiting_container(image, yes_to_all=False):
    """Start a container for the given SDK image."""
    container_name = image.split("/")[-1].replace(":", "_")

    # 🔍 Check if container already exists
    existing = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        text=True, capture_output=True
    ).stdout.splitlines()

    if container_name in existing:
        console.print(f"[yellow]⚠️  Container '{container_name}' already exists.[/yellow]")
        if yes_to_all:
            console.print(f"[cyan]Auto-removing '{container_name}' (yes_to_all=True).[/cyan]")
            subprocess.run(["docker", "rm", "-f", container_name], check=False)
            console.print(f"✅ Removed old container '{container_name}'.", style="green")
        else:
            resp = input("🗑️  Remove and recreate it? [Y/n]: ").strip().lower()
            if resp in {"", "y", "yes"}:
                subprocess.run(["docker", "rm", "-f", container_name], check=False)
                console.print(f"✅ Removed old container '{container_name}'.", style="green")
            else:
                console.print(f"⏩ Skipping existing '{container_name}'.", style="yellow")
                return container_name


def sanitize_container_name(image: str) -> str:
    """
    Convert an image name (e.g. 'sima-docker/abc:def.gpu') into a valid Docker container name.
    """
    name = image

    # Normalize known registry prefixes
    name = re.sub(r"\b(sima-docker)\b", "", name)
    name = re.sub(r"\bartifacts\.eng\.sima\.ai\b", "jfrog", name)
    # remove any AWS ECR registry domain like "123456789012.dkr.ecr.us-west-1.amazonaws.com"
    name = re.sub(r"\b\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com\b", "", name)

    # Replace separators and invalid characters
    name = name.replace("/", "-").replace(":", "-").lower()
    name = re.sub(r"[^a-z0-9_.-]", "_", name)

    # Clean up leading/trailing non-alphanumeric chars
    name = name.strip("._-")
    name = name.replace("--", '-')

    # Ensure it starts with an alphanumeric
    if not name or not name[0].isalnum():
        name = f"c_{name}"

    # Docker name limit: 128 chars
    return name[:128]


def sanitize_container_hostname(container_name: str) -> str:
    """
    Convert a Docker container name into a valid Linux hostname.

    Docker container names may be up to 128 characters, but Linux hostnames
    are limited to 63 characters. Keep long names deterministic with a hash
    suffix so SHA-tagged CI images still start cleanly.
    """
    hostname = container_name.lower().replace("_", "-")
    hostname = re.sub(r"[^a-z0-9-]", "-", hostname)
    hostname = re.sub(r"-+", "-", hostname).strip("-")
    if not hostname:
        hostname = "sima-sdk"
    if len(hostname) <= 63:
        return hostname

    suffix = hashlib.sha1(container_name.encode("utf-8")).hexdigest()[:12]
    prefix = hostname[: 63 - len(suffix) - 1].rstrip("-")
    if not prefix:
        prefix = "sima-sdk"
    return f"{prefix}-{suffix}"

def is_neat_elxr_image(image: str) -> bool:
    """
    Backward-compatible alias for Neat SDK image detection.
    """
    return is_neat_sdk_image(image)

def extract_short_name(image: str) -> str:
    """
    Extracts the short SDK name from a full image string.
    Examples:
        sima-docker/modelsdk:1.8         → modelsdk
        artifacts.eng.sima.ai/elxr:1.9   → elxr
        512422982161.dkr.ecr.../yocto:v2 → yocto
    """
    return _canonical_sdk_image_name(image)

def detect_current_user():
    """
    Return (login_name, uid, gid) in a cross-platform way.

    On Windows, UID/GID are set to 0 since os.getuid/getgid are unavailable.
    """
    # ────────────────────────────────────────
    # 1. Determine username
    # ────────────────────────────────────────
    try:
        login_name = getpass.getuser()
    except Exception:
        # Fallbacks for rare environments
        login_name = (
            os.getenv("USERNAME")
            or os.getenv("USER")
            or os.getenv("LOGNAME")
            or "unknown"
        )

    # ────────────────────────────────────────
    # 2. Determine UID / GID
    # ────────────────────────────────────────
    if platform.system() == "Windows":
        uid, gid = 0, 0  # Not applicable on Windows
    else:
        try:
            uid = os.getuid()
            gid = os.getgid()
        except Exception:
            uid, gid = 0, 0

    return login_name, uid, gid

def select_containers(containers, single_select=False, yes_to_all=False):
    """
    Prompt user to select one or more containers by name.
    Supports both single and multiple selection modes.

    Args:
        containers (list[dict|str]): List of Docker containers.
        single_select (bool): If True, use single-select (like radio buttons).
                              If False, allow multiple selection.
    Returns:
        list[str] | str: List of selected container names (or a single name if single_select=True).
    """
    if not containers:
        print("⚠️  No running containers found.")
        return [] if not single_select else None

    # Extract names from dicts or strings
    names = []
    for c in containers:
        if isinstance(c, dict):
            name = c.get("Names") or c.get("Name") or c.get("name")
            if name:
                names.append(name)
        elif isinstance(c, str):
            names.append(c)

    if not names:
        print("⚠️  No valid container names found.")
        return [] if not single_select else None

    # Respect the requested mode; force single-select only when there is
    # exactly one available option.
    single_select = bool(single_select or len(names) == 1)
    if single_select and yes_to_all:
        return names[0] if names else None

    # Build menu choices
    choices = [{"name": n, "value": n} for n in names]

    # Single select mode
    if single_select:
        selected = inquirer.fuzzy(
            message="Select a container:",
            choices=choices,
            qmark="🐳",
            instruction="Use ↑/↓ to navigate, Enter to confirm",
        ).execute()
        return selected  # returns a single name string

    # Multi-select mode
    selected = inquirer.checkbox(
        message="Select containers:",
        choices=choices,
        qmark="🐳",
        instruction="(Space to select, Enter to confirm)",
    ).execute()

    return selected  # returns a list of names
