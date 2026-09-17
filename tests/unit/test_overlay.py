import json
import os
from unittest.mock import patch, MagicMock

import click

from sima_cli.update.overlay import collect_overlay, inspect_overlay, render_overlay
from sima_cli.update import swu


def test_disabled_is_not_an_empty_inventory():
    with patch('subprocess.check_output', return_value=json.dumps({'filesystems': [{'fstype': 'ext4'}]})):
        assert collect_overlay()['status'] == 'disabled'


def test_inventory_packages_whiteouts_and_symlinks(tmp_path):
    lower = tmp_path / 'lower'
    upper = tmp_path / 'upper'
    (lower / 'var/lib/dpkg').mkdir(parents=True)
    (upper / 'etc/systemd/system').mkdir(parents=True)
    (upper / 'opt').mkdir()
    (upper / 'etc/systemd/system/custom.service').write_text('secret contents must not be printed')
    (upper / 'opt/link').symlink_to('/outside')
    original = ('Package: base\nArchitecture: arm64\nVersion: 1\nStatus: install ok installed\n\n'
                'Package: simaai-palette-modalix\nArchitecture: arm64\nVersion: 3.0-1454\nStatus: install ok installed\n\n')
    current = (original.replace('Version: 1', 'Version: 2', 1)
               .replace('Version: 3.0-1454', 'Version: 3.0-1369'))
    current += 'Package: extra\nArchitecture: arm64\nVersion: 1\nStatus: install ok installed\n\n'
    (lower / 'var/lib/dpkg/status').write_text(original)
    live = tmp_path / 'status'
    live.write_text(current)
    real_open = open

    def read(path, *args, **kwargs):
        return real_open(live if path == '/var/lib/dpkg/status' else path, *args, **kwargs)

    mount = {'filesystems': [{'fstype': 'overlay', 'options': 'rw,lowerdir=%s,upperdir=%s' % (lower, upper)}]}

    def command_output(command, **kwargs):
        if command[:2] == ['apt-mark', 'showmanual']:
            return 'extra\n'
        return json.dumps(mount)

    with patch('subprocess.check_output', side_effect=command_output), patch('builtins.open', side_effect=read), \
            patch('os.listxattr', return_value=[], create=True):
        report = collect_overlay()
    assert report['packages']['additional'] == ['extra:arm64 1']
    assert report['packages']['requested'] == ['extra:arm64 1']
    assert report['packages']['dependencies'] == []
    assert report['packages']['changed'] == [
        'base:arm64 1 -> 2',
        'simaai-palette-modalix:arm64 3.0-1454 -> 3.0-1369',
    ]
    assert report['package_builds'] == {
        'image': '1454', 'metadata': '1369', 'source': 'simaai-palette-modalix'}
    assert any(e['path'] == '/opt/link' and e['kind'] == 'symlink' for e in report['entries'])
    assert any(e['category'] == 'configuration / services' for e in report['entries'])
    assert 'secret contents' not in json.dumps(report)


def test_unavailable_collection_and_command_failure_are_visible(capsys):
    with patch('subprocess.check_output', side_effect=OSError('missing findmnt')):
        assert collect_overlay()['status'] == 'unavailable'
    target = MagicMock()
    target.run.side_effect = click.ClickException('python3 unavailable')
    assert inspect_overlay(target)['status'] == 'unavailable'
    assert 'python3 unavailable' in capsys.readouterr().out


def test_remote_inspection_uses_same_target_and_closes_it():
    with patch('sima_cli.update.remote.get_remote_board_info', return_value=('modalix', '3.0.0', '', False, 'elxr')), \
            patch.object(swu, 'Target') as target, patch.object(swu, 'inspect_target'), \
            patch.object(swu, 'inspect_overlay') as overlay:
        assert swu.handle_update(None, ip='192.0.2.1', inspect=True)
        overlay.assert_called_once_with(target.return_value, state=swu.inspect_target.return_value,
                                        details=False)
        target.return_value.close.assert_called_once()


def test_update_analyzes_before_bundle_selection_even_with_yes():
    order = []
    with patch.object(swu, 'Target'), patch.object(swu, 'prepare_key', return_value=(None, None)), \
            patch.object(swu, 'preflight'), \
            patch.object(swu, 'inspect_overlay',
                         side_effect=lambda target, **kwargs: (order.append('overlay') or {})), \
            patch.object(swu, 'resolve_bundle', side_effect=lambda *a, **k: order.append('bundle')):
        try:
            swu.update_system(None, 'modalix', auto_confirm=True)
        except click.Abort:
            pass
    assert order == ['overlay', 'bundle']


