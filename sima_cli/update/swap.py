"""Select an existing healthy A/B system without installing an update."""
import shlex

import click

from sima_cli.update.ab_state import inspect_target
from sima_cli.update.overlay import inspect_overlay
from sima_cli.update.swu_target import Target


def swap_destination(state):
    running = state.get('running slot')
    if running not in ('A', 'B') or state.get('active slot') != running:
        raise click.ClickException('Cannot establish the running slot. Run update --inspect.')
    other = 'B' if running == 'A' else 'A'
    if state.get('fallback slot') != other or state.get('validity ' + other) != 'valid':
        raise click.ClickException(f'Inactive slot {other} is unavailable, blank, invalid, or unverified. Install a full system update first.')
    if state.get('rollback') != 'normal':
        raise click.ClickException('Rollback state is unhealthy or unknown. Run update --inspect before swapping.')
    if state.get('upgrade_available') != 'no' or state.get('next-boot') != running:
        raise click.ClickException('An activation is pending or boot state is unknown. Run update --inspect; finish the pending boot before swapping.')
    version = state.get('fallback version', '').strip().strip('\"\'')
    if not version or version.lower() in ('unknown', 'unavailable', 'n/a'):
        raise click.ClickException(f'Cannot identify the version in slot {other}. Run update --inspect before swapping.')
    return other, version


def switch_script(state, slot):
    """Recheck the inspected snapshot under the same lock used by SWU installs."""
    if slot not in ('A', 'B') or swap_destination(state)[0] != slot:
        raise ValueError('Expected inactive slot A or B')
    return r'''set -eu
exec 9>/run/lock/sima-cli-swupdate.lock
flock -n 9 || { echo 'Another sima-cli update is running'; exit 1; }
if pgrep -x swupdate >/dev/null; then
    echo 'SWUpdate is running; cannot swap slots'; exit 1
fi
current=$(simaai-ab-info)
if [ "$current" != ''' + shlex.quote(state['raw'].rstrip('\n')) + r''' ]; then
    echo 'Slot state changed since confirmation. Run update --inspect and retry.'
    exit 1
fi
simaai-trootctl switch-slot ''' + slot.lower() + '\n'


def swap_slots(ip=None, passwd='edgeai', auto_confirm=False, dryrun=False,
               reboot=False, verbose=False):
    from sima_cli.update.swu import reboot_script, _reboot_and_verify

    target = Target(ip, passwd)
    switching = False
    try:
        before = inspect_target(target)
        slot, version = swap_destination(before)
        inspect_overlay(target, state=before, details=verbose)
        click.echo(f"Current: slot {before['running slot']} — {before.get('active version', 'unknown')}")
        click.echo(f'Target:  slot {slot} — {version}')
        click.echo('Shared data and overlay customizations persist and may override files or package metadata in the target version.')
        click.echo('The device will reboot after confirmation and slot selection.' if reboot else 'The selected version takes effect on the next reboot; the current system keeps running until then.')
        if dryrun:
            click.echo(f'Dry run: would select slot {slot} ({version}). No slot changed or reboot scheduled.')
            return
        if not auto_confirm:
            click.confirm(f'Switch to slot {slot} ({version})' + (' and reboot now?' if reboot else ' on next boot?'), default=False, abort=True)
        switching = True
        target.run(switch_script(before, slot))
        after = inspect_target(target)
        if after.get('next-boot') != slot or after.get('upgrade_available') != 'yes':
            raise click.ClickException('Slot selection could not be verified. Run update --inspect before rebooting or retrying.')
        click.echo(f'Slot {slot} ({version}) selected for next boot. Boot health is confirmed after reboot.')
        if reboot:
            target.run(reboot_script(slot))
            _reboot_and_verify(target, before, ip, passwd, expected=version)
        else:
            click.echo('Reboot when ready, then run sima-cli update --inspect to verify the running version and boot health.')
    except (KeyboardInterrupt, Exception) as exc:
        if switching:
            raise click.ClickException('Swap did not finish cleanly; slot state may have changed. Run update --inspect before rebooting or retrying. ' + str(exc)) from exc
        raise
    finally:
        target.close()
