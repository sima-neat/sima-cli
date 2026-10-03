# System Update Operations

Use daily builds, signed eLxr 3.0 SWU bundles, A/B inspection, storage staging, and persistent-overlay handling with `sima-cli update`.

Command reference: [`sima-cli update`](../commands/sima-cli-update.md)

## Internal daily updates on eLxr 3.0+

Internal mode displays a pre-release warning. On Modalix devices already running
eLxr 3.0+, `sima-cli -i update` queries Artifactory first. Missing credentials,
HTTP 401/403 or 5xx responses, connection failures, and timeouts fall back to the
[daily platform index](https://artifacts.neat.sima.ai/daily-platform-images/index.json).
A successful query with no matching build does not fall back.

```bash
sima-cli -i update -f -v 3.0
sima-cli -i update -f -v 3.0.0_daily_develop_B1247
```

Exact matches take priority. Partial queries filter the index and display matches
newest first. Only builds containing a palette SWU are eligible. Mirror downloads
verify indexed size and SHA-256 before signed installation. With `-f`, a failed
Artifactory artifact download may fall back only to the same selected build.
A missing artifact or failed integrity check stops before installation.

Devices running eLxr 2.1.3 keep their APT update behavior and `--force` policy.
Moving those devices to 3.0 requires recovery or provisioning.

## eLxr 3.0 signed A/B updates

On an A/B-provisioned Modalix system, `update` installs a signed full-system SWU
into the inactive slot while preserving the running slot and `/data`.

```bash
sima-cli update /path/to/full-system.swu
sima-cli update --ip 192.168.6.5 /path/to/full-system.swu
sima-cli update --inspect
sima-cli update --ip 192.168.6.5 --inspect
```

Staging checks `/data`, `/media/nvme/swupdate`, then `/tmp`, requiring room for
the bundle plus a 64 MiB margin. SWUpdate also needs extraction space in `/tmp`.
When `/tmp` is RAM-backed, available RAM is checked even if `df` reports enough
space. A dedicated tmpfs may be enlarged for the current mount only when at least
512 MiB or 10% of RAM, whichever is larger, remains reserved for the system.
`--dryrun` reports a feasible expansion without changing the mount.

NVMe is mounted or remounted read/write before use except during `--dryrun`.
Downloaded bundles and temporary staging are removed immediately after a
successful install. Failed installs retain their staged bundle for diagnosis;
user-provided source files are never removed.

Remote updates download on the host, transfer to selected board storage, verify
the transfer checksum, and run SWUpdate on the board. Both local and remote modes
verify the signed bundle and select `update,full`. If `/etc/swupdate/public.pem`
is absent, the bundled SiMa certificate is provisioned temporarily and removed
afterward; existing keys are preserved. `--force` changes mirror fallback only
and never disables signature verification.

A successful installation requires reboot. With `--reboot`, remote updates
reconnect and verify the new slot and health confirmation. Connection loss or a
failed installation should be inspected before retrying.

## A/B inspection

`--inspect` displays both slots, versions and validity, the running and next-boot
slots, pending-update and rollback status, and boot count. It never downloads or
installs firmware, switches slots, reboots, or self-updates sima-cli.

On persistent-overlay images, inspection reads the active version from the
pristine root at `/oldroot` after verifying its backing device. If necessary it
temporarily mounts the peer root read-only with journal replay disabled and then
restores its prior LVM activation state. Missing or unverifiable metadata remains
unknown.

## Persistent-overlay customization handling

Inspection and regular 3.0 updates show a read-only overlay overview. The report
compares the effective and lower-layer dpkg databases and groups upper-layer
entries into configuration/services, local software candidates, deletions,
package state, caches/logs, and other files. `--verbose` adds complete package
comparisons and category counts.

When `simaai-palette-*` identifies different package-set build IDs, sima-cli
warns that persistent metadata came from an earlier image and that APT may report
incorrect versions or mix image and overlay files. Copied-up files are not
automatically classified as intentional customizations, and unavailable or
unreadable baselines are reported rather than guessed.

During an update, a package-build mismatch offers a clean-overlay workflow:

1. Verify that the selected SWU supports `SWUPDATE_CLEAN_OVERLAY`.
2. Save and checksum an inventory under `/data/.overlay-backup`.
3. Run SWUpdate with overlay cleanup enabled.
4. Print post-reboot verification and reinstall guidance.

With `-y`, a detected mismatch selects cleanup automatically. Without `-y`, the
user can retain the overlay. A bundle lacking the cleanup contract is rejected
before installation or backup. The inventory records package selections, image
identity, paths, ownership, and permissions, but no file contents, dpkg database,
APT caches, or complete upper layer. It is recovery information, not an automatic
restore archive. The same workflow applies through `--ip` on a remote DevKit.
