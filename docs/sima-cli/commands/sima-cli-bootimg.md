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

  Examples:

      # Write an SD card image for an MLSoC DevKit

      sima-cli bootimg -v 1.6.0 --boardtype mlsoc

      # Set up netboot for a Modalix DevKit

      sima-cli bootimg -v 1.6.0 --boardtype modalix --netboot

      # Select a DevKit for confirmed remote U-Boot setup and reboot

      sima-cli bootimg -v 2.1.0 --boardtype modalix --netboot --devkit-ip
      192.168.1.20

      # Allow daily mirror fallback for internal eLxr 3.0+ netboot

      sima-cli -i bootimg -v 1247 --boardtype modalix --fwtype elxr --netboot
      -f

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

### Daily netboot fallback

For internal Modalix eLxr 3.0+ builds, `--netboot` (also `--autoflash`) uses the public daily platform mirror only when `-f/--force` is provided and Artifactory cannot be reached or authentication fails. Without `-f`, preparation stops with an error explaining how to enable fallback. Size and SHA-256 verification remain mandatory with `-f`. `-v` accepts an exact build name or a search term such as `1247`, `3.0`, or `develop`. Multiple matches appear newest build first.

The minimal TFTP archive, palette `.img.gz` eMMC image, and `troot_blob.be` come from the same selected build. Mirror downloads must match the index size and SHA-256 before extraction or TFTP startup. If an Artifactory artifact request fails after selection, the fallback retains that exact build. Missing or invalid mirror artifacts stop preparation with an error.

Existing local-file, direct-URL, Yocto, and older eLxr download paths remain available.

### Remote netboot preparation

Use `--netboot --devkit 192.168.2.2` to select a DevKit, or omit `--devkit` to use SDK setup discovery. Multiple discovered devices prompt for a selection. If none are discovered, TFTP remains available and the CLI shows instructions for configuring the DevKit manually. Automatic flashing is disabled without a selected device; once SSH is available, type `f` to flash. `--devkit-ip` remains an alias. The device must be reachable over SSH and provide the U-Boot binary and environment files under `/boot`. The CLI matches the environment format to the bootloader: a single-file FAT loader uses a four-byte CRC header, while a redundant loader uses the two-file format. Existing files written in the wrong redundant format are backed up and converted for single-file loaders before applying settings.

The host route to the selected DevKit determines the TFTP server IP, including on hosts with multiple interfaces. The DevKit's active address, subnet, and return-route gateway are reused for static netboot; DHCP is not required.

After downloading the image and starting TFTP, a yellow panel shows the device, network settings, persistent U-Boot changes, and reboot consequences. Confirmation defaults to No. Accepting temporarily remounts `/boot` writable when needed, restores its original mount mode on exit, and saves the previous environment under `/boot/sima-cli-netboot-backup.*`, sets `boot_targets=net` and the static network boot commands, verifies the settings, and schedules a reboot. Declining leaves U-Boot unchanged. Failures during environment preparation restore the saved environment and prevent reboot.

Keep the host and TFTP server running. The settings persist until changed: failed network boots retry and reboot, so recovery may require serial access. The backup directory includes both original environment files and a readable `environment.txt`. By default, wait for `✅ SSH is available on <IP>`, then type `f` to flash the device. With `-a/--autoflash`, flashing starts automatically once the selected DevKit is SSH-ready and confirmed to be running the network boot image. The confirmation panel explains that flashing will start automatically. Automatic flashing runs once and does not select another discovered device.

```bash
sima-cli -i bootimg -v 1247 --boardtype modalix --fwtype elxr --netboot -f --devkit 192.168.2.2
```

Remote preparation supports legacy 2.1 images without `/boot/u-boot.bin` by validating their redundant FAT environment configuration and CRCs. Other platforms retain bootloader-based format detection.

### Recovering from an IP change during netboot

The board may receive a different IP address when Linux boots. If the SSH
reboot check keeps waiting on the old address, press **Ctrl+C at the `netboot>`
prompt** to stop automatic SSH checks. The TFTP server stays running; use `q`
to exit the session.

Type `d` at the prompt to run multicast discovery on the local network. If the
board does not respond, connect its serial console, run `sima-cli serial` in
another terminal, log in, and run `ip -4 addr` (or `ifconfig`) on the board.
Once you have identified the correct board and its current IP, type `f <ip>`
(for example, `f 192.168.2.20`) to flash it. Discovery does not select a flash
target automatically. A timed-out SSH check also prints these recovery steps
and leaves the board unconnected.

### Preparing eMMC for flashing

Before netboot flashing writes the eMMC image, sima-cli lists all mounted
filesystems backed by the eMMC, including LVM volumes such as `/data` and bind
mounts. It unmounts them before releasing LVM mappings. Flashing stops if a mount
is busy, the running root filesystem or active swap uses eMMC, or a device
mapping remains active. Close the processes using those filesystems and retry
from the network recovery environment. Unmounting and image-write errors stop
flashing instead of reporting success.
