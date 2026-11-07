# `sima-cli cloudex connect`

Connect to an allocated DevKit using a securely supplied key.

Parent command: [`sima-cli cloudex`](./sima-cli-cloudex.md)

## Usage

```bash
sima-cli cloudex connect [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--key-file` | Read the allocation key from a mode-0600 file. |
| `--key-stdin` | Read the allocation key from standard input for automation. |
| `--allocation` | Connect to your NeatOps allocation ID without copying a key. |
| `--environment` | (default: production) |
| `--transport` |  |
| `--attempts` |  |
| `--verbose` | Print each sanitized connection stage for troubleshooting. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli cloudex connect [OPTIONS]

  Connect to an allocated DevKit using a securely supplied key.

Options:
  --key-file FILE                 Read the allocation key from a mode-0600
                                  file.
  --key-stdin                     Read the allocation key from standard input
                                  for automation.
  --allocation TEXT               Connect to your NeatOps allocation ID
                                  without copying a key.
  --environment [production|staging]
                                  [default: production]
  --verbose                       Print each sanitized connection stage for
                                  troubleshooting.
  --help                          Show this message and exit.
```
