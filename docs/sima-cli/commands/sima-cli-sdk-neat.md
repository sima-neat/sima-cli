# `sima-cli sdk neat`

Launch the Neat SDK tool environment.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk neat [OPTIONS] [CMD]...
```

## Options

None.

## Arguments

| Name | Description |
| --- | --- |
| `CMD` | Optional passthrough command and arguments. All remaining tokens are joined and executed inside the selected SDK container with `bash -lc`; if omitted, sima-cli opens an interactive login shell. (accepts zero or more values) |

## Full Help

```text
Usage: sima-cli sdk neat [OPTIONS] [CMD]...

  Launch the Neat SDK tool environment.

  If CMD is provided, all remaining tokens are executed inside the matching
  running container with bash -lc. If CMD is omitted, sima-cli opens an
  interactive login shell.

  If no matching Neat SDK container is running, existing stopped Neat SDK
  container(s) are started automatically and the command is retried.

  Examples:
      sima-cli sdk neat
      sima-cli sdk neat python --version
      sima-cli sdk neat "python app.py --config config.json"

Options:
  --help  Show this message and exit.
```
