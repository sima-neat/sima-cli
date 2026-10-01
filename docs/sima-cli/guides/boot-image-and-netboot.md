# Boot Image and Netboot Operations

Prepare local or downloaded images, configure remote netboot safely, recover from address changes, and prepare eMMC for flashing with `sima-cli bootimg`.

Command reference: [`sima-cli bootimg`](../commands/sima-cli-bootimg.md)

## Local images and cache reuse

Use `--images DIRECTORY` with `--netboot` or `--autoflash` to prepare a netboot
session from artifacts already present on the host. The directory is searched
recursively. eLxr requires one minimal TFTP archive and one matching non-recovery
`.img.gz` eMMC image. Yocto requires one release archive containing the TFTP
files and its `.wic.gz` and `.wic.bmap` images. Ambiguous or incomplete sets stop
before the TFTP server starts.

The source directory is never modified. sima-cli copies and extracts TFTP content
into a managed cache under the platform temporary directory at `sima-cli/netboot`.
The cache is reused when its source identity and prepared file sizes still match.
Downloaded netboot images use the same cache. Add `--delete-cache` to remove only
that session's managed cache after the TFTP server stops. A cache in use by
another session is preserved.

## Daily netboot fallback

For internal Modalix eLxr 3.0+ builds, `--netboot` and `--autoflash` use the
public daily platform mirror only when `-f/--force` is provided and Artifactory
cannot be reached or authentication fails. Without `-f`, preparation stops and
explains how to enable fallback. Size and SHA-256 verification remain mandatory.
`-v` accepts an exact build or a search term such as `1247`, `3.0`, or `develop`;
multiple matches are shown newest first.

The minimal TFTP archive, palette `.img.gz` eMMC image, and `troot_blob.be` must
come from the same selected build. Missing, mismatched, or invalid mirror
artifacts stop preparation before extraction or TFTP startup. Local-file,
direct-URL, Yocto, and older eLxr paths remain available.

## Remote netboot preparation

Use `--netboot --devkit 192.168.2.2` to select a DevKit, or omit `--devkit` to
use SDK discovery. Multiple devices prompt for selection. If none are found,
TFTP remains available and the CLI prints manual configuration instructions.
`--devkit-ip` remains an alias.

The device must be reachable over SSH and expose its U-Boot binary and
environment files under `/boot`. sima-cli matches the environment format to the
bootloader, backs up the current environment, and converts an incompatible
redundant format when it is safe to do so. The host route to the selected DevKit
determines the TFTP server address, including on multi-interface hosts.

Before changing the device, a confirmation panel shows the network settings,
persistent U-Boot changes, and reboot consequences. Confirmation defaults to
No. Accepting temporarily remounts `/boot` writable when necessary, restores its
previous mount mode, saves a backup under `/boot/sima-cli-netboot-backup.*`,
sets `boot_targets=net`, verifies the result, and schedules a reboot. A failure
during preparation restores the saved environment and prevents reboot.

Platform 2.1.2 and earlier may use a legacy `fw_env.config` layout that cannot
be identified safely. In that case sima-cli restores the backup, keeps TFTP
running, and prints the exact U-Boot commands to enter through the serial console.

Keep the host and TFTP server running. The settings persist until changed, so a
failed network boot can retry and reboot. By default, wait for SSH and type `f`
at the `netboot>` prompt to flash. With `--autoflash`, flashing starts once the
selected device is reachable and confirmed to be running the network image.

```bash
sima-cli -i bootimg -v 1247 --netboot -f --devkit 192.168.2.2
```

## Recovering from an IP change

The device may receive a different address when Linux boots. If the SSH check
keeps waiting on the old address, press Ctrl+C at the `netboot>` prompt. The
TFTP server stays active; use `q` to exit.

Type `d` to run multicast discovery. If the device does not respond, open its
serial console with `sima-cli serial` in another terminal and inspect `ip -4 addr`
or `ifconfig`. Once the correct device and address are known, type `f <ip>` to
flash it. Discovery never selects a flash target automatically.

## Preparing eMMC for flashing

Before writing the image, sima-cli lists every mounted filesystem backed by the
eMMC, including LVM volumes and bind mounts. It unmounts them before releasing
LVM mappings. Flashing stops if a mount is busy, the running root filesystem or
active swap uses eMMC, or a mapping remains active. Close processes using those
filesystems and retry from the network recovery environment. Unmounting and
image-write errors stop flashing instead of reporting success.
