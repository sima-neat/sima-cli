# `sima-cli bootimg`

Prepare a bootable image for the SiMa DevKit.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli bootimg [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `-v, --version` | Firmware version to download and write (e.g., 1.6.0) |
| `-b, --boardtype` | Target board type. (default: modalix) |
| `-t, --fwtype` | Target firmware type. (default: elxr) |
| `-n, --netboot` | Prepare image for network boot and launch TFTP server. |
| `--images` | Directory containing local netboot source images. |
| `--delete-cache` | Delete the managed netboot cache after the session exits. |
| `-f, --force` | Allow daily mirror fallback if Artifactory is unavailable (internal Modalix eLxr netboot only). |
| `--recovery` | Write eLxr Modalix recovery media for automatic eMMC recovery. |
| `--devkit, --devkit-ip` | DevKit IP for remote netboot; discover and select a DevKit when omitted. |
| `-r, --rootfs` | Custom root fs folders (internal use only) |
| `-a, --autoflash` | Network boot the selected DevKit, then automatically flash its internal storage once SSH is ready. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli bootimg [OPTIONS]

  Prepare a bootable image for the SiMa DevKit.

  This command downloads the specified firmware version and prepares a
  removable boot medium (SD card or USB) or configures a TFTP-based network
  boot environment. It supports both MLSoC- and Modalix-based DevKits, as well
  as Yocto and eLxr firmware types.

  Matching internal builds appear in aligned Version and Build time (UTC)
  columns, newest first. Build time uses the newest Artifactory archive
  creation timestamp for each version. Unknown timestamps appear last.

  Operations Performed:

    • Download the correct firmware bundle for the selected version

    • Build a bootable disk image (SD/USB) OR configure TFTP netboot

    • (Optional) Boot the DevKit over the network and flash internal eMMC
    storage (use `f` command)

    • Support for internal/testing rootfs overrides (`--rootfs`)

  Typical Use Cases:

      • Flashing a new firmware version to an SD card

      • Setting up a fast development loop using TFTP netboot

      • Preparing an eLxr-based bring-up image for Modalix DevKits

      • Automating eMMC flashing over the network

  Local Images and Cache:

    • Use ``--images DIRECTORY`` to recursively discover local netboot
    artifacts. eLxr requires a minimal TFTP archive and an eMMC
    ``.img.gz``; Yocto requires a release archive.

    • The supplied directory remains read-only. Prepared content is cached
    under the platform temporary directory in ``sima-cli/netboot`` and is
    reused by default. ``--delete-cache`` removes only the managed cache
    entry after the session exits.

  Examples:

      # Write an SD card image for an MLSoC DevKit

      sima-cli bootimg -v 1.6.0 --boardtype mlsoc --fwtype yocto

      # Set up netboot for a Modalix DevKit

      sima-cli bootimg -v 3.0.0 --netboot

      # Select a DevKit for confirmed remote U-Boot setup and reboot

      sima-cli bootimg -v 3.0.0 --netboot --devkit-ip 192.168.1.20

      # Allow daily mirror fallback for internal eLxr 3.0+ netboot

      sima-cli -i bootimg -v 1247 --netboot -f

      # Prepare an eLxr netboot image for Modalix

      sima-cli bootimg -v 2.0.0 --netboot

      # Prepare netboot from images already downloaded to a local directory

      sima-cli bootimg --netboot --boardtype modalix --fwtype elxr --images
      /path/to/images

      # Prepare USB/SD recovery media that automatically recovers eMMC

      sima-cli bootimg -v 3.0.0 --recovery

Options:
  -v, --version TEXT              Firmware version to download and write
                                  (e.g., 1.6.0)
  -b, --boardtype [modalix|mlsoc]
                                  Target board type.  [default: modalix]
  -t, --fwtype [yocto|elxr]       Target firmware type.  [default: elxr]
  -n, --netboot                   Prepare image for network boot and launch
                                  TFTP server.
  --images DIRECTORY              Directory containing local netboot source
                                  images.
  --delete-cache                  Delete the managed netboot cache after the
                                  session exits.
  -f, --force                     Allow daily mirror fallback if Artifactory
                                  is unavailable (internal Modalix eLxr
                                  netboot only).
  --recovery                      Write eLxr Modalix recovery media for
                                  automatic eMMC recovery.
  --devkit, --devkit-ip TEXT      DevKit IP for remote netboot; discover and
                                  select a DevKit when omitted.
  -r, --rootfs TEXT               Custom root fs folders (internal use only)
  -a, --autoflash                 Network boot the selected DevKit, then
                                  automatically flash its internal storage
                                  once SSH is ready.
  --help                          Show this message and exit.
```
