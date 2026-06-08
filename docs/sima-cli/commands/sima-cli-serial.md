# `sima-cli serial`

Connect to the UART serial console of the DevKit.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli serial [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `-b, --baud` | Baud rate for the serial connection (default: 115200) |

## Arguments

None.

## Full Help

```text
Usage: sima-cli serial [OPTIONS]

  Connect to the UART serial console of the DevKit.

  Automatically detects the serial port and launches a terminal emulator:

  - macOS: uses 'picocom'

  - Linux: uses 'picocom'

  - Windows: shows PuTTY/Tera Term setup instructions

Options:
  -b, --baud INTEGER  Baud rate for the serial connection  [default: 115200]
  --help              Show this message and exit.
```
