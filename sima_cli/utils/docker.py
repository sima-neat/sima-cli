#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil

from sima_cli.utils.env import get_environment_type

def is_docker_running():
    """Return True if Docker daemon is active and responsive."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def print_manual_start_instructions(os_name):
    """Print manual startup hints for each OS."""
    print("\n🧭 Manual startup instructions:")
    if os_name == "Linux":
        print("  → Try running: sudo systemctl start docker")
    elif os_name == "Darwin":
        print("  → Open Docker Desktop from Applications folder or run: open -a Docker")
    elif os_name == "Windows":
        print("  → Start Docker Desktop manually from Start Menu.")
    print()


def confirm(prompt, default_yes=True):
    """Utility: prompt user for Y/n input."""
    suffix = "[Y/n]" if default_yes else "[y/N]"
    response = input(f"{prompt} {suffix}: ").strip().lower()
    if not response:
        return default_yes
    return response in {"y", "yes"}


def find_docker_desktop_exe():
    """Try to locate Docker Desktop executable in common locations."""
    common_paths = [
        r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        r"C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Docker\Docker Desktop.exe"),
    ]

    # 1️⃣ Check common paths
    for path in common_paths:
        if os.path.exists(path):
            return path

    # 2️⃣ Fallback: use `where` to search in PATH
    try:
        result = subprocess.run(
            ["where", "Docker Desktop.exe"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode == 0:
            exe_path = result.stdout.strip().splitlines()[0]
            if os.path.exists(exe_path):
                return exe_path
    except Exception:
        pass

    # 3️⃣ Not found
    return None

def find_docker_app():
    """Try to locate Docker.app in common locations on macOS."""
    paths = [
        "/Applications/Docker.app",
        os.path.expanduser("~/Applications/Docker.app"),
    ]
    for path in paths:
        if os.path.exists(path):
            return path

    # fallback: if CLI exists, user might be running engine without Desktop
    if shutil.which("docker"):
        return None  # CLI present, no GUI app required

    return None

def start_docker_macos(use_sudo=False):
    """
    Start Docker Desktop on macOS (system-wide or user-level).
    Wait up to ~2 minutes for the daemon to become ready.
    """
    docker_app = find_docker_app()

    # 1️⃣ Locate Docker Desktop or CLI
    if docker_app:
        print(f"🧩 Found Docker Desktop at: {docker_app}")
        open_target = docker_app
    else:
        print("⚠️ Docker Desktop app not found — checking if CLI is installed...")
        if shutil.which("docker"):
            print("✅ Docker CLI found in PATH — skipping Desktop launch.")
            return True
        print("❌ Docker not found. Please install Docker Desktop for macOS:")
        print("   https://www.docker.com/products/docker-desktop/")
        sys.exit(1)

    # 2️⃣ Try to launch Docker Desktop
    start_cmd = ["open", "-a", open_target]
    if use_sudo:
        start_cmd.insert(0, "sudo")

    print("🚀 Launching Docker Desktop for macOS...")
    try:
        subprocess.Popen(start_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"❌ Failed to start Docker Desktop: {e}")
        sys.exit(1)

    # 3️⃣ Wait for the Docker daemon to become ready
    print("⏳ Waiting for Docker daemon to initialize (this may take 1–2 minutes)...")
    for i in range(12):  # 12 * 10s = 120 seconds
        time.sleep(10)
        try:
            result = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                print("✅ Docker daemon started successfully.")
                return True
        except Exception:
            pass
        print(f"⌛ Checking again ({i+1}/12)...")

    print("❌ Docker daemon did not start within 2 minutes.")
    print("Please open Docker Desktop manually and try again.")
    sys.exit(1)

def check_and_start_docker():
    """
    Check if Docker daemon is running and start it if necessary.

    Automatically detects OS:
    - Linux: systemctl start/restart docker.service
    - macOS: open -a Docker
    - Windows: launches Docker Desktop.exe
    """
    platform, platform_os = get_environment_type()
    if platform_os == "unknown":
        print("❌ Unsupported or unknown platform.")
        sys.exit(1)

    print(f"🖥️  Detected platform: {platform_os.capitalize()}")

    # Step 1: Check current status
    if is_docker_running():
        print("✅ Docker daemon is running.")
        return True

    print("⚠️  Docker daemon is not running.")
    if not confirm("Would you like to start it now?"):
        print("❌ Please start Docker manually and re-run this script.")
        print_manual_start_instructions(platform_os)
        sys.exit(1)

    use_sudo = False
    if platform_os in {"linux", "macos"}:
        use_sudo = confirm("🔒 Grant sudo access to start Docker?", default_yes=True)

    print(f"⏳ Attempting to start Docker on {platform_os} ...")

    if platform_os == "linux":
        docker_cmd = ["systemctl", "start", "docker"]
        if use_sudo:
            docker_cmd.insert(0, "sudo")

        for attempt in range(1, 4):
            subprocess.run(docker_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(5)
            if is_docker_running():
                print("✅ Docker daemon started successfully.")
                return True
            print(f"⌛ Retrying... ({attempt}/3)")

        print("❌ Failed to start Docker service after 3 attempts.")
        print_manual_start_instructions(platform_os)
        sys.exit(1)

    elif platform_os == "windows":
        docker_exe = find_docker_desktop_exe()
        if not docker_exe:
            print("❌ Docker Desktop executable not found.")
            print("Please install Docker Desktop or start it manually.")
            sys.exit(1)

        print("🚀 Launching Docker Desktop...")
        subprocess.Popen(["cmd.exe", "/c", "start", "", docker_exe])
        for i in range(6):
            time.sleep(10)
            if is_docker_running():
                print("✅ Docker daemon started successfully.")
                return True
            print(f"⌛ Waiting for Docker Desktop to initialize ({i+1}/6)...")

        print("❌ Docker daemon did not start within 1 minute.")
        print_manual_start_instructions(platform_os)
        sys.exit(1)

    elif platform_os == "mac":
        start_docker_macos()

    return False

# ───────────────────────────────────────────────
# Entrypoint
# ───────────────────────────────────────────────
if __name__ == "__main__":
    try:
        check_and_start_docker()
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user.")
        sys.exit(130)
