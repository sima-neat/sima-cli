"""Confirmed remote U-Boot preparation for a host-served netboot image."""
import ipaddress
import inspect
import json
import os
import shlex
import shutil
import socket
import tempfile
from dataclasses import dataclass

import click
import paramiko
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from sima_cli.update.remote import init_ssh_session, run_remote_command_capture, wait_for_ssh

LEGACY_LAYOUT_ERROR = 'Unsupported legacy 2.1 fw_env.config'
MANUAL_FALLBACK_READY = 'SIMA_CLI_MANUAL_NETBOOT_FALLBACK_READY'
BACKUP_MARKER = 'SIMA_CLI_NETBOOT_BACKUP='
BACKUP_EXPORT_MARKER = 'SIMA_CLI_NETBOOT_EXPORT='


@dataclass
class NetbootConfiguration:
    devkit: str
    backup_dir: str = None
    export_dir: str = None
    local_backup_dir: str = None
    changed: bool = False

    def cleanup(self):
        if self.local_backup_dir:
            shutil.rmtree(self.local_backup_dir, ignore_errors=True)
            self.local_backup_dir = None


def _capture_environment_backup(ssh, configuration):
    """Copy the backup off the eMMC before a whole-device flash can erase it."""
    local_dir = tempfile.mkdtemp(prefix='sima-cli-netboot-uboot-')
    sftp = ssh.open_sftp()
    try:
        for name in ('uboot.env', 'uboot-redund.env'):
            sftp.get(
                configuration.export_dir + '/' + name,
                os.path.join(local_dir, name),
            )
    except Exception:
        shutil.rmtree(local_dir, ignore_errors=True)
        raise
    finally:
        sftp.close()
    configuration.local_backup_dir = local_dir
    _checked(ssh, 'sudo rm -rf ' + shlex.quote(configuration.export_dir))
    configuration.export_dir = None


def resolve_device(devkit=None):
    # Share SDK setup's discovery, deduplication, selection, and IP validation.
    from sima_cli.sdk.commands import _resolve_devkit_ipv4, NoDevkitsDiscovered
    try:
        return _resolve_devkit_ipv4(devkit or 'auto')
    except NoDevkitsDiscovered:
        return None


def server_address(devkit):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect((devkit, 69))
        address = route.getsockname()[0]
    if ipaddress.ip_address(address).is_loopback:
        raise click.ClickException('The DevKit needs a reachable host address for TFTP.')
    return address


def _checked(ssh, command):
    code, out, err = run_remote_command_capture(ssh, command, sudo_pty=False)
    if code != 0:
        raise click.ClickException(f'Remote netboot preparation failed: {out.strip()} {err.strip()} (exit {code})')
    return out.strip()


def network_settings(ssh, devkit, server_ip):
    """Read the address and return route used by this SSH connection."""
    try:
        addresses = json.loads(_checked(ssh, 'ip -j address show'))
        routes = json.loads(_checked(ssh, 'ip -j route get ' + shlex.quote(server_ip)
                                     + ' from ' + shlex.quote(devkit)))
        route = routes[0]
        interface = next(item for item in addresses if item['ifname'] == route['dev'])
        address = next(addr for addr in interface['addr_info']
                       if addr.get('family') == 'inet' and addr.get('local') == devkit)
        network = ipaddress.IPv4Interface(f"{devkit}/{address['prefixlen']}")
        gateway = str(ipaddress.IPv4Address(route.get('gateway', '0.0.0.0')))
        return {'interface': interface['ifname'], 'netmask': str(network.netmask), 'gateway': gateway}
    except (ValueError, KeyError, IndexError, StopIteration, TypeError) as exc:
        raise click.ClickException('Cannot determine the DevKit interface, subnet, and return route. '
                                   'U-Boot was not changed.') from exc


