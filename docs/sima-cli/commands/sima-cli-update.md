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
sima-cli -i update -v 3.0
# Select an exact build directly.
sima-cli -i update -v 3.0.0_daily_develop_B1247
```

Exact matches take priority. Partial queries such as `3.0`, `develop`, or `B1247`
filter the index. Multiple matches show an aligned selector ordered by descending
build number, without build times. Only builds containing a palette SWU are
eligible. A missing build may have expired from the mirror's retained daily builds.

The selected build's indexed `artifacts/palette/elxr-palette-modalix-*.swu` is
downloaded from the public mirror without Artifactory credentials. Its size and
SHA-256 are verified before the existing signed SWUpdate installation. If an
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
| `-f, --force` | If the internal mirror is unreachable, fall back to the external pre-release mirror without signature verification; without --internal, select that mirror directly (ELXR only). |
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
                                 without signature verification; without
                                 --internal, select that mirror directly (ELXR
                                 only).
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

Staging checks `/tmp`, `/media/nvme/swupdate`, then `/data`, choosing the first location
with room for the bundle plus a 64 MiB margin. NVMe is mounted or remounted
read/write before checking it (except during `--dryrun`). If no location is
usable, the update stops with a storage error. Local downloads use one staged
copy. After successful installation, the downloaded bundle and its temporary
staging directory are removed to free space, including for remote updates.

Remote updates download on the host, transfer to the selected board storage,
verify the transfer checksum, and run SWUpdate there. Both modes fetch the current
SiMa public verification certificate from
`https://debian.neat.sima.ai/daily/swupdate-signing-cert.pem` on each update,
including `sima-cli -i update`. The CLI contains no bundled certificate. It
validates the PEM certificate, checks its validity against the DevKit clock,
stages it privately under `/tmp`, and passes that path to `swupdate -k` with
`-e update,full`. The temporary certificate is removed after the update attempt;
existing certificates on the board are preserved.

For an image signed with your own key, supply its matching **public certificate**
as an HTTP(S) URL or a file on the machine running the CLI:

```sh
sima-cli update ./custom-image.swu --signing-cert ./my-signing-cert.pem
sima-cli update --ip 192.168.6.5 ./custom-image.swu --signing-cert ./my-signing-cert.pem
sima-cli update ./custom-image.swu --signing-cert https://updates.example.com/signing-cert.pem
```

The CLI retrieves the certificate on the host and provisions it on the remote
DevKit; the DevKit does not need access to the certificate URL. For offline use,
supply a local bundle and certificate. When running the CLI directly on the
DevKit, `--signing-cert /data/my-cert.pem` can use a certificate already there.
This option requires the eLxr 3.0+ SWUpdate flow; older APT/Yocto update paths
are unchanged.

Certificate download or validation failures stop the update without a bundled
fallback or unsigned installation. `/data` must be mounted. Synchronize the
DevKit clock before updating: these boards have no RTC, and a certificate can
appear not yet valid until the clock is stepped. An expired certificate also
stops the update. Firmware-only/OS-only modes and unsigned `--force` updates
are not supported in this flow.

Download and transfer show byte progress. Installation shows the current
artifact, step, and percentage from `swupdate-progress` when available; otherwise
installer diagnostics remain visible. `--dryrun` checks the target and resolves
the bundle and validates the selected certificate without staging it, installation, or reboot.

A successful installation reports that a reboot is required. Add `--reboot` to
reboot after success; for a remote board the CLI reconnects and checks the new
slot and health confirmation. The board's health service commits the boot;
`update` does not clear pending or rollback flags. Connection loss or a failed
install must be inspected before retrying.

`--inspect` displays both slots, their versions/OS and validity, the running and
next-boot slots, pending-update status, rollback status, and boot count. It does
not download or install firmware, switch slots, reboot, or self-update the CLI.
Migration from an older layout to the 3.0 A/B layout requires provisioning/recovery.
