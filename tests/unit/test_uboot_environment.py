import struct
import zlib

import pytest

from sima_cli.update.uboot_environment import configure_environment

SIZE = 0x80000


def env(value, flag=None):
    payload = b'boot_targets=' + value + b'\0\0'
    payload = payload.ljust(SIZE - (4 if flag is None else 5), b'\0')
    return struct.pack('<I', zlib.crc32(payload)) + (b'' if flag is None else bytes([flag])) + payload


@pytest.mark.parametrize('flags,expected', [((10, 11), b'new'), ((255, 0), b'new'), ((0, 255), b'old')])
def test_converts_to_crc_accepted_by_single_file_uboot(tmp_path, flags, expected):
    (tmp_path / 'u-boot.bin').write_bytes(b'FAT\0uboot.env\0')
    (tmp_path / 'uboot.env').write_bytes(env(b'old', flags[0]))
    (tmp_path / 'uboot-redund.env').write_bytes(env(b'new', flags[1]))
    config = tmp_path / 'config'
    configure_environment(str(config), str(tmp_path))
    converted = (tmp_path / 'uboot.env').read_bytes()
    assert len(converted) == SIZE
    assert struct.unpack('<I', converted[:4])[0] == zlib.crc32(converted[4:])
    assert converted[4:].startswith(b'boot_targets=' + expected + b'\0')
    assert len(config.read_text().splitlines()) == 1


@pytest.mark.parametrize('redundant', [False, True])
def test_matching_format_is_not_rewritten(tmp_path, redundant):
    (tmp_path / 'u-boot.bin').write_bytes(b'uboot.env\0' + (b'uboot-redund.env\0' if redundant else b''))
    original = env(b'net', 1 if redundant else None)
    (tmp_path / 'uboot.env').write_bytes(original)
    (tmp_path / 'uboot-redund.env').write_bytes(env(b'old', 0))
    config = tmp_path / 'config'
    configure_environment(str(config), str(tmp_path))
    assert (tmp_path / 'uboot.env').read_bytes() == original
    assert len(config.read_text().splitlines()) == (2 if redundant else 1)


def test_corrupt_environment_is_not_replaced_with_defaults(tmp_path):
    (tmp_path / 'u-boot.bin').write_bytes(b'uboot.env\0')
    (tmp_path / 'uboot.env').write_bytes(b'broken')
    with pytest.raises(RuntimeError, match='CRC-valid'):
        configure_environment(str(tmp_path / 'config'), str(tmp_path))
    assert (tmp_path / 'uboot.env').read_bytes() == b'broken'
