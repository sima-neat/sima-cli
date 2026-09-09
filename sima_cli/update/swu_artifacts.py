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


def release_tuple(version):
    match = re.match(r'^(\d+)\.(\d+)(?:\.(\d+))?(?=$|[_-])', (version or '').strip().strip('\"\''))
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _created(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def internal_bundles(board, keyword):
    if not re.fullmatch(r'[a-z0-9-]+', board):
        raise click.ClickException('Invalid board identity.')
    criteria = {'repo': 'soc-images', 'type': 'file',
                'path': {'$match': f'elxr/bsp/{board}/*/artifacts/palette'},
                'name': {'$match': f'elxr-palette-{board}-*.swu'}}
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(ARTIFACTORY_BASE_URL + '/api/search/aql',
                                data='items.find(' + json.dumps(criteria) + ').include("repo","path","name","created","size")',
                                headers={'Authorization': 'Bearer ' + get_auth_token(internal=True),
                                         'Content-Type': 'text/plain'}, timeout=30)
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
        raise click.ClickException('Developer portal login is required.')
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
        builds = internal_bundles(board, requested)
        if not builds:
            raise click.ClickException(f'No matching eLxr 3.0+ SWU builds for {requested or "latest"}.')
        if len(builds) == 1:
            return builds[0]['url']
        if not requested:
            click.echo(f"Selecting newest SWU build: {builds[0]['version']}")
            return builds[0]['url']
        from InquirerPy import inquirer
        return inquirer.fuzzy(message='Select a full-system SWU build (newest first):', choices=[
            {'name': b['version'] + '  Created: ' + (b['created'].strftime('%Y-%m-%d %H:%M:%S UTC') if b['created'] else 'unknown'),
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
        session = requests.Session()
        session.trust_env = False
        headers['Authorization'] = 'Bearer ' + get_auth_token(internal=True)
    elif urlparse(source).hostname in ('docs.sima.ai', 'docs-dev.sima.ai'):
        session = _portal_session()
    else:
        session = requests.Session()
    response = session.head(source, headers=headers, timeout=30)
    response.raise_for_status()
    if 'text/html' in response.headers.get('Content-Type', '').lower():
        raise click.ClickException('The bundle URL returned an HTML page, not an SWU download.')
    return int(response.headers.get('Content-Length', 0))
