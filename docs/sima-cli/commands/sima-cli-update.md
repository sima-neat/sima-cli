# `sima-cli update`

Update the software on a SiMa DevKit or remote SiMa device.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli update [OPTIONS] [VERSION_OR_URL]
```

## Internal daily updates on eLxr 3.0+

Internal mode (`-i`, `--internal`, or `SIMA_CLI_INTERNAL=1`) displays an
informational warning panel: **Pre-release software may be unstable. Use at your
own risk.** This applies across internal commands and does not add a confirmation
prompt. The panel is written to stderr so JSON stdout remains usable.

On Modalix devices already running eLxr 3.0+, `sima-cli -i update` first queries
Artifactory. If credentials are missing, access returns HTTP 401/403, the server
returns HTTP 5xx, or the connection fails or times out, it announces the reason
and falls back to the [daily platform index](https://artifacts.neat.sima.ai/daily-platform-images/index.json).
A successful Artifactory query with no matching builds does not trigger fallback.

```bash
# Search for matching builds; add --ip <device-ip> when running from a host.
sima-cli -i update -f -v 3.0
# Select an exact build directly.
sima-cli -i update -f -v 3.0.0_daily_develop_B1247
```

Exact matches take priority. Partial queries such as `3.0`, `develop`, or `B1247`
filter the index. Multiple matches show an aligned selector ordered by descending
build number, without build times. Only builds containing a palette SWU are
eligible. A missing build may have expired from the mirror's retained daily builds.

The selected build's indexed `artifacts/palette/elxr-palette-modalix-*.swu` is
downloaded from the public mirror without Artifactory credentials. Its size and
SHA-256 are verified before the existing signed SWUpdate installation. With `-f`, if an
Artifactory download fails after selection, fallback is restricted to that exact
build. The selected build identity is also used for post-reboot verification.

An unavailable index, missing artifact, or failed integrity check stops before
installation. If connection is lost after installation starts, inspect the board
with `sima-cli update --inspect --ip <device-ip>` before retrying.

Devices running eLxr 2.1.3 retain their existing APT update behavior, remote-update
limitations, and `--force` policy. Explicitly targeting 3.0 on those devices is
still rejected; recovery/provisioning is required first. This mirror fallback
applies only to eLxr 3.0+ SWUpdate, not bootimg or recovery-media creation.

## Options

| Name | Description |
| --- | --- |
| `-v, --version` | Specify version string (e.g., '1.7.0', 'ga', 'beta', or a direct firmware URL). Default is GA if not specifiedOverrides positional argument if both are given. |
| `--ip` | Target device IP address for remote firmware update. |
| `-y, --yes` | Assume yes for update confirmation prompts. |
| `-p, --passwd` | Password for remote board SSH or local ELXR sudo authentication. (default: edgeai) |
| `--flavor` | Firmware flavor: 'full' image supports NVMe and GUI on Modalix DevKit. This option is deprecated for 2.0 and above (default: auto) |
| `-f, --force` | If the internal mirror is unreachable, fall back to the external pre-release mirror (eLxr only). On 3.0+, signed-bundle verification remains required. The legacy 2.1 behavior is unchanged. |
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

      # Update ELXR to the latest official release without prompts

      sima-cli update -y

      # Update ELXR from the internal mirror without prompts

      sima-cli -i update -y

      # Update ELXR from the public pre-release mirror without prompts

      sima-cli -y update -f -y

      # Validate ELXR update path without running simaai-ota

      sima-cli update --dryrun

      # Provide root password for remote updates

      sima-cli update --ip 192.168.6.5 --passwd root

Options:
  -v, --version TEXT             Specify version string (e.g., '1.7.0', 'ga',
                                 'beta', or a direct firmware URL). Default is
                                 GA if not specifiedOverrides positional
                                 argument if both are given.
  --ip TEXT                      Target device IP address for remote firmware
                                 update.
  -y, --yes                      Assume yes for update confirmation prompts.
  -p, --passwd TEXT              Password for remote board SSH or local ELXR
                                 sudo authentication.  [default: edgeai]
  --flavor [headless|full|auto]  Firmware flavor: 'full' image supports NVMe
                                 and GUI on Modalix DevKit. This option is
                                 deprecated for 2.0 and above  [default: auto]
  -f, --force                    If the internal mirror is unreachable, fall
                                 back to the external pre-release mirror
                                 (eLxr only). eLxr 3.0+ still verifies signed
                                 full-system bundles. On eLxr 2.1, this disables
                                 repository signature verification and, without
                                 --internal, selects the mirror directly.
  -t, --troot_only               Only update tRoot and not the root file
                                 system, compatible with Yocto system only,
                                 used for Yocto to eLxr conversion.
  --dryrun                       For ELXR updates only, validate the update
                                 path and print the simaai-ota command without
                                 running it.
  --help                         Show this message and exit.
```

