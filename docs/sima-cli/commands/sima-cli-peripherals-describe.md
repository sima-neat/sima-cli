# `sima-cli peripherals describe`

Show one device in detail: every resolution and frame rate, and whether Neat can use it.

Parent command: [`sima-cli peripherals`](./sima-cli-peripherals.md)

## Usage

```bash
sima-cli peripherals describe <ID>
```

## Options

| Name | Description |
| --- | --- |
| `--socket` | Development only: the local socket of the SiMa Sentinel service to read from. (default: /run/simaai-sentinel/api.sock) |
| `--refresh` | Look for devices again first (after plugging one in). |
| `--json` | Print JSON instead of text. |

## Arguments

| Name | Description |
| --- | --- |
| `<ID>` | (required) |

## Full Help

```text
Usage: sima-cli peripherals describe <ID>

  Show one device in detail: every resolution and frame rate, and whether Neat
  can use it.

  ID is from `sima-cli peripherals list`; the device's name works too, for
  example:

    sima-cli peripherals describe imx477

Options:
  --refresh  Look for devices again first (after plugging one in).
  --json     Print JSON instead of text.
  --help     Show this message and exit.
```
