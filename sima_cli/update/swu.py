"""Full signed A/B system updates on eLxr 3.0+, locally or through SSH."""
import os
import re
import shlex
import tempfile
import time
from urllib.parse import unquote, urlparse

import click
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from sima_cli.download import download_file_from_url
from sima_cli.update.ab_state import inspect_target
from sima_cli.update.swu_artifacts import release_tuple, resolve_bundle, bundle_size
from sima_cli.update.swu_target import Target

DEFAULT_KEY = '/etc/swupdate/public.pem'


def install_script(bundle, key):
    command = shlex.join(['swupdate', '-v', '-i', bundle, '-k', key, '-e', 'update,full'])
    return r'''set -eu
exec 9>/run/lock/sima-cli-swupdate.lock
flock -n 9 || { echo 'Another sima-cli update is running'; exit 1; }
if pgrep -x swupdate >/dev/null; then
    echo 'SWUpdate is already running; inspect it before starting another install'; exit 1
fi
state=$(simaai-trootctl get-active-slot)
if ! printf '%s\n' "$state" | grep -Eq 'upgrade_available:[[:space:]]*no([,[:space:]]|$)'; then
    # A factory control block is the supported initial state before the first A/B update.
    if printf '%s\n' "$state" | grep -q 'upgrade_available:' ||
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


def preflight(target, key):
    target.run('''set -eu
for tool in swupdate simaai-ab-info simaai-trootctl findmnt readlink sha256sum flock pgrep; do
    command -v "$tool" >/dev/null || { echo "Missing required tool: $tool"; exit 1; }
