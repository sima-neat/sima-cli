# `sima-cli sessions archive`

Hide SESSION from the session lists without deleting it.

Parent command: [`sima-cli sessions`](./sima-cli-sessions.md)

## Usage

```bash
sima-cli sessions archive [OPTIONS] SESSION
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
Usage: sima-cli sessions archive [OPTIONS] SESSION

  Hide SESSION from the session lists without deleting it.

Options:
  --all                   Sessions from every folder, not just the current
                          one.
  --agent [claude|codex]  Only sessions of this agent.
  --help                  Show this message and exit.
```
