# `sima-cli peripherals list`

List connected peripherals with their type, connection, status and support.

Parent command: [`sima-cli peripherals`](./sima-cli-peripherals.md)

## Usage

```bash
sima-cli peripherals list [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--type` | Only devices of this type, as Sentinel names it: camera, microphone, or a type a newer Sentinel adds. |
| `--socket` | Sentinel's API socket. (default: /run/simaai-sentinel/api.sock) |
| `--non-interactive` | Never prompt; fail with a result code instead. Accepted for scripts: these commands do not prompt. |
| `--refresh` | Rescan the board before reading (otherwise the catalog Sentinel holds is returned). |
| `--json` | Print a versioned JSON document on stdout; implies --non-interactive. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli peripherals list [OPTIONS]

  List connected peripherals with their type, connection, status and support.

Options:
  --type TYPE        Only devices of this type, as Sentinel names it: camera,
                     microphone, or a type a newer Sentinel adds.
  --non-interactive  Never prompt; fail with a result code instead. Accepted
                     for scripts: these commands do not prompt.
  --refresh          Rescan the board before reading (otherwise the catalog
                     Sentinel holds is returned).
  --json             Print a versioned JSON document on stdout; implies --non-
                     interactive.
  --help             Show this message and exit.
```
