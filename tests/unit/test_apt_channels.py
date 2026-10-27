from pathlib import Path
import subprocess
import sys

import pytest

from sima_cli.update.apt_channels import channel_for_build, configure, configure_command, URLS


@pytest.mark.parametrize('build,channel', [
    ('3.0.0_daily_develop_B2373','daily'), ('3.0.0_develop_B2373','daily'),
    ('3.0.0_master_B2372','pre-release'), ('3.0.0_pre-release_master_B2372','pre-release'),
    ('3.0.0_pre-release_develop_B2372','pre-release'), ('3.0.0~pre2372','pre-release'),
    ('3.0.0~git20261024.hash-2373','daily'), ('3.0.0','release'),
    ('3.0.0_ga','release'), ('3.0.0_feature_B123',None), ('',None),
])
def test_build_selects_channel(build, channel):
    assert channel_for_build(build) == channel


def apt_root(tmp_path, source):
    root=tmp_path/'etc/apt'
    (root/'sources.list.d').mkdir(parents=True)
    (root/'sources.list.d/0000mirror.list').write_text(source)
    return root


@pytest.mark.parametrize('build,channel', [('3.0.0_daily_develop_B42','daily'),
    ('3.0.0_pre-release_master_B42','pre-release'), ('3.0.0','release')])
def test_switch_disables_conflicts_and_is_idempotent(tmp_path, build, channel):
    original='deb [arch=arm64 signed-by=/old/key] http://sw-web.eng.sima.ai/deb/daily agate non-free\n'
    root=apt_root(tmp_path,original)
    (root/'sources.list').write_text('deb https://debian.neat.sima.ai/pre-release agate non-free\n' +
        'deb https://repo.sima.ai/elxr/deb/release bookworm non-free\n' +
        'deb https://deb.debian.org/debian bookworm main\n')
    assert configure(root,build)==channel
    assert (root/'sources.list.d/0000mirror.list').read_text()=='# '+original
    assert 'deb https://deb.debian.org/debian bookworm main' in (root/'sources.list').read_text()
    managed=(root/'sources.list.d/simaai-channel.list').read_text()
    assert URLS[channel]+' agate non-free' in managed
    assert 'arch=arm64' in managed
    assert ('trusted=yes' in managed)==(channel!='release')
    assert (root/'kerrigan-backups/sources.list.d/0000mirror.list.kerrigan-backup').read_text()==original
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    configure(root,build)
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_bootstrap_preserves_explicit_prerelease_on_agate_when_identity_unknown(tmp_path):
    root=apt_root(tmp_path,'deb https://debian.neat.sima.ai/pre-release agate non-free\n')
    assert configure(root)=='pre-release'
    assert '/pre-release agate' in (root/'sources.list.d/simaai-channel.list').read_text()


def test_bootstrap_uses_actual_firmware_after_upgrade_or_rollback(tmp_path):
    root=apt_root(tmp_path,'deb https://debian.neat.sima.ai/daily agate non-free\n')
    (root.parent/'buildinfo').write_text('SIMA_BUILD_VERSION="3.0.0_master_B42"\n')
    assert configure(root)=='pre-release'
    (root.parent/'buildinfo').write_text('SIMA_BUILD_VERSION="3.0.0_daily_develop_B41"\n')
    assert configure(root)=='daily'


def test_unknown_build_with_conflicting_channels_fails_without_mutation(tmp_path):
    root=apt_root(tmp_path,'deb https://debian.neat.sima.ai/daily agate non-free\n'+
        'deb https://debian.neat.sima.ai/pre-release agate non-free\n')
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='Conflicting'):
        configure(root,build='3.0.0_feature_B1')
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_deb822_conflicts_and_mixed_stanza_preserve_unrelated_repositories(tmp_path):
    root=apt_root(tmp_path,'deb https://debian.neat.sima.ai/daily agate non-free\n')
    source=root/'sources.list.d/mixed.sources'
    source.write_text('Types: deb\nURIs: https://debian.neat.sima.ai/pre-release https://deb.debian.org/debian\nSuites: agate\nComponents: non-free\nSigned-By: /old/key\n')
    configure(root,'3.0.0_master_B42')
    blocks=source.read_text().split('\n\n')
    assert any('URIs: https://deb.debian.org/debian' in b and 'Enabled: no' not in b for b in blocks)
    assert any('URIs: https://debian.neat.sima.ai/pre-release' in b and 'Enabled: no' in b for b in blocks)
    assert source.read_text().count('Signed-By: /old/key')==2
    before=source.read_text()
    configure(root,'3.0.0_master_B42')
    assert source.read_text()==before


def test_serialized_target_script_runs_without_package_imports(tmp_path):
    import shlex
    script=configure_command('3.0.0_master_B42')
    # Execute the actual standalone payload against an isolated /etc/apt fixture.
    root=apt_root(tmp_path,'deb https://debian.neat.sima.ai/daily agate non-free\n')
    payload=shlex.split(script)[-1]
    payload=payload.rsplit('\nconfigure(',1)[0]+f'\nconfigure(root=Path({str(root)!r}),build="3.0.0_master_B42")\n'
    subprocess.run([sys.executable,'-c',payload],check=True)
    assert '/pre-release agate' in (root/'sources.list.d/simaai-channel.list').read_text()


def test_release_preserves_keyring_scope_from_deb822(tmp_path):
    root=apt_root(tmp_path,'deb https://debian.neat.sima.ai/daily agate non-free\n')
    (root/'sources.list.d/release.sources').write_text(
        'Types: deb\nURIs: https://repo.sima.ai/elxr/deb/release\nSuites: agate\nComponents: non-free\nArchitectures: arm64\nSigned-By: /usr/share/keyrings/sima.gpg\n')
    configure(root,'3.0.0')
    text=(root/'sources.list.d/simaai-channel.list').read_text()
    assert 'signed-by=/usr/share/keyrings/sima.gpg' in text
    assert 'arch=arm64' in text
    assert 'trusted=yes' not in text


def test_unrelated_deb822_is_byte_preserved(tmp_path):
    root=apt_root(tmp_path,'deb https://debian.neat.sima.ai/pre-release agate non-free\n')
    text='# Debian main\nTypes: deb\nURIs: https://deb.debian.org/debian\nSuites: bookworm\nComponents: main\n\n'
    source=root/'sources.list.d/debian.sources'
    source.write_text(text)
    configure(root,'3.0.0_master_B42')
    assert source.read_text()==text
