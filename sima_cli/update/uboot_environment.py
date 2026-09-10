"""Match fw_env configuration to the FAT environment supported by U-Boot."""


def configure_environment(config_path, boot_dir='/boot'):
    """Run after backup: repair redundant files for a single-file FAT loader.

    This function is also sent to the DevKit as standalone Python source.
    """
    import os
    from pathlib import Path
    import struct
    import tempfile
    import zlib

    boot = Path(boot_dir)
    binary = (boot / 'u-boot.bin').read_bytes()
    primary = boot / 'uboot.env'
    secondary = boot / 'uboot-redund.env'
    if b'uboot.env\0' not in binary:
        raise RuntimeError('Cannot identify the U-Boot FAT environment filename; refusing to guess its format.')
    redundant = b'uboot-redund.env\0' in binary
    size = 0x80000

    def valid(data, header):
        return len(data) == size and struct.unpack('<I', data[:4])[0] == zlib.crc32(data[header:])

    data = primary.read_bytes()
    if redundant:
        other = secondary.read_bytes()
        if not (valid(data, 5) or valid(other, 5)):
            raise RuntimeError('No valid redundant U-Boot environment; refusing to replace it with defaults.')
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
            fd, temporary = tempfile.mkstemp(prefix='.sima-cli-env-', dir=str(boot))
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(converted)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, primary.stat().st_mode & 0o777)
                os.replace(temporary, primary)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            print('Converted U-Boot environment to the single-file CRC format required by this bootloader.')
        paths = [primary]
    Path(config_path).write_text(''.join(f'{path} 0x0000 0x80000\n' for path in paths))
    print('U-Boot environment format: ' + ('redundant' if redundant else 'single-file') + '; ' + str(primary))
