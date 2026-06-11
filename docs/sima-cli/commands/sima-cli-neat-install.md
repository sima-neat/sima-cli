# `sima-cli neat install`

Install a Neat artifact package from TARGET.

Parent command: [`sima-cli neat`](./sima-cli-neat.md)

## Usage

```bash
sima-cli neat install [OPTIONS] TARGET
```

## Options

| Name | Description |
| --- | --- |
| `--dev` | Use dev artifacts. |
| `--stg, --staging` | Use staging artifacts. |
| `--prd, --prod` | Use production artifacts. |
| `--env` | Artifact environment. Overrides the parent --env. |
| `--base-url` | Override the artifact base URL. Overrides the parent --base-url. |
| `-d, --install-dir` | Directory where package resources are downloaded and installed. (default: .) |
| `-t, --type` | Install metadata variant metadata-<type>.json instead of metadata.json. |
| `-f, --force` | Force installation even if compatibility checks fail. |
| `--json` | Print resolved metadata URL and exit. |

## Arguments

| Name | Description |
| --- | --- |
| `TARGET` | (required) |

## Full Help

```text
Usage: sima-cli neat install [OPTIONS] TARGET

  Install a Neat artifact package from TARGET.

  TARGET supports REPO, REPO@branch, REPO@branch:spec, REPO@latest, or
  REPO@githash. If no branch or spec is provided, latest main is used.

Options:
  --dev                           Use dev artifacts.
  --stg, --staging                Use staging artifacts.
  --prd, --prod                   Use production artifacts.
  --env [dev|stg|staging|prd|prod|production]
                                  Artifact environment. Overrides the parent
                                  --env.
  --base-url TEXT                 Override the artifact base URL. Overrides
                                  the parent --base-url.
  -d, --install-dir DIRECTORY     Directory where package resources are
                                  downloaded and installed.  [default: .]
  -t, --type TEXT                 Install metadata variant
                                  metadata-<type>.json instead of
                                  metadata.json.
  -f, --force                     Force installation even if compatibility
                                  checks fail.
  --json                          Print resolved metadata URL and exit.
  --help                          Show this message and exit.
```
