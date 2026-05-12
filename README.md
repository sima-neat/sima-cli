# 🛠️ sima-cli – SiMa Developer Portal CLI Tool

[![Python 3.8](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/build.yml?branch=main&job=Compatibility%20Python%203.8&label=python%203.8)](https://github.com/sima-neat/sima-cli/actions/workflows/build.yml)
[![Python 3.9](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/build.yml?branch=main&job=Compatibility%20Python%203.9&label=python%203.9)](https://github.com/sima-neat/sima-cli/actions/workflows/build.yml)
[![Python 3.10](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/build.yml?branch=main&job=Compatibility%20Python%203.10&label=python%203.10)](https://github.com/sima-neat/sima-cli/actions/workflows/build.yml)
[![Python 3.11](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/build.yml?branch=main&job=Compatibility%20Python%203.11&label=python%203.11)](https://github.com/sima-neat/sima-cli/actions/workflows/build.yml)
[![Python 3.12](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/build.yml?branch=main&job=Compatibility%20Python%203.12&label=python%203.12)](https://github.com/sima-neat/sima-cli/actions/workflows/build.yml)
[![Python 3.13](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/build.yml?branch=main&job=Compatibility%20Python%203.13&label=python%203.13)](https://github.com/sima-neat/sima-cli/actions/workflows/build.yml)
[![Python 3.14](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/build.yml?branch=main&job=Compatibility%20Python%203.14&label=python%203.14)](https://github.com/sima-neat/sima-cli/actions/workflows/build.yml)

`sima-cli` is a command-line interface (CLI) utility designed to interact with the SiMa Developer Portal. It supports downloading models and apps from the Model/App Zoo, performing firmware updates, and authenticating against internal or external environments.

---

## 📦 Installation

Install from the latest `main` build:

### Linux / macOS

```bash
curl -fsSL https://tools.sima-neat.com/sima-cli-install.py -o sima-cli-install.py
python3 sima-cli-install.py main latest
```

### Windows PowerShell

```powershell
Invoke-WebRequest https://tools.sima-neat.com/sima-cli-install.py -OutFile sima-cli-install.py
python .\sima-cli-install.py main latest
```

Run without arguments to choose from available branches and releases:

```bash
python3 sima-cli-install.py
```

Install a specific branch or release:

```bash
python3 sima-cli-install.py feature/my-branch latest
python3 sima-cli-install.py v2.1.6 latest
```

Public PyPI releases can also be installed directly:

```bash
pip install sima-cli
```

---

## 🚀 Getting Started

```bash
sima-cli --help
```

### Global Option

- `--internal`: Use internal Artifactory resources (can also be set via `SIMA_CLI_INTERNAL=1`).

Environment detection output will appear like:

```
🔧 Environment: dev (sandbox) | Internal: True
```

If external mode is detected and not supported:

```
external environment is not supported yet..
```

---

## 🔐 Authentication

```bash
sima-cli login
```

Authenticates with the SiMa Developer Portal. Internal or external login is selected based on context.

---

## 📥 Download Resources

```bash
sima-cli download <URL> [-d DEST]
```

- Downloads a single file or an entire folder from the provided URL.
- Options:
  - `-d`, `--dest`: Destination folder (default is current directory).

---

## 🔧 Firmware Update

```bash
sima-cli update <version_or_url> [--ip IP] [--passwd PASSWORD] [--flavor {headless|full|auto}] [-y]
sima-cli update --version <VERSION> [--ip IP] [--passwd PASSWORD] [--flavor {headless|full|auto}] [-y]
```

- Updates firmware either locally (when running on the board) or over the network.
- Positional:
  - `<version_or_url>`: Version tag (e.g. `1.6.0_master_B1611`) or direct URL.
- Options:
  - `-v`, `--version`: Provide the version via flag instead of positional argument.
  - `--ip`: IP/FQDN of the remote device (required when running from a host).
  - `-p`, `--passwd`: SSH password for the remote board (default `edgeai`).
  - `-f`, `--flavor`: Override firmware flavor (`headless`, `full`, or `auto`).
  - `-y`, `--yes`: Skip the confirmation prompt before flashing.

---

## 🧠 Model Zoo

### List Models

```bash
sima-cli modelzoo list [--ver VERSION]
```

- Lists available models for a given SDK version.

### Get Model

```bash
sima-cli modelzoo get <MODEL_NAME> [--ver VERSION]
```

- Downloads the specified model.

---

## 📱 App Zoo

### List Apps

```bash
sima-cli appzoo list [--ver VERSION]
```

- Lists available apps for a given SDK version.

### Get App

```bash
sima-cli appzoo get <APP_NAME> [--ver VERSION]
```

- Downloads the specified app.

---

## 🖥️ Device Management

### Discover Devices

```bash
sima-cli device discover
```

- Discover nearby SiMa.ai DevKits via ARP or multicast on the local network.

### Connect to Device

```bash
sima-cli device connect --target <IP> [--user sima] [--password edgeai]
sima-cli device connect --slot <SLOT_NUMBER>
```

- Connect to a device over Ethernet (using `--target`) or PCIe (using `--slot`).

### List Connected Devices

```bash
sima-cli device list
```

- Show all currently connected devices with their status.

### Disconnect from Device

```bash
sima-cli device disconnect --target <IP>
sima-cli device disconnect --slot <SLOT_NUMBER>
```

- Disconnect from a device using either IP/FQDN or PCIe slot number.

### Reboot Device

```bash
sima-cli device reboot --target <IP>
sima-cli device reboot --slot <SLOT_NUMBER>
```

- Reboot a connected device.

### Firmware Upgrade

```bash
sima-cli device firmware-upgrade --file <PATH_TO_SWU> --target <IP> [--reboot-on-upgrade]
sima-cli device firmware-upgrade --file <PATH_TO_SWU> --slot-number <SLOT_NUMBER>
```

- Upgrade firmware on a device using a software update file (.swu).

---

## 📦 MPK Package Management

### Deploy MPK

```bash
sima-cli mpk deploy --file <PATH_TO_MPK> --target <IP> [--set-default]
sima-cli mpk deploy --file <PATH_TO_MPK> --slot <SLOT_NUMBER>
```

- Deploy a prebuilt MPK package to a connected device.

### Launch Pipeline

```bash
sima-cli mpk launch --application <APP_NAME> --target <IP>
sima-cli mpk launch --application <APP_NAME> --slot <SLOT_NUMBER>
```

- Launch a deployed pipeline on the device.

### Kill Pipeline

```bash
sima-cli mpk kill --id <PIPELINE_ID> --target <IP>
sima-cli mpk kill --pid <PID> --slot <SLOT_NUMBER>
```

- Kill a running pipeline using either pipeline ID or process ID.

### Remove Pipeline

```bash
sima-cli mpk remove --application <APP_NAME> --target <IP>
```

- Remove a deployed pipeline from the device.

### List Pipelines

```bash
sima-cli mpk list
```

- Show all deployed pipelines on connected devices.

---

## 🔧 Install Components

```bash
sima-cli install hostdriver -v 1.7.0
```

- Install the PCIe host driver for a specific SDK version.

```bash
sima-cli install optiview
```

- Install OptiView tool (SDK-independent).

```bash
sima-cli install -m <METADATA_URL>
```

- Install a package from a metadata.json URL.

```bash
sima-cli install gh:<USER>/<REPO>/<PATH_TO_METADATA>
```

- Install from a GitHub metadata file.

```bash
sima-cli install cr:<REGISTRY>/<IMAGE>:<TAG>
```

- Install from a container registry.

```bash
sima-cli install ghcr:<OWNER>/<IMAGE>:<TAG>
```

- Install from public GitHub Container Registry (GHCR).

---

## 📋 Package Registry

### List Installed Packages

```bash
sima-cli packages list
```

- Show all packages registered in the local sima-cli registry.

### Show Package Details

```bash
sima-cli packages show <PACKAGE_NAME> [--version VERSION]
```

- Display metadata and post-installation instructions for a package.

---

## 🛠️ SDK Container Management

### Setup SDK

```bash
sima-cli sdk setup [--noninteractive] [-y]
```

- Initialize SDK environment and select components to start.

### Start SDK Containers

```bash
sima-cli sdk start [-y]
```

- Start one or more SDK containers.

### Stop SDK Containers

```bash
sima-cli sdk stop [SDK_NAME] [-y]
```

- Stop running SDK containers (e.g., `yocto`, `mpk`, `model`, `neat`, `elxr`).

### Remove SDK Containers

```bash
sima-cli sdk remove [SDK_NAME] [-y]
```

- Remove SDK containers and images to free up storage.

### Access SDK Container

```bash
sima-cli sdk mpk
sima-cli sdk model
sima-cli sdk yocto
sima-cli sdk neat
sima-cli sdk elxr
```

- Launch an interactive shell in the respective SDK container.

### List SDK Containers

```bash
sima-cli sdk ls
```

- Show installed SDK containers with their version and running status.

---

## 💾 Storage Management

### Format NVMe Drive

```bash
sima-cli nvme format
```

- Format the NVMe drive on Modalix DevKit and mount it at `/media/nvme`.

### Remount NVMe Drive

```bash
sima-cli nvme remount
```

- Remount an existing NVMe partition to `/media/nvme`.

### Format SD Card

```bash
sima-cli sdcard format
```

- Prepare the SD Card as data storage for MLSoc or Modalix DevKit.

---

## 🌐 Network Configuration

```bash
sima-cli network
```

- Interactive menu to configure network settings on the DevKit (DHCP/Static IP, default route).
- Only works on SiMa boards.

---

## 🖥️ Serial Console

```bash
sima-cli serial [--baud 115200]
```

- Connect to the UART serial console of the DevKit.
- Auto-detects the serial port and launches `picocom` (Linux/macOS) or shows instructions for PuTTY/Tera Term (Windows).

---

## 📸 Boot Image Creation

```bash
sima-cli bootimg -v 1.7.0 [--boardtype modalix] [--netboot] [--autoflash]
```

- Download firmware and write to removable media or setup TFTP netboot.
- Options:
  - `--netboot`: Prepare image for network boot and launch TFTP server.
  - `--autoflash`: Automatically flash internal storage after netboot.

---

## 🔄 Convert Yocto to eLxr

Convert a Yocto-based DevKit to the eLxr runtime environment (SDK 2.0.0+, Modalix DevKit only).

### Prerequisites

1. Connect DevKit Ethernet to host PC (set host to static IP `192.168.1.10`)
2. Update to latest Yocto headless (required for tRoot compatibility):

```bash
sima-cli update --ip <IP_DEVKIT> 2.0.0 -f headless
```

### Conversion Steps

**Prepare netboot environment:**

```bash
# macOS
sima-cli bootimg --boardtype modalix --fwtype elxr -v 2.0.0 --netboot

# Linux (requires sudo for port 69)
sudo ~/.sima-cli/.venv/bin/sima-cli bootimg --boardtype modalix --fwtype elxr -v 2.0.0 --netboot
```

**Configure u-boot (from serial console):**

```bash
setenv cpio_name simaai-image-palette-modalix.cpio.gz
setenv boot_targets net
saveenv
boot
```

**Flash eMMC (from host terminal after netboot completes):**

```bash
f
```

### Revert to Yocto

Use the same process with `--fwtype yocto`:

```bash
sima-cli bootimg --boardtype modalix --fwtype yocto -v 2.0.0 --netboot
```

---

## 🔄 Self Update

```bash
sima-cli selfupdate
```

- Update sima-cli to the latest version from PyPI.

```bash
sima-cli selfupdate -v 0.0.46
```

- Update to a specific version.

```bash
sima-cli --internal selfupdate -v 0.0.46
```

- Update from internal Artifactory (requires `--internal` flag).

---

## 📊 MLA Memory Info

```bash
sima-cli mla meminfo
```

- Display real-time MLA memory usage chart on supported boards.

---

## 🔓 Logout

```bash
sima-cli logout
```

- Delete cached credentials and config files.

---

## 🌍 Environment Variable Support

Instead of using `--internal` flag every time, you can set:

```bash
export SIMA_CLI_INTERNAL=1
```

---

## 📌 Version

```bash
sima-cli version
```

- Display the currently installed sima-cli version.

---

## 🧩 Requirements

- Python 3.8+
- Internal network access if using `--internal` features

---

## 📞 Support

Please reach out to **SiMa Support** (support@sima.ai) if you encounter issues with downloads or updates.

---
