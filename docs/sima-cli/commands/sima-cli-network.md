# `sima-cli network`

Setup Network IP address on the DevKit

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli network [OPTIONS]
```

## Options

None.

## Arguments

None.

## Full Help

```text
Usage: sima-cli network [OPTIONS]

  Setup Network IP address on the DevKit

  This command only works on the DevKit. Select an interface, then choose
  DHCP, Default Static IP, or Custom Static IP.

  Custom Static IP prompts for an IPv4 address, such as 192.168.1.50, or an
  address with a subnet prefix, such as 192.168.1.50/24. A bare address uses
  the default static profile's prefix. Invalid addresses are rejected; leave
  the prompt blank to cancel.

  Custom addresses are activated without changing the existing boot
  configuration. The default static settings are copied into a temporary
  custom profile with autoconnect disabled on NetworkManager, or a runtime
  configuration on systemd-networkd. After reboot, the board uses its existing
  startup configuration. Selecting DHCP or Default Static IP disables the
  custom configuration. DNS and other default static settings are inherited.
  If an inherited IPv4 gateway is incompatible with the new address, enter a
  replacement gateway or leave it blank for no gateway. The default profile is
  not modified.

  Network changes must be made from the DevKit serial console. SSH sessions,
  including commands launched through sudo, are rejected before configuration
  changes. A replacement custom profile is activated before the old one is
  removed.

Options:
  --help  Show this message and exit.
```
