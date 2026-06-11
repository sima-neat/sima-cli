# `sima-cli neat`

Discover, download, and install Neat build artifacts.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli neat [OPTIONS] COMMAND [ARGS]...
```

## Options

| Name | Description |
| --- | --- |
| `--dev` | Use dev artifacts. |
| `--stg, --staging` | Use staging artifacts. |
| `--prd, --prod` | Use production artifacts. |
| `--env` | Artifact environment. Defaults to production. |
| `--base-url` | Override the artifact base URL. |

## Arguments

None.

## Subcommands

- [`sima-cli neat download`](./sima-cli-neat-download.md): Download artifacts for REPO and branch or tag REF.
- [`sima-cli neat install`](./sima-cli-neat-install.md): Install a Neat artifact package from TARGET.

## Full Help

```text
Usage: sima-cli neat [OPTIONS] COMMAND [ARGS]...

  Discover, download, and install Neat build artifacts.

Options:
  --dev                           Use dev artifacts.
  --stg, --staging                Use staging artifacts.
  --prd, --prod                   Use production artifacts.
  --env [dev|stg|staging|prd|prod|production]
                                  Artifact environment. Defaults to
                                  production.
  --base-url TEXT                 Override the artifact base URL.
  --help                          Show this message and exit.

Commands:
  download  Download artifacts for REPO and branch or tag REF.
  install   Install a Neat artifact package from TARGET.
```