def configure_and_reboot(devkit, server_ip, autoflash=False, configuration=None):
    """Return whether the user confirmed and the DevKit reboot was scheduled."""
    try:
        ssh = init_ssh_session(devkit)
    except (OSError, paramiko.SSHException) as exc:
        click.secho(
            f'Could not connect to DevKit {devkit} over SSH: {exc}. '
            'Remote setup was skipped; no boot settings were changed.', fg='yellow')
        return False
    try:
        _checked(ssh, "set -eu; "
                 "for tool in fw_setenv fw_printenv python3; do "
                 "command -v \"$tool\" >/dev/null || { echo \"Missing required tool: $tool\" >&2; exit 1; }; done; "
                 "for file in /boot/uboot.env /boot/uboot-redund.env; do "
                 "test -f \"$file\" || { echo \"Missing U-Boot environment file: $file\" >&2; exit 1; }; done; "
                 "if ! test -f /boot/u-boot.bin; then "
                 "grep -Eq \"^SIMA_BUILD_VERSION[[:space:]]*=[[:space:]]*2[.]1[.]\" /etc/build /etc/buildinfo 2>/dev/null "
                 "|| { echo 'Missing /boot/u-boot.bin; cannot identify the bootloader environment format for this platform.' >&2; exit 1; }; "
                 "test -f /etc/fw_env.config || { echo 'Missing /etc/fw_env.config for legacy 2.1 environment validation.' >&2; exit 1; }; fi")
        network = network_settings(ssh, devkit, server_ip)
        message = Text()
        message.append(f"DevKit: {devkit} ({network['interface']})\nTFTP server: {server_ip}\n"
                       f"Netmask: {network['netmask']}  Gateway: {network['gateway']}\n\n", style='bold cyan')
        message.append('After confirmation:\n', style='bold yellow')
        message.append('• Boot parameters will be automatically updated on the DevKit to let it boot from the network.\n'
                       '• The DevKit will reboot, interrupting running applications.\n\n')
        if autoflash:
            message.append('• Open SSH is available after network boot, this tool will automatically flash the selected devkit.\n\n', style='bold red')
        message.append('These boot settings persist across reboots. No DHCP is required.\nKeep this host and TFTP server running.\n'
                       'If netboot fails, the DevKit retries and reboots; restoring local boot may require serial access.\n'
                       + ('Automatic flashing is enabled.' if autoflash else 'Netboot preparation itself does not write the eMMC image.'), style='bold yellow')
        Console().print(Panel(message, title='Reboot DevKit into network boot', border_style='yellow'))
        if not click.confirm('Set up network boot, reboot, and automatically flash this DevKit?' if autoflash
                             else 'Set up network boot and reboot this DevKit?', default=False):
            return False
        # Match Kerrigan's redundant environment layout. Use a temporary config,
        # preserving any system fw_env.config, and back up before the first write.
        script = _uboot_script(devkit, server_ip, network)
        try:
            output = _checked(ssh, 'sudo sh -c ' + shlex.quote(script))
        except click.ClickException as exc:
            error = str(exc)
            if (LEGACY_LAYOUT_ERROR not in error or MANUAL_FALLBACK_READY not in error
                    or 'ERROR:' in error):
                raise
            _show_manual_legacy_setup(devkit, server_ip, network, autoflash)
            return False
        click.echo(output)
        if configuration is not None:
            match = next((line for line in output.splitlines()
                          if line.startswith(BACKUP_MARKER)), None)
            if not match:
                raise click.ClickException(
                    'Remote netboot preparation did not report its U-Boot backup; '
                    'the DevKit will not be rebooted.'
                )
            configuration.backup_dir = match[len(BACKUP_MARKER):]
            configuration.changed = True
            export = next((line for line in output.splitlines()
                           if line.startswith(BACKUP_EXPORT_MARKER)), None)
            if not export:
                raise click.ClickException(
                    'Remote netboot preparation did not report its readable U-Boot export; '
                    'the DevKit will not be rebooted.'
                )
            configuration.export_dir = export[len(BACKUP_EXPORT_MARKER):]
            _capture_environment_backup(ssh, configuration)
        # A delayed systemd job acknowledges scheduling before SSH disconnects.
        _checked(ssh, 'sudo systemd-run --on-active=3s /sbin/reboot')
        click.secho(
            f'Reboot scheduled for {devkit}. Keep this program running to serve images through TFTP. '
            'You may connect to the device through its serial port to monitor netboot progress, but this is optional. '
            + (f'Once you see "✅ SSH is available on {devkit}", flashing will start automatically.' if autoflash
             else f'Once you see "✅ SSH is available on {devkit}", type "f" to flash the device.'),
            fg='green',
        )
        return True
    finally:
        ssh.close()


