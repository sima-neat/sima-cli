import os
import subprocess
import platform
import shutil
import re
from typing import Tuple, Optional

# Utility functions to determine the environment:
# - Whether we are running on a SiMa board
# - Or from a PCIe host (e.g., a developer workstation)

def is_sima_board() -> bool:
    """
    Detect if running on a SiMa board.

    This is done by checking for the existence of known build info files
    (/etc/build or /etc/buildinfo) and looking for specific identifiers like 
    SIMA_BUILD_VERSION and MACHINE.

    Returns:
        bool: True if running on a SiMa board, False otherwise.
    """
    build_file_paths = ["/etc/build", "/etc/buildinfo"]

    for path in build_file_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    content = f.read()
                    if "SIMA_BUILD_VERSION" in content and "MACHINE" in content:
                        return True
            except Exception:
                continue

    return False

def get_sima_build_version() -> Tuple[Optional[str], Optional[str]]:
    """
    Retrieve the current SiMa build version from /etc/build or /etc/buildinfo.

    It searches for 'SIMA_BUILD_VERSION=' and extracts:
      - core_version: the semantic version (e.g., '2.0.0')
      - full_version: the complete build string (e.g., '2.0.0_develop_B1932')

    Returns:
        tuple: (core_version, full_version)
               If not found, returns (None, None)
    """
    build_file_paths = ["/etc/build", "/etc/buildinfo"]
    version_pattern = re.compile(r"SIMA_BUILD_VERSION\s*=\s*([\w\.\-\+]+)")

    for path in build_file_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    for line in f:
                        match = version_pattern.search(line)
                        if match:
                            full_version = match.group(1)
                            # Extract only major.minor.patch portion
                            core_match = re.match(r"(\d+\.\d+\.\d+)", full_version)
                            core_version = core_match.group(1) if core_match else None
                            return core_version, full_version
            except Exception:
                continue

    return None, None

def is_pcie_host() -> bool:
    """
    Detect if running from a PCIe host (typically a Linux or macOS dev machine).

    This assumes a PCIe host is not a SiMa board and is running on a standard Linux platform.

    Returns:
        bool: True if likely a PCIe host, False otherwise.
    """
    import platform
    return not is_sima_board() and platform.system() in ["Linux"]

def get_sima_board_type() -> str:
    """
    If running on a SiMa board, extract the board type from the MACHINE field
    in /etc/build or /etc/buildinfo.

    Returns:
        str: The board type (e.g., "modalix", "davinci"), or an empty string if not found or not a SiMa board.
    """
    build_file_paths = ["/etc/build", "/etc/buildinfo"]

    for path in build_file_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("MACHINE"):
                            # Format: MACHINE = modalix
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                return parts[1].strip()
            except Exception:
                continue

    return ""

def is_devkit_running_elxr() -> bool:
    """
    Check if the system is a SiMa devkit and is running ELXR software.

    Conditions:
      - Must be identified as a SiMa board via is_sima_board()
      - /etc/buildinfo exists and contains "elxr" (case-insensitive)

    Returns:
        bool: True if SiMa devkit with ELXR software, False otherwise.
    """
    if not is_sima_board():
        return False

    buildinfo_path = "/etc/buildinfo"
    if not os.path.exists(buildinfo_path):
        return False

    try:
        with open(buildinfo_path, "r") as f:
            content = f.read().lower()
            return "elxr" in content
    except Exception:
        return False

def is_modalix_devkit() -> bool:
    """
    Determines whether the current system is a Modalix DevKit (SoM)
    by checking if "Modalix SoM" is present in /proc/device-tree/model.

    Returns:
        bool: True if running on a Modalix DevKit (SoM), False otherwise.
    """
    model_path = "/proc/device-tree/model"
    if not os.path.exists(model_path):
        return False

    try:
        with open(model_path, "r") as f:
            line = f.readline().strip()
            return "Modalix SoM" in line
    except Exception:
        return False
    

def get_exact_devkit_type() -> str:
    """
    Extracts the exact devkit type from /proc/device-tree/model.

    Example mappings:
        "SiMa.ai Modalix SoM Board"       -> "modalix-som"
        "SiMa.ai Modalix DVT Board"       -> "modalix-dvt"
        "SiMa.ai DaVinci Half-Height..."  -> "davinci-half-height-half-length"

    Returns:
        str: Normalized devkit type (lowercase, spaces -> "-"),
             or an empty string if not found or unavailable.
    """
    model_path = "/proc/device-tree/model"
    if not os.path.exists(model_path):
        return ""

    try:
        with open(model_path, "r") as f:
            line = f.readline().strip()
            # Remove "SiMa.ai " prefix if present
            if line.startswith("SiMa.ai "):
                line = line[len("SiMa.ai "):]
            # Remove trailing "Board"
            line = line.replace(" Board", "")
            # Normalize
            return line.lower().replace(" ", "-")
    except Exception:
        return ""

    return ""

