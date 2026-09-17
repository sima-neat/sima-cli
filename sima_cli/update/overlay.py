"""Read-only overlay inventory shared by local and remote 3.0 updates."""
import inspect
import json
import shlex

import click


def collect_overlay():
    """Executed on the device using only Python's standard library."""
    import json
    import os
    import re
    import stat
    import subprocess
    from collections import Counter

    report = {'status': 'unavailable', 'warnings': [], 'entries': [], 'packages': {}}
    try:
        mounts = json.loads(subprocess.check_output(
            ['findmnt', '--json', '--mountpoint', '/', '--output', 'FSTYPE,OPTIONS'],
            universal_newlines=True))['filesystems']
        mount = mounts[0]
        report['filesystem'] = mount['fstype']
        if mount['fstype'] != 'overlay':
            report['status'] = 'disabled'
            return report
        options = dict(item.split('=', 1) for item in mount['options'].split(',') if '=' in item)
        upper = options.get('upperdir', '')
        lower = options.get('lowerdir', '')
        # eLxr creates the overlay before pivot_root: mount options retain
        # the old namespace paths after / becomes /oldroot and data moves.
        if lower == '/' and upper == '/run/data/overlay/upper':
            oldroot = json.loads(subprocess.check_output(
                ['findmnt', '--json', '--mountpoint', '/oldroot', '--output', 'SOURCE,FSTYPE,OPTIONS'],
                universal_newlines=True))['filesystems'][0]
            data = json.loads(subprocess.check_output(
                ['findmnt', '--json', '--mountpoint', '/data', '--output', 'FSTYPE,OPTIONS'],
                universal_newlines=True))['filesystems'][0]
            if oldroot['fstype'] != 'ext4' or data['fstype'] != 'ext4':
                raise ValueError('Cannot resolve eLxr overlay paths after pivot_root')
            with open('/proc/cmdline') as source:
                root_argument = next((value[5:] for value in source.read().split()
                                      if value.startswith('root=')), '')
            if (not root_argument.startswith('/dev/') or
                    os.path.realpath(oldroot.get('source', '')) != os.path.realpath(root_argument)):
                raise ValueError('/oldroot is not the running root device')
            lower, upper = '/oldroot', '/data/overlay/upper'
            report['warnings'].append('Using the eLxr lower root at /oldroot; this is an image baseline, not a cryptographically verified manifest.')
        report.update(upper=upper, lower=lower, status='enabled')
        # Multiple lower layers need a merged baseline, which this first version
        # deliberately does not attempt to reconstruct.
        baseline = lower if lower != '/' and lower.startswith('/') and ':' not in lower and os.path.isdir(lower) else None
        report['baseline'] = baseline
        if not baseline:
            report['warnings'].append('No accessible single lower layer; package comparison is unavailable.')
        if not upper.startswith('/') or not os.path.isdir(upper):
            raise ValueError('Overlay upper directory is inaccessible')

        def packages(path):
            result = {}
            with open(path) as source:
                for paragraph in source.read().split('\n\n'):
                    fields = dict(line.split(': ', 1) for line in paragraph.splitlines()
                                  if ': ' in line and not line.startswith(' '))
                    if fields.get('Status') == 'install ok installed' and 'Package' in fields:
                        name = fields['Package'] + ':' + fields.get('Architecture', 'unknown')
                        result[name] = fields.get('Version', 'unknown')
            if not result:
                raise ValueError('No installed packages found in ' + path)
            return result

        if baseline:
            try:
                current = packages('/var/lib/dpkg/status')
                original = packages(os.path.join(baseline, 'var/lib/dpkg/status'))
                report['packages'] = {
                    'additional': ['%s %s' % (k, current[k]) for k in sorted(current.keys() - original.keys())],
                    'changed': ['%s %s -> %s' % (k, original[k], current[k]) for k in sorted(current.keys() & original.keys()) if current[k] != original[k]],
                    'removed': ['%s %s' % (k, original[k]) for k in sorted(original.keys() - current.keys())],
                }
                marker = next((key for key in original
                               if key.split(':', 1)[0].startswith('simaai-palette-')
                               and key in current), None)
                if marker:
                    image_match = re.search(r'-(\d+)$', original[marker])
                    metadata_match = re.search(r'-(\d+)$', current[marker])
                    if image_match and metadata_match:
                        report['package_builds'] = {
                            'image': image_match.group(1),
                            'metadata': metadata_match.group(1),
                            'source': marker.split(':', 1)[0],
                        }
                if 'package_builds' not in report:
                    build_pairs = []
                    for key in current.keys() & original.keys():
                        image_match = re.search(r'-(\d+)$', original[key])
                        metadata_match = re.search(r'-(\d+)$', current[key])
                        if current[key] != original[key] and image_match and metadata_match:
                            build_pairs.append((image_match.group(1), metadata_match.group(1)))
                    if len(build_pairs) >= 3:
                        image_id, image_count = Counter(pair[0] for pair in build_pairs).most_common(1)[0]
                        metadata_id, metadata_count = Counter(pair[1] for pair in build_pairs).most_common(1)[0]
                        if (image_count / len(build_pairs) >= 0.8 and
                                metadata_count / len(build_pairs) >= 0.8):
                            report['package_builds'] = {
                                'image': image_id,
                                'metadata': metadata_id,
                                'source': 'consensus across %d changed packages' % len(build_pairs),
                            }
                try:
                    manual = set(subprocess.check_output(
                        ['apt-mark', 'showmanual'], stderr=subprocess.DEVNULL,
                        universal_newlines=True).splitlines())
                    additional = current.keys() - original.keys()
                    report['packages']['requested'] = [
                        '%s %s' % (key, current[key]) for key in sorted(additional)
                        if key.split(':', 1)[0] in manual
                    ]
                    report['packages']['dependencies'] = [
                        '%s %s' % (key, current[key]) for key in sorted(additional)
                        if key.split(':', 1)[0] not in manual
                    ]
                except (OSError, subprocess.SubprocessError) as exc:
                    report['warnings'].append(
                        'Could not distinguish requested packages from dependencies: ' + str(exc))
                for name in ('etc/buildinfo', 'etc/os-release'):
                    try:
                        with open(os.path.join(baseline, name)) as source:
                            report.setdefault('identity', {})[name] = source.read(4096).strip()
                    except OSError:
                        pass
            except (OSError, ValueError) as exc:
                report['warnings'].append('Package baseline comparison unavailable: ' + str(exc))

        def walk_error(exc):
            if len(report['warnings']) < 20:
                report['warnings'].append('Incomplete inventory: ' + str(exc))

        for directory, dirs, files in os.walk(upper, followlinks=False, onerror=walk_error):
            dirs.sort()
            for name in sorted(dirs + files):
                path = os.path.join(directory, name)
                relative = os.path.relpath(path, upper)
                try:
                    info = os.lstat(path)
                    attrs = os.listxattr(path, follow_symlinks=False)
                    whiteout = (stat.S_ISCHR(info.st_mode) and info.st_rdev == 0) or any(
                        a in attrs for a in ('trusted.overlay.whiteout', 'user.overlay.whiteout'))
                    opaque = any(a in attrs and os.getxattr(path, a, follow_symlinks=False) == b'y'
                                 for a in ('trusted.overlay.opaque', 'user.overlay.opaque'))
                    if stat.S_ISDIR(info.st_mode) and not opaque:
                        continue
                    category = ('deletions / opaque directories' if whiteout or opaque else
                                'package state / caches / logs' if relative.startswith(('var/lib/dpkg/', 'var/lib/apt/', 'var/cache/', 'var/log/')) else
                                'configuration / services' if relative.startswith('etc/') else
                                'local software candidates' if relative.startswith(('usr/local/', 'opt/')) else
                                'other overlay files')
                    kind = 'whiteout' if whiteout else 'opaque' if opaque else 'symlink' if stat.S_ISLNK(info.st_mode) else 'file'
                    report['entries'].append({'path': '/' + relative, 'category': category, 'kind': kind,
                                              'mode': oct(stat.S_IMODE(info.st_mode)),
                                              'uid': info.st_uid, 'gid': info.st_gid})
                    if len(report['entries']) >= 100000:
                        report['warnings'].append('Inventory capped at 100000 entries; totals are partial.')
                        return report
                except OSError as exc:
                    walk_error(exc)
        report['warnings'].append('Overlay entries include incidental copy-up. Package changes reflect the effective dpkg database, which may itself be stale. File ownership, contents, and installation intent are not inferred.')
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        report['status'] = 'unavailable'
        report['warnings'].append(str(exc))
    return report


