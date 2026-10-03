"""Resolve a complete, same-build netboot set before starting TFTP."""
import hashlib
import os
import tempfile
import shutil
from dataclasses import dataclass

import click

from sima_cli.download import download_file_from_url
from sima_cli.update.query import (
    ARTIFACTORY_BASE_URL, _list_available_firmware_versions_internal,
    elxr_firmware_path, resolve_elxr_palette_image,
)
from sima_cli.update.swu_artifacts import (
    _matching_builds, artifactory_failure_reason, mirror_bundles, release_tuple,
)


@dataclass(frozen=True)
class NetbootImageSelection:
    """Concrete same-build artifact selection used for cache identity and download."""

    version: str
    urls: tuple = ()
    mirror: bool = False
    legacy: bool = False

    def cache_identity(self):
        return {
            'version': self.version,
            'mirror': self.mirror,
            'legacy': self.legacy,
            'artifacts': [
                {
                    'url': str(url),
                    'size': getattr(url, 'size', None),
                    'sha256': getattr(url, 'sha256', None),
                }
                for url in self.urls
            ],
        }


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


def _mirror_build(requested, board, reason, exact=False):
    click.echo(f'{reason} Using the public daily platform mirror for netboot.')
    builds = mirror_bundles(board, requested, netboot=True)
    if exact:
        builds = [b for b in builds if b['version'] == requested]
    if not builds:
        raise click.ClickException(
            f"The daily mirror has no complete netboot files for '{requested}'. "
            'Required: minimal TFTP archive, palette eMMC image, and tRoot blob. TFTP was not started.'
        )
    return _choose(builds, mirror=True)


def _mirror_files(requested, board, reason, exact=False):
    return _mirror_build(requested, board, reason, exact=exact)['artifacts']


def _download_set(urls, board, flavor, mirror, destination_dir=None):
    from sima_cli.update.updater import _extract_required_files
    # Keep different attempts/builds separate; never reuse unverified extracted files.
    directory = destination_dir or tempfile.mkdtemp(prefix='sima-cli-netboot-')
    os.makedirs(directory, exist_ok=True)
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
        if destination_dir is None:
            shutil.rmtree(directory, ignore_errors=True)
        raise


def resolve_netboot_image_selection(requested, board, flavor='headless',
                                    allow_daily_fallback=False):
    """Resolve a selector to one concrete, complete netboot build."""
    selected = None
    try:
        builds = _list_available_firmware_versions_internal(
            board, requested, flavor, 'elxr', with_metadata=True, strict=True)
        selected = _choose(_matching_builds(builds, requested))['version']
        if release_tuple(selected) < (3, 0, 0):
            return NetbootImageSelection(selected, legacy=True)
        base = f'{ARTIFACTORY_BASE_URL}/soc-images/{elxr_firmware_path(board, selected)}/{selected}/artifacts/'
        urls = (base + f'minimal/{board}-tftp-boot-minimal.tar.gz',
                resolve_elxr_palette_image(base + 'palette/', board),
                base + 'minimal/troot_blob.be')
        return NetbootImageSelection(selected, urls=urls)
    except Exception as exc:
        reason = artifactory_failure_reason(exc, post_selection=selected is not None)
        if not reason:
            raise
        if release_tuple(selected or requested) and release_tuple(selected or requested) < (3, 0, 0):
            raise click.ClickException(
                'Artifactory is unavailable; daily netboot fallback requires an eLxr 3.0+ build.'
            ) from exc
        if not allow_daily_fallback:
            raise click.ClickException(
                f'{reason} Daily mirror fallback is disabled. '
                'Retry with -f/--force to allow daily mirror downloads, or restore Artifactory access. '
                'TFTP was not started.'
            ) from exc
        build = _mirror_build(
            selected or requested, board, reason, exact=selected is not None
        )
        return NetbootImageSelection(
            build['version'], urls=tuple(build['artifacts']), mirror=True
        )


def download_selected_netboot_image(selection, board, flavor='headless',
                                    allow_daily_fallback=False, destination_dir=None):
    """Download a previously resolved selection without changing its build identity."""
    from sima_cli.update.updater import _download_image

    if selection.legacy:
        kwargs = ({'destination_dir': destination_dir}
                  if destination_dir is not None else {})
        return _download_image(
            selection.version, board, True, 'netboot', flavor, 'elxr', **kwargs
        )

    try:
        return _download_set(
            selection.urls, board, flavor, mirror=selection.mirror,
            destination_dir=destination_dir,
        )
    except Exception as exc:
        if selection.mirror:
            raise
        reason = artifactory_failure_reason(exc, post_selection=True)
        if not reason:
            raise
        if not allow_daily_fallback:
            raise click.ClickException(
                f'{reason} Daily mirror fallback is disabled. '
                'Retry with -f/--force to allow daily mirror downloads, or restore Artifactory access. '
                'TFTP was not started.'
            ) from exc
        build = _mirror_build(selection.version, board, reason, exact=True)
        return _download_set(
            build['artifacts'], board, flavor, mirror=True,
            destination_dir=destination_dir,
        )


def download_netboot_image(requested, board, flavor='headless', allow_daily_fallback=False,
                           destination_dir=None):
    selection = resolve_netboot_image_selection(
        requested, board, flavor, allow_daily_fallback=allow_daily_fallback
    )
    return download_selected_netboot_image(
        selection,
        board,
        flavor,
        allow_daily_fallback=allow_daily_fallback,
        destination_dir=destination_dir,
    )