def test_elxr_pivot_paths_resolved_without_using_live_root():
    mounts = [
        {'filesystems': [{'fstype': 'overlay', 'options': 'lowerdir=/,upperdir=/run/data/overlay/upper'}]},
        {'filesystems': [{'source': '/dev/mapper/rootfs', 'fstype': 'ext4', 'options': 'rw'}]},
        {'filesystems': [{'fstype': 'ext4', 'options': 'rw'}]},
    ]
    def read(path, *args, **kwargs):
        if path == '/proc/cmdline':
            from io import StringIO
            return StringIO('console=ttyS0 root=/dev/mapper/rootfs ro')
        raise OSError('unreadable ' + str(path))

    with patch('subprocess.check_output', side_effect=[json.dumps(m) for m in mounts]), \
            patch('os.path.isdir', return_value=True), patch('builtins.open', side_effect=read), \
            patch('os.walk', return_value=[]):
        report = collect_overlay()
    assert report['baseline'] == '/oldroot'
    assert report['upper'] == '/data/overlay/upper'
    assert report['packages'] == {}
    assert any('unavailable' in w for w in report['warnings'])


def test_elxr_pivot_rejects_oldroot_from_another_device():
    mounts = [
        {'filesystems': [{'fstype': 'overlay', 'options': 'lowerdir=/,upperdir=/run/data/overlay/upper'}]},
        {'filesystems': [{'source': '/dev/dm-9', 'fstype': 'ext4', 'options': 'rw'}]},
        {'filesystems': [{'fstype': 'ext4', 'options': 'rw'}]},
    ]

    def read(path, *args, **kwargs):
        from io import StringIO
        if path == '/proc/cmdline':
            return StringIO('root=/dev/dm-0 ro')
        raise AssertionError(path)

    with patch('subprocess.check_output', side_effect=[json.dumps(m) for m in mounts]), \
            patch('builtins.open', side_effect=read):
        report = collect_overlay()
    assert report['status'] == 'unavailable'
    assert any('not the running root device' in warning for warning in report['warnings'])


def test_executes_self_contained_readonly_collector():
    target = MagicMock()
    target.run.return_value = json.dumps({'status': 'disabled'})
    with patch('rich.console.Console.status') as status:
        inspect_overlay(target)
    status.return_value.__enter__.assert_called_once()
    status.return_value.__exit__.assert_called_once()
    assert status.call_args.args[0] == 'Analyzing packages, configuration, and local software...'
    assert status.call_args.kwargs['spinner'] == 'line'
    import shlex
    command = shlex.split(target.run.call_args.args[0])
    assert command[:2] == ['python3', '-c']
    compile(command[2], '<collector>', 'exec')


def test_discovery_and_report_share_one_console():
    target = MagicMock()
    target.run.return_value = json.dumps({'status': 'disabled'})
    with patch('sima_cli.update.overlay.render_overlay') as render:
        inspect_overlay(target)
    console = render.call_args.kwargs['console']
    assert console is not None
    assert render.call_args.kwargs['state'] is None


def test_whiteouts_and_opaque_directories_are_reported(tmp_path):
    (tmp_path / 'deleted').touch()
    (tmp_path / 'opaque').mkdir()
    mount = {'filesystems': [{'fstype': 'overlay', 'options': 'lowerdir=/missing:other,upperdir=' + str(tmp_path)}]}

    def attrs(path, **kwargs):
        return ['trusted.overlay.whiteout'] if path.endswith('deleted') else ['trusted.overlay.opaque']

    with patch('subprocess.check_output', return_value=json.dumps(mount)), \
            patch('os.listxattr', side_effect=attrs, create=True), \
            patch('os.getxattr', return_value=b'y', create=True):
        report = collect_overlay()
    assert report['baseline'] is None
    assert {e['kind'] for e in report['entries']} == {'whiteout', 'opaque'}
    assert all(e['category'] == 'deletions / opaque directories' for e in report['entries'])
    assert report['packages'] == {}


def test_summary_uses_software_roots_and_explains_counts(capsys):
    from sima_cli.update.overlay import render_overlay
    render_overlay({
        'status': 'enabled', 'baseline': '/oldroot',
        'identity': {'etc/buildinfo': 'SIMA_BUILD_VERSION = B1371', 'etc/os-release': 'BUG_REPORT_URL=hidden'},
        'packages': {'additional': ['git:arm64 1', 'jq:arm64 2'],
                     'requested': ['git:arm64 1'], 'dependencies': ['jq:arm64 2'],
                     'changed': [], 'removed': []},
        'entries': [{'path': '/opt/kerrigan/bundles/a/lib/module.py', 'kind': 'file', 'category': 'local software candidates'}],
    }, state={'running slot': 'A', 'next-boot': 'B', 'fallback slot': 'B',
              'fallback version': 'B1454'})
    output = capsys.readouterr().out
    assert 'git' in output
    assert 'Supporting dependencies: 1' in output
    assert 'kerrigan (/opt/kerrigan)' in output
    assert 'module.py' not in output
    assert 'BUG_REPORT_URL' not in output
    assert 'file counts' not in output
    assert 'This device has custom software' in output
    assert 'Review required' not in output
    assert 'Package metadata could not be compared' in output
    assert 'B1371' in output
    assert 'slot B (B1454)' in output


