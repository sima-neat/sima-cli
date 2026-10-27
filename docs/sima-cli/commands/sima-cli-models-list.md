# `sima-cli models list`

Browse registered models, or list them with --json.

Parent command: [`sima-cli models`](./sima-cli-models.md)

## Usage

```bash
sima-cli models list [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `-b, --branch` | Models repository branch. If omitted, select interactively; --json defaults to main. |
| `-q, --query` | Search model names, variants, categories, or branch. Multiple words must all match. |
| `--stg, --staging` | Use the staging Model Registry unless SIMA_MODELS_REGISTRY_BASE_URL is set. |
| `--json` | Print machine-readable JSON without prompting. |
| `-o, --output` | Root directory for an interactively downloaded artifact. (default: models) |
| `--force` | Replace an existing downloaded artifact. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli models list [OPTIONS]

  Browse registered models, or list them with --json.

Options:
  -b, --branch TEXT       Models repository branch. If omitted, select
                          interactively; --json defaults to main.
  -q, --query TEXT        Search model names, variants, categories, or branch.
                          Multiple words must all match.
  --stg, --staging        Use the staging Model Registry unless
                          SIMA_MODELS_REGISTRY_BASE_URL is set.
  --json                  Print machine-readable JSON without prompting.
  -o, --output DIRECTORY  Root directory for an interactively downloaded
                          artifact.  [default: models]
  --force                 Replace an existing downloaded artifact.
  --help                  Show this message and exit.
```
