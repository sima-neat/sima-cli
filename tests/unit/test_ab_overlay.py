import subprocess
from unittest.mock import MagicMock

import pytest

from sima_cli.update.ab_state import inspect_target
from sima_cli.update.rootfs import ROOT_DEVICE_SCRIPT, ROOT_IDENTITY_SCRIPT


@pytest.mark.parametrize('mounted', ['oldroot', 'mnt', 'unrelated'])
def test_root_identity_verifies_mount_device(tmp_path, mounted):
    for name in ('oldroot', 'mnt'):
        etc = tmp_path / name / 'etc'
        etc.mkdir(parents=True)
        (etc / 'buildinfo').write_text(f'SIMA_BUILD_VERSION = {name}-build\n')
        (etc / 'os-release').write_text('PRETTY_NAME="eLxr test"\n')
    script = ROOT_IDENTITY_SCRIPT.replace(ROOT_DEVICE_SCRIPT, 'dev=dm-test; rootdev=/dev/dm-test\n')
    script = script.replace('/oldroot', str(tmp_path / 'oldroot')).replace('/mnt', str(tmp_path / 'mnt'))
    mocks = f'''
blkid() {{ echo 740d31f2-aa09-56e0-9c6e-ee357eb533d0; }}
ls() {{ echo mmcblk0p3; }}
lsblk() {{ echo mmcblk0; }}
cat() {{ echo 254:0; }}
findmnt() {{
    case "$*" in
        *FSTYPE*) echo overlay ;;
        *"/{mounted} "*) echo 254:0 ;;
        *) echo 0:42 ;;
    esac
}}
'''
    result = subprocess.run(['sh', '-c', mocks + script], capture_output=True, text=True)
    if mounted == 'unrelated':
        assert result.returncode != 0
        assert 'active version:' not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert f'active version: {mounted}-build' in result.stdout
        assert 'active os: eLxr test' in result.stdout


def test_inspect_reads_peer_metadata_without_overwriting_boot_control():
    target = MagicMock()
    target.run.side_effect = [
        'running slot: A\nnext-boot slot (CB): B\nslots valid: A,B\nupgrade_available: yes\nnormal boot',
        'boot retries: 2',
        'active slot: A\nfallback slot: B\nactive version: B1368\nactive os: eLxr',
        'fallback slot: B\nfallback version: B1307\nfallback os: eLxr',
    ]
    state = inspect_target(target, display=False)
    assert state['active version'] == 'B1368'
    assert state['fallback version'] == 'B1307'
    assert state['running slot'] == 'A'
    assert state['next-boot'] == 'B'
    assert state['upgrade_available'] == 'yes'
    assert state['bootcount'] == '2'
