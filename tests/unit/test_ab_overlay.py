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


@pytest.mark.parametrize('scenario', ['success', 'lock_busy', 'updater_running', 'interrupted', 'interrupted_mounted', 'unmount_failure', 'already_active'])
def test_fallback_serialization_and_cleanup(tmp_path, scenario):
    import os
    from sima_cli.update.rootfs import FALLBACK_IDENTITY_SCRIPT

    script = FALLBACK_IDENTITY_SCRIPT.replace(
        ROOT_DEVICE_SCRIPT, 'dev=dm-test; rootdev=/dev/dm-test\n'
    ).replace('/run/lock/sima-cli-swupdate.lock', str(tmp_path / 'lock'))
    mocks = r'''
record() { echo "$*" >> "$TEST_DIR/events"; }
flock() { record lock; [ "$SCENARIO" != lock_busy ]; }
pgrep() { [ "$SCENARIO" = updater_running ]; }
blkid() {
    if [ "$5" = /dev/dm-test ]; then
        echo 740d31f2-aa09-56e0-9c6e-ee357eb533d0
    else
        echo 905013a4-b365-5e4a-8ded-0f223098085a
    fi
}
ls() { echo mmcblk0p3; }
vgs() { echo testvg; }
lvs() { record lvs; if [ "$SCENARIO" = already_active ]; then echo active; fi; }
lvchange() { record "lvchange $*"; }
mktemp() { mkdir "$TEST_DIR/mount"; echo "$TEST_DIR/mount"; }
mount() {
    record mount
    if [ "$SCENARIO" = interrupted ]; then kill -TERM $$; return 1; fi
    touch "$TEST_DIR/mounted"
    if [ "$SCENARIO" = interrupted_mounted ]; then kill -TERM $$; return 1; fi
    mkdir "$TEST_DIR/mount/etc"
    echo 'SIMA_BUILD_VERSION = B1307' > "$TEST_DIR/mount/etc/buildinfo"
    echo 'PRETTY_NAME="eLxr test"' > "$TEST_DIR/mount/etc/os-release"
}
findmnt() { [ -f "$TEST_DIR/mounted" ]; }
umount() {
    record umount
    [ "$SCENARIO" != unmount_failure ] || return 1
    rm -f "$TEST_DIR/mounted"
    rm -rf "$TEST_DIR/mount/etc"
}
rmdir() { record rmdir; command rmdir "$@"; }
'''
    result = subprocess.run(['sh', '-c', mocks + script], capture_output=True, text=True,
                            env=dict(os.environ, TEST_DIR=str(tmp_path), SCENARIO=scenario))
    events = (tmp_path / 'events').read_text()
    assert events.splitlines()[0] == 'lock'
    if scenario in ('lock_busy', 'updater_running'):
        assert result.returncode != 0
        assert 'lvs' not in events
        assert 'lvchange' not in events
    elif scenario == 'unmount_failure':
        assert 'umount' in events
        assert '-an' not in events
        assert (tmp_path / 'mounted').exists()
    else:
        assert not (tmp_path / 'mount').exists()
        assert not (tmp_path / 'mounted').exists()
        if scenario == 'already_active':
            assert 'lvchange' not in events
        else:
            assert '-ay' in events and '-an' in events
        if scenario.startswith('interrupted'):
            assert result.returncode != 0
            assert 'fallback version:' not in result.stdout
        else:
            assert result.returncode == 0, result.stderr
            assert 'fallback version: B1307' in result.stdout
