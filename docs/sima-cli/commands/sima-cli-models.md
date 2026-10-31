# `sima-cli models`

Browse and download Model Registry artifacts from production by default.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli models [OPTIONS] COMMAND [ARGS]...
```

## Options

| Name | Description |
| --- | --- |
| `--stg, --staging` | Use the staging Model Registry unless SIMA_MODELS_REGISTRY_BASE_URL is set. |

## Arguments

None.

## Subcommands

- [`sima-cli models branches`](./sima-cli-models-branches.md): List branches containing registered models.
- [`sima-cli models download`](./sima-cli-models-download.md): Download a model variant from the branch catalog.
- [`sima-cli models list`](./sima-cli-models-list.md): Browse registered models, or list them with --json.

## Full Help

```text
Usage: sima-cli models [OPTIONS] COMMAND [ARGS]...

  Browse and download Model Registry artifacts from production by default.

  Uses https://models-registry.neat.sima.ai without AWS credentials. This
  preview command remains hidden from top-level help.

  SIMA_MODELS_REGISTRY_BASE_URL overrides all CLI endpoint selections,
  including --stg. Otherwise --stg/--staging selects https://models-
  registry.stg.neat.sima.ai; SIMA_MODELS_REGISTRY_PRODUCTION_URL overrides
  only the production default. The Python resolve_base_url helper's explicit
  override takes precedence over environment variables.

  Branch listings describe registered runs; listed branches may lack a
  published catalog. Unpublished catalogs report an error; published catalogs
  with no matching builds return an empty models array with --json or a
  message interactively. No fallback to another branch or environment is
  attempted.

  Examples (choose a currently published branch and model):

    sima-cli models branches --json
    sima-cli models list --branch BRANCH --json
    sima-cli models download --branch BRANCH --id MODEL --variant VARIANT --output ./models --json
    sima-cli models --stg branches --json
    sima-cli models list --staging --branch develop --json

Options:
  --stg, --staging  Use the staging Model Registry unless
                    SIMA_MODELS_REGISTRY_BASE_URL is set.
  --help            Show this message and exit.

Commands:
  branches  List branches containing registered models.
  download  Download a model variant from the branch catalog.
  list      Browse registered models, or list them with --json.
```
