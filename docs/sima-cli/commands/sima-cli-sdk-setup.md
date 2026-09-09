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
| `-y, --yes` | Skip confirmation before starting the container. |
| `--devkit` | Configure DevKit integration for setup. Use '--devkit <IP>'. |
| `--no-insight` | Start Neat SDK without Insight UI/video/WebRTC port mappings. |
| `--insight-video-channels` | Number of Insight video channels to configure (four exposed ports per channel). (default: 4) |
| `--no-model-compiler, --no-model-sdk` | Skip Model Compiler extension setup. --no-model-sdk is kept for compatibility. |
| `--edgematic-studio, --studio` | Install the Edgematic Studio extension and publish its port. Off by default. |
| `--edgematic-studio-port, --studio-port` | Publish the Edgematic Studio port without installing it, for a manual install later. |
| `--minimal` | Skip optional Neat SDK container extras for CI compilation jobs. |
| `--workspace` | Host workspace directory to mount into SDK containers instead of ~/workspace. |
| `--persistent-network-profile` | Allow setup to install a persistent NetworkManager shared-network repair profile without prompting. |
| `--image` | Start only the SDK image matching this repository:tag or tag (e.g. 'ghcr.io/sima-neat/sdk:latest' or 'latest'). Repeatable; skips the selection prompt. |

## Edgematic Studio opt-in

Default setup does not display an Edgematic Studio panel, ask about installing
it, install it, or publish its port. This applies to interactive setup as well
as `-y` and `--noninteractive`.

Use `--edgematic-studio` to install Studio and publish its port. The explicit
`--edgematic-studio-port` option publishes only the port for a later manual
installation.

## Arguments

None.

## Browser VS Code extension versions

When browser VS Code extensions are selected during setup, sima-cli installs
exact versions of Codex and Claude and pins them against automatic extension
updates. Rerunning setup replaces a different installed version, including a
newer incompatible version. A matching version is retained and pinned without
downloading it again. Updates for unrelated extensions are unaffected.

SDK images can declare the versions validated with their bundled OpenVSCode
Server in `/etc/sima-neat/vscode-extensions.json`:

```json
{
  "schema_version": 1,
  "extensions": {
    "openai.chatgpt": "26.5825.51511",
    "anthropic.claude-code": "2.1.266"
  }
}
```

The manifest does not install extensions or change the setup opt-in behavior.
SDK publishers should ship it even when extensions are not preinstalled, and
validate both versions with each SDK release. Schema version 1 requires exact
versions for both IDs; additional extension entries do not cause installation.

For older images without this file, including the existing SDK 2.1.3 image,
sima-cli uses Codex `26.5825.51511` and Claude `2.1.266` as compatibility
fallbacks. Codex `26.901.22334` was reported to fail activation with
`Unexpected identifier 'p'` on the SDK 2.1.3 OpenVSCode Server `1.109.5`;
downgrading to the fallback version restored functionality.

An unreadable or invalid manifest reports an error and skips browser extension
installation while the rest of SDK setup continues. An unavailable requested
version also reports an installation error; sima-cli never retries with an
unpinned latest release.

The existing environment overrides take precedence over SDK/default versions:

| Variable | Behavior |
| --- | --- |
| `SIMA_CLI_CODEX_EXTENSION_ID` | Override the Codex target with `publisher.extension@exact-version`. Empty disables Codex installation. |
| `SIMA_CLI_CLAUDE_EXTENSION_ID` | Override the Claude target with `publisher.extension@exact-version`. Empty disables Claude installation. |
| `SIMA_CLI_INSTALL_CODEX_EXTENSION` | A truthy value automatically selects browser extension installation without its interactive prompt; retained for compatibility. |

A bare `openai.chatgpt` or `anthropic.claude-code` override uses the corresponding
SDK/default pin. Other extension IDs now require an explicit version rather
than resolving to latest. Overrides still require a valid manifest when one
is present. `--minimal` skips optional extension installation.

Setup verifies the installed versions and writes each selected extension's
`metadata.pinned` flag in the mapped user's
`~/.openvscode-server/extensions/extensions.json`, then restarts the browser
VS Code service. This also pins matching versions installed before this policy
was introduced, which OpenVSCode's install command otherwise leaves unpinned.
Reload an open browser VS Code tab after setup to load the selected versions.

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
  --image TEXT                    Start only the SDK image matching this
                                  repository:tag or tag (e.g. 'ghcr.io/sima-
                                  neat/sdk:latest' or 'latest'). Repeatable;
                                  skips the selection prompt.
  --help                          Show this message and exit.
```
