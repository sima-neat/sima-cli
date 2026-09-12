"""Full signed A/B system updates on eLxr 3.0+, locally or through SSH."""
import os
import hashlib
import re
import shlex
import tempfile
import time
from urllib.parse import unquote, urlparse

import click
import requests
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from sima_cli.download import download_file_from_url
from sima_cli.update.ab_state import inspect_target
from sima_cli.update.swu_artifacts import (
    release_tuple, resolve_bundle, bundle_size, is_mirror_source, fallback_for_bundle,
)
from sima_cli.update.swu_target import Target
from sima_cli.update.staging import SPACE_MARGIN, expand_tmpfs
from sima_cli.update.rootfs import ROOT_DEVICE_SCRIPT
from sima_cli.update.swu_certificate import load_certificate, certificate_source


def install_script(bundle, key):
    key_args = ['-k', key] if key else []
    command = shlex.join(['swupdate', '-v', '-i', bundle, *key_args, '-e', 'update,full'])
    return r'''set -eu
exec 9>/run/lock/sima-cli-swupdate.lock
flock -n 9 || { echo 'Another sima-cli update is running'; exit 1; }
if pgrep -x swupdate >/dev/null; then
    echo 'SWUpdate is already running; inspect it before starting another install'; exit 1
fi
state=$(simaai-trootctl get-active-slot)
if ! printf '%s\n' "$state" | grep -Eq 'upgrade_available[[:space:]]*:[[:space:]]*no([,[:space:]]|$)'; then
    # A factory control block is the supported initial state before the first A/B update.
    if printf '%s\n' "$state" | grep -Eq 'upgrade_available[[:space:]]*:' ||
       ! printf '%s\n' "$state" | grep -Eq '^control block[[:space:]]*:[[:space:]]*blank/invalid -> factory \(boots slot A\)$' ||
       ! printf '%s\n' "$state" | grep -Eq '^running slot[[:space:]]*:[[:space:]]*A[[:space:]]*$' ||
       ! simaai-trootctl rollback-status | grep -q 'normal boot'; then
        echo 'An update is pending or commit state is unknown; inspect before retrying'; exit 1
    fi
fi
monitor=
cleanup() { if [ -n "$monitor" ]; then kill "$monitor" 2>/dev/null || true; wait "$monitor" 2>/dev/null || true; fi; }
trap cleanup EXIT
if command -v swupdate-progress >/dev/null; then
    swupdate-progress -w &
    monitor=$!
else
    echo 'SWUpdate progress monitor unavailable; streaming installer diagnostics'
fi
''' + command


class InstallProgress:
    def __init__(self):
        self.progress = Progress(SpinnerColumn(), TextColumn('{task.description}'), BarColumn(), TaskProgressColumn())
        self.task = None

    def __enter__(self):
        self.progress.start()
        self.task = self.progress.add_task('Installing full system', total=None)
        return self

    def __call__(self, line):
        match = re.search(r'(\d+) of (\d+) (\d+)% \(([^)]*)\)', line)
        if match:
            step, total, percent = map(int, match.groups()[:3])
            if total > 0 and 1 <= step <= total and 0 <= percent <= 100:
                self.progress.update(self.task, description=f'Installing step {step}/{total}: {match.group(4)}',
                                     total=100, completed=percent)
                return
        if line.strip():
            self.progress.console.print(line, markup=False, highlight=False)

    def __exit__(self, *args):
        self.progress.stop()


def prepare_key(target, dryrun=False, signing_cert=None, internal=False):
    """Stage a freshly retrieved certificate on the target."""
    source = certificate_source(signing_cert, internal)
    if source is None:
        return None, None
    click.echo('Loading SWUpdate verification certificate: ' + source)
    certificate, not_before, not_after = load_certificate(source)
    try:
        target_time = int(target.run('date -u +%s').strip())
    except ValueError as exc:
        raise click.ClickException('Cannot read the DevKit clock; synchronize time before signed updates.') from exc
    if not not_before <= target_time <= not_after:
        raise click.ClickException(
            'The signing certificate is not valid at the DevKit system time. '
            'Synchronize the DevKit clock and check the certificate validity dates before updating.'
        )
    if dryrun:
        return None, None
    directory = target.run('mktemp -d /tmp/sima-cli-key.XXXXXXXX').strip()
    if not re.fullmatch(r'/tmp/sima-cli-key\.[A-Za-z0-9]+', directory):
        raise click.ClickException('Invalid temporary key directory returned by target.')
    path = directory + '/public.pem'
    try:
        target.run('set -eu; umask 077; printf %s ' + shlex.quote(certificate) + ' > ' + shlex.quote(path))
    except BaseException:
        target.run('rm -rf -- ' + shlex.quote(directory), check=False)
        raise
    return path, directory


