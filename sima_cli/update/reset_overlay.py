"""Reset the shared eLxr overlay without installing firmware or selecting a slot."""
import shlex

import click

from sima_cli.update.ab_state import inspect_target
from sima_cli.update.overlay import inspect_overlay
from sima_cli.update.swu_target import Target


def validate_reset_state(state):
    running = state.get('running slot')
    if (running not in ('A', 'B') or state.get('active slot') != running
            or state.get('next-boot') != running
            or state.get('upgrade_available') != 'no'
            or state.get('rollback') != 'normal'):
        raise click.ClickException('Boot state is pending, unhealthy, or unknown. Run update --inspect before resetting the overlay.')


def reset_script(state, reboot=False):
    """Use update_modalix.sh's rename-then-delete cleanup, under the SWU lock."""
    validate_reset_state(state)
    return r'''set -eu
exec 9>/run/lock/sima-cli-swupdate.lock
flock -n 9 || { echo 'Another sima-cli update is running'; exit 1; }
if pgrep -x swupdate >/dev/null; then
    echo 'SWUpdate is running; cannot reset the overlay'; exit 1
fi
current=$(simaai-ab-info)
if [ "$current" != ''' + shlex.quote(state['raw'].rstrip('\n')) + r''' ]; then
    echo 'Boot state changed since confirmation. Run update --inspect and retry.'; exit 1
fi
findmnt -n -M /data >/dev/null
test "$(findmnt -n -M / -o FSTYPE)" = overlay
findmnt -n -M / -o OPTIONS | grep -Eq '(^|,)upperdir=/(run/)?data/overlay/upper(,|$)'
mounts=$(findmnt -rn -o TARGET)
if printf '%s\n' "$mounts" | grep -Eq '^/data/overlay(/|$)'; then
    echo 'Unexpected mount inside the overlay storage; refusing reset'; exit 1
fi
for path in /data/overlay /data/overlay/upper /data/overlay/work; do
    test ! -L "$path" && test -d "$path"
    if mountpoint -q "$path"; then
        echo "Unexpected mount at $path; refusing reset"; exit 1
    fi
done
# Same cleanup contract as the platform SWUpdate post-install script.
# The running overlay pins the renamed directories until reboot.
rm -rf --one-file-system /data/overlay/upper.discarded /data/overlay/work.discarded
mv /data/overlay/upper /data/overlay/upper.discarded
mv /data/overlay/work /data/overlay/work.discarded
rm -rf --one-file-system /data/overlay/upper.discarded /data/overlay/work.discarded
sync
''' + ("nohup sh -c 'sleep 3; /sbin/reboot' >/dev/null 2>&1 </dev/null &\n" if reboot else '')


def reset_system_overlay(ip=None, passwd='edgeai', auto_confirm=False,
                         dryrun=False, reboot=False, verbose=False):
    from sima_cli.update.swu import preserve_overlay_info, print_overlay_recovery_guidance

    target = Target(ip, passwd)
    resetting = False
    try:
        before = inspect_target(target)
        validate_reset_state(before)
        report = inspect_overlay(target, state=before, details=verbose)
        if report.get('status') != 'enabled' or report.get('upper') != '/data/overlay/upper':
            raise click.ClickException('Cannot verify the active eLxr overlay at /data/overlay/upper; no reset performed.')
        click.echo('This removes ALL overlay software, packages, settings, and home-directory files. '
                   'The current firmware and A/B slot stay selected. Other /data files are retained.')
        click.echo('The inventory contains metadata only, not file contents. Back up files you need before continuing. '
                   'Network and SSH settings may return to image defaults; remote access may change.')
        if dryrun:
            click.echo('Dry run: would save the inventory and reset the overlay' +
                       (' and reboot.' if reboot else '; reboot required.') +
                       ' No files changed or reboot scheduled.')
            return
        if not auto_confirm:
            click.confirm('Discard all overlay changes' + (' and reboot now?' if reboot else '?'),
                          default=False, abort=True)
        backup = preserve_overlay_info(target, report, before, '')
        print_overlay_recovery_guidance(*backup)
        resetting = True
        target.run(reset_script(before, reboot=reboot))
        click.echo('Overlay reset completed. ' + ('Reboot scheduled; reconnect and run update --inspect to verify.'
                   if reboot else 'Reboot now to use the clean system; avoid making further changes before reboot.'))
    except (KeyboardInterrupt, Exception) as exc:
        if resetting:
            raise click.ClickException('Overlay reset did not finish cleanly; files may have been removed. '
                                       'Inspect the device before retrying. ' + str(exc)) from exc
        raise
    finally:
        target.close()
