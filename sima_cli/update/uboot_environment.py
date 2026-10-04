"""Match fw_env configuration to the FAT environment supported by U-Boot."""


def configure_environment(
        config_path, boot_dir='/boot', etc_dir='/etc',
        target_redundant=None, allow_data_inference=False):
    """Validate and normalize FAT environment files for the target loader.

    This function is also sent to the DevKit as standalone Python source. When
    ``target_redundant`` is supplied, it is the authoritative target layout;
    otherwise the bootloader, legacy platform metadata, or explicitly allowed
    CRC-based inference determines the layout.
    """
    import os
    import re
    from pathlib import Path
    import struct
    import tempfile
    import zlib

    boot = Path(boot_dir)
    primary = boot / 'uboot.env'
    secondary = boot / 'uboot-redund.env'
    size = 0x80000

    def valid(data, header):
        return len(data) == size and struct.unpack('<I', data[:4])[0] == zlib.crc32(data[header:])

    def replace(path, content):
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        fd, temporary = tempfile.mkstemp(prefix='.sima-cli-env-', dir=str(boot))
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    if target_redundant is not None:
        redundant = bool(target_redundant)
    elif (boot / 'u-boot.bin').is_file():
        binary = (boot / 'u-boot.bin').read_bytes()
        if b'uboot.env\0' not in binary:
            raise RuntimeError('Cannot identify the U-Boot FAT environment filename; refusing to guess its format.')
        redundant = b'uboot-redund.env\0' in binary
    elif allow_data_inference:
        data = primary.read_bytes()
        other = secondary.read_bytes() if secondary.exists() else b''
        if valid(data, 4):
            redundant = False
        elif valid(data, 5) or valid(other, 5):
            redundant = True
        else:
            raise RuntimeError('Cannot infer the target U-Boot environment format from its CRC-valid files.')
    else:
        etc = Path(etc_dir)
        builds = '\n'.join(p.read_text() for p in (etc / 'build', etc / 'buildinfo') if p.is_file())
        if not re.search(r'^SIMA_BUILD_VERSION\s*=\s*2\.1\.', builds, re.MULTILINE):
            raise RuntimeError('Missing /boot/u-boot.bin; cannot identify the bootloader environment format.')
        entries = [line.split('#', 1)[0].split() for line in (etc / 'fw_env.config').read_text().splitlines()]
        entries = [entry for entry in entries if entry]
        expected = ['/boot/uboot.env', '/boot/uboot-redund.env']
        if len(entries) != 2 or any(len(e) != 3 or e[0] != path or int(e[1], 0) != 0 or int(e[2], 0) != 0x80000 for e, path in zip(entries, expected)):
            raise RuntimeError('Unsupported legacy 2.1 fw_env.config; refusing to guess the environment layout.')
        # Legacy 2.1 images omit the binary but declare their redundant FAT layout.
        redundant = True

    data = primary.read_bytes()
    if redundant:
        other = secondary.read_bytes() if secondary.exists() else b''
        if valid(data, 4):
            if data[-1:] != b'\0':
                raise RuntimeError('Single-file U-Boot environment has no padding byte for redundant conversion.')
            payload = data[4:-1]
            for path, flag in ((primary, 0), (secondary, 1)):
                converted = struct.pack('<I', zlib.crc32(payload)) + bytes([flag]) + payload
                replace(path, converted)
            print('Converted U-Boot environment to the redundant CRC format required by this bootloader.')
        elif not (valid(data, 5) or valid(other, 5)):
            raise RuntimeError('No CRC-valid U-Boot environment available for redundant repair.')
        paths = [primary, secondary]
    else:
        if not valid(data, 4):
            # Older CLI versions used a two-file config even for single-file
            # bootloaders. Recover the newest CRC-valid copy before converting.
            copies = [p.read_bytes() for p in (primary, secondary) if p.exists()]
            candidates = [copy for copy in copies if valid(copy, 5)]
            if not candidates:
                raise RuntimeError('No CRC-valid U-Boot environment available for repair.')
            selected = candidates[0]
            if len(candidates) == 2:
                first, second = candidates
                # FAT redundant environments use an incrementing byte, with wrap.
                if (first[4] == 255 and second[4] == 0) or (
                        not (first[4] == 0 and second[4] == 255) and second[4] > first[4]):
                    selected = second
            payload = selected[5:] + b'\0'
            converted = struct.pack('<I', zlib.crc32(payload)) + payload
            replace(primary, converted)
            print('Converted U-Boot environment to the single-file CRC format required by this bootloader.')
        paths = [primary]
    Path(config_path).write_text(''.join(f'{path} 0x0000 0x80000\n' for path in paths))
    print('U-Boot environment format: ' + ('redundant' if redundant else 'single-file') + '; ' + str(primary))
    return redundant