def preflight(target, key):
    target.run('''set -eu
for tool in swupdate simaai-ab-info simaai-trootctl findmnt readlink sha256sum flock pgrep; do
    command -v "$tool" >/dev/null || { echo "Missing required tool: $tool"; exit 1; }
done
findmnt -n -M /data >/dev/null || { echo '/data must be mounted'; exit 1; }
''' + ROOT_DEVICE_SCRIPT + '''test -s /etc/hwrevision || { echo 'Hardware identity is missing'; exit 1; }
test "$(date +%Y)" -ge 2024 || { echo 'Set the system clock before signed updates'; exit 1; }
''' + ("test -s " + shlex.quote(key) + " || { echo 'SWUpdate verification key is missing'; exit 1; }" if key else ':'))
    state = inspect_target(target)
    if state.get('running slot') not in ('A', 'B') or state.get('active slot') != state.get('running slot'):
        raise click.ClickException('Cannot establish a consistent running A/B slot. Inspect the system before updating.')
    factory_boot = (state.get('factory') and state.get('running slot') == 'A'
                    and state.get('rollback') == 'normal'
                    and state.get('upgrade_available') == 'unknown')
    if state.get('upgrade_available', 'unknown').lower() != 'no' and not factory_boot:
        raise click.ClickException('An update is pending or its commit state is unknown. Resolve it before starting another update.')
    return state


STAGING_ROOTS = ('/tmp', '/media/nvme/swupdate', '/data')
STAGING_PATTERN = r'/(?:tmp|media/nvme/swupdate|data)/sima-cli-update\.[A-Za-z0-9]+'


def _available_space(target, root):
    output = target.run("set -eu; test -d " + shlex.quote(root) +
                        "; df -Pk " + shlex.quote(root) + " | tail -1 | awk '{print $4}'")
    try:
        return int(output.strip()) * 1024
    except ValueError as exc:
        raise click.ClickException(f'Cannot determine free staging space on {root}.') from exc


def _check_space(target, size, root='/data', allow_expand=False, dryrun=False):
    if _available_space(target, root) < size + SPACE_MARGIN:
        if root == '/tmp' and allow_expand and expand_tmpfs(target.run, size + SPACE_MARGIN, dryrun=dryrun):
            return
        raise click.ClickException(f'Insufficient {root} space: need {(size + SPACE_MARGIN) / 1024**3:.2f} GiB for the SWU bundle and staging margin.')


def _select_staging_root(target, size, dryrun=False):
    failures = []
    for root in STAGING_ROOTS:
        try:
            if root == '/media/nvme/swupdate':
                if dryrun:
                    target.run('findmnt -n -M /media/nvme >/dev/null')
                else:
                    click.echo('Preparing NVMe staging storage...')
                    target.run('''set -eu
if findmnt -n -M /media/nvme >/dev/null; then
    mount -o remount,rw /media/nvme
else
    test -b /dev/nvme0n1p1
    mkdir -p /media/nvme
    mount /dev/nvme0n1p1 /media/nvme
fi
findmnt -n -M /media/nvme >/dev/null
mkdir -p /media/nvme/swupdate''')
            # A dry run can assess the mounted parent without creating the staging root.
            check_root = '/media/nvme' if dryrun and root == '/media/nvme/swupdate' else root
            target.run('test -w ' + shlex.quote(check_root))
            _check_space(target, size, check_root, allow_expand=True, dryrun=dryrun)
            click.echo(f'Staging storage: {root}')
            return root
        except click.ClickException as exc:
            failures.append(f'{root}: {exc.format_message()}')
    raise click.ClickException('No staging storage has enough usable space. ' + ' '.join(failures))