def render_overlay(report, state=None, details=False, console=None):
    """Summarize user-relevant changes without presenting files as applications."""
    from collections import Counter
    import re
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = console or Console()
    if report['status'] == 'disabled':
        console.print(Panel('Overlay is disabled. Changes made directly to the system are not analyzed by this report.',
                            title='System customizations'))
        return
    if report['status'] == 'unavailable':
        console.print(Panel(Text('Could not analyze system customizations.\n' +
                                '\n'.join(report.get('warnings', []))), title='System customizations'))
        return

    buildinfo = report.get('identity', {}).get('etc/buildinfo', '')
    match = re.search(r'^SIMA_BUILD_VERSION\s*=\s*(.+)$', buildinfo, re.M)
    baseline = match.group(1).strip() if match else report.get('baseline') or 'unavailable'
    state = state or {}
    next_slot = state.get('next-boot', 'unknown')
    next_version = 'unknown'
    if next_slot in ('A', 'B'):
        prefix = ('active' if state.get('active slot') == next_slot else
                  'fallback' if state.get('fallback slot') == next_slot else None)
        if prefix:
            next_version = state.get(prefix + ' version', 'unknown')
    heading = ('OverlayFS is enabled. Local changes are stored separately from the system image.\n'
               'Compared with running image: ' + baseline)
    if next_slot in ('A', 'B') and next_slot != state.get('running slot'):
        heading += ('\nThe same overlay will apply when slot %s (%s) boots.' %
                    (next_slot, next_version))
    console.print(Panel(Text(heading), title='System customizations'))
    packages = report.get('packages', {})
    package_builds = report.get('package_builds', {})
    if package_builds and package_builds['image'] != package_builds['metadata']:
        console.print(Panel(
            Text('Image build ID: B%s\n'
                 'Overlay package-metadata build ID: B%s\n\n'
                 'Why: the persistent overlay retained the package database from an earlier image. '
                 'This commonly happens when apt install was used on the previous platform version.\n'
                 'Risk: APT/dpkg may report incorrect installed versions or select incompatible packages, '
                 'while the filesystem contains a mixture of image and overlay files.' %
                 (package_builds['image'], package_builds['metadata'])),
            title='Package metadata mismatch', style='yellow', border_style='yellow'))
    if packages:
        requested = [value.split()[0].split(':')[0] for value in packages.get('requested', [])]
        dependencies = [value.split()[0].split(':')[0] for value in packages.get('dependencies', [])]
        additional = [value.split()[0].split(':')[0] for value in packages.get('additional', [])]
        if 'requested' in packages:
            console.print(Text('Packages explicitly installed after the image was built (%d):' % len(requested),
                               style='bold blue'))
            console.print(Text('  ' + (', '.join(requested) if requested else 'None recorded.')))
            console.print(Text('Supporting dependencies: %d' % len(dependencies)))
            if details and dependencies:
                console.print(Text('  ' + ', '.join(dependencies)))
        else:
            console.print(Text('Additional system packages (%d; installation intent is unknown):' %
                               len(additional), style='bold blue'))
            console.print(Text('  ' + (', '.join(additional) if additional else 'None recorded.')))
        changed = packages.get('changed', [])
        removed = packages.get('removed', [])
        console.print(Text('Image package versions overridden by overlay metadata: %d' % len(changed),
                           style='bold blue'))
        if details:
            for value in changed:
                package, versions = value.split(' ', 1)
                image_version, overlay_version = versions.split(' -> ', 1)
                console.print(Text('  %s\n    Image: %s\n    Overlay: %s' %
                                   (package, image_version, overlay_version)))
        console.print(Text('Image packages absent from the overlay package database: %d' % len(removed),
                           style='bold blue'))
        if details:
            for value in removed:
                package, image_version = value.split(' ', 1)
                console.print(Text('  %s\n    Image: %s\n    Overlay: not installed' %
                                   (package, image_version)))
    else:
        console.print('Package comparison is unavailable; additional installations could not be determined.')

    entries = report.get('entries', [])
    counts = Counter(entry['category'] for entry in entries)
    software = Counter()
    for entry in entries:
        if entry['category'] == 'local software candidates':
            parts = entry['path'].split('/')
            root = '/'.join(parts[:3] if entry['path'].startswith('/opt/') else parts[:4])
            software[root] += 1
    if software:
        console.print(Text('Software directories stored in the overlay:', style='bold blue'))
        for path in sorted(software):
            name = path.rstrip('/').rsplit('/', 1)[-1]
            console.print(Text('  • %s (%s)' % (name, path)))

    if details:
        if software:
            console.print(Text('Software directories with local files (file counts, not application counts):',
                               style='bold blue'))
            for path, count in software.most_common(8):
                console.print(Text('  %s — %s files/links' % (path, format(count, ','))))
            if len(software) > 8:
                console.print(Text('  ... %d more directories' % (len(software) - 8)))
        table = Table(title='Overlay details')
        table.add_column('Category')
        table.add_column('Entries', justify='right')
        table.add_column('Meaning')
        for key, label, explanation in (
            ('configuration / services', 'Settings and services', 'May include device identity, networking, and service setup.'),
            ('deletions / opaque directories', 'Hidden base files/directories', 'May be system-generated; not necessarily user deletions.'),
            ('other overlay files', 'Other local files', 'Includes home-directory files and installation downloads.'),
            ('package state / caches / logs', 'System bookkeeping', 'Package records, caches, and logs; not additional applications.'),
        ):
            table.add_row(label, format(counts[key], ','), explanation)
        console.print(table)

    has_changes = bool(packages.get('additional') or packages.get('changed') or
                       packages.get('removed') or software)
    conclusion = ('This device has custom software or package changes that may need '
                  'reinstallation after an overlay reset.' if has_changes else
                  'No additional software or package changes were detected.')
    if next_slot in ('A', 'B') and next_slot != state.get('running slot'):
        conclusion += ('\nRebooting now will use slot %s (%s) with the current shared overlay.' %
                       (next_slot, next_version))
    console.print(Panel(
        conclusion + '\n'
        'Keeping the overlay may carry old files and package records into the new image.\n'
        'Clearing it removes local changes; custom software and settings must be reinstalled.',
        title='Review before updating', style='yellow', border_style='yellow'))
    if details:
        if package_builds:
            console.print('Package build IDs are derived from %s in the image and overlay package databases.' %
                          package_builds['source'])
        console.print('Package results rely on the current package database, which may be stale. '
                      'Some files are recorded automatically by the system; this report does not prove who changed '
                      'them or whether their contents differ. The baseline is not cryptographically verified.')
    for warning in report.get('warnings', []):
        if not warning.startswith(('Using the eLxr lower root', 'Overlay entries include incidental')):
            console.print(Text('Analysis limitation: ' + warning))


def inspect_overlay(target, state=None, details=False):
    from rich.console import Console

    source = inspect.getsource(collect_overlay) + '\nimport json\nprint(json.dumps(collect_overlay()))\n'
    console = Console()
    try:
        with console.status(
                'Analyzing packages, configuration, and local software...', spinner='line'):
            report = json.loads(target.run('python3 -c ' + shlex.quote(source)))
        if not isinstance(report, dict) or report.get('status') not in ('enabled', 'disabled', 'unavailable'):
            raise ValueError('Invalid overlay report')
    except (click.ClickException, ValueError, TypeError) as exc:
        report = {'status': 'unavailable', 'warnings': ['Could not analyze overlay: ' + str(exc)]}
    render_overlay(report, state=state, details=details, console=console)
    return report
