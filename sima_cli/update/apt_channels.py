"""Firmware-aligned SiMa APT source policy; target execution uses only stdlib."""
from pathlib import Path
import re
import shlex

URLS = {
    'daily': 'https://debian.neat.sima.ai/daily',
    'pre-release': 'https://debian.neat.sima.ai/pre-release',
    'release': 'https://repo.sima.ai/elxr/deb/release',
}


def channel_for_build(build):
    build = str(build or '').strip().strip('\"\'')
    if re.fullmatch(r'\d+\.\d+\.\d+~pre\d+', build):
        return 'pre-release'
    if re.match(r'^\d+\.\d+\.\d+_pre-release_', build):
        return 'pre-release'
    if re.match(r'^\d+\.\d+\.\d+_(?:daily|develop)_', build) or re.match(r'^\d+\.\d+\.\d+~git', build):
        return 'daily'
    if re.match(r'^\d+\.\d+\.\d+_master_', build):
        return 'pre-release'
    if re.fullmatch(r'\d+\.\d+\.\d+(?:_(?:release|ga)(?:_[A-Za-z0-9.-]+)?)?', build, re.I):
        return 'release'
    return None


def url_channel(url):
    url = url.rstrip('/')
    for channel, target in URLS.items():
        if url == target:
            return channel
    if url in ('https://debian.neat.sima.ai', 'https://debian.neat.sima.ai/elxr/deb/pre-release',
               'http://sw-web.eng.sima.ai/deb/pre-release', 'https://sw-web.eng.sima.ai/deb/pre-release'):
        return 'pre-release'
    if url in ('http://sw-web.eng.sima.ai/deb/daily', 'https://sw-web.eng.sima.ai/deb/daily'):
        return 'daily'
    return None


LINE = re.compile(r'^(\s*deb(?:-src)?\s+)(?:\[([^\]\n]*)\]\s+)?(\S+)\s+(\S+)([^\n]*)$')


def running_build(root):
    # /etc/buildinfo is image identity; never infer the channel from distro suite.
    for name in ('buildinfo', 'build'):
        path = root.parent / name
        if path.is_file():
            match = re.search(r'^\s*SIMA_BUILD_VERSION\s*=\s*(.+?)\s*$', path.read_text(), re.M)
            if match:
                return match[1].strip().strip('\"\'')
    return ''


def deb822_fields(block):
    fields = {}
    last = None
    for line in block.splitlines():
        if line.startswith((' ', '\t')) and last:
            fields[last] += '\n' + line
        elif ':' in line and not line.lstrip().startswith('#'):
            key, value = line.split(':', 1)
            last = key.lower()
            fields[last] = value.strip()
    return fields


def without_field(block, name):
    result = []
    dropping = False
    for line in block.splitlines():
        if line.startswith((' ', '\t')):
            if not dropping:
                result.append(line)
        else:
            dropping = line.lower().startswith(name.lower() + ':')
            if not dropping:
                result.append(line)
    return '\n'.join(result)


def disable_deb822(text):
    blocks = []
    changed = False
    for block in re.split(r'\n\s*\n', text.strip()):
        fields = deb822_fields(block)
        urls = fields.get('uris', '').split()
        if fields.get('enabled', 'yes').lower() != 'no' and any(url_channel(u) for u in urls):
            changed = True
            # Split mixed stanzas so unrelated Debian sources stay enabled.
            unrelated = [u for u in urls if not url_channel(u)]
            if unrelated:
                kept = without_field(block, 'URIs') + '\nURIs: ' + ' '.join(unrelated)
                blocks.append(kept)
                block = without_field(block, 'URIs') + '\nURIs: ' + ' '.join(u for u in urls if url_channel(u))
            block = without_field(block, 'Enabled') + '\nEnabled: no'
        blocks.append(block)
    return '\n\n'.join(blocks) + '\n' if changed else text


