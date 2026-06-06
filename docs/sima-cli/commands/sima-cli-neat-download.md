# `sima-cli neat download`

Download artifacts for REPO and branch or tag REF.

Parent command: [`sima-cli neat`](./sima-cli-neat.md)

## Usage

```bash
sima-cli neat download [OPTIONS] [REPO] [REF]
```

## Options

| Name | Description |
| --- | --- |
| `--dev` | Use dev artifacts. |
| `--stg, --staging` | Use staging artifacts. |
| `--prd, --prod` | Use production artifacts. |
| `--env` | Artifact environment. Overrides the parent --env. |
| `--base-url` | Override the artifact base URL. Overrides the parent --base-url. |
| `-o, --output` | Output directory. (default: neat-downloads) |
| `--artifact` | Artifact path glob to download from manifest.json. May be repeated. |
| `--json` | Print a machine-readable JSON summary. |

## Arguments

| Name | Description |
| --- | --- |
| `REPO` |  |
| `REF` |  |

## Full Help

```text
Usage: sima-cli neat download [OPTIONS] [REPO] [REF]

  Download artifacts for REPO and branch or tag REF.

Options:
  --dev                           Use dev artifacts.
  --stg, --staging                Use staging artifacts.
  --prd, --prod                   Use production artifacts.
  --env [dev|stg|staging|prd|prod|production]
                                  Artifact environment. Overrides the parent
                                  --env.
  --base-url TEXT                 Override the artifact base URL. Overrides
                                  the parent --base-url.
  -o, --output DIRECTORY          Output directory.  [default: neat-downloads]
  --artifact TEXT                 Artifact path glob to download from
                                  manifest.json. May be repeated.
  --json                          Print a machine-readable JSON summary.
  --help                          Show this message and exit.
```