## eLxr 3.0 and later

On an A/B-provisioned Modalix system, `update` installs a signed full-system
SWU bundle into the inactive slot. It preserves the running slot and persistent
`/data`. Developer-portal version lookup for 3.0 is not available yet; use an
explicit bundle URL/file or internal build selection.

```sh
sima-cli update /path/to/full-system.swu
sima-cli update --ip 192.168.6.5 /path/to/full-system.swu
sima-cli update --inspect
sima-cli update --ip 192.168.6.5 --inspect
```

Staging checks `/data`, `/media/nvme/swupdate`, then `/tmp`, choosing the first
location with room for the bundle plus a 64 MiB margin. If none is usable,
the error reports the storage checks and asks you to remove unneeded files in
`/data`, showing the required free space before retrying. This applies both to
updates on the board and to `update --ip` from a host.

SWUpdate also needs temporary extraction space in `/tmp`. Before installation,
the CLI checks that `/tmp` has free space equal to the SWU archive size plus
64 MiB, after the bundle has been staged. The uncompressed CPIO archive size
bounds its extracted members, including `rootfs.ext4.gz`; the raw image handler
decompresses that member directly into the inactive slot. When `/tmp` is also
the bundle's staging location, selection reserves room for both copies.

For a RAM-backed `/tmp`, available RAM is checked even when `df` reports enough
space: a tmpfs size limit does not guarantee that memory is available.

If a dedicated `/tmp` tmpfs is too small, the CLI can increase its limit. The
existing memory guard reserves at least 512 MiB or 10% of total RAM for the
system, whichever is larger. Raising a tmpfs limit does not add physical RAM.
Disk-backed `/tmp` filesystems are not resized. If there is insufficient
extraction space and safe expansion is unavailable, installation stops before
SWUpdate starts, even if `/data` or NVMe has enough space for the bundle.

`--dryrun` reports a feasible expansion without remounting `/tmp`. A real
expansion lasts for the current mount (normally until reboot); it does not edit
persistent mount configuration. The CLI rechecks free space after expansion.

NVMe is mounted or remounted
read/write before checking it (except during `--dryrun`). If no location is
usable, the update stops with a storage error. Local downloads use one staged
copy. After successful installation, the downloaded bundle and its temporary
staging directory are removed to free space, including for remote updates.

Remote updates download on the host, transfer to the selected board storage,
verify the transfer checksum, and run SWUpdate there. Both modes verify the signed bundle
with `/etc/swupdate/public.pem` and select `update,full`. If that default key
is absent, the CLI temporarily provisions the bundled SiMa certificate under
`/tmp` and removes it after the update attempt. Existing keys are preserved.
`--key` can select a
verification key already installed on the target. Firmware-only/OS-only modes
are not supported in this flow. `--force` only permits external pre-release
mirror fallback; signature verification remains required.

Download and transfer show byte progress. Installation shows the current
artifact, step, and percentage from `swupdate-progress` when available; otherwise
installer diagnostics remain visible. `--dryrun` checks the target and resolves
the bundle without installation or reboot.

A successful installation reports that a reboot is required. Add `--reboot` to
reboot after success; for a remote board the CLI reconnects and checks the new
slot and health confirmation. The board's health service commits the boot;
`update` does not clear pending or rollback flags. Connection loss or a failed
install must be inspected before retrying.

`--inspect` displays both slots, their versions/OS and validity, the running and
next-boot slots, pending-update status, rollback status, and boot count. It does
not download or install firmware, switch slots, reboot, or self-update the CLI.
Migration from an older layout to the 3.0 A/B layout requires provisioning/recovery.

### Inspection with a persistent root overlay

On overlay-enabled images, `--inspect` reads the active slot version from the
pristine root at `/oldroot` (or `/mnt` on older images), after verifying that the
mount belongs to the running rootfs device. It does not use the overlay's package
database to identify the slot. If the platform inspector omits fallback metadata,
the CLI temporarily mounts the peer rootfs read-only with journal replay disabled
and restores its previous LVM activation state. Inspection does not switch slots
or clear boot flags. Missing or unverifiable metadata remains unknown.

For eLxr 3.0+, `sima-cli -i update -f` tries Artifactory first and permits
external pre-release fallback on supported discovery or artifact-download failures.
Without `-f`, it stops instead of switching mirrors.
