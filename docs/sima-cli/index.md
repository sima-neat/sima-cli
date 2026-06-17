# sima-cli Command Reference

Generated Markdown reference documentation for the sima-cli command line interface.

## Installation

For most users, install the latest official release from the public installer URL for your operating system.

### Linux, macOS, and DevKit

Run the installer from a terminal:

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh | bash
```

After installation, open a new terminal or reload your shell profile, then verify the install:

```bash
sima-cli --version
```

### Windows PowerShell

Download and run the Windows installer from PowerShell:

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/windows.bat -OutFile windows.bat
.\windows.bat
```

After installation, open a new Command Prompt or PowerShell window, then verify the install:

```powershell
sima-cli --version
```

### Advanced: choose a branch or release

Use `install.py` only when you need to choose a specific tested branch build or release instead of installing the latest official PyPI release.

On Linux, macOS, or DevKit:

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/install.py -o sima-cli-install.py
python3 sima-cli-install.py
```

Install a specific branch or release:

```bash
python3 sima-cli-install.py feature/my-branch latest
python3 sima-cli-install.py v2.1.6 latest
```

On Windows PowerShell:

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/install.py -OutFile sima-cli-install.py
python .\sima-cli-install.py
```

To install a specific branch or release:

```powershell
python .\sima-cli-install.py feature/my-branch latest
python .\sima-cli-install.py v2.1.6 latest
```

Release tags such as `v2.1.6` install from public PyPI. Branch names install tested artifacts from `artifacts.neat.sima.ai/sima-cli`.

Public PyPI releases can also be installed directly:

```bash
pip install sima-cli
```

## Top-Level Commands

| Command | Description |
| --- | --- |
| [`sima-cli appzoo`](commands/sima-cli-appzoo.md) | Access sample apps from the App Zoo. |
| [`sima-cli bootimg`](commands/sima-cli-bootimg.md) | Prepare a bootable image for the SiMa DevKit. |
| [`sima-cli device`](commands/sima-cli-device.md) | Discover and manage SiMa.ai device(s) on the local network, compatible with both PCIe and Ethernet connections. Host side only. |
| [`sima-cli download`](commands/sima-cli-download.md) | Download a file or a whole folder from a given URL. |
| [`sima-cli install`](commands/sima-cli-install.md) | Install SiMa packages. |
| [`sima-cli login`](commands/sima-cli-login.md) | Authenticate with the SiMa Developer Portal. |
| [`sima-cli logout`](commands/sima-cli-logout.md) | Log out by deleting cached credentials and config files. |
| [`sima-cli mcp`](commands/sima-cli-mcp.md) | Run sima-cli as an MCP server so coding agents can drive the DevKit. |
| [`sima-cli mla`](commands/sima-cli-mla.md) | Machine Learning Accelerator Utilities. |
| [`sima-cli modelzoo`](commands/sima-cli-modelzoo.md) | Access models from the Model Zoo. |
| [`sima-cli neat`](commands/sima-cli-neat.md) | Discover, download, and install Neat build artifacts. |
| [`sima-cli network`](commands/sima-cli-network.md) | Setup Network IP address on the DevKit |
| [`sima-cli nvme`](commands/sima-cli-nvme.md) | Perform NVMe operations on the Modalix DevKit. |
| [`sima-cli packages`](commands/sima-cli-packages.md) | Manage sima-cli package registry (list, inspect, clean, etc.) |
| [`sima-cli playbooks`](commands/sima-cli-playbooks.md) | Install and manage playbooks (Codex/Claude). |
| [`sima-cli sdcard`](commands/sima-cli-sdcard.md) | Prepare the SD Card as a data storage device for MLSoc DevKit or Modalix Early Access Unit |
| [`sima-cli sdk`](commands/sima-cli-sdk.md) | Manage and launch SiMa SDK 2.0 container environments (Beta). |
| [`sima-cli selfupdate`](commands/sima-cli-selfupdate.md) | Update sima-cli manually from PyPI or a direct wheel URL. |
| [`sima-cli serial`](commands/sima-cli-serial.md) | Connect to the UART serial console of the DevKit. |
| [`sima-cli update`](commands/sima-cli-update.md) | Update the software on a SiMa DevKit or remote SiMa device. |

## Complete Command List

