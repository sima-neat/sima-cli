# `sima-cli peripherals describe`

Show one peripheral: identity, device nodes, every mode and Sentinel's verdict on each.

Parent command: [`sima-cli peripherals`](./sima-cli-peripherals.md)

## Usage

```bash
sima-cli peripherals describe [OPTIONS] DEVICE_ID
```

## Options

| Name | Description |
| --- | --- |
| `--socket` | Sentinel's API socket. (default: /run/simaai-sentinel/api.sock) |
| `--non-interactive` | Never prompt; fail with a result code instead. Accepted for scripts: these commands do not prompt. |
| `--refresh` | Rescan the board before reading (otherwise the catalog Sentinel holds is returned). |
| `--json` | Print a versioned JSON document on stdout; implies --non-interactive. |

## Arguments

| Name | Description |
| --- | --- |
| `DEVICE_ID` | (required) |

## Full Help

```text
Usage: sima-cli peripherals describe [OPTIONS] DEVICE_ID

  Show one peripheral: identity, device nodes, every mode and Sentinel's
  verdict on each.

Options:
  --non-interactive  Never prompt; fail with a result code instead. Accepted
                     for scripts: these commands do not prompt.
  --refresh          Rescan the board before reading (otherwise the catalog
                     Sentinel holds is returned).
  --json             Print a versioned JSON document on stdout; implies --non-
                     interactive.
  --help             Show this message and exit.
```
