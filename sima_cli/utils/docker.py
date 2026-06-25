#!/usr/bin/env python3
import os
import shlex
import sys
import subprocess
import time
import shutil
import getpass

from sima_cli.utils.env import get_environment_type

_REEXEC_ENV_FLAG = "SIMA_CLI_DOCKER_GROUP_REEXEC"


def _docker_info_probe():
    """Run `docker info` and return (returncode, combined_stdout_stderr).

    returncode is None when the command itself could not be executed.
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except FileNotFoundError:
        return None, "docker: command not found"
    except subprocess.SubprocessError as e:
        return None, str(e)


def _is_socket_permission_denied(output: str) -> bool:
    """True if `docker info` failed because the socket is inaccessible to this user."""
    lowered = (output or "").lower()
    if "permission denied" not in lowered:
        return False
    return "docker.sock" in lowered or "docker daemon socket" in lowered


def is_docker_running():
    """Return True if Docker daemon is active and responsive to this user."""
    returncode, _ = _docker_info_probe()
    return returncode == 0


def _current_user() -> str:
    return os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()


def _user_in_docker_group(user: str) -> bool:
    """Check persisted membership of the 'docker' group via getent."""
    try:
        result = subprocess.run(
            ["getent", "group", "docker"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    parts = result.stdout.strip().split(":")
    if len(parts) < 4:
        return False
    members = [m.strip() for m in parts[3].split(",") if m.strip()]
    return user in members


def _active_user_in_docker_group() -> bool:
    """Check whether this running process has the docker group active."""
    try:
        import grp

        group_ids = set(os.getgroups())
        group_ids.add(os.getgid())
        group_ids.add(os.getegid())
        active_group_names = {
            grp.getgrgid(group_id).gr_name
            for group_id in group_ids
        }
    except (ImportError, KeyError, OSError):
        return False
    return "docker" in active_group_names


def _try_reexec_with_docker_group() -> None:
    """Best-effort re-run under `sg docker` when it is known to work.

    Some systems allow `sg docker -c ...` to activate a newly-added docker
    group immediately, while others reject it or prompt for group auth. Probe
    first so failures can fall back to deterministic user instructions.
    """
    if os.environ.get(_REEXEC_ENV_FLAG) == "1":
        return
    if not shutil.which("sg"):
        return

    probe_env = os.environ.copy()
    probe_env[_REEXEC_ENV_FLAG] = "1"
    try:
        probe = subprocess.run(
            ["sg", "docker", "-c", "docker info >/dev/null 2>&1"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=probe_env,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if probe.returncode != 0:
        return

    cmdline = shlex.join(sys.argv)
    print("🔁 Activating 'docker' group for this command...")
    try:
        os.execvpe("sg", ["sg", "docker", "-c", cmdline], probe_env)
    except OSError:
        return


def _print_docker_group_refresh_instructions() -> None:
    command = shlex.join(sys.argv)
    print("ℹ️  Docker group membership is configured, but this shell session cannot use it yet.")
    print("   Start a shell with the updated group membership, then re-run this command:")
    print("   newgrp docker")
    print(f"   {command}")
    print("   If that still does not work, log out and back in before retrying.")


def _handle_docker_socket_permission_denied() -> bool:
    """Linux: daemon is up but the current user can't access the socket.

    Offers to add the user to the 'docker' group via sudo when needed, then
    exits with clear instructions because newly-added group membership is not
    available to the current shell/process on many Linux systems.
    """
    user = _current_user()
    print("⚠️  Docker daemon is running, but this user lacks permission to access it.")
    print(f"   (Cannot access /var/run/docker.sock as '{user}'.)")

    if _user_in_docker_group(user):
        if _active_user_in_docker_group():
            print(f"ℹ️  User '{user}' already has the 'docker' group active in this session.")
            print("   The socket is still not accessible, so this is likely a Docker daemon")
            print("   or /var/run/docker.sock ownership issue rather than group activation.")
            print("   Check:  ls -l /var/run/docker.sock")
            print("   Check:  sudo systemctl status docker")
            sys.exit(1)
        print(f"ℹ️  User '{user}' is already a member of the 'docker' group, but this")
        print("   shell session has not picked up that membership yet.")
        _try_reexec_with_docker_group()
        _print_docker_group_refresh_instructions()
        sys.exit(1)

    print(f"ℹ️  User '{user}' is not a member of the 'docker' group.")
    if not confirm(f"Add '{user}' to the 'docker' group now? (requires sudo)", default_yes=True):
        print("❌ Cannot proceed without docker socket access.")
        print(f"   Add the user manually:  sudo usermod -aG docker {user}")
        print("   Then run 'newgrp docker' (or log out and back in) and retry.")
        sys.exit(1)

    cmd = ["sudo", "usermod", "-aG", "docker", user]
    print(f"🔧 Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("❌ Failed to add user to the 'docker' group.")
        print(f"   Try manually:  sudo usermod -aG docker {user}")
        sys.exit(1)

    print(f"✅ Added '{user}' to the 'docker' group.")
    _try_reexec_with_docker_group()
    _print_docker_group_refresh_instructions()
    sys.exit(1)


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
    returncode, probe_output = _docker_info_probe()
    if returncode == 0:
        print("✅ Docker daemon is running.")
        return True

    # Distinguish "daemon up but socket not accessible to this user" (Linux)
    # from "daemon down". The former is fixed by docker-group membership,
    # not by `systemctl start docker`.
    if platform_os == "linux" and _is_socket_permission_denied(probe_output):
        _handle_docker_socket_permission_denied()
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

        last_failure = None
        for attempt in range(1, 4):
            # Let stdout/stderr stream to the terminal so the sudo prompt is
            # visible AND so systemctl's actual error (e.g. masked unit,
            # missing package) is shown to the user. Capture exit code so we
            # only wait/retry when it makes sense to.
            result = subprocess.run(docker_cmd)
            if result.returncode == 0:
                # Daemon may take a moment to publish the socket; poll briefly.
                for _ in range(10):
                    time.sleep(1)
                    returncode, probe_output = _docker_info_probe()
                    if returncode == 0:
                        print("✅ Docker daemon started successfully.")
                        return True
                    # Daemon is up but THIS user can't talk to the socket —
                    # systemctl can't fix that. Route to the group flow.
                    if _is_socket_permission_denied(probe_output):
                        _handle_docker_socket_permission_denied()
                        return True
                last_failure = "systemctl returned 0 but `docker info` is still failing."
            else:
                last_failure = f"systemctl exited with code {result.returncode}."
                # Non-zero from systemctl rarely fixes itself by retrying
                # (masked unit, package not installed, auth denied, …).
                # Show what the user can act on and stop early.
                print(f"⚠️  {last_failure}")
                break
            print(f"⌛ Retrying... ({attempt}/3)")

        print("❌ Failed to start Docker service.")
        if last_failure:
            print(f"   Reason: {last_failure}")
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
