# `sima-cli sdk start`

Select and start one or more SDK containers.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk start [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--noninteractive, --non-interactive, -n` | Run in non-interactive mode (auto-select defaults). |
| `-y, --yes` | Skip confirmation before starting the container. |
| `--image` | Start only the SDK image matching this repository:tag or tag (e.g. 'ghcr.io/sima-neat/sdk:latest' or 'latest'). Repeatable; skips the selection prompt. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sdk start [OPTIONS]

  Select and start one or more SDK containers.

Options:
  -n, --noninteractive, --non-interactive
                                  Run in non-interactive mode (auto-select
                                  defaults).
  -y, --yes                       Skip confirmation before starting the
                                  container.
  --image TEXT                    Start only the SDK image matching this
                                  repository:tag or tag (e.g. 'ghcr.io/sima-
                                  neat/sdk:latest' or 'latest'). Repeatable;
                                  skips the selection prompt.
  --help                          Show this message and exit.
```