- [`sima-cli`](commands/sima-cli.md)
- [`sima-cli appzoo`](commands/sima-cli-appzoo.md)
- [`sima-cli bootimg`](commands/sima-cli-bootimg.md)
- [`sima-cli device`](commands/sima-cli-device.md)
- [`sima-cli download`](commands/sima-cli-download.md)
- [`sima-cli install`](commands/sima-cli-install.md)
- [`sima-cli login`](commands/sima-cli-login.md)
- [`sima-cli logout`](commands/sima-cli-logout.md)
- [`sima-cli mcp`](commands/sima-cli-mcp.md)
- [`sima-cli mla`](commands/sima-cli-mla.md)
- [`sima-cli modelzoo`](commands/sima-cli-modelzoo.md)
- [`sima-cli neat`](commands/sima-cli-neat.md)
- [`sima-cli network`](commands/sima-cli-network.md)
- [`sima-cli nvme`](commands/sima-cli-nvme.md)
- [`sima-cli packages`](commands/sima-cli-packages.md)
- [`sima-cli playbooks`](commands/sima-cli-playbooks.md)
- [`sima-cli sdcard`](commands/sima-cli-sdcard.md)
- [`sima-cli sdk`](commands/sima-cli-sdk.md)
- [`sima-cli selfupdate`](commands/sima-cli-selfupdate.md)
- [`sima-cli serial`](commands/sima-cli-serial.md)
- [`sima-cli update`](commands/sima-cli-update.md)
- [`sima-cli appzoo clone`](commands/sima-cli-appzoo-clone.md)
- [`sima-cli appzoo describe`](commands/sima-cli-appzoo-describe.md)
- [`sima-cli appzoo get`](commands/sima-cli-appzoo-get.md)
- [`sima-cli appzoo list`](commands/sima-cli-appzoo-list.md)
- [`sima-cli device discover`](commands/sima-cli-device-discover.md)
- [`sima-cli mcp available`](commands/sima-cli-mcp-available.md)
- [`sima-cli mcp install`](commands/sima-cli-mcp-install.md)
- [`sima-cli mcp serve`](commands/sima-cli-mcp-serve.md)
- [`sima-cli mcp status`](commands/sima-cli-mcp-status.md)
- [`sima-cli mla meminfo`](commands/sima-cli-mla-meminfo.md)
- [`sima-cli modelzoo describe`](commands/sima-cli-modelzoo-describe.md)
- [`sima-cli modelzoo get`](commands/sima-cli-modelzoo-get.md)
- [`sima-cli modelzoo list`](commands/sima-cli-modelzoo-list.md)
- [`sima-cli neat download`](commands/sima-cli-neat-download.md)
- [`sima-cli neat install`](commands/sima-cli-neat-install.md)
- [`sima-cli packages build`](commands/sima-cli-packages-build.md)
- [`sima-cli packages list`](commands/sima-cli-packages-list.md)
- [`sima-cli packages show`](commands/sima-cli-packages-show.md)
- [`sima-cli playbooks apply`](commands/sima-cli-playbooks-apply.md)
- [`sima-cli playbooks delete`](commands/sima-cli-playbooks-delete.md)
- [`sima-cli playbooks describe`](commands/sima-cli-playbooks-describe.md)
- [`sima-cli playbooks install`](commands/sima-cli-playbooks-install.md)
- [`sima-cli playbooks list`](commands/sima-cli-playbooks-list.md)
- [`sima-cli playbooks remove`](commands/sima-cli-playbooks-remove.md)
- [`sima-cli playbooks update`](commands/sima-cli-playbooks-update.md)
- [`sima-cli sdk elxr`](commands/sima-cli-sdk-elxr.md)
- [`sima-cli sdk ls`](commands/sima-cli-sdk-ls.md)
- [`sima-cli sdk model`](commands/sima-cli-sdk-model.md)
- [`sima-cli sdk mpk`](commands/sima-cli-sdk-mpk.md)
- [`sima-cli sdk neat`](commands/sima-cli-sdk-neat.md)
- [`sima-cli sdk remove`](commands/sima-cli-sdk-remove.md)
- [`sima-cli sdk run`](commands/sima-cli-sdk-run.md)
- [`sima-cli sdk setup`](commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk start`](commands/sima-cli-sdk-start.md)
- [`sima-cli sdk stop`](commands/sima-cli-sdk-stop.md)
- [`sima-cli sdk yocto`](commands/sima-cli-sdk-yocto.md)
