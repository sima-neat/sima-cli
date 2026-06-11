# `sima-cli selfupdate`

Update sima-cli manually from PyPI or a direct wheel URL.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli selfupdate [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `-v, --version` | Version to update to (cannot be combined with --manual-url). |
| `-m, --manual-url` | Manual wheel URL (cannot be combined with --version). |
| `--dev` | Self-update from the Vulcan dev environment. |
| `--stg, --staging` | Self-update from the Vulcan staging environment. |
| `--prd, --prod, --neat, --vulcan` | Self-update from the Vulcan production environment. |
| `--branch` | Vulcan sima-cli branch to install. If omitted, prompts with branches.json choices. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli selfupdate [OPTIONS]

  Update sima-cli manually from PyPI or a direct wheel URL.

  This command downloads and installs a new version of sima-cli. You may
  update to the latest PyPI release, update to a specific version, or install
  from a manually supplied wheel URL.

  Update modes:
    - No options: update to the latest PyPI release
    - --version: update to the specified PyPI version
    - --manual-url: install from a direct wheel link
    - --dev: install from Vulcan dev artifacts
    - --stg/--staging: install from Vulcan staging artifacts
    - --prd/--prod/--neat/--vulcan: install from Vulcan production artifacts

  Rules:
    - --version and --manual-url cannot be used together
    - Manual URLs must point to a valid .whl file
    - Internal builds may be installed using the global -i flag

  Examples:

    sima-cli selfupdate

    sima-cli selfupdate --dev

    sima-cli selfupdate --stg --branch main

    sima-cli selfupdate -v 0.0.45

    sima-cli selfupdate -m https://.../sima_cli-0.0.46.whl

Options:
  -v, --version TEXT              Version to update to (cannot be combined
                                  with --manual-url).
  -m, --manual-url TEXT           Manual wheel URL (cannot be combined with
                                  --version).
  --dev                           Self-update from the Vulcan dev environment.
  --stg, --staging                Self-update from the Vulcan staging
                                  environment.
  --prd, --prod, --neat, --vulcan
                                  Self-update from the Vulcan production
                                  environment.
  --branch TEXT                   Vulcan sima-cli branch to install. If
                                  omitted, prompts with branches.json choices.
  --help                          Show this message and exit.
```
