# `sima-cli sessions list`

List sessions, newest first.

Parent command: [`sima-cli sessions`](./sima-cli-sessions.md)

## Usage

```bash
sima-cli sessions list [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--all` | Sessions from every folder, not just the current one. |
| `--agent` | Only sessions of this agent. |
| `--archived` | Archived sessions instead of active ones. |
| `--json` | Print the sessions as JSON. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sessions list [OPTIONS]

  List sessions, newest first.

  The # column is what `resume`, `archive` and `unarchive` accept, along with
  a session id, an id prefix or a title.

Options:
  --all                   Sessions from every folder, not just the current
                          one.
  --agent [claude|codex]  Only sessions of this agent.
  --archived              Archived sessions instead of active ones.
  --json                  Print the sessions as JSON.
  --help                  Show this message and exit.
```
