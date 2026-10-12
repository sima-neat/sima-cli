# `sima-cli peripherals list`

Show the connected cameras and microphones.

Parent command: [`sima-cli peripherals`](./sima-cli-peripherals.md)

## Usage

```bash
sima-cli peripherals list
```

## Options

| Name | Description |
| --- | --- |
| `--socket` | Development only: the local socket of the SiMa Sentinel service to read from. (default: /run/simaai-sentinel/api.sock) |
| `--refresh` | Look for devices again first (after plugging one in). |
| `--json` | Print JSON instead of text. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli peripherals list

  Show the connected cameras and microphones.

  SUPPORT is "Supported" when a Neat application can capture from the camera,
  or "Unsupported" with the reason under the table.

Options:
  --refresh  Look for devices again first (after plugging one in).
  --json     Print JSON instead of text.
  --help     Show this message and exit.
```
