# `sima-cli sessions`

List, resume and archive Claude and Codex sessions.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli sessions [OPTIONS] [COMMAND] [ARGS]...
```

## Options

None.

## Arguments

None.

## Subcommands

- [`sima-cli sessions archive`](./sima-cli-sessions-archive.md): Hide SESSION from the session lists without deleting it.
- [`sima-cli sessions list`](./sima-cli-sessions-list.md): List sessions, newest first.
- [`sima-cli sessions resume`](./sima-cli-sessions-resume.md): Continue SESSION in the agent that started it, in the folder it was started in.
- [`sima-cli sessions unarchive`](./sima-cli-sessions-unarchive.md): Bring an archived SESSION back (pick it from `list --archived`).

## Full Help

```text
Usage: sima-cli sessions [OPTIONS] [COMMAND] [ARGS]...

  List, resume and archive Claude and Codex sessions.

  Shows the sessions of both agents for the current folder in one list.
  Resuming hands off to the agent's own command, so a session always continues
  in the agent that started it.

Options:
  --help  Show this message and exit.

Commands:
  archive    Hide SESSION from the session lists without deleting it.
  list       List sessions, newest first.
  resume     Continue SESSION in the agent that started it, in the folder...
  unarchive  Bring an archived SESSION back (pick it from `list...
```
