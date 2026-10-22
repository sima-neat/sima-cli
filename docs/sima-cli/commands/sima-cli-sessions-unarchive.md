# `sima-cli sessions unarchive`

Bring an archived SESSION back (pick it from `list --archived`).

Parent command: [`sima-cli sessions`](./sima-cli-sessions.md)

## Usage

```bash
sima-cli sessions unarchive [OPTIONS] SESSION
```

## Options

| Name | Description |
| --- | --- |
| `--all` | Sessions from every folder, not just the current one. |
| `--agent` | Only sessions of this agent. |

## Arguments

| Name | Description |
| --- | --- |
| `SESSION` | (required) |

## Full Help

```text
Usage: sima-cli sessions unarchive [OPTIONS] SESSION

  Bring an archived SESSION back (pick it from `list --archived`).

Options:
  --all                   Sessions from every folder, not just the current
                          one.
  --agent [claude|codex]  Only sessions of this agent.
  --help                  Show this message and exit.
```
