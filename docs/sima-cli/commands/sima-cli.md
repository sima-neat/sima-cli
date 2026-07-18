# `sima-cli`

sima-cli – SiMa Developer Portal CLI Tool

## Usage

```bash
sima-cli [OPTIONS] COMMAND [ARGS]...
```

## Options

| Name | Description |
| --- | --- |
| `-i, --internal` | Use internal Artifactory resources, Authorized Sima employees only |
| `--version` | Show the version and exit. |

## Arguments

None.

## Subcommands

- [`sima-cli appzoo`](./sima-cli-appzoo.md): Access sample apps from the App Zoo.
- [`sima-cli bootimg`](./sima-cli-bootimg.md): Prepare a bootable image for the SiMa DevKit.
- [`sima-cli device`](./sima-cli-device.md): Discover and manage SiMa.ai device(s) on the local network, compatible with both PCIe and Ethernet connections. Host side only.
- [`sima-cli download`](./sima-cli-download.md): Download a file or a whole folder from a given URL.
- [`sima-cli install`](./sima-cli-install.md): Install SiMa packages.
- [`sima-cli login`](./sima-cli-login.md): Authenticate with the SiMa Developer Portal.
- [`sima-cli logout`](./sima-cli-logout.md): Log out by deleting cached credentials and config files.
- [`sima-cli mcp`](./sima-cli-mcp.md): Run sima-cli as an MCP server so coding agents can drive the DevKit.
- [`sima-cli mla`](./sima-cli-mla.md): Machine Learning Accelerator Utilities.
- [`sima-cli modelzoo`](./sima-cli-modelzoo.md): Access models from the Model Zoo.
- [`sima-cli neat`](./sima-cli-neat.md): Discover, download, and install Neat build artifacts.
- [`sima-cli network`](./sima-cli-network.md): Setup Network IP address on the DevKit
- [`sima-cli nvme`](./sima-cli-nvme.md): Perform NVMe operations on the Modalix DevKit.
- [`sima-cli packages`](./sima-cli-packages.md): Manage sima-cli package registry (list, inspect, clean, etc.)
- [`sima-cli playbooks`](./sima-cli-playbooks.md): Install and manage playbooks (Codex/Claude).
- [`sima-cli sdcard`](./sima-cli-sdcard.md): Prepare the SD Card as a data storage device for MLSoc DevKit or Modalix Early Access Unit
- [`sima-cli sdk`](./sima-cli-sdk.md): Manage and launch SiMa SDK 2.0 container environments (Beta).
- [`sima-cli selfupdate`](./sima-cli-selfupdate.md): Update sima-cli manually from PyPI or a direct wheel URL.
- [`sima-cli serial`](./sima-cli-serial.md): Connect to the UART serial console of the DevKit.
- [`sima-cli update`](./sima-cli-update.md): Update the software on a SiMa DevKit or remote SiMa device.

## Full Help

```text
Usage: sima-cli [OPTIONS] COMMAND [ARGS]...

  sima-cli – SiMa Developer Portal CLI Tool

  Global Options:   --internal  Use internal Artifactory resources (can also
  be set via env variable SIMA_CLI_INTERNAL=1)

Options:
  -i, --internal  Use internal Artifactory resources, Authorized Sima
                  employees only
  --version       Show the version and exit.
  --help          Show this message and exit.

Commands:
  appzoo      Access sample apps from the App Zoo.
  bootimg     Prepare a bootable image for the SiMa DevKit.
  device      Discover and manage SiMa.ai device(s) on the local network,...
  download    Download a file or a whole folder from a given URL.
  install     Install SiMa packages.
  login       Authenticate with the SiMa Developer Portal.
  logout      Log out by deleting cached credentials and config files.
  mcp         Run sima-cli as an MCP server so coding agents can drive...
  mla         Machine Learning Accelerator Utilities.
  modelzoo    Access models from the Model Zoo.
  neat        Discover, download, and install Neat build artifacts.
  network     Setup Network IP address on the DevKit
  nvme        Perform NVMe operations on the Modalix DevKit.
  packages    Manage sima-cli package registry (list, inspect, clean, etc.)
  playbooks   Install and manage playbooks (Codex/Claude).
  sdcard      Prepare the SD Card as a data storage device for MLSoc...
  sdk         Manage and launch SiMa SDK 2.0 container environments (Beta).
  selfupdate  Update sima-cli manually from PyPI or a direct wheel URL.
  serial      Connect to the UART serial console of the DevKit.
  update      Update the software on a SiMa DevKit or remote SiMa device.
```