done
findmnt -n -M /data >/dev/null || { echo '/data must be mounted'; exit 1; }
rootdev=$(findmnt -n -o SOURCE /)
dev=$(basename "$(readlink -f "$rootdev")")
test -b "$rootdev" && test "$(cat "/sys/class/block/$dev/dm/name")" = rootfs || {
    echo 'A/B rootfs mapping missing; use recovery to provision this board'; exit 1
}
test -s /etc/hwrevision || { echo 'Hardware identity is missing'; exit 1; }
test "$(date +%Y)" -ge 2024 || { echo 'Set the system clock before signed updates'; exit 1; }
test -s ''' + shlex.quote(key) + " || { echo 'SWUpdate verification key is missing'; exit 1; }")
    state = inspect_target(target)
    if state.get('running slot') not in ('A', 'B') or state.get('active slot') != state.get('running slot'):
        raise click.ClickException('Cannot establish a consistent running A/B slot. Inspect the system before updating.')
    factory_boot = (state.get('factory') and state.get('running slot') == 'A'
                    and state.get('rollback') == 'normal'
                    and state.get('upgrade_available') == 'unknown')
    if state.get('upgrade_available', 'unknown').lower() != 'no' and not factory_boot:
        raise click.ClickException('An update is pending or its commit state is unknown. Resolve it before starting another update.')
    return state


def _check_space(target, size):
    output = target.run("df -Pk /data | tail -1 | awk '{print $4}'")
    try:
        available = int(output.strip()) * 1024
    except ValueError as exc:
        raise click.ClickException('Cannot determine free staging space on /data.') from exc
    if available < size + 64 * 1024 * 1024:
        raise click.ClickException('Insufficient /data space for the SWU bundle and staging margin.')


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


def update_system(requested, board, ip=None, passwd='edgeai', internal=False,
                  auto_confirm=False, dryrun=False, key=DEFAULT_KEY, reboot=False):
    target = Target(ip, passwd)
    staging = None
    installing = False
    complete = False
    try:
        click.echo('Checking target and A/B state...')
        before = preflight(target, key)
        source = resolve_bundle(requested, board, internal)
        if not source:
            raise click.Abort()
        click.echo(f'Full-system bundle: {source}')
        local_source = urlparse(source).scheme not in ('http', 'https')
        size_hint = bundle_size(source, internal)
        _check_space(target, size_hint * (1 if ip or local_source else 2))
        if dryrun:
            click.echo('Dry run: ' + shlex.join(['sudo', 'swupdate', '-v', '-i', '/data/<staged-bundle>.swu', '-k', key, '-e', 'update,full']))
            click.echo('No bundle installed or reboot scheduled.')
            return
        if not auto_confirm:
            click.confirm('Install the full system into the inactive slot?', default=False, abort=True)
        staging = target.run('set -eu; d=$(mktemp -d /data/sima-cli-update.XXXXXXXX); chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$d"; printf "%s" "$d"').strip()
        if not re.fullmatch(r'/data/sima-cli-update\.[A-Za-z0-9]+', staging):
            raise click.ClickException('Invalid staging directory returned by target.')
        with tempfile.TemporaryDirectory(prefix='sima-cli-swu-', dir=None if ip else staging) as cache:
            if local_source:
                local = source
            else:
                click.echo('Downloading signed SWU bundle...')
                local = download_file_from_url(source, cache, internal=internal)
            size = os.path.getsize(local)
            if not size:
                raise click.ClickException('The SWU bundle is empty.')
            _check_space(target, size)
            remote = staging + '/bundle.swu'
            click.echo('Staging and verifying bundle integrity...')
            target.transfer(local, remote)
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
            expected = None
            parts = urlparse(source).path.split('/')
            if 'bsp' in parts:
                index = parts.index('bsp') + 2
                if index < len(parts) and release_tuple(parts[index]):
                    expected = parts[index]
            target.run('rm -rf -- ' + shlex.quote(staging))
            staging = None
            _reboot_and_verify(target, before, ip, passwd, expected=expected)
    except (KeyboardInterrupt, EOFError, OSError) as exc:
        raise click.ClickException('Update interrupted or connection lost. State is unknown; inspect the board before retrying.') from exc
    finally:
        if staging and re.fullmatch(r'/data/sima-cli-update\.[A-Za-z0-9]+', staging):
            if not installing or complete:
                try:
                    target.run('rm -rf -- ' + shlex.quote(staging))
                except Exception:
                    click.echo(f'Staging cleanup deferred: {staging}')
            else:
                click.echo(f'Installation did not finish cleanly. Retained bundle at {staging}; inspect before retrying.')
        target.close()


def handle_update(requested, ip=None, passwd='edgeai', internal=False, auto_confirm=False,
                  dryrun=False, key=DEFAULT_KEY, reboot=False, inspect=False,
                  force=False, troot_only=False, flavor='auto', local_elxr=False):
    """Return False for legacy platforms; handle all eLxr 3.0+ operations here."""
    if not (ip or local_elxr or inspect):
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
        if fwtype == 'elxr' and (explicit_swu or reboot or key != DEFAULT_KEY or
                                 (requested_release and requested_release >= (3, 0, 0))):
            raise click.ClickException(
                f'This DevKit is running eLxr {version.strip()}; this firmware version does not support '
                'the SWUpdate command. SWUpdate requires eLxr 3.0+ with the A/B layout. '
                'Use recovery/provisioning to install eLxr 3.0+ first.'
            )
        if reboot or key != DEFAULT_KEY:
            raise click.ClickException('--reboot and --key require the eLxr 3.0+ SWUpdate flow.')
        return False
    if force or troot_only or flavor == 'full':
        raise click.ClickException('eLxr 3.0+ supports signed full-system updates only; --force, --troot_only, and legacy --flavor full are not supported.')
    if requested_release and requested_release < (3, 0, 0):
        raise click.ClickException('A/B SWUpdate requires an eLxr 3.0+ bundle; use recovery for older layouts.')
    if board != 'modalix':
        raise click.ClickException(f'eLxr SWUpdate is not supported for board {board or "unknown"}.')
    update_system(requested, board, ip=ip, passwd=passwd, internal=internal,
                  auto_confirm=auto_confirm, dryrun=dryrun, key=key, reboot=reboot)
    return True
