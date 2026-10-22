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
| `--agent` | Only sessions of this agent (row numbers stay the same). |
| `--archived` | Archived sessions instead of active ones. |
| `--json` | Print a JSON document (schema_version, command, result, sessions, warnings). |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sessions list [OPTIONS]

  List sessions, newest first.

  The # column is what `resume`, `archive` and `unarchive` accept when given
  the same --all and --archived flags, along with a session id, an id prefix
  or a title. Rows are counted over every agent that could be listed, so they
  shift if the Codex CLI appears or disappears in between.

Options:
  --all                   Sessions from every folder, not just the current
                          one.
  --agent [claude|codex]  Only sessions of this agent (row numbers stay the
                          same).
  --archived              Archived sessions instead of active ones.
  --json                  Print a JSON document (schema_version, command,
                          result, sessions, warnings).
  --help                  Show this message and exit.
```
