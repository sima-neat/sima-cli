# `sima-cli install`

Install SiMa packages.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli install [OPTIONS] [COMPONENT]
```

## Options

| Name | Description |
| --- | --- |
| `-v, --version` | SDK version (required for SDK-dependent components unless --metadata is provided) |
| `-m, --mirror` | URL to a metadata.json file for generic installation |
| `-t, --tag` | Tag of the package. With --neat, metadata variant type such as minimum. |
| `--neat` | Install from Neat artifacts using the Neat package resolver. |
| `--vulcan` | Install from Neat/Vulcan artifacts. Compatibility alias for --neat. |
| `--dev` | Use dev artifacts. |
| `--stg, --staging` | Use staging artifacts. |
| `--prd, --prod` | Use production artifacts. |
| `--env` | Neat artifact environment. Used with --neat or --vulcan. Defaults to production. |
| `--base-url` | Override the Neat artifact base URL. Used with --neat or --vulcan. |
| `-d, --install-dir` | Directory where package resources are downloaded and installed. (default: .) |
| `--json` | With --neat or --vulcan, print resolved metadata URL and exit. |
| `-f, --force` | Force installation even if compatibility checks fail. |

## Arguments

| Name | Description |
| --- | --- |
| `COMPONENT` |  |

## Full Help

```text
Usage: sima-cli install [OPTIONS] [COMPONENT]

  Install SiMa packages.

  This command is the unified installer for all SiMa-defined packages—
  regardless of package type, platform, or deployment location. Packages
  follow the standard metadata specification (metadata.json), with optional
  extended formats such as metadata-v2.json.

  Key Features:

    • Automatically resolves the package URL, allowing simplified command
    syntax.

    • Handles all supported asset retrieval flows: Developer Portal
    authenticated downloads, Hugging Face artifacts, GitHub releases/assets,
    Docker Hub images, and raw file URLs.

    • Performs prerequisite checks (e.g., OS compatibility, disk space,
    platform model).

    • Supports interactive multi-selection when a package defines optional
    components.

    • Automatically prompts for authentication when required.

    • Works uniformly across all SiMa package types—including SDK releases,
    OptiView, LLiMa demos, 16-channel vision demos, and internal tools.

  Examples:
      sima-cli install hostdriver -v 1.6.0

      sima-cli install optiview

      sima-cli install -m https://custom-server/packages/foo/metadata.json

      sima-cli install samples/llima -v 1.7.0

Options:
  -v, --version TEXT              SDK version (required for SDK-dependent
                                  components unless --metadata is provided)
  -m, --mirror TEXT               URL to a metadata.json file for generic
                                  installation
  -t, --tag TEXT                  Tag of the package. With --neat, metadata
                                  variant type such as minimum.
  --neat                          Install from Neat artifacts using the Neat
                                  package resolver.
  --vulcan                        Install from Neat/Vulcan artifacts.
                                  Compatibility alias for --neat.
  --dev                           Use dev artifacts.
  --stg, --staging                Use staging artifacts.
  --prd, --prod                   Use production artifacts.
  --env [dev|stg|staging|prd|prod|production]
                                  Neat artifact environment. Used with --neat
                                  or --vulcan. Defaults to production.
  --base-url TEXT                 Override the Neat artifact base URL. Used
                                  with --neat or --vulcan.
  -d, --install-dir DIRECTORY     Directory where package resources are
                                  downloaded and installed.  [default: .]
  --json                          With --neat or --vulcan, print resolved
                                  metadata URL and exit.
  -f, --force                     Force installation even if compatibility
                                  checks fail.
  --help                          Show this message and exit.
```
