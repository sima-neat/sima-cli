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
    # Current overlay-init reserves /oldroot; older images used /mnt.
    # Match the actual root device so an NFS mount at /mnt is never trusted.
    expected=$(cat "/sys/class/block/$dev/dev")
    base=
    for candidate in /oldroot /mnt; do
        mounted=$(findmnt -n -M "$candidate" -o MAJ:MIN | tr -d ' ')
        if [ "$mounted" = "$expected" ]; then
            base=$candidate
            break
        fi
    done
    [ -n "$base" ] || exit 1
fi
printf 'active version: %s\n' "$(sed -n 's/^SIMA_BUILD_VERSION *= *//p' "$base/etc/buildinfo" 2>/dev/null)"
printf 'active os: %s\n' "$(. "$base/etc/os-release"; echo "$PRETTY_NAME")"
'''


# Used only when the platform inspector cannot read the peer slot. Keep existing
# LVM activation unchanged, and suppress journal replay on the temporary mount.
FALLBACK_IDENTITY_SCRIPT = ROOT_DEVICE_SCRIPT + r'''
export LVM_SUPPRESS_FD_WARNINGS=1
case "$(blkid -s UUID -o value "$rootdev")" in
740d31f2-aa09-56e0-9c6e-ee357eb533d0) inactive=B; expected=905013a4-b365-5e4a-8ded-0f223098085a ;;
905013a4-b365-5e4a-8ded-0f223098085a) inactive=A; expected=740d31f2-aa09-56e0-9c6e-ee357eb533d0 ;;
*) exit 1 ;;
esac
slave=$(ls "/sys/class/block/$dev/slaves" | head -1)
pv=/dev/$slave
vg=$(vgs --devices "$pv" --noheadings -o vg_name | tr -d ' ')
[ -n "$vg" ] || exit 1
lv=$vg/rootfs.$inactive
was_active=$(lvs --devices "$pv" --noheadings -o lv_active "$lv" 2>/dev/null) || exit 1
was_active=$(printf '%s' "$was_active" | tr -d ' ')
case "$was_active" in active) ;; ""|inactive) was_active=inactive ;; *) exit 1 ;; esac
activated=0
mnt=
cleanup() {
    if [ -n "$mnt" ]; then
        umount "$mnt" || return 1
        rmdir "$mnt"
    fi
    [ "$activated" = 0 ] || lvchange --devices "$pv" -an "$lv"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
if [ "$was_active" = inactive ]; then
    lvchange --devices "$pv" -K -ay "$lv" || exit 1
    activated=1
fi
[ "$(blkid -s UUID -o value "/dev/$lv")" = "$expected" ] || exit 1
mnt=$(mktemp -d) || exit 1
if ! mount -t ext4 -o ro,noload "/dev/$lv" "$mnt"; then
    rmdir "$mnt"; mnt=
    exit 1
fi
printf 'fallback slot: %s\n' "$inactive"
printf 'fallback version: %s\n' "$(sed -n 's/^SIMA_BUILD_VERSION *= *//p' "$mnt/etc/buildinfo" 2>/dev/null)"
printf 'fallback os: %s\n' "$(. "$mnt/etc/os-release"; echo "$PRETTY_NAME")"
'''
