# `sima-cli sessions unarchive`

Bring back SESSION, picked from `sima-cli sessions list --archived`.

Parent command: [`sima-cli sessions`](./sima-cli-sessions.md)

## Usage

```bash
sima-cli sessions unarchive [OPTIONS] SESSION
```

## Options

| Name | Description |
| --- | --- |
| `--all` | Sessions from every folder, not just the current one. |
| `--agent` | Only sessions of this agent (row numbers stay the same). |

## Arguments

| Name | Description |
| --- | --- |
| `SESSION` | (required) |

## Full Help

```text
Usage: sima-cli sessions unarchive [OPTIONS] SESSION

  Bring back SESSION, picked from `sima-cli sessions list --archived`.

Options:
  --all                   Sessions from every folder, not just the current
                          one.
  --agent [claude|codex]  Only sessions of this agent (row numbers stay the
                          same).
  --help                  Show this message and exit.
```