def restore_environment(configuration):
    """Restore the exact U-Boot files saved before this netboot session."""
    if not configuration or not configuration.changed or not configuration.backup_dir:
        return True
    if not wait_for_ssh(configuration.devkit, timeout=180):
        raise click.ClickException(
            f'DevKit {configuration.devkit} did not return to SSH for U-Boot restoration.'
        )
    ssh = init_ssh_session(configuration.devkit)
    remote_stage = None
    source_dir = configuration.backup_dir
    local_files = [
        os.path.join(configuration.local_backup_dir or '', name)
        for name in ('uboot.env', 'uboot-redund.env')
    ]
    if all(os.path.isfile(path) for path in local_files):
        remote_stage = '/tmp/sima-cli-netboot-uboot-restore'
        _checked(ssh, 'sudo rm -rf ' + shlex.quote(remote_stage)
                 + ' && mkdir -m 700 ' + shlex.quote(remote_stage))
        sftp = ssh.open_sftp()
        try:
            for local_path in local_files:
                sftp.put(local_path, remote_stage + '/' + os.path.basename(local_path))
        finally:
            sftp.close()
        source_dir = remote_stage
    backup = shlex.quote(source_dir)
    script = '\n'.join([
        'set -eu',
        'restore_ro=0',
        'restore_mount=',
        'boot_root=',
        'cleanup() {',
        '  status=$?',
        '  trap - EXIT',
        '  if [ -n "$restore_mount" ]; then umount "$restore_mount" || status=1; rmdir "$restore_mount" || status=1; fi',
        '  if [ "$restore_ro" = 1 ]; then sync; mount -o remount,ro /boot || status=1; fi',
        *(([f'  rm -rf {shlex.quote(remote_stage)} || status=1'] if remote_stage else [])),
        '  exit "$status"',
        '}',
        'trap cleanup EXIT',
        f'test -f {backup}/uboot.env && test -f {backup}/uboot-redund.env',
        'boot_target=$(findmnt -n -o TARGET -T /boot 2>/dev/null || true)',
        'boot_source=$(findmnt -n -o SOURCE -T /boot 2>/dev/null || true)',
        'case "$boot_target:$boot_source" in',
        '  /boot:/dev/mmcblk0p*)',
        '    boot_root=/boot',
        '    options=$(findmnt -n -o OPTIONS -T /boot)',
        '    case ",$options," in *,ro,*) mount -o remount,rw /boot; restore_ro=1 ;; esac ;;',
        '  *)',
        '    probe=$(mktemp -d /tmp/sima-cli-netboot-boot.XXXXXX)',
        '    selected=',
        '    for part in /dev/mmcblk0p*; do',
        '      mount -o ro "$part" "$probe" 2>/dev/null || continue',
        '      if [ -f "$probe/uboot.env" ] && [ -f "$probe/uboot-redund.env" ]; then',
        '        test -z "$selected" || { umount "$probe"; rmdir "$probe"; echo "Multiple eMMC boot partitions contain U-Boot environments." >&2; exit 1; }',
        '        selected=$part',
        '      fi',
        '      umount "$probe"',
        '    done',
        '    test -n "$selected" || { rmdir "$probe"; echo "Cannot locate the eMMC boot partition containing U-Boot environments." >&2; exit 1; }',
        '    restore_mount=$probe',
        '    mount -o rw "$selected" "$probe"',
        '    boot_root=$probe ;;',
        'esac',
        'test -n "$boot_root"',
        f'cp -p {backup}/uboot.env "$boot_root/uboot.env"',
        f'cp -p {backup}/uboot-redund.env "$boot_root/uboot-redund.env"',
        f'cmp {backup}/uboot.env "$boot_root/uboot.env"',
        f'cmp {backup}/uboot-redund.env "$boot_root/uboot-redund.env"',
        'sync',
    ])
    try:
        _checked(ssh, 'sudo sh -c ' + shlex.quote(script))
    finally:
        ssh.close()
    configuration.changed = False
    configuration.cleanup()
    click.secho(
        f'Restored the saved U-Boot environment on {configuration.devkit}.',
        fg='green',
    )
    return True


