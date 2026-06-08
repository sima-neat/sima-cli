# `sima-cli update`

Update the software on a SiMa DevKit or remote SiMa device.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli update [OPTIONS] [VERSION_OR_URL]
```

## Options

| Name | Description |
| --- | --- |
| `-v, --version` | Specify version string (e.g., '1.7.0', 'ga', 'beta', or a direct firmware URL). Default is GA if not specifiedOverrides positional argument if both are given. |
| `--ip` | Target device IP address for remote firmware update. |
| `-y, --yes` | Skip confirmation after firmware file is downloaded. |
| `-p, --passwd` | Optional SSH password for remote board (default is 'edgeai'). (default: edgeai) |
| `-f, --flavor` | Firmware flavor: 'full' image supports NVMe and GUI on Modalix DevKit. This option is deprecated for 2.0 and above (default: auto) |
| `-t, --troot_only` | Only update tRoot and not the root file system, compatible with Yocto system only, used for Yocto to eLxr conversion. |
| `--dryrun` | For ELXR updates only, validate the update path and print the simaai-ota command without running it. |

## Arguments

| Name | Description |
| --- | --- |
| `VERSION_OR_URL` |  |

## Full Help

```text
Usage: sima-cli update [OPTIONS] [VERSION_OR_URL]

  Update the software on a SiMa DevKit or remote SiMa device.

  This command downloads and applies system software updates across different
  SiMa environments (Modalix, MLSoC/Davinci, headless images, or remote
  devices accessible over the network). Updates may be installed directly on
  the device or pushed from a development host.

  How Version Resolution Works:

    • If a version string is provided (e.g., ``1.7.0``), sima-cli
    automatically resolves it to the correct downloadable firmware asset based
    on channel, flavor, and board type.

    • If a URL or local bundle path is provided, sima-cli will use the
    specified file directly.

    • The ``--version`` option overrides the positional argument
    (``VERSION_OR_URL``).

  Requirements:

    • A valid SiMa Developer Portal account

    • You must run ``sima-cli login`` before performing updates

    • Remote updates require an accessible IP address (``--ip``)

  Typical Use Cases:

    • Updating a SiMa DevKit to the latest GA release

    • Pushing a test build to a remote Modalix device

    • Applying a specific firmware version during bring-up

    • Running updates from both the device itself or a host PC

  Examples:

      # Update the device you're currently logged into

      sima-cli update

      # Update a remote device by IP address

      sima-cli update --ip 192.168.6.5

      # Update to a specific version

      sima-cli update -v 1.7.0

      # Update using a direct firmware bundle URL

      sima-cli update https://example.com/fw/sima-1.8.0.tar.gz

      # Silent/auto-confirm mode

      sima-cli update -v 1.7.0 -y

      # Validate ELXR update path without running simaai-ota

      sima-cli update --dryrun

      # Provide root password for remote updates

      sima-cli update --ip 192.168.6.5 --passwd root

Options:
  -v, --version TEXT              Specify version string (e.g., '1.7.0', 'ga',
                                  'beta', or a direct firmware URL). Default
                                  is GA if not specifiedOverrides positional
                                  argument if both are given.
  --ip TEXT                       Target device IP address for remote firmware
                                  update.
  -y, --yes                       Skip confirmation after firmware file is
                                  downloaded.
  -p, --passwd TEXT               Optional SSH password for remote board
                                  (default is 'edgeai').  [default: edgeai]
  -f, --flavor [headless|full|auto]
                                  Firmware flavor: 'full' image supports NVMe
                                  and GUI on Modalix DevKit. This option is
                                  deprecated for 2.0 and above  [default:
                                  auto]
  -t, --troot_only                Only update tRoot and not the root file
                                  system, compatible with Yocto system only,
                                  used for Yocto to eLxr conversion.
  --dryrun                        For ELXR updates only, validate the update
                                  path and print the simaai-ota command
                                  without running it.
  --help                          Show this message and exit.
```