def test_verbose_summary_includes_diagnostic_counts(capsys):
    from sima_cli.update.overlay import render_overlay
    render_overlay({
        'status': 'enabled', 'baseline': '/oldroot',
        'packages': {
            'additional': [], 'requested': [], 'dependencies': [],
            'changed': ['base:arm64 1454 -> 1369', 'camera:arm64 2.0 -> 1.0'],
            'removed': ['linux-libc-dev:arm64 1454'],
        },
        'entries': [{'path': '/usr/local/tool/file', 'kind': 'file',
                     'category': 'local software candidates'}],
    }, details=True)
    output = capsys.readouterr().out
    assert 'file counts, not application counts' in output
    assert 'Overlay details' in output
    assert 'baseline is not' in output
    assert 'cryptographically verified' in output
    assert 'base:arm64' in output
    assert 'Image: 1454' in output
    assert 'Overlay: 1369' in output
    assert 'camera:arm64' in output
    assert 'Overlay: not installed' in output


def test_normal_summary_hides_package_version_details(capsys):
    render_overlay({
        'status': 'enabled', 'baseline': '/oldroot',
        'packages': {
            'additional': [], 'requested': [], 'dependencies': [],
            'changed': ['base:arm64 1454 -> 1369'],
            'removed': ['linux-libc-dev:arm64 1454'],
        },
        'package_builds': {
            'image': '1454', 'metadata': '1369', 'source': 'simaai-palette-modalix'},
        'entries': [],
    })
    output = capsys.readouterr().out
    assert 'Image package versions overridden by overlay metadata: 1' in output
    assert 'Image packages absent from the overlay package database: 1' in output
    assert 'base:arm64' not in output
    assert 'linux-libc-dev:arm64' not in output
    assert 'Image build ID: B1454' in output
    assert 'Overlay package-metadata build ID: B1369' in output
    assert 'persistent overlay retained the package database' in output
    assert 'earlier' in output
    assert 'apt install was used' in output
    assert 'previous' in output
    assert 'platform version' in output
    assert 'APT/dpkg may report incorrect installed' in output
    assert 'versions' in output
    assert 'How to correct it:' not in output
    assert 'future sima-cli update' not in output


def test_review_panel_says_selected_build_decides_overlay_reset(capsys):
    render_overlay({
        'status': 'enabled',
        'packages': {'additional': [], 'changed': [], 'removed': []},
        'package_builds': {'image': '1454', 'metadata': '1454'},
        'entries': [],
    })
    output = capsys.readouterr().out
    assert 'Package metadata matches the running image' in output
    assert 'depends on the build selected for the update' in output


def test_before_updating_panel_uses_warning_color():
    with patch('rich.console.Console.print') as output:
        render_overlay({'status': 'enabled', 'packages': {}, 'entries': []})
    panel = next(call.args[0] for call in output.call_args_list
                 if getattr(call.args[0], 'title', None) == 'Review before updating')
    assert str(panel.style) == 'yellow'
    assert str(panel.border_style) == 'yellow'


def test_system_customizations_panel_fits_its_content():
    with patch('rich.console.Console.print') as output:
        render_overlay({'status': 'enabled', 'packages': {}, 'entries': []})
    panel = next(call.args[0] for call in output.call_args_list
                 if getattr(call.args[0], 'title', None) == 'System customizations')
    assert panel.expand is False
    assert panel.width == 68


def test_overlay_section_headings_are_blue():
    from rich.text import Text

    with patch('rich.console.Console.print') as output:
        render_overlay({
            'status': 'enabled', 'packages': {
                'additional': [], 'requested': [], 'dependencies': [],
                'changed': [], 'removed': [],
            },
            'entries': [{'path': '/opt/tool/file', 'kind': 'file',
                         'category': 'local software candidates'}],
        }, details=True)
    headings = [call.args[0] for call in output.call_args_list
                if call.args and isinstance(call.args[0], Text)
                and ('packages' in call.args[0].plain.lower()
                     or 'software directories' in call.args[0].plain.lower())]
    assert headings
    assert all('blue' in str(heading.style) for heading in headings)
