# SDK Setup and Extension Management

Choose optional SDK services, browser VS Code extensions, and the Model Compiler installation source when running `sima-cli sdk setup`.

Command reference: [`sima-cli sdk setup`](../commands/sima-cli-sdk-setup.md)

## Edgematic Studio opt-in

Default setup does not display an Edgematic Studio prompt, install Studio, or
publish its port. This also applies to `-y` and `--noninteractive`.

Use `--edgematic-studio` to install Studio and publish its port. Use
`--edgematic-studio-port` to publish only the port for a later manual install.

## Browser VS Code extension selection

Interactive setup offers Neat, Codex, and Claude extensions. Use Space to select
extensions and Enter to confirm. All start unchecked; selecting none skips
installation, and existing unselected extensions remain installed.

For automation, install all three without the checklist:

```bash
sima-cli sdk setup --noninteractive --all-extensions
```

`--all-extensions` bypasses only extension selection. Use `--noninteractive` to
skip other questions. It also installs extensions when reusing an existing SDK
container. Without it, `-y` and `--noninteractive` skip optional extensions
unless the legacy `SIMA_CLI_INSTALL_CODEX_EXTENSION` setting requests them.
`--all-extensions` cannot be combined with `--minimal` and does not enable
Edgematic Studio or alter Model Compiler selection.

## Browser VS Code extension versions

sima-cli installs exact Codex and Claude versions and pins them against automatic
updates. Rerunning setup replaces a different version; a matching version is
retained and pinned without another download. SDK images can declare validated
versions in `/etc/sima-neat/vscode-extensions.json`:

```json
{
  "schema_version": 1,
  "extensions": {
    "openai.chatgpt": "26.5825.51511",
    "anthropic.claude-code": "2.1.266"
  }
}
```

The manifest does not opt users into installation. Schema version 1 requires
exact versions for both IDs. Older images without a manifest use Codex
`26.5825.51511` and Claude `2.1.266` as compatibility fallbacks. An unreadable
or invalid manifest reports an error and skips extension installation while the
rest of setup continues. Failed Codex or Claude install commands are retried up
to three times using the same exact version and normal TLS verification.

Environment overrides take precedence:

| Variable | Behavior |
| --- | --- |
| `SIMA_CLI_CODEX_EXTENSION_ID` | Override Codex with `publisher.extension@exact-version`; empty disables it unless `--all-extensions` is used. |
| `SIMA_CLI_CLAUDE_EXTENSION_ID` | Override Claude with `publisher.extension@exact-version`; empty disables it unless `--all-extensions` is used. |
| `SIMA_CLI_INSTALL_CODEX_EXTENSION` | Automatically select extension installation without the checklist. |

A bare built-in extension ID uses the SDK/default pin. Other IDs require an
explicit version. `--minimal` skips optional extension installation. Setup
verifies installed versions, writes each selected extension's `metadata.pinned`
flag, and restarts browser VS Code; reload an open browser tab afterward.

## Model Compiler installation source

Setup looks only in the current working directory for
`model-compiler-arm64.zip` or `model-compiler-amd64.zip`, based on the SDK
container architecture. Extracted folders, parent directories, workspace ZIPs,
and the other architecture are not searched.

When a valid local ZIP is found, setup offers local, online, or skip. Without a
valid ZIP, it offers online or skip, with skip as the Enter default.

| Flags | Local ZIP available | No valid local ZIP |
| --- | --- | --- |
| `--noninteractive` | Install local | Skip |
| `--noninteractive -y` | Install local | Install online |
| `-y` | Install local | Install online |

Online unattended installation requires `-y`. For legacy SDK sources,
authenticate on the host with `sima-cli login`. `--minimal`,
`--no-model-compiler`, and `--no-model-sdk` always skip installation.

The official ZIP must contain `install_modelsdk_wheels.sh`, `source.json`,
`manifest.txt`, and package payloads at its root, and it must match the compiler
version selected for the SDK. The archive is extracted to temporary host storage
and copied into temporary Linux-container storage, so allow room for both copies.
Temporary data is removed after success or failure and the original ZIP is kept.
A failed local install never falls back online. System packages and Python
prerequisites may still require network access.
