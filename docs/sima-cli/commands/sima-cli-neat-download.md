# `sima-cli neat download`

Download a Neat package's metadata resources without installing it.

Parent command: [`sima-cli neat`](./sima-cli-neat.md)

## Usage

```bash
sima-cli neat download [OPTIONS] TARGET
```

## Options

| Name | Description |
| --- | --- |
| `--dev` | Use dev artifacts. |
| `--stg, --staging` | Use staging artifacts. |
| `--prd, --prod` | Use production artifacts. |
| `--env` | Artifact environment. Overrides the parent --env. |
| `--base-url` | Override the artifact base URL. Overrides the parent --base-url. |
| `-d, --install-dir` | Directory where package resources are downloaded. (default: .) |
| `-t, --type` | Download metadata variant metadata-<type>.json instead of metadata.json. |
| `-f, --force` | Skip available-space checks while downloading. |
| `--json` | Print resolved metadata URL and exit. |

## Arguments

| Name | Description |
| --- | --- |
| `TARGET` | (required) |

## Full Help

```text
Usage: sima-cli neat download [OPTIONS] TARGET

  Download a Neat package's metadata resources without installing it.

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
                                  downloaded.  [default: .]
  -t, --type TEXT                 Download metadata variant
                                  metadata-<type>.json instead of
                                  metadata.json.
  -f, --force                     Skip available-space checks while
                                  downloading.
  --json                          Print resolved metadata URL and exit.
  --help                          Show this message and exit.
```