def _reboot_and_verify(target, before, ip, passwd, expected=None):
    # Delay reboot until the SSH acknowledgement has reached the caller.
    target.run("nohup sh -c 'sleep 3; reboot' >/dev/null 2>&1 </dev/null &")
    if not ip:
        click.echo('Reboot scheduled. Run sima-cli update --inspect after boot to check health confirmation.')
        return
    target.close()
    click.echo('Reboot scheduled; waiting for the updated slot and health confirmation...')
    deadline = time.monotonic() + 180
    observed = None
    with Progress(SpinnerColumn(), TextColumn('{task.description}')) as progress:
        progress.add_task('Waiting for board', total=None)
        while time.monotonic() < deadline:
            time.sleep(5)
            peer = None
            try:
                peer = Target(ip, passwd)
                state = inspect_target(peer, display=False)
                observed = state
                if state.get('rollback') == 'rollback':
                    raise click.ClickException('The board rolled back the update. Inspect the fallback and installer logs.')
                if state.get('running slot') != before.get('running slot') and state.get('running slot') in ('A', 'B'):
                    if state.get('upgrade_available', '').lower() == 'no' and state.get('rollback') == 'normal':
                        actual = state.get('active version', '').strip().strip('\"\'')
                        normalized_expected = re.sub(r'^(\d+\.\d+\.\d+)_(?:daily|custom)_', r'\1_', expected or '')
                        if expected and actual != normalized_expected:
                            raise click.ClickException(f'Updated slot reports {actual or "unknown"}, expected {expected}. Inspect before retrying.')
                        inspect_target(peer)
                        click.echo('Updated slot is running and the board health service has committed the boot.')
                        return
            except click.ClickException:
                # A rollback observation is definitive; connection/command failures can be transient.
                if observed and (observed.get('rollback') == 'rollback' or
                                 (expected and observed.get('running slot') != before.get('running slot')
                                  and observed.get('upgrade_available') == 'no')):
                    raise
            except (OSError, EOFError):
                pass
            finally:
                if peer:
                    peer.close()
    if observed and observed.get('running slot') != before.get('running slot'):
        raise click.ClickException('New slot is running but health confirmation is still pending. Run update --inspect.')
    raise click.ClickException('Could not verify the new boot. Update state is unknown; inspect before retrying.')


