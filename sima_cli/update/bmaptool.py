import subprocess
import sys
import os
import urllib.request
import tarfile
import tempfile
import json
import shutil

def run_command(command, check=True, shell=False, cwd=None):
    """Run a shell command and return its output or raise an exception if it fails."""
    try:
        result = subprocess.run(
            command,
            check=check,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd
        )
        return result.stdout.strip(), result.stderr.strip()
    except subprocess.CalledProcessError as e:
        if check:
            print(f"Error running command {command}: {e.stderr}")
            raise
        return "", str(e)

def is_bmaptool_installed():
    """Check if bmaptool is installed and callable from the shell."""
    try:
        result = subprocess.run(
            ["bmaptool", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"✅ bmaptool is installed: {result.stdout.strip()}")
            return True
        else:
            print(f"⚠️ bmaptool returned non-zero exit: {result.stderr.strip()}")
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"❌ bmaptool is not installed or not in PATH: {e}")
        return False

def clone_bmaptool_repo(temp_dir, branch_or_tag="main"):
    """Clone the bmaptool repository to a temporary directory."""
    repo_url = "https://github.com/yoctoproject/bmaptool.git"
    print(f"Cloning bmaptool from {repo_url} (branch/tag: {branch_or_tag})...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch_or_tag, repo_url, temp_dir],
            check=True,
            capture_output=True,
            text=True
        )
        print(f"Cloned bmaptool to {temp_dir}")
        return temp_dir
    except subprocess.CalledProcessError as e:
        print(f"Failed to clone repository: {e.stderr}")
        sys.exit(1)

def get_python_and_pip_bins():
    """Get the Python and pip binaries from the active virtual environment or system."""
    if "VIRTUAL_ENV" in os.environ:
        venv_dir = os.environ["VIRTUAL_ENV"]
        print(f"Using existing virtual environment: {venv_dir}")
        if sys.platform == "win32":
            python_bin = os.path.join(venv_dir, "Scripts", "python.exe")
            pip_bin = os.path.join(venv_dir, "Scripts", "pip.exe")
        else:
            python_bin = os.path.join(venv_dir, "bin", "python3")
            pip_bin = os.path.join(venv_dir, "bin", "pip3")
    else:
        print("No virtual environment detected; using system Python.")
        python_bin = shutil.which("python3") or sys.executable
        pip_bin = shutil.which("pip3") or "pip3"
    return python_bin, pip_bin

def install_bmaptool(temp_dir):
    """Install bmaptool from the cloned repository."""
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", temp_dir],
            check=True,
            capture_output=True,
            text=True
        )
        print("Installed bmaptool successfully")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install bmaptool: {e.stderr}")
        sys.exit(1)

def verify_installation(python_bin):
    """Verify bmaptool installation by running python -m bmaptool --version."""
    stdout, stderr = run_command([python_bin, "-m", "bmaptool", "--version"])
    if stdout:
        print(f"bmaptool installation verified: {stdout}")
    else:
        print(f"Verification failed: {stderr}")
        sys.exit(1)

def check_bmap_and_install():
    # Get Python and pip binaries from the active virtual environment or system
    python_bin, _ = get_python_and_pip_bins()
    
    # Check if bmaptool is already installed
    if is_bmaptool_installed():
        return
    
    # Create a temporary directory
    temp_dir = "bmaptool_temp"
    with tempfile.TemporaryDirectory() as temp_dir:            
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)
        
        clone_bmaptool_repo(temp_dir, branch_or_tag="main")  # Or specify a tag like "v3.8"
        install_bmaptool(temp_dir)
        
        # Verify installation
        verify_installation(python_bin)
        
        # Provide usage instructions
        if "VIRTUAL_ENV" in os.environ:
            print(f"\nbmaptool is installed in the virtual environment at {os.environ['VIRTUAL_ENV']}")
            print("The virtual environment is already active.")
            print("Run 'bmaptool --help' to see available commands.")
        else:
            print("\nbmaptool is installed in the system Python environment.")
            print("Run 'bmaptool --help' to see available commands.")

if __name__ == "__main__":
    check_bmap_and_install()
