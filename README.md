# sima-cli - SiMa Developer Portal CLI Tool

[![Python 3.8](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=Compatibility%20Python%203.8&label=python%203.8)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![Python 3.9](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=Compatibility%20Python%203.9&label=python%203.9)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![Python 3.10](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=Compatibility%20Python%203.10&label=python%203.10)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![Python 3.11](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=Compatibility%20Python%203.11&label=python%203.11)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![Python 3.12](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=Compatibility%20Python%203.12&label=python%203.12)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![Python 3.13](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=Compatibility%20Python%203.13&label=python%203.13)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![Python 3.14](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=Compatibility%20Python%203.14&label=python%203.14)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![E2E macOS](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=E2E%20Install%20(macOS)&label=e2e%20macOS&logo=apple&logoColor=white)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![E2E Windows](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=E2E%20Install%20(Windows)&label=e2e%20Windows&logo=windows&logoColor=white)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![E2E Ubuntu x86](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=E2E%20Install%20(Ubuntu%20x86)&label=e2e%20Ubuntu%20x86&logo=ubuntu&logoColor=white)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)
[![E2E Ubuntu ARM64](https://img.shields.io/github/actions/workflow/status/sima-neat/sima-cli/vulcan-ci.yml?branch=main&job=E2E%20Install%20(Ubuntu%20ARM64)&label=e2e%20Ubuntu%20ARM64&logo=ubuntu&logoColor=white)](https://github.com/sima-neat/sima-cli/actions/workflows/vulcan-ci.yml)

`sima-cli` is the command-line interface for SiMa developer workflows. Use it to authenticate, set up SDK containers, update DevKits, install packages, download artifacts, and access Model Zoo and App Zoo content.

## Documentation

The full command reference is generated as Markdown under [docs/sima-cli](docs/sima-cli/index.md).

Use the generated docs for detailed options, arguments, subcommands, and full help text:

- [Complete command reference](docs/sima-cli/index.md)
- [Top-level command help](docs/sima-cli/commands/sima-cli.md)

## Installation

For most users, install the latest official release from the public installer URL for your operating system.

### Linux, macOS, and DevKit

Run the installer from a terminal:

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh | bash
```

After installation, open a new terminal or reload your shell profile, then verify the install:

```bash
sima-cli version
```

### Windows PowerShell

Download and run the Windows installer from PowerShell:

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/windows.bat -OutFile windows.bat
.\windows.bat
```

After installation, open a new Command Prompt or PowerShell window, then verify the install:

```powershell
sima-cli version
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

## Quick Start

```bash
sima-cli --help
sima-cli --version
sima-cli login
```

Use `--internal` or `SIMA_CLI_INTERNAL=1` when internal Artifactory resources are required:

```bash
sima-cli --internal login
SIMA_CLI_INTERNAL=1 sima-cli install -v 2.1.1 sdk-extensions/model
```

## Common Workflows

Set up SDK containers:

```bash
sima-cli sdk setup
sima-cli sdk neat
```

Install Model Compiler from package metadata:

```bash
sima-cli install -v 2.1.1 sdk-extensions/model
```

Download or install Neat artifacts:

```bash
sima-cli neat download core main
sima-cli install --neat core main
```

Update a DevKit:

```bash
sima-cli update -v 2.1.1 -y
```

Explore Model Zoo and App Zoo content:

```bash
sima-cli modelzoo list
sima-cli appzoo list
```

## Top-Level Commands

| Command | Description | Docs |
| --- | --- | --- |
| `sima-cli appzoo` | Access sample apps from the App Zoo. | [docs](docs/sima-cli/commands/sima-cli-appzoo.md) |
| `sima-cli bootimg` | Prepare a bootable image for the SiMa DevKit. | [docs](docs/sima-cli/commands/sima-cli-bootimg.md) |
| `sima-cli device` | Discover nearby SiMa.ai devices on the local network. | [docs](docs/sima-cli/commands/sima-cli-device.md) |
| `sima-cli download` | Download a file or folder from a URL. | [docs](docs/sima-cli/commands/sima-cli-download.md) |
| `sima-cli install` | Install SiMa packages from metadata. | [docs](docs/sima-cli/commands/sima-cli-install.md) |
| `sima-cli login` | Authenticate with the SiMa Developer Portal. | [docs](docs/sima-cli/commands/sima-cli-login.md) |
| `sima-cli logout` | Remove cached credentials and config files. | [docs](docs/sima-cli/commands/sima-cli-logout.md) |
| `sima-cli mla` | Machine Learning Accelerator utilities. | [docs](docs/sima-cli/commands/sima-cli-mla.md) |
| `sima-cli modelzoo` | Access models from the Model Zoo. | [docs](docs/sima-cli/commands/sima-cli-modelzoo.md) |
| `sima-cli neat` | Discover, download, and install Neat build artifacts. | [docs](docs/sima-cli/commands/sima-cli-neat.md) |
| `sima-cli network` | Configure DevKit network settings. | [docs](docs/sima-cli/commands/sima-cli-network.md) |
| `sima-cli nvme` | Perform NVMe operations on the Modalix DevKit. | [docs](docs/sima-cli/commands/sima-cli-nvme.md) |
| `sima-cli packages` | Manage the local sima-cli package registry. | [docs](docs/sima-cli/commands/sima-cli-packages.md) |
| `sima-cli playbooks` | Install and manage coding-agent playbooks. | [docs](docs/sima-cli/commands/sima-cli-playbooks.md) |
| `sima-cli sdcard` | Prepare SD card storage. | [docs](docs/sima-cli/commands/sima-cli-sdcard.md) |
| `sima-cli sdk` | Manage and launch SDK container environments. | [docs](docs/sima-cli/commands/sima-cli-sdk.md) |
| `sima-cli selfupdate` | Update sima-cli manually. | [docs](docs/sima-cli/commands/sima-cli-selfupdate.md) |
| `sima-cli serial` | Connect to the UART serial console of a DevKit. | [docs](docs/sima-cli/commands/sima-cli-serial.md) |
| `sima-cli update` | Update a SiMa DevKit or remote device. | [docs](docs/sima-cli/commands/sima-cli-update.md) |

## Development

Install development dependencies and run tests:

```bash
pip install -e ".[dev]"
python -m pytest tests/unit
```

Regenerate CLI documentation:

```bash
python scripts/generate_cli_markdown_docs.py
```

`build.sh` also regenerates the command docs before building the package.

## Requirements

- Python 3.8 or newer
- Docker for SDK container workflows
- DevKit connectivity for device update, serial, network, and boot-image workflows

For command-specific prerequisites, see the generated [command reference](docs/sima-cli/index.md).

## Support

For issues and feature requests, use the sima-cli GitHub repository or contact the SiMa.ai development team.