def _manual_netboot_commands(devkit, server_ip, network):
    values = {
        'bootfile': 'netboot.scr.uimg',
        'netcfg': 'static',
        'forcenetcfg': 'static',
        'ipaddr': devkit,
        'netmask': network['netmask'],
        'gatewayip': network['gateway'],
        'nfs_linux_intf': network['interface'],
        'serverip': server_ip,
    }
    commands = [f'setenv {name} {value}' for name, value in values.items()]
    commands.extend([
        "setenv bootcmd_net 'tftp ${scriptaddr} ${serverip}:${bootfile} && source ${scriptaddr}'",
        "setenv bootcmd 'for attempt in 1 2 3 4 5; do echo Netboot attempt ${attempt}; run bootcmd_net; sleep 10; done; reset'",
        'setenv boot_targets net',
        'saveenv',
        'reset',
    ])
    return '\n'.join(commands)


def _show_manual_legacy_setup(devkit, server_ip, network, autoflash):
    message = Text()
    message.append(
        'This Platform 2.1 DevKit uses an older U-Boot environment layout that '
        'sima-cli cannot safely modify. The saved environment was restored and '
        'the DevKit was not rebooted.\n\n', style='bold yellow',
    )
    message.append(
        'Keep this command running. Connect to the DevKit serial console, interrupt '
        'autoboot, and enter:\n\n',
    )
    message.append(_manual_netboot_commands(devkit, server_ip, network), style='cyan')
    message.append(
        '\n\nAfter the board boots from TFTP and SSH becomes available, type "f" at the '
        'netboot prompt to flash it.',
    )
    if autoflash:
        message.append(
            '\nAutomatic flashing is disabled because manual serial setup is required.',
            style='bold yellow',
        )
    Console().print(Panel(
        message, title='Manual netboot setup required', border_style='yellow',
    ))


