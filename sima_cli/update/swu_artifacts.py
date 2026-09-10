"""Resolve signed full-system bundles without guessing internal build filenames."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import click
import requests

from sima_cli.auth.devportal import login_external
from sima_cli.update.query import ARTIFACTORY_BASE_URL
from sima_cli.utils.config import get_auth_token


DAILY_MIRROR = 'https://artifacts.neat.sima.ai/daily-platform-images/'


class ArtifactoryUnavailable(click.ClickException):
    """Artifactory cannot supply a build because access is unavailable."""


class BundleSource(str):
    """Download URL carrying build identity and optional mirror integrity data."""
    def __new__(cls, url, version=None, size=None, sha256=None):
        source = super().__new__(cls, url)
        source.version = version
        source.size = size
        source.sha256 = sha256
        return source


def is_mirror_source(source):
    return source.startswith(DAILY_MIRROR)


def artifactory_failure_reason(error, post_selection=False):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ArtifactoryUnavailable):
            return error.format_message()
        if isinstance(error, requests.RequestException):
            status = error.response.status_code if error.response is not None else None
            if status is not None and (post_selection or status in (401, 403) or 500 <= status < 600):
                return f'Artifactory returned HTTP {status}.'
            if isinstance(error, (requests.ConnectionError, requests.Timeout)):
                return 'Artifactory could not be reached.'
            if post_selection:
                return 'The selected Artifactory bundle request failed.'
        error = error.__cause__ or error.__context__
    return None


def _matching_builds(builds, requested):
    exact = [b for b in builds if b['version'] == requested]
    if exact or not requested:
        return exact or builds
    if re.fullmatch(r'\d+\.\d+(?:\.\d+)?', requested):
        return [b for b in builds if re.match(re.escape(requested) + r'(?=$|[._-])', b['version'])]
    return [b for b in builds if requested.lower() in b['version'].lower()]


def mirror_bundles(board, requested):
    """Read only complete palette SWUs from the public daily manifest."""
    try:
        with requests.Session() as session:
            # No Artifactory credentials or ambient .netrc credentials on the mirror.
            session.trust_env = False
            response = session.get(DAILY_MIRROR + 'index.json', timeout=30)
            response.raise_for_status()
            index = response.json()
        if index.get('schema_version') != 1 or index.get('platform') != board:
            raise ValueError('unsupported index schema or platform')
        builds = []
        seen = set()
        for entry in index['builds']:
            version = entry['name']
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', version) or version in ('.', '..'):
                raise ValueError('invalid build name')
            if version in seen:
                raise ValueError('duplicate build name')
            seen.add(version)
            release = release_tuple(version)
            if not release or release < (3, 0, 0):
                continue
            files = [f for f in entry['files'] if re.fullmatch(
                r'artifacts/palette/elxr-palette-' + re.escape(board) + r'-[A-Za-z0-9_.-]+\.swu', f['path'])]
            if not files:
                continue
            if len(files) != 1:
                raise ValueError(f'multiple palette SWUs for {version}')
            artifact = files[0]
            key = f"daily-platform-images/{version}/{artifact['path']}"
            if artifact['key'] != key or not re.fullmatch(r'[0-9a-fA-F]{64}', artifact['sha256']):
                raise ValueError('invalid artifact key or checksum')
            if type(artifact['size']) is not int or artifact['size'] <= 0 or type(entry['build_number']) is not int:
                raise ValueError('invalid artifact size or build number')
            builds.append({'version': version, 'build_number': entry['build_number'],
                           'url': BundleSource(DAILY_MIRROR + quote(version + '/' + artifact['path'], safe='/'),
                                               version, artifact['size'], artifact['sha256'].lower())})
        builds.sort(key=lambda b: (b['build_number'], b['version']), reverse=True)
        return _matching_builds(builds, requested)
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise click.ClickException(
            'Unable to read the daily platform build index. Check your network and mirror availability. '
            'No firmware was installed.'
        ) from exc


def select_mirror_bundle(requested, board, reason, exact=False):
    click.echo(f'{reason} Using the public daily platform mirror.')
    builds = mirror_bundles(board, requested)
    if exact:
        builds = [b for b in builds if b['version'] == requested]
    if not builds:
        raise click.ClickException(
            f'No matching palette SWU for {requested or "latest"} in the retained daily builds. '
            'The build may have expired or may not contain a palette SWU. No firmware was installed.'
        )
    if len(builds) == 1:
        return builds[0]['url']
    from InquirerPy import inquirer
    width = max(len(b['version']) for b in builds)
    selected = inquirer.fuzzy(message='Select a daily SWU build (newest first):', choices=[
        {'name': f"{b['version']:<{width}}  Build {b['build_number']}", 'value': b['version']}
        for b in builds
    ]).execute()
    return next((b['url'] for b in builds if b['version'] == selected), None)


def fallback_for_bundle(source, board, error):
    # Discovery is deliberately stricter; any failed artifact request after
    # selection may retry the same build, including 404 races and protocol errors.
    reason = artifactory_failure_reason(error, post_selection=True)
    version = getattr(source, 'version', None)
    if reason and version and source.startswith(ARTIFACTORY_BASE_URL.rstrip('/') + '/'):
        return select_mirror_bundle(version, board, reason, exact=True)
    return None


def release_tuple(version):
    match = re.match(r'^(\d+)\.(\d+)(?:\.(\d+))?(?=$|[_-])', (version or '').strip().strip('\"\''))
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _created(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _internal_headers():
    token = get_auth_token(internal=True)
    if not token or not token.strip():
        raise ArtifactoryUnavailable(
            'Artifactory login is required on this machine. Run `sima-cli -i login`, then retry the update. No firmware was installed.'
        )
    return {'Authorization': 'Bearer ' + token}


def internal_bundles(board, keyword):
    if not re.fullmatch(r'[a-z0-9-]+', board):
        raise click.ClickException('Invalid board identity.')
    if not ARTIFACTORY_BASE_URL.startswith(('https://', 'http://')):
        raise ArtifactoryUnavailable('Artifactory is not configured on this machine.')
    criteria = {'repo': 'soc-images', 'type': 'file',
                'path': {'$match': f'elxr/bsp/{board}/*/artifacts/palette'},
                'name': {'$match': f'elxr-palette-{board}-*.swu'}}
    headers = _internal_headers()
    headers['Content-Type'] = 'text/plain'
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(ARTIFACTORY_BASE_URL + '/api/search/aql',
                                data='items.find(' + json.dumps(criteria) + ').include("repo","path","name","created","size")',
                                headers=headers, timeout=30)
        response.raise_for_status()
        items = response.json().get('results', [])
    finally:
        session.close()
    builds = {}
    for item in items:
        parts = item['path'].split('/')
        if len(parts) != 6 or parts[:3] != ['elxr', 'bsp', board] or parts[4:] != ['artifacts', 'palette']:
            continue
        version = parts[3]
        if not release_tuple(version) or release_tuple(version) < (3, 0, 0):
            continue
        if keyword:
            if re.fullmatch(r'\d+\.\d+(?:\.\d+)?', keyword):
                if not re.match(re.escape(keyword) + r'(?=$|[._-])', version):
                    continue
            elif keyword.lower() not in version.lower():
                continue
        name = item['name']
        if '/' in name or not name.startswith(f'elxr-palette-{board}-') or not name.endswith('.swu'):
            continue
        if version in builds:
            raise click.ClickException(f'Multiple full-system SWU bundles found for {version}. Use an explicit bundle URL.')
        builds[version] = {'version': version, 'created': _created(item.get('created')),
                           'url': ARTIFACTORY_BASE_URL + '/soc-images/' + quote(item['path'] + '/' + name, safe='/')}
    return sorted(builds.values(), key=lambda b: (b['created'] or datetime.min.replace(tzinfo=timezone.utc), b['version']), reverse=True)


def _portal_session():
    session = login_external(loginDocker=False)
    if session is None:
        raise click.ClickException(
            'Developer portal login is required on this machine. '
            'Run `sima-cli login`, then retry the update. No firmware was installed.'
        )
    return session


def resolve_bundle(requested, board, internal=False):
    """Return a local path or a download URL; never install or unpack the SWU."""
    if requested and urlparse(requested).scheme not in ('http', 'https') and Path(requested).is_file():
        if not requested.endswith('.swu'):
            raise click.ClickException('A full-system .swu bundle is required.')
        return str(Path(requested).resolve())
    if requested and urlparse(requested).scheme in ('http', 'https'):
        if not urlparse(requested).path.endswith('.swu'):
            raise click.ClickException('The URL must identify a .swu bundle.')
        if internal and not requested.startswith(ARTIFACTORY_BASE_URL.rstrip('/') + '/'):
            raise click.ClickException('Internal bundle URLs must use the configured Artifactory.')
        return requested
    if internal:
        try:
            builds = internal_bundles(board, requested)
        except Exception as exc:
            reason = artifactory_failure_reason(exc)
            if not reason:
                raise
            return select_mirror_bundle(requested, board, reason)
        builds = _matching_builds(builds, requested)
        for build in builds:
            build['url'] = BundleSource(build['url'], build['version'])
        if not builds:
            raise click.ClickException(f'No matching eLxr 3.0+ SWU builds for {requested or "latest"}.')
        if len(builds) == 1:
            return builds[0]['url']
        from InquirerPy import inquirer
        version_width = max(len(b['version']) for b in builds)
        return inquirer.fuzzy(message='Select a full-system SWU build (newest first):', choices=[
            {'name': b['version'].ljust(version_width) + '  Created: ' + (b['created'].strftime('%Y-%m-%d %H:%M:%S UTC') if b['created'] else 'unknown'),
             'value': b['url']} for b in builds
        ]).execute()
    raise click.ClickException(
        'eLxr 3.0 developer-portal releases are not published yet. '
        'Use --internal to select an Artifactory build, or supply a .swu URL/local file.'
    )


def bundle_size(source, internal=False):
    if urlparse(source).scheme not in ('http', 'https') and Path(source).is_file():
        return Path(source).stat().st_size
    headers = {}
    if internal:
        headers = _internal_headers()
        session = requests.Session()
        session.trust_env = False
    elif urlparse(source).hostname in ('docs.sima.ai', 'docs-dev.sima.ai'):
        session = _portal_session()
    else:
        session = requests.Session()
    response = session.head(source, headers=headers, timeout=30)
    response.raise_for_status()
    if 'text/html' in response.headers.get('Content-Type', '').lower():
        raise click.ClickException('The bundle URL returned an HTML page, not an SWU download.')
    return int(response.headers.get('Content-Length', 0))
