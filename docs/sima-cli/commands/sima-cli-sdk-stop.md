# `sima-cli sdk stop`

Stop one or more running SDK containers.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk stop [OPTIONS] [SDK]
```

## Options

| Name | Description |
| --- | --- |
| `-y, --yes` | Skip confirmation before stopping SDK containers. |

## Arguments

| Name | Description |
| --- | --- |
| `SDK` |  |

## Full Help

```text
Usage: sima-cli sdk stop [OPTIONS] [SDK]

  Stop one or more running SDK containers.

  Examples:     sima-cli sdk stop     sima-cli sdk stop yocto     sima-cli sdk
  -v latest_develop stop mpk -y

Options:
  -y, --yes  Skip confirmation before stopping SDK containers.
  --help     Show this message and exit.
```
