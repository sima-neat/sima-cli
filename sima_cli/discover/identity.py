"""Bounded, noninteractive IPv4 neighbor and DevKit discovery helpers."""
import ipaddress
import json
import platform
import re
import select
import socket
import subprocess
import threading
import time

from sima_cli.utils.net import get_local_ip_candidates

MCAST_GRP = '239.255.42.1'
MCAST_PORT = 50000
SRC_PORT = 60000
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


def query_multicast(*, iface=None, timeout=1.0, cancel_event=None, require_mac=True):
    """Query local interfaces within one deadline, without SSH or UI effects.

    Keep source port 60000 for compatibility with existing discovery responders.
    The datagram source is authoritative for the candidate IP, not JSON's IP.
    """
    cancel = cancel_event if cancel_event is not None else threading.Event()
    deadline = time.monotonic() + timeout
    sockets = {}
    records = {}
    try:
        for name, address in get_local_ip_candidates(include_link_local=True):
            if cancel.is_set() or time.monotonic() >= deadline:
                break
            if iface and name != iface:
                continue
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((address, SRC_PORT))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                sock.setblocking(False)
                sock.sendto(b'DISCOVER', (MCAST_GRP, MCAST_PORT))
                sockets[sock] = name
            except OSError:
                if sock is not None:
                    sock.close()
        while sockets and not cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(list(sockets), [], [], min(0.1, remaining))
            for sock in ready:
                try:
                    data, source = sock.recvfrom(2048)
                    message = json.loads(data.decode('utf-8'))
                    if not isinstance(message, dict):
                        continue
                    mac = valid_mac(message.get('mac'))
                    ip = str(ipaddress.IPv4Address(source[0]))
                    if mac or not require_mac:
                        record = dict(message, ip=ip, mac=mac, iface=sockets[sock])
                        records[(ip, mac, sockets[sock])] = record
                except (OSError, ValueError, UnicodeError):
                    continue
    finally:
        for sock in sockets:
            sock.close()
    return list(records.values())