def configure(root=Path('/etc/apt'), build=None, channel=None):
    root = Path(root)
    build = running_build(root) if build is None else build
    channel = channel or channel_for_build(build)
    paths = [root / 'sources.list'] + sorted((root / 'sources.list.d').glob('*.list')) + sorted((root / 'sources.list.d').glob('*.sources'))
    contents = {p:p.read_text() for p in paths if p.is_file()}
    active = []
    for path, text in contents.items():
        if path.suffix == '.sources':
            for block in re.split(r'\n\s*\n', text.strip()):
                f = deb822_fields(block)
                if f.get('enabled', 'yes').lower() == 'no':
                    continue
                for url in f.get('uris', '').split():
                    if url_channel(url):
                        active.append((url_channel(url), f.get('suites', 'bookworm').split()[0]))
        else:
            for line in text.splitlines():
                m = LINE.match(line)
                if m and url_channel(m[3]):
                    active.append((url_channel(m[3]), m[4]))
    if channel is None:
        choices = {c for c, _ in active}
        if len(choices) > 1:
            raise ValueError('Conflicting SiMa APT channels with unknown firmware identity; select a firmware build explicitly')
        channel = next(iter(choices), None)
    if channel is None:
        return None
    if channel not in URLS:
        raise ValueError('Unsupported SiMa APT channel')
    suite = ('agate' if re.match(r'^3\.', str(build)) else
             'bookworm' if re.match(r'^2\.', str(build)) else
             next((s for _, s in active), 'bookworm'))
    # Preserve source architecture restrictions, but remove channel-specific trust.
    options = []
    for text in contents.values():
        for line in text.splitlines():
            m = LINE.match(line)
            if m and url_channel(m[3]):
                options = [v for v in (m[2] or '').split() if v.lower().startswith('arch=')]
                break
        if options:
            break
    if channel == 'release':
        for text in contents.values():
            for line in text.splitlines():
                m = LINE.match(line)
                if m and url_channel(m[3]) == 'release':
                    options.extend(v for v in (m[2] or '').split() if v.lower().startswith('signed-by=') and v not in options)
    for path, text in contents.items():
        if path.suffix != '.sources':
            continue
        for block in re.split(r'\n\s*\n', text.strip()):
            fields = deb822_fields(block)
            if fields.get('enabled', 'yes').lower() == 'no':
                continue
            if not any(url_channel(u) == channel for u in fields.get('uris', '').split()):
                continue
            if fields.get('architectures') and not any(v.startswith('arch=') for v in options):
                options.append('arch=' + ','.join(fields['architectures'].split()))
            if channel == 'release' and fields.get('signed-by'):
                signing_key = fields['signed-by']
                if '\n' in signing_key or not signing_key.startswith('/'):
                    raise ValueError('Preserve the release signing key in a keyring file before switching APT channels')
                option = 'signed-by=' + signing_key
                if option not in options:
                    options.append(option)
    if channel != 'release':
        options.append('trusted=yes')
    option_text = '[' + ' '.join(options) + '] ' if options else ''
    canonical = root / 'sources.list.d/simaai-channel.list'
    changes = {}
    for path, text in contents.items():
        if path == canonical:
            continue
        if path.suffix == '.sources':
            updated = disable_deb822(text)
        else:
            updated = '\n'.join('# ' + line if (LINE.match(line) and url_channel(LINE.match(line)[3])) else line for line in text.splitlines()) + '\n'
        if updated != text:
            changes[path] = updated
    # Keep non-SiMa entries even if an operator put them in our managed file.
    extra = ''
    for line in contents.get(canonical, '').splitlines():
        m = LINE.match(line)
        if line.strip() and not line.startswith('# Managed by') and not (m and url_channel(m[3])):
            extra += line + '\n'
    generated = '# Managed by firmware APT channel selection\n' + extra + f'deb {option_text}{URLS[channel]} {suite} non-free\n'
    if generated != contents.get(canonical):
        changes[canonical] = generated
    # Write all backups before changing any source; replace each file atomically.
    for path in changes:
        if path in contents:
            backup = root / 'kerrigan-backups' / path.relative_to(root)
            backup = backup.with_name(backup.name + '.kerrigan-backup')
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_text(contents[path])
    for path, text in changes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name('.' + path.name + '.tmp')
        tmp.write_text(text)
        tmp.chmod(0o644)
        tmp.replace(path)
    return channel


def configure_command(build=None):
    script = Path(__file__).read_text() + '\nconfigure(build=' + repr(build) + ')\n'
    return 'python3 -c ' + shlex.quote(script)