def _artifact_request_error(error, internal):
    """Recognize HTTP errors even when the downloader wraps them."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, requests.RequestException):
            service = 'Artifactory' if internal else 'the bundle server'
            status = error.response.status_code if error.response is not None else None
            if status == 401:
                action = ('Run `sima-cli -i login` on this machine, then retry the update.'
                          if internal else 'Check your bundle-server credentials or log in with `sima-cli login`, then retry.')
                return f'{service} rejected authentication (HTTP 401). Your login may be missing or expired. {action}'
            if status == 403:
                action = ('Run `sima-cli -i login` to refresh your credentials. If access is still denied, ask your Artifactory administrator for permission.'
                          if internal else 'Check that your account has permission to download this bundle.')
                return f'Access to {service} was denied (HTTP 403). {action}'
            if status == 404:
                return f'The requested update artifact was not found on {service} (HTTP 404). Check the selected version or bundle URL.'
            if status is not None:
                return f'{service} returned HTTP {status} while accessing the update artifact. Retry later or contact the server administrator.'
            if isinstance(error, requests.Timeout):
                return f'The request to {service} timed out. Check your network or VPN connection, then retry.'
            return f'Unable to access {service}. Check your network or VPN connection and bundle URL, then retry.'
        error = error.__cause__ or error.__context__
    return None


def update_system(requested, board, ip=None, passwd='edgeai', internal=False,
                  auto_confirm=False, dryrun=False, reboot=False, signing_cert=None):
    target = Target(ip, passwd)
    source = ''
    staging = None
    key_directory = None
    installing = False
    complete = False
    phase = "checking the target"
    try:
        click.echo('Checking target and A/B state...')
        key, key_directory = prepare_key(target, dryrun=dryrun, signing_cert=signing_cert, internal=internal)
        before = preflight(target, key)
        phase = "resolving the update bundle"
        source = resolve_bundle(requested, board, internal)
        if not source:
            raise click.Abort()
        click.echo(f'Full-system bundle: {source}')
        local_source = urlparse(source).scheme not in ('http', 'https')
        phase = "checking the update bundle"
        try:
            size_hint = (source.size if is_mirror_source(source) and getattr(source, 'size', None)
                         else bundle_size(source, internal))
        except Exception as exc:
            fallback = fallback_for_bundle(source, board, exc) if internal else None
            if fallback is None:
                raise
            source = fallback
            size_hint = source.size
        phase = "preparing staging storage"
        staging_root = _select_staging_root(target, size_hint, dryrun=dryrun)
        if dryrun:
            key_args = (['-k', '/tmp/<temporary-key>/public.pem']
                        if certificate_source(signing_cert, internal) is not None else [])
            click.echo('Dry run: ' + shlex.join(['sudo', 'swupdate', '-v', '-i', staging_root + '/<staged-bundle>.swu', *key_args, '-e', 'update,full']))
            click.echo('No bundle installed or reboot scheduled.')
            return
        if not auto_confirm:
            click.confirm('Install the full system into the inactive slot?', default=False, abort=True)
        staging = target.run('set -eu; d=$(mktemp -d ' + shlex.quote(staging_root + '/sima-cli-update.XXXXXXXX') + '); chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$d"; printf "%s" "$d"').strip()
        if not re.fullmatch(STAGING_PATTERN, staging):
            raise click.ClickException('Invalid staging directory returned by target.')
        with tempfile.TemporaryDirectory(prefix='sima-cli-swu-', dir=None if ip else staging) as cache:
            if local_source:
                local = source
            else:
                phase = 'downloading the update bundle'
                click.echo('Downloading signed SWU bundle...')
                try:
                    local = download_file_from_url(source, cache, internal=internal and not is_mirror_source(source))
                except Exception as exc:
                    fallback = fallback_for_bundle(source, board, exc) if internal else None
                    if fallback is None:
                        raise
                    source = fallback
                    _check_space(target, source.size, staging_root, allow_expand=True)
                    local = download_file_from_url(source, cache, internal=False)
                if is_mirror_source(source) and getattr(source, 'sha256', None):
                    click.echo('Verifying the daily mirror bundle size and SHA-256...')
                    digest = hashlib.sha256()
                    with open(local, 'rb') as bundle:
                        for chunk in iter(lambda: bundle.read(1024 * 1024), b''):
                            digest.update(chunk)
                    if os.path.getsize(local) != source.size or digest.hexdigest() != source.sha256:
                        raise click.ClickException(
                            'The daily mirror bundle failed size or SHA-256 verification. '
                            'No firmware was installed. Retry the download.'
                        )
            phase = "staging the update bundle"
            size = os.path.getsize(local)
            if not size:
                raise click.ClickException('The SWU bundle is empty.')
            _check_space(target, size if ip or local_source else 0, staging_root, allow_expand=True)
            remote = staging + '/bundle.swu'
            click.echo('Staging and verifying bundle integrity...')
            target.transfer(local, remote, move=not ip and not local_source)
            click.echo('Validating signature and installing the full inactive slot...')
            installing = True
            with InstallProgress() as progress:
                target.run(install_script(remote, key), stream=progress)
            complete = True
        after = inspect_target(target)
        expected_slot = 'B' if before['running slot'] == 'A' else 'A'
        if after.get('next-boot', '').upper() != expected_slot or after.get('upgrade_available', '').lower() != 'yes':
            raise click.ClickException('SWUpdate exited successfully, but pending activation could not be verified. Run update --inspect before rebooting.')
        click.echo('Full system installed. Reboot required; the new slot is not yet health-confirmed.')
        if reboot:
            expected = getattr(source, 'version', None)
            parts = urlparse(source).path.split('/')
            if 'bsp' in parts:
                index = parts.index('bsp') + 2
                if index < len(parts) and release_tuple(parts[index]):
                    expected = parts[index]
            target.run('rm -rf -- ' + shlex.quote(staging))
            staging = None
            if key_directory:
                target.run('rm -rf -- ' + shlex.quote(key_directory))
                key_directory = None
            _reboot_and_verify(target, before, ip, passwd, expected=expected)
    except (KeyboardInterrupt, Exception) as exc:
        if isinstance(exc, click.ClickException):
            raise
        if not installing:
            artifact_error = _artifact_request_error(exc, internal and not is_mirror_source(source))
            if artifact_error:
                raise click.ClickException(artifact_error + ' No firmware was installed.') from exc
            if isinstance(exc, (KeyboardInterrupt, EOFError, OSError)):
                raise click.ClickException(
                    f'Update stopped while {phase}, before firmware installation started. '
                    'No firmware was installed. Check the host and target connection, then retry.'
                ) from exc
        elif isinstance(exc, (KeyboardInterrupt, EOFError, OSError)):
            raise click.ClickException('Update interrupted or connection lost. State is unknown; inspect the board before retrying.') from exc
        raise
    finally:
        if staging and re.fullmatch(STAGING_PATTERN, staging):
            if not installing or complete:
                try:
                    target.run('rm -rf -- ' + shlex.quote(staging))
                except Exception:
                    click.echo(f'Staging cleanup deferred: {staging}')
            else:
                click.echo(f'Installation did not finish cleanly. Retained bundle at {staging}; inspect before retrying.')
        if key_directory:
            try:
                target.run('rm -rf -- ' + shlex.quote(key_directory))
            except Exception:
                click.echo(f'Temporary key cleanup deferred: {key_directory}')
        target.close()


def handle_update(requested, ip=None, passwd='edgeai', internal=False, auto_confirm=False,
                  dryrun=False, reboot=False, inspect=False,
                  force=False, troot_only=False, flavor='auto', local_elxr=False, signing_cert=None):
    """Return False for legacy platforms; handle all eLxr 3.0+ operations here."""
    if not (ip or local_elxr or inspect):
        if signing_cert is not None:
            raise click.ClickException('--signing-cert requires a local eLxr 3.0+ DevKit or --ip.')
        return False
    if ip:
        from sima_cli.update.remote import get_remote_board_info
        info = get_remote_board_info(ip, passwd)
    else:
        from sima_cli.update.local import get_local_board_info
        info = get_local_board_info()
    board, version, _, _, fwtype = info
    board, fwtype = board.strip().strip('\"\''), fwtype.strip().strip('\"\'').lower()
    running = release_tuple(version)
    requested_release = release_tuple(requested)
    if inspect:
        if fwtype != 'elxr' or not running or running < (3, 0, 0):
            raise click.ClickException('A/B inspection requires a reachable eLxr 3.0+ board.')
        target = Target(ip, passwd)
        try:
            inspect_target(target)
        finally:
            target.close()
        return True
    if fwtype == 'elxr' and not running:
        raise click.ClickException('Cannot determine the target eLxr firmware version.')
    if fwtype != 'elxr' or running < (3, 0, 0):
        explicit_swu = bool(requested and unquote(urlparse(requested).path).lower().endswith('.swu'))
        if fwtype == 'elxr' and (explicit_swu or reboot or signing_cert is not None or
                                 (requested_release and requested_release >= (3, 0, 0))):
            raise click.ClickException(
                f'This DevKit is running eLxr {version.strip()}; this firmware version does not support '
                'the SWUpdate command. SWUpdate requires eLxr 3.0+ with the A/B layout. '
                'Use recovery/provisioning to install eLxr 3.0+ first.'
            )
        if reboot or signing_cert is not None:
            raise click.ClickException('--reboot and --signing-cert require the eLxr 3.0+ SWUpdate flow.')
        return False
    if force or troot_only or flavor == 'full':
        raise click.ClickException('eLxr 3.0+ supports signed full-system updates only; --force, --troot_only, and legacy --flavor full are not supported.')
    if requested_release and requested_release < (3, 0, 0):
        raise click.ClickException('A/B SWUpdate requires an eLxr 3.0+ bundle; use recovery for older layouts.')
    if board != 'modalix':
        raise click.ClickException(f'eLxr SWUpdate is not supported for board {board or "unknown"}.')
    update_system(requested, board, ip=ip, passwd=passwd, internal=internal,
                  auto_confirm=auto_confirm, dryrun=dryrun, reboot=reboot, signing_cert=signing_cert)
    return True
