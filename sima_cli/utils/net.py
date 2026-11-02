import psutil
import socket

def get_local_ip_candidates(*, include_link_local=False):
    """
    Return a list of IPv4 addresses on physical interfaces,
    excluding VPN and loopback interfaces. Link-local addresses are excluded
    unless requested for directly connected netboot devices.
    """
    vpn_prefixes = ("tun", "tap", "utun", "tailscale", "wg", "docker")  # WireGuard, Tailscale, etc.
    ip_list = []

    for iface_name, iface_addrs in psutil.net_if_addrs().items():
        # Exclude VPN or tunnel interfaces
        if iface_name.startswith(vpn_prefixes):
            continue

        for addr in iface_addrs:
            if addr.family == socket.AF_INET:
                ip = addr.address

                # Skip loopback and link-local
                if ip.startswith("127.") or (not include_link_local and ip.startswith("169.254.")):
                    continue

                ip_list.append((iface_name, ip))

    # Prioritize physical interfaces: eth0, en0, wlan0, etc.
    ip_list.sort(key=lambda x: (not x[0].startswith(("eth", "en", "wlan", "wl")), x[0]))
    return ip_list
