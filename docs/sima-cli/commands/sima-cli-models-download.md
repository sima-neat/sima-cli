# `sima-cli models download`

Download a model variant from the branch catalog.

Parent command: [`sima-cli models`](./sima-cli-models.md)

## Usage

```bash
sima-cli models download [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `-b, --branch` | Models repository branch. (default: main) |
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

  Download a model variant from the branch catalog.

Options:
  -b, --branch TEXT       Models repository branch.  [default: main]
  --id TEXT               Model ID.  [required]
  --variant TEXT          Model variant ID.  [required]
  --stg, --staging        Use the staging Model Registry unless
                          SIMA_MODELS_REGISTRY_BASE_URL is set.
  -o, --output DIRECTORY  Root output directory.  [default: models]
  --force                 Replace an existing downloaded artifact.
  --json                  Print a machine-readable result.
  --help                  Show this message and exit.
```