def _uboot_script(devkit, server_ip, network):
    """Prepare redundant environment files while preserving the boot mount mode."""
    from sima_cli.update.uboot_environment import configure_environment
    helper = inspect.getsource(configure_environment) + '\nimport sys\nconfigure_environment(sys.argv[1])\n'
    return '\n'.join([
        'set -eu',
        'config=$(mktemp)',
        'backup=',
        'restore_ro=0',
        'backup_complete=0',
        'cleanup_complete=1',
        'cleanup() {',
        '  status=$?',
        '  trap - EXIT',
        '  set +e',
        '  if [ "$status" -ne 0 ] && [ "$backup_complete" = 1 ]; then',
        '    if cp -p "$backup/uboot.env" /boot/uboot.env && cp -p "$backup/uboot-redund.env" /boot/uboot-redund.env; then',
        '      echo "Restored saved U-Boot environment after preparation failed."',
        '    else',
        '      echo "ERROR: Could not restore U-Boot environment. Backup: $backup. DevKit will not be rebooted." >&2',
        '      status=1',
        '      cleanup_complete=0',
        '    fi',
        '    if ! sync; then',
        '      echo "ERROR: Could not sync the restored U-Boot environment. DevKit will not be rebooted." >&2',
        '      status=1',
        '      cleanup_complete=0',
        '    fi',
        '  fi',
        '  if ! rm -f "$config"; then',
        '    echo "ERROR: Could not remove the temporary U-Boot configuration." >&2',
        '    status=1',
        '    cleanup_complete=0',
        '  fi',
        '  if [ "$restore_ro" = 1 ]; then',
        '    if ! sync; then',
        '      echo "ERROR: Could not sync /boot before restoring it read-only." >&2',
        '      status=1',
        '      cleanup_complete=0',
        '    fi',
        '    if ! mount -o remount,ro /boot; then',
        '      echo "ERROR: Could not restore /boot to read-only. DevKit will not be rebooted." >&2',
        '      status=1',
        '      cleanup_complete=0',
        '    fi',
        '  fi',
        f'  if [ "$status" -ne 0 ] && [ "$backup_complete" = 1 ] && [ "$cleanup_complete" = 1 ]; then echo {MANUAL_FALLBACK_READY}; fi',
        '  exit "$status"',
        '}',
        'trap cleanup EXIT',
        "printf '/boot/uboot.env 0x0000 0x80000\\n/boot/uboot-redund.env 0x0000 0x80000\\n' > \"$config\"",
        'test "$(findmnt -n -o TARGET -T /boot)" = /boot || { echo "Expected a separate /boot mount; U-Boot was not changed." >&2; exit 1; }',
        'options=$(findmnt -n -o OPTIONS -T /boot)',
        'case ",$options," in',
        '  *,ro,*)',
        '    mount -o remount,rw /boot || { echo "Cannot remount /boot writable; U-Boot was not changed and the DevKit will not be rebooted." >&2; exit 1; }',
        '    restore_ro=1 ;;',
        'esac',
        'backup=$(mktemp -d /boot/sima-cli-netboot-backup.XXXXXX)',
        'cp -p /boot/uboot.env /boot/uboot-redund.env "$backup/"',
        'backup_complete=1',
        'python3 -c ' + shlex.quote(helper) + ' "$config"',
        'fw_printenv -c "$config" > "$backup/environment.txt"',
        f'echo "{BACKUP_MARKER}$backup"',
        'export_dir=$(mktemp -d /tmp/sima-cli-netboot-export.XXXXXX)',
        'cp -p "$backup/uboot.env" "$backup/uboot-redund.env" "$export_dir/"',
        'owner=${SUDO_USER:-sima}',
        'chown -R "$owner" "$export_dir"',
        'chmod 700 "$export_dir"',
        'chmod 600 "$export_dir/uboot.env" "$export_dir/uboot-redund.env"',
        f'echo "{BACKUP_EXPORT_MARKER}$export_dir"',
        'fw_setenv -c "$config" bootfile netboot.scr.uimg',
        'fw_setenv -c "$config" netcfg static',
        'fw_setenv -c "$config" forcenetcfg static',
        'fw_setenv -c "$config" ipaddr ' + shlex.quote(devkit),
        'fw_setenv -c "$config" netmask ' + shlex.quote(network['netmask']),
        'fw_setenv -c "$config" gatewayip ' + shlex.quote(network['gateway']),
        'fw_setenv -c "$config" nfs_linux_intf ' + shlex.quote(network['interface']),
        'fw_setenv -c "$config" serverip ' + shlex.quote(server_ip),
        "fw_setenv -c \"$config\" bootcmd_net 'tftp ${scriptaddr} ${serverip}:${bootfile} && source ${scriptaddr}'",
        "fw_setenv -c \"$config\" bootcmd 'for attempt in 1 2 3 4 5; do echo Netboot attempt ${attempt}; run bootcmd_net; sleep 10; done; reset'",
        'echo "Setting boot_targets=net"',
        'fw_setenv -c "$config" boot_targets net',
        'test "$(fw_printenv -c "$config" -n boot_targets)" = net',
        'test "$(fw_printenv -c "$config" -n serverip)" = ' + shlex.quote(server_ip),
        'fw_printenv -c "$config" boot_targets serverip ipaddr',
        'sync',
    ])
