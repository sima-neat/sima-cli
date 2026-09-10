"""Resolve a complete, same-build netboot set before starting TFTP."""
import hashlib
import os
import tempfile
import shutil

import click

from sima_cli.download import download_file_from_url
from sima_cli.update.query import (
    ARTIFACTORY_BASE_URL, _list_available_firmware_versions_internal,
    elxr_firmware_path, resolve_elxr_palette_image,
)
from sima_cli.update.swu_artifacts import (
    _matching_builds, artifactory_failure_reason, mirror_bundles, release_tuple,
)


def _choose(builds, mirror=False):
    if not builds:
        raise click.ClickException('No matching netboot build with all required files was found.')
    if len(builds) == 1:
        return builds[0]
    from InquirerPy import inquirer
    width = max(48, max(len(b['version']) for b in builds))
    selected = inquirer.fuzzy(message='Select a netboot build (newest first):', choices=[
        {'value': b['version'], 'name': f"{b['version']:<{width}}  " + (
            f"Build {b['build_number']}" if mirror else b.get('created') or 'Unknown')}
        for b in builds
    ]).execute()
    if not selected:
        raise click.Abort()
    return next(b for b in builds if b['version'] == selected)


def _mirror_files(requested, board, reason, exact=False):
    click.echo(f'{reason} Using the public daily platform mirror for netboot.')
    builds = mirror_bundles(board, requested, netboot=True)
    if exact:
        builds = [b for b in builds if b['version'] == requested]
    if not builds:
        raise click.ClickException(
            f"The daily mirror has no complete netboot files for '{requested}'. "
            'Required: minimal TFTP archive, palette eMMC image, and tRoot blob. TFTP was not started.'
        )
    return _choose(builds, mirror=True)['artifacts']


def _download_set(urls, board, flavor, mirror):
    from sima_cli.update.updater import _extract_required_files
    # Keep different attempts/builds separate; never reuse unverified extracted files.
    directory = tempfile.mkdtemp(prefix='sima-cli-netboot-')
    try:
        paths = []
        for url in urls:
            path = download_file_from_url(url, directory, internal=not mirror)
            if mirror:
                digest = hashlib.sha256()
                with open(path, 'rb') as image:
                    for chunk in iter(lambda: image.read(1024 * 1024), b''):
                        digest.update(chunk)
                if os.path.getsize(path) != url.size or digest.hexdigest() != url.sha256:
                    raise click.ClickException('Daily netboot artifact failed size or SHA-256 verification. TFTP was not started.')
            paths.append(path)
        try:
            extracted = _extract_required_files(paths[0], board, 'netboot', flavor)
        except SystemExit as exc:
            raise click.ClickException('The netboot archive contains no usable files. TFTP was not started.') from exc
        if not extracted:
            raise click.ClickException('The netboot archive could not be extracted. TFTP was not started.')
        # Use the explicitly selected tRoot blob, not a similarly named archive member.
        return [p for p in extracted if os.path.basename(p) != 'troot_blob.be'] + paths[1:]
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def download_netboot_image(requested, board, flavor='headless', allow_daily_fallback=False):
    from sima_cli.update.updater import _download_image
    selected = None
    try:
        builds = _list_available_firmware_versions_internal(
            board, requested, flavor, 'elxr', with_metadata=True, strict=True)
        selected = _choose(_matching_builds(builds, requested))['version']
        if release_tuple(selected) < (3, 0, 0):
            return _download_image(selected, board, True, 'netboot', flavor, 'elxr')
        base = f'{ARTIFACTORY_BASE_URL}/soc-images/{elxr_firmware_path(board, selected)}/{selected}/artifacts/'
        urls = [base + f'minimal/{board}-tftp-boot-minimal.tar.gz',
                resolve_elxr_palette_image(base + 'palette/', board),
                base + 'minimal/troot_blob.be']
        return _download_set(urls, board, flavor, mirror=False)
    except Exception as exc:
        reason = artifactory_failure_reason(exc, post_selection=selected is not None)
        if not reason:
            raise
        if release_tuple(selected or requested) and release_tuple(selected or requested) < (3, 0, 0):
            raise click.ClickException('Artifactory is unavailable; daily netboot fallback requires an eLxr 3.0+ build.') from exc
        if not allow_daily_fallback:
            raise click.ClickException(
                f'{reason} Daily mirror fallback is disabled. '
                'Retry with -f/--force to allow daily mirror downloads, or restore Artifactory access. '
                'TFTP was not started.'
            ) from exc
        urls = _mirror_files(selected or requested, board, reason, exact=selected is not None)
        return _download_set(urls, board, flavor, mirror=True)
