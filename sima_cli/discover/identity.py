"""MAC validation and bounded, noninteractive IPv4 neighbor-table lookup."""
import ipaddress
import platform
import re
import subprocess

MAC_PATTERN = r'(?:[0-9a-f]{1,2}[:-]){5}[0-9a-f]{1,2}'


def valid_mac(value):
    if not isinstance(value, str) or not re.fullmatch(MAC_PATTERN, value, re.I):
        return None
    mac = ':'.join(part.zfill(2) for part in re.split('[:-]', value.lower()))
    if mac == '00:00:00:00:00:00' or int(mac[:2], 16) & 1:
        return None
    return mac


def parse_neighbors(output, system):
    records = []
    for line in output.splitlines():
        if re.search(r'\b(INCOMPLETE|FAILED|NOARP)\b', line, re.I):
            continue
        if system == 'Darwin':
            match = re.search(r'\(([\d.]+)\)\s+at\s+(' + MAC_PATTERN + r')\s+on\s+(\S+)', line, re.I)
        else:
            match = re.search(r'^([\d.]+)\s+dev\s+(\S+)\s+lladdr\s+(' + MAC_PATTERN + r')\b', line, re.I)
            if match:
                ip, iface, mac = match.groups()
                match = (ip, mac, iface)
            else:  # net-tools arp -n: IP HWtype HWaddress Flags Mask Iface
                fallback = re.search(r'^([\d.]+)\s+\S+\s+(' + MAC_PATTERN + r')\s+.*?\s+(\S+)\s*$', line, re.I)
                match = fallback.groups() if fallback else None
        if not match:
            continue
        ip, mac, iface = match.groups() if hasattr(match, 'groups') else match
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        mac = valid_mac(mac)
        if mac:
            records.append({'ip': ip, 'mac': mac, 'iface': iface})
    return records


def read_neighbors():
    system = platform.system()
    if system not in ('Darwin', 'Linux'):
        return []
    command = ['arp', '-n', '-a'] if system == 'Darwin' else ['ip', '-4', 'neigh', 'show']
    try:
        try:
            output = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=1)
        except FileNotFoundError:
            if system != 'Linux':
                return []
            output = subprocess.check_output(['arp', '-n'], text=True, stderr=subprocess.DEVNULL, timeout=1)
        return parse_neighbors(output, system)
    except (OSError, subprocess.SubprocessError):
        return []


def identity_for_ip(ip):
    """Only direct neighbor entries identify a device; never use a gateway MAC."""
    matches = {(r['mac'], r['iface']) for r in read_neighbors() if r['ip'] == ip}
    if len(matches) == 1:
        mac, iface = matches.pop()
        return {'ip': ip, 'mac': mac, 'iface': iface}
    return None
