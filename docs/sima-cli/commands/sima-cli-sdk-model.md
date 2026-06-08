# `sima-cli sdk model`

Launch the Model SDK tool environment.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk model [OPTIONS] [CMD]...
```

## Options

None.

## Arguments

| Name | Description |
| --- | --- |
| `CMD` | Optional passthrough command and arguments. All remaining tokens are joined and executed inside the selected SDK container with `bash -lc`; if omitted, sima-cli opens an interactive login shell. (accepts zero or more values) |

## Full Help

```text
Usage: sima-cli sdk model [OPTIONS] [CMD]...

  Launch the Model SDK tool environment.

  If CMD is provided, all remaining tokens are executed inside the matching
  running container with bash -lc. If CMD is omitted, sima-cli opens an
  interactive login shell.

  Examples:
      sima-cli sdk model
      sima-cli sdk model python --version
      sima-cli sdk model "python script.py --flag"

Options:
  --help  Show this message and exit.
```