def is_board_running_full_image() -> bool:
    """
    Heuristic: return True if the 'full' image appears to be installed.
    We detect this by checking for the NVMe CLI ('nvme'), which is bundled
    with the full image but not the headless image.

    Returns:
        bool: True if nvme binary is found, else False.
    """
    try:
        # Ensure sbin dirs are in search path (non-root shells often miss these)
        search_path = os.environ.get("PATH", "")
        sbin = "/usr/sbin:/sbin"
        if search_path:
            search_path = f"{search_path}:{sbin}"
        else:
            search_path = sbin

        nvme_path = shutil.which("nvme", path=search_path)
        if nvme_path and os.path.exists(nvme_path):
            return True

        # Fallback: direct checks (in case PATH is unusual)
        for p in ("/usr/sbin/nvme", "/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return True

        return False
    except Exception:
        return False

def is_palette_sdk() -> bool:
    """
    Check if the environment is running inside the Palette SDK container.

    This is detected by checking for the /etc/sdk-release file and verifying
    it contains the string 'Palette_SDK'.

    Returns:
        bool: True if running in Palette SDK, False otherwise.
    """
    sdk_release_path = "/etc/sdk-release"
    if not os.path.exists(sdk_release_path):
        return False

    try:
        with open(sdk_release_path, "r") as f:
            content = f.read()
            return "Palette_SDK" in content or "sima" in content
    except Exception:
        return False

def get_environment_type() -> Tuple[str, str]:
    """
    Return the environment type and subtype as a tuple.

    Returns:
        tuple:
            - env_type (str): "board", "sdk", or "host"
            - env_subtype (str): board type (e.g., "modalix"), "palette", or host OS (e.g., "mac", "linux", "windows")
    """
    if is_palette_sdk():
        return "sdk", "palette"

    if is_sima_board():
        board_type = get_sima_board_type()
        return "board", board_type or "unknown"

    import platform
    system = platform.system().lower()
    if system == "darwin":
        return "host", "mac"
    elif system == "linux":
        return "host", "linux"
    elif system == "windows":
        return "host", "windows"

    return "host", "unknown"

def check_pcie_card_installation() -> Tuple[bool, str]:
    """
    Check whether the PCIe SiMa card is properly installed on a supported Linux host.

    Returns:
        tuple:
            - success (bool): True if all checks pass, False otherwise.
            - message (str): Summary of results or error message.
    """
    # Platform check
    if platform.system().lower() != "linux":
        return False, "❌ This check is only supported on Linux hosts."

    if is_sima_board():
        return False, "❌ This check is not applicable when running on a SiMa board."

    if is_palette_sdk():
        return False, "❌ This check is not applicable inside the Palette SDK container."

    try:
        # Check GStreamer plugin (new name + legacy backward compatibility)
        gst_candidates = ["simaaipciehost", "pciehost"]
        detected_gst_plugin = None
        for plugin_name in gst_candidates:
            gst_result = subprocess.run(
                ["gst-inspect-1.0", plugin_name],
                capture_output=True, text=True
            )
            if gst_result.returncode == 0:
                detected_gst_plugin = plugin_name
                break
        if not detected_gst_plugin:
            return False, "❌ GStreamer plugin not found: expected 'simaaipciehost' (or legacy 'pciehost')."

        # Check kernel module (new name + legacy backward compatibility)
        module_candidates = ["simaai_mla_drv", "sima_mla_drv"]
        detected_module = None
        for module_name in module_candidates:
            modinfo_result = subprocess.run(
                ["modinfo", module_name],
                capture_output=True, text=True
            )
            if modinfo_result.returncode == 0:
                detected_module = module_name
                break
        if not detected_module:
            return False, "❌ Kernel module not found: expected 'simaai_mla_drv' (or legacy 'sima_mla_drv')."

        # Check PCI device presence
        lspci_result = subprocess.run(
            ["lspci", "-vd", "1f06:abcd"],
            capture_output=True, text=True
        )
        if lspci_result.returncode != 0 or "Device 1f06:abcd" not in lspci_result.stdout:
            return False, "❌ PCIe SiMa card not detected."

        return True, "✅ PCIe SiMa card is properly installed and recognized."

    except FileNotFoundError as e:
        return False, f"❌ Required system tool not found: {e.filename}"

    except Exception as e:
        return False, f"❌ Unexpected error: {str(e)}"


if __name__ == "__main__":
    env_type, env_subtype = get_environment_type()
    print(f"Environment Type: {env_type}")
    print(f"Environment Subtype: {env_subtype}")
