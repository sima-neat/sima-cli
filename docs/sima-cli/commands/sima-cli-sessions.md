# `sima-cli sessions`

List, resume and archive Claude and Codex sessions.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli sessions [<command>]
```

## Options

None.

## Arguments

None.

## Subcommands

- [`sima-cli sessions archive`](./sima-cli-sessions-archive.md): Hide SESSION from the session lists without deleting it.
- [`sima-cli sessions list`](./sima-cli-sessions-list.md): List sessions, newest first.
- [`sima-cli sessions resume`](./sima-cli-sessions-resume.md): Continue SESSION in the agent that started it, in the folder it was started in.
- [`sima-cli sessions unarchive`](./sima-cli-sessions-unarchive.md): Bring back SESSION, picked from `sima-cli sessions list --archived`.

## Full Help

```text
Usage: sima-cli sessions [<command>]

  List, resume and archive Claude and Codex sessions.

  Shows the sessions of both agents for the current folder in one list.
  Resuming hands off to the agent's own command, so a session always continues
  in the agent that started it.

Options:
  --help  Show this message and exit.

Commands:
  archive    Hide a session from the lists without deleting it.
  list       List sessions, newest first.
  resume     Continue a session in its own agent and folder.
  unarchive  Bring back a session from `list --archived`.
```
