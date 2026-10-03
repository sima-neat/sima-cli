# `sima-cli update`

Update a SiMa DevKit, remote device, or Linux PCIe host.

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
| `-y, --yes` | Assume yes for update confirmation prompts and select the newest matching full-system build. |
| `-p, --passwd` | Password for remote board SSH or local ELXR sudo authentication. (default: edgeai) |
| `--flavor` | Firmware flavor: 'full' image supports NVMe and GUI on Modalix DevKit. This option is deprecated for 2.0 and above (default: auto) |
| `-f, --force` | If the internal mirror is unreachable, fall back to the external pre-release mirror (eLxr only). eLxr 3.0+ still verifies signed full-system bundles. On eLxr 2.1, this disables repository signature verification and, without --internal, selects the mirror directly. |
| `-t, --troot_only` | Only update tRoot and not the root file system, compatible with Yocto system only, used for Yocto to eLxr conversion. |
| `--dryrun` | For eLxr updates, validate the update path and show the command without installing. |
| `--inspect` | Show eLxr 3.0+ A/B slots and overlay customizations without updating (local or --ip). |
| `--verbose` | Show detailed overlay file categories during eLxr 3.0+ inspection or update. |
| `--signing-cert` | SWUpdate PEM verification certificate: HTTP(S) URL or local file. Defaults to the certificate for the selected channel when configured (eLxr 3.0+). |
| `--reboot` | Reboot after successful eLxr 3.0+ installation; verify remote boot health. |

## Arguments

| Name | Description |
| --- | --- |
| `VERSION_OR_URL` |  |

## Full Help

```text
Usage: sima-cli update [OPTIONS] [VERSION_OR_URL]

  Update a SiMa DevKit, remote device, or Linux PCIe host.

  This command downloads and applies system software updates across different
  SiMa environments (Modalix, MLSoC/Davinci, headless images, or remote
  devices accessible over the network). Updates may be installed directly on
  the device or pushed from a development host.

  eLxr 3.0+ uses signed full-system SWU bundles. Use --inspect for A/B state,
  --ip for remote updates, and --reboot to reboot after installation. The
  verification certificate is downloaded for the selected channel when
  configured; use --signing-cert URL_OR_FILE for an image signed with your own
  certificate. Developer-portal version lookup for 3.0 is not available yet.

  Internal mode warns that pre-release software may be unstable and is used at
  your own risk. On eLxr 3.0+, unavailable Artifactory access falls back to
  the public daily platform mirror, including missing login, HTTP 401/403,
  connection failures, timeouts, and server errors. Exact build names select
  directly; partial matches show builds newest first by build number.

  Mirror downloads verify the indexed size and SHA-256 before signed
  installation. A missing build or failed download stops before installation.
  A connection loss after installation starts requires inspecting the board
  before retrying. Devices running eLxr 2.1.3 retain the APT update flow;
  upgrading those devices to 3.0 requires recovery/provisioning first.

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

    • PCIe host updates require Linux and ``-i/--internal``; omit the
    version to choose from the available daily platform builds

  Typical Use Cases:

    • Updating a SiMa DevKit to the latest GA release

    • Pushing a test build to a remote Modalix device

    • Applying a specific firmware version during bring-up

    • Running updates from both the device itself or a host PC

    • Installing a daily PCIe host package on a Linux development host

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

      # Update legacy eLxr (<3.0) to the latest official release without
      prompts

      sima-cli update -y

      # Update ELXR from the internal mirror without prompts

      sima-cli -i update -y

      # Select and install a PCIe host package from the daily build catalog
      (Linux only)

      sima-cli -i update

      # Or install a specific indexed daily platform build

      sima-cli -i update -v 3.0.0_daily_develop_B1774

      # Update legacy eLxr (<3.0) from the public pre-release mirror without
      prompts

      sima-cli -y update -f -y

      # Validate the eLxr update path without installing

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
  -y, --yes                      Assume yes for update confirmation prompts
                                 and select the newest matching full-system
                                 build.
  -p, --passwd TEXT              Password for remote board SSH or local ELXR
                                 sudo authentication.  [default: edgeai]
  --flavor [headless|full|auto]  Firmware flavor: 'full' image supports NVMe
                                 and GUI on Modalix DevKit. This option is
                                 deprecated for 2.0 and above  [default: auto]
  -f, --force                    If the internal mirror is unreachable, fall
                                 back to the external pre-release mirror (eLxr
                                 only). eLxr 3.0+ still verifies signed full-
                                 system bundles. On eLxr 2.1, this disables
                                 repository signature verification and,
                                 without --internal, selects the mirror
                                 directly.
  -t, --troot_only               Only update tRoot and not the root file
                                 system, compatible with Yocto system only,
                                 used for Yocto to eLxr conversion.
  --dryrun                       For eLxr updates, validate the update path
                                 and show the command without installing.
  --inspect                      Show eLxr 3.0+ A/B slots and overlay
                                 customizations without updating (local or
                                 --ip).
  --verbose                      Show detailed overlay file categories during
                                 eLxr 3.0+ inspection or update.
  --signing-cert URL_OR_FILE     SWUpdate PEM verification certificate:
                                 HTTP(S) URL or local file. Defaults to the
                                 certificate for the selected channel when
                                 configured (eLxr 3.0+).
  --reboot                       Reboot after successful eLxr 3.0+
                                 installation; verify remote boot health.
  --help                         Show this message and exit.
```
