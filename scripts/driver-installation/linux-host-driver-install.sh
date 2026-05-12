#!/bin/bash

# ==============================================================================
# Sima Host Driver Installer & Validator
# ==============================================================================

# Exit immediately if a command exits with a non-zero status
set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------

print_header() {
    echo -e "\n${YELLOW}=== $1 ===${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "Please run this script with sudo or as root."
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# 1. Validation Logic (Replaces _check_driver_installation)
# ------------------------------------------------------------------------------

validate_installation() {
    print_header "Driver Installation Validation"

    local fmt="%-20s | %-10s | %s\n"
    local div="----------------------------------------------------------------"

    printf "$fmt" "Component" "Status" "Details"
    echo "$div"

    # --- Check 1: GStreamer Plugin ---
    local gst_status="FAIL"
    local gst_msg=""

    if ! command -v gst-inspect-1.0 &> /dev/null; then
        gst_msg="❌ gst-inspect-1.0 missing (install gstreamer1.0-tools)"
    elif gst-inspect-1.0 simaaipciehost &> /dev/null; then
        gst_status="PASS"
        gst_msg="✅ GStreamer plugin 'simaaipciehost' installed"
    elif gst-inspect-1.0 pciehost &> /dev/null; then
        gst_status="PASS"
        gst_msg="✅ GStreamer plugin 'pciehost' installed (legacy)"
    else
        gst_msg="❌ GStreamer plugin not found: expected 'simaaipciehost' (or legacy 'pciehost')"
    fi
    printf "$fmt" "GStreamer" "$gst_status" "$gst_msg"

    # --- Check 2: Kernel Module ---
    local mod_status="FAIL"
    local mod_msg=""

    if modinfo simaai_mla_drv &> /dev/null; then
        mod_status="PASS"
        mod_msg="✅ Kernel module 'simaai_mla_drv' installed"
    elif modinfo sima_mla_drv &> /dev/null; then
        mod_status="PASS"
        mod_msg="✅ Kernel module 'sima_mla_drv' installed (legacy)"
    else
        mod_msg="❌ Kernel module not found: expected 'simaai_mla_drv' (or legacy 'sima_mla_drv')"
    fi
    printf "$fmt" "Kernel Module" "$mod_status" "$mod_msg"

    # --- Check 3: PCI Devices (Vendor 1f06) ---
    declare -A DEVICE_MAP=(
        [abcd]="Davinci default"
        [0001]="Modalix default"
        [1031]="Davinci 10L DM.2"
        [0031]="Davinci 08L DM.2"
        [0041]="Davinci HHHL"
        [1011]="Davinci DVT 933"
        [0011]="Davinci DVT"
        [0101]="Modalix DVT"
        [0121]="Modalix HHHL"
        [1121]="Modalix HHHL x16"
        [0123]="Modalix HHHL v2"
        [2123]="Modalix HHHL v2 1R"
        [1fe5]="Modalix Zebu PCIe"
    )

    local pci_out
    pci_out=$(lspci -Dnnd 1f06: 2>/dev/null || true)

    if [[ -z "$pci_out" ]]; then
        printf "$fmt" "PCI Devices" "FAIL" "❌ No SiMa (1f06) PCIe devices detected"
        echo ""
        return
    fi

    local pci_status="PASS"
    printf "$fmt" "PCI Devices" "$pci_status" "✅ Detected SiMa PCIe device(s):"

    while read -r line; do
        # Example line:
        # 0000:01:00.0 Processing accelerators [1200]: 1f06:1031
        local bdf dev_id dev_name driver

        bdf=$(awk '{print $1}' <<< "$line")
        dev_id=$(sed -n 's/.*1f06:\([0-9a-fA-F]\{4\}\).*/\1/p' <<< "$line")
        dev_id=${dev_id,,}

        dev_name="${DEVICE_MAP[$dev_id]:-Unknown device ($dev_id)}"

        driver=$(lspci -ks "$bdf" 2>/dev/null | awk -F': ' '/Kernel driver in use/ {print $2}')
        driver=${driver:-"not bound"}

        printf "  - %-12s %-28s driver=%s\n" "$bdf" "$dev_name" "$driver"
    done <<< "$pci_out"

    echo ""
}


# ------------------------------------------------------------------------------
# 2. Main Installation Logic
# ------------------------------------------------------------------------------

# Input Argument: Path to the installer .sh file
DRIVER_PKG_SCRIPT="$1"

if [[ -z "$DRIVER_PKG_SCRIPT" ]]; then
    echo "Usage: sudo $0 <path_to_sima_pcie_host_pkg.sh>"
    echo "Example: sudo $0 ./sima_pcie_host_pkg.sh"
    exit 1
fi

check_root

# --- Step 1: Dependencies ---
print_header "Installing required system packages..."

# Added gstreamer1.0-tools/plugins-base so validation check doesn't fail on missing binary
apt-get update
sudo apt-get install -y \
    make cmake gcc g++ dkms doxygen \
    pkg-config \
    build-essential \
    libjson-c-dev libjsoncpp-dev \
    libglib2.0-dev \
    libjson-glib-dev \
    libgstreamer1.0-dev \
    net-tools \
    libgstreamer-plugins-base1.0-dev \
    linux-headers-generic linux-headers-$(uname -r) \
    gstreamer1.0-tools gstreamer1.0-plugins-base \
    pciutils

print_success "Dependencies installed successfully."

# --- Step 2: Verification & Permission ---
if [[ ! -f "$DRIVER_PKG_SCRIPT" ]]; then
    print_error "File not found: $DRIVER_PKG_SCRIPT"
    exit 1
fi

echo -e "\n📦 Host driver script found: $DRIVER_PKG_SCRIPT"
read -p "This will install drivers on your system. Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted by user."
    exit 1
fi

# --- Step 3: Execution ---
print_header "Running Driver Installer..."
chmod +x "$DRIVER_PKG_SCRIPT"
./"$DRIVER_PKG_SCRIPT"

print_success "Host driver installation script finished."

# --- Step 4: Validation ---
validate_installation

# --- Step 5: Reboot (optional) ---

echo ""
echo -e "${YELLOW}A system reboot is required to ensure the kernel driver is fully loaded.${NC}"
read -p "Reboot the machine now? [y/N]: " -r
echo

if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}Rebooting system gracefully...${NC}"
    sync
    sleep 2
    reboot
else
    echo -e "Reboot skipped. Please remember to reboot later if required."
fi
