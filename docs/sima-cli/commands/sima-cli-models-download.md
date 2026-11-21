# `sima-cli models download`

Download a model variant from a branch or published catalog.

Parent command: [`sima-cli models`](./sima-cli-models.md)

## Usage

```bash
sima-cli models download [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `-b, --branch` | Models repository branch (default: main). |
| `--platform-version` | Read the published catalog of this SiMa.ai Platform version instead of a branch. |
| `--core-version` | Neat Core version of the published catalog (default: the platform's default). Requires --platform-version. |
| `--id` | Model ID. (required) |
| `--variant` | Model variant ID. (required) |
| `--stg, --staging` | Use the staging Model Registry unless SIMA_MODELS_REGISTRY_BASE_URL is set. |
| `-o, --output` | Root output directory. (default: models) |
| `--force` | Replace an existing downloaded artifact. |
| `--json` | Print a machine-readable result. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli models download [OPTIONS]

  Download a model variant from a branch or published catalog.

Options:
  -b, --branch TEXT        Models repository branch (default: main).
  --platform-version TEXT  Read the published catalog of this SiMa.ai Platform
                           version instead of a branch.
  --core-version TEXT      Neat Core version of the published catalog
                           (default: the platform's default). Requires
                           --platform-version.
  --id TEXT                Model ID.  [required]
  --variant TEXT           Model variant ID.  [required]
  --stg, --staging         Use the staging Model Registry unless
                           SIMA_MODELS_REGISTRY_BASE_URL is set.
  -o, --output DIRECTORY   Root output directory.  [default: models]
  --force                  Replace an existing downloaded artifact.
  --json                   Print a machine-readable result.
  --help                   Show this message and exit.
```
