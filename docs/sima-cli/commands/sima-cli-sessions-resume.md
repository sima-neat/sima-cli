# `sima-cli sessions resume`

Continue SESSION in the agent that started it, in the folder it was started in.

Parent command: [`sima-cli sessions`](./sima-cli-sessions.md)

## Usage

```bash
sima-cli sessions resume [OPTIONS] SESSION
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
Usage: sima-cli sessions resume [OPTIONS] SESSION

  Continue SESSION in the agent that started it, in the folder it was started
  in.

Options:
  --all                   Sessions from every folder, not just the current
                          one.
  --agent [claude|codex]  Only sessions of this agent.
  --help                  Show this message and exit.
```
