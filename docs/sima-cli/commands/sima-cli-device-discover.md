# `sima-cli device discover`

Discover nearby SiMa.ai DevKits via ARP or multicast.

Parent command: [`sima-cli device`](./sima-cli-device.md)

## Usage

```bash
sima-cli device discover [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--ignore-cache` | Ignore ARP cache and run multicast/mDNS discovery directly. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli device discover [OPTIONS]

  Discover nearby SiMa.ai DevKits via ARP or multicast.

Options:
  --ignore-cache  Ignore ARP cache and run multicast/mDNS discovery directly.
  --help          Show this message and exit.
```
