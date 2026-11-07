# `sima-cli cloudex request`

Request a NeatOps device, wait for preparation, and connect automatically.

Parent command: [`sima-cli cloudex`](./sima-cli-cloudex.md)

## Usage

```bash
sima-cli cloudex request [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--device` | Device ID or exact name; otherwise choose interactively. |
| `--cluster` | Appliance ID, to disambiguate devices. |
| `--duration` | (default: 1h) |
| `--environment` | (default: production) |
| `--verbose` | Print sanitized connection stages. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli cloudex request [OPTIONS]

  Request a NeatOps device, wait for preparation, and connect automatically.

Options:
  --device TEXT                   Device ID or exact name; otherwise choose
                                  interactively.
  --cluster TEXT                  Appliance ID, to disambiguate devices.
  --duration [1h|4h|8h|1d|7d|4w]  [default: 1h]
  --environment [production|staging]
                                  [default: production]
  --verbose                       Print sanitized connection stages.
  --help                          Show this message and exit.
```
