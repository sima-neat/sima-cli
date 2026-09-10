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
| `-v, --version` | Firmware version to download and write (e.g., 1.6.0) (required) |
| `-b, --boardtype` | Target board type. (default: mlsoc) |
| `-t, --fwtype` | Target firmware type. (default: yocto) |
| `-n, --netboot` | Prepare image for network boot and launch TFTP server. |
| `--recovery` | Write eLxr Modalix recovery media for automatic eMMC recovery. |
| `--devkit-ip` | Optional DevKit IP address for pre-netboot version probing. |
| `-r, --rootfs` | Custom root fs folders (internal use only) |
| `-a, --autoflash` | Net boot the DevKit and automatically flash the internal storage - TBD |

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

  Examples:

      # Write an SD card image for an MLSoC DevKit

      sima-cli bootimg -v 1.6.0 --boardtype mlsoc

      # Set up netboot for a Modalix DevKit

      sima-cli bootimg -v 1.6.0 --boardtype modalix --netboot

      # Set up netboot and probe an existing DevKit first

      sima-cli bootimg -v 2.1.0 --boardtype modalix --netboot --devkit-ip
      192.168.1.20

      # Internal eLxr 3.0+ netboot falls back to the daily mirror when
      Artifactory is unavailable

      sima-cli -i bootimg -v 1247 --boardtype modalix --fwtype elxr --netboot

      # Prepare an eLxr netboot image for Modalix

      sima-cli bootimg -v 2.0.0 --boardtype modalix --fwtype elxr --netboot

      # Prepare USB/SD recovery media that automatically recovers eMMC

      sima-cli bootimg -v 3.0.0 --recovery

Options:
  -v, --version TEXT              Firmware version to download and write
                                  (e.g., 1.6.0)  [required]
  -b, --boardtype [modalix|mlsoc]
                                  Target board type.  [default: mlsoc]
  -t, --fwtype [yocto|elxr]       Target firmware type.  [default: yocto]
  -n, --netboot                   Prepare image for network boot and launch
                                  TFTP server.
  --recovery                      Write eLxr Modalix recovery media for
                                  automatic eMMC recovery.
  --devkit-ip TEXT                Optional DevKit IP address for pre-netboot
                                  version probing.
  -r, --rootfs TEXT               Custom root fs folders (internal use only)
  -a, --autoflash                 Net boot the DevKit and automatically flash
                                  the internal storage - TBD
  --help                          Show this message and exit.
```

### Daily netboot fallback

For internal Modalix eLxr 3.0+ builds, `--netboot` (also `--autoflash`) uses the public daily platform mirror if Artifactory cannot be reached or authentication fails. `-v` accepts an exact build name or a search term such as `1247`, `3.0`, or `develop`. Multiple matches appear newest build first.

The minimal TFTP archive, palette `.img.gz` eMMC image, and `troot_blob.be` come from the same selected build. Mirror downloads must match the index size and SHA-256 before extraction or TFTP startup. If an Artifactory artifact request fails after selection, the fallback retains that exact build. Missing or invalid mirror artifacts stop preparation with an error.

Existing local-file, direct-URL, Yocto, and older eLxr download paths remain available.
