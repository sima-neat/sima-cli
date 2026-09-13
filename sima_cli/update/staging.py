"""Space checks and bounded tmpfs growth on the update target."""
import click

SPACE_MARGIN = 64 * 1024 * 1024
MIB = 1024 * 1024
MEMORY_RESERVE = 512 * MIB

TMPFS_INFO = r'''set -eu
findmnt -n -M /tmp -o FSTYPE
LC_ALL=C df -Pk /tmp | tail -1 | awk '{print $2, $3, $4}'
awk '/^MemAvailable:/ {available=$2} /^MemTotal:/ {total=$2} END {print available+0, total+0}' /proc/meminfo
'''
TMP_FREE = "set -eu; LC_ALL=C df -Pk /tmp | tail -1 | awk '{print $4}'"


def expand_tmpfs(run, required_free, dryrun=False):
    """Grow only a dedicated /tmp tmpfs, leaving RAM for the running system.

    run executes checked commands on the target as root. Increasing a tmpfs
    limit does not create RAM: the entire incoming payload must fit in available
    memory, not just the difference between the old and new filesystem limits.
    """
    try:
        report = run(TMPFS_INFO)
        fields = report.split() if isinstance(report, str) else []
        if len(fields) != 6 or fields[0] != 'tmpfs':
            return False
        capacity, used, free, available, total = [int(value) * 1024 for value in fields[1:]]
        if min(capacity, available, total) <= 0 or min(used, free) < 0:
            return False
        reserve = max(MEMORY_RESERVE, total // 10)
        if required_free > available - reserve:
            click.echo('Not expanding /tmp: insufficient available RAM after reserving memory for the system.')
            return False
        limit = ((used + required_free + MIB - 1) // MIB) * MIB
        if limit <= capacity:
            return False
        if dryrun:
            click.echo(f'Dry run: would expand /tmp tmpfs to {limit // MIB} MiB for update staging.')
            return True
        click.echo(f'Expanding /tmp tmpfs to {limit // MIB} MiB for update staging...')
        # Recheck the mount type immediately before remounting. Do not alter a
        # disk-backed /tmp or remount a filesystem containing other directories.
        run('set -eu; test "$(findmnt -n -M /tmp -o FSTYPE)" = tmpfs; '
            f'mount -o remount,size={limit} /tmp')
        if int(run(TMP_FREE).strip()) * 1024 < required_free:
            click.echo('/tmp still has insufficient free space after expansion.')
            return False
        return True
    except (click.ClickException, ValueError) as exc:
        click.echo(f'Could not expand /tmp for update staging: {exc}')
        return False


def check_tmpfs_memory(run, required_free):
    """Check actual RAM even when a tmpfs size limit already has room."""
    report = run("set -eu; findmnt -n -T /tmp -o FSTYPE; "
                 "awk '/^MemAvailable:/ {available=$2} /^MemTotal:/ {total=$2} "
                 "END {print available+0, total+0}' /proc/meminfo")
    fields = report.split() if isinstance(report, str) else []
    if fields and fields[0] != 'tmpfs':
        return
    try:
        if len(fields) != 3:
            raise ValueError('missing memory information')
        available, total = [int(value) * 1024 for value in fields[1:]]
        if available < 0 or total <= 0:
            raise ValueError('invalid memory information')
    except ValueError as exc:
        raise click.ClickException('Cannot verify available RAM for /tmp extraction.') from exc
    reserve = max(MEMORY_RESERVE, total // 10)
    if required_free > available - reserve:
        raise click.ClickException(
            'Insufficient available RAM for /tmp extraction after reserving memory '
            'for the system. Stop unneeded applications or free temporary files, then retry.'
        )
