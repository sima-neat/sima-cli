"""Confirmed remote U-Boot preparation for a host-served netboot image."""
import ipaddress
import inspect
import json
import shlex
import socket

import click
import paramiko
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from sima_cli.update.remote import init_ssh_session, run_remote_command_capture


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


def configure_and_reboot(devkit, server_ip, autoflash=False):
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
        output = _checked(ssh, 'sudo sh -c ' + shlex.quote(script))
        click.echo(output)
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
        'cleanup() {',
        '  status=$?',
        '  trap - EXIT',
        '  set +e',
        '  if [ "$status" -ne 0 ] && [ "$backup_complete" = 1 ]; then',
        '    if cp -p "$backup/uboot.env" /boot/uboot.env && cp -p "$backup/uboot-redund.env" /boot/uboot-redund.env; then',
        '      echo "Restored saved U-Boot environment after preparation failed."',
        '    else',
        '      echo "ERROR: Could not restore U-Boot environment. Backup: $backup. DevKit will not be rebooted." >&2',
        '    fi',
        '    sync',
        '  fi',
        '  rm -f "$config"',
        '  if [ "$restore_ro" = 1 ]; then',
        '    sync',
        '    if ! mount -o remount,ro /boot; then',
        '      echo "ERROR: Could not restore /boot to read-only. DevKit will not be rebooted." >&2',
        '      status=1',
        '    fi',
        '  fi',
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
        'echo "Saved U-Boot environment to $backup"',
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
