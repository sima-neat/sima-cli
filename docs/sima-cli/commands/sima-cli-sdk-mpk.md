# `sima-cli sdk mpk`

Access MPK CLI toolset container for managing and building pipelines along with the device manager.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk mpk [OPTIONS] [CMD]...
```

## Options

None.

## Arguments

| Name | Description |
| --- | --- |
| `CMD` | Optional passthrough command and arguments. All remaining tokens are joined and executed inside the selected SDK container with `bash -lc`; if omitted, sima-cli opens an interactive login shell. (accepts zero or more values) |

## Full Help

```text
Usage: sima-cli sdk mpk [OPTIONS] [CMD]...

  Access MPK CLI toolset container for managing and building pipelines along
  with the device manager. It also includes the plugins zoo and the
  Performance Estimator tool.

  If CMD is provided, all remaining tokens are executed inside the matching
  running container with bash -lc. If CMD is omitted, sima-cli opens an
  interactive login shell.

  Examples:
      sima-cli sdk mpk
      sima-cli sdk mpk mpk --help
      sima-cli sdk mpk "mpk compile --help"

Options:
  --help  Show this message and exit.
```
