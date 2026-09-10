"""Resolve the dm-init A/B root, including eLxr's persistent overlay boot."""

ROOT_DEVICE_SCRIPT = r'''rootdev=$(findmnt -n -o SOURCE /)
if [ "$(findmnt -n -o FSTYPE /)" = overlay ]; then
    rootdev=$(tr ' ' '\n' < /proc/cmdline | sed -n 's/^root=//p' | head -1)
fi
dev=$(basename "$(readlink -f "$rootdev")")
test -b "$rootdev" && test "$(cat "/sys/class/block/$dev/dm/name" 2>/dev/null)" = rootfs || {
    echo 'Cannot identify the A/B rootfs device beneath the running filesystem'; exit 1
}
'''

ROOT_IDENTITY_SCRIPT = ROOT_DEVICE_SCRIPT + r'''
case "$(blkid -s UUID -o value "$rootdev")" in
740d31f2-aa09-56e0-9c6e-ee357eb533d0) active=A; fallback=B ;;
905013a4-b365-5e4a-8ded-0f223098085a) active=B; fallback=A ;;
*) echo 'Unknown A/B rootfs UUID'; exit 1 ;;
esac
printf 'active slot: %s\nfallback slot: %s\n' "$active" "$fallback"
slave=$(ls "/sys/class/block/$dev/slaves" | head -1)
medium=$(lsblk -dno PKNAME "/dev/$slave")
[ -z "$medium" ] || printf 'medium: /dev/%s\n' "$medium"
base=
if [ "$(findmnt -n -o FSTYPE /)" = overlay ]; then
    # The overlay init pivots the underlying root to /mnt. Verify it before reading.
    mounted=$(findmnt -n -M /mnt -o MAJ:MIN | tr -d ' ')
    [ "$mounted" = "$(cat "/sys/class/block/$dev/dev")" ] || exit 1
    base=/mnt
fi
printf 'active version: %s\n' "$(sed -n 's/^SIMA_BUILD_VERSION *= *//p' "$base/etc/buildinfo" 2>/dev/null)"
printf 'active os: %s\n' "$(. "$base/etc/os-release"; echo "$PRETTY_NAME")"
'''
