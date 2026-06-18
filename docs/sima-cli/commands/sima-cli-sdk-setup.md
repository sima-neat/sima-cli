# `sima-cli sdk setup`

Initialize SDK environment and select components to start.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

When `--devkit <IP>` is provided on an affected Ubuntu/NetworkManager host,
setup applies the current-session SDK bridge forwarding repair automatically.
If a persistent NetworkManager dispatcher hook would help across reconnects or
reboots, setup prompts before installing it. Use `-y` or non-interactive setup
to accept the default automated path.

## Usage

```bash
sima-cli sdk setup [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--noninteractive, --non-interactive, -n` | Run in non-interactive mode (auto-select defaults). |
| `-y, --yes` | Skip confirmation before starting the container. |
| `--devkit` | Configure DevKit integration for setup. Use '--devkit <IP>' or '--devkit auto'. |
| `--no-insight` | Start Neat SDK without Insight UI/video/WebRTC port mappings. |
| `--no-model-compiler, --no-model-sdk` | Skip Model Compiler extension setup. --no-model-sdk is kept for compatibility. |
| `--minimal` | Skip optional Neat SDK container extras for CI compilation jobs. |
| `--workspace` | Host workspace directory to mount into SDK containers instead of ~/workspace. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sdk setup [OPTIONS]

  Initialize SDK environment and select components to start.

Options:
  -n, --noninteractive, --non-interactive
                                  Run in non-interactive mode (auto-select
                                  defaults).
  -y, --yes                       Skip confirmation before starting the
                                  container.
  --devkit TEXT                   Configure DevKit integration for setup. Use
                                  '--devkit <IP>' or '--devkit auto'.
  --no-insight                    Start Neat SDK without Insight
                                  UI/video/WebRTC port mappings.
  --no-model-compiler, --no-model-sdk
                                  Skip Model Compiler extension setup. --no-
                                  model-sdk is kept for compatibility.
  --minimal                       Skip optional Neat SDK container extras for
                                  CI compilation jobs.
  --workspace DIRECTORY           Host workspace directory to mount into SDK
                                  containers instead of ~/workspace.
  --help                          Show this message and exit.
```
