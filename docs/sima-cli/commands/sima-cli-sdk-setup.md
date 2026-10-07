# `sima-cli sdk setup`

Initialize SDK environment and select components to start.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk setup [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--noninteractive, --non-interactive, -n` | Run in non-interactive mode (auto-select defaults). |
| `-y, --yes` | Accept setup defaults; install Model Compiler from a local ZIP if available, otherwise online. |
| `--devkit` | Configure DevKit integration for setup. Use '--devkit <IP>'. |
| `--no-insight` | Start Neat SDK without Insight UI/video/WebRTC port mappings. |
| `--insight-video-channels` | Number of Insight video channels to configure (four exposed ports per channel). (default: 4) |
| `--no-model-compiler, --no-model-sdk` | Skip Model Compiler extension setup. --no-model-sdk is kept for compatibility. |
| `--all-extensions` | Install Neat, Codex, and Claude VS Code extensions without prompting. |
| `--edgematic-studio, --studio` | Install the Edgematic Studio extension and publish its port. Off by default. |
| `--edgematic-studio-port, --studio-port` | Publish the Edgematic Studio port without installing it, for a manual install later. |
| `--minimal` | Skip optional Neat SDK container extras for CI compilation jobs. |
| `--workspace` | Host workspace directory to mount into SDK containers instead of ~/workspace. |
| `--persistent-network-profile` | Allow setup to install a persistent NetworkManager shared-network repair profile without prompting. |
| `--no-container-registry` | Skip the local registry that lets a configured DevKit download images built in the SDK. |
| `--container-registry-port` | Use this host port for the local DevKit container registry. Existing stored images are kept. |
| `--install-devkit-docker` | Install and configure Docker on the DevKit if it is missing. Requires --devkit and passwordless sudo. |
| `--image` | Start only the SDK image matching this repository:tag or tag (e.g. 'ghcr.io/sima-neat/sdk:latest' or 'latest'). Repeatable; skips the selection prompt. |

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
  -y, --yes                       Accept setup defaults; install Model
                                  Compiler from a local ZIP if available,
                                  otherwise online.
  --devkit TEXT                   Configure DevKit integration for setup. Use
                                  '--devkit <IP>'.
  --no-insight                    Start Neat SDK without Insight
                                  UI/video/WebRTC port mappings.
  --insight-video-channels INTEGER RANGE
                                  Number of Insight video channels to
                                  configure (four exposed ports per channel).
                                  [default: 4; 1<=x<=80]
  --no-model-compiler, --no-model-sdk
                                  Skip Model Compiler extension setup. --no-
                                  model-sdk is kept for compatibility.
  --all-extensions                Install Neat, Codex, and Claude VS Code
                                  extensions without prompting.
  --edgematic-studio, --studio    Install the Edgematic Studio extension and
                                  publish its port. Off by default.
  --edgematic-studio-port, --studio-port
                                  Publish the Edgematic Studio port without
                                  installing it, for a manual install later.
  --minimal                       Skip optional Neat SDK container extras for
                                  CI compilation jobs.
  --workspace DIRECTORY           Host workspace directory to mount into SDK
                                  containers instead of ~/workspace.
  --persistent-network-profile    Allow setup to install a persistent
                                  NetworkManager shared-network repair profile
                                  without prompting.
  --no-container-registry         Skip the local registry that lets a
                                  configured DevKit download images built in
                                  the SDK.
  --container-registry-port INTEGER RANGE
                                  Use this host port for the local DevKit
                                  container registry. Existing stored images
                                  are kept.  [1<=x<=65535]
  --install-devkit-docker         Install and configure Docker on the DevKit
                                  if it is missing. Requires --devkit and
                                  passwordless sudo.
  --image TEXT                    Start only the SDK image matching this
                                  repository:tag or tag (e.g. 'ghcr.io/sima-
                                  neat/sdk:latest' or 'latest'). Repeatable;
                                  skips the selection prompt.
  --help                          Show this message and exit.
```
