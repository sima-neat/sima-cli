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
| `-f, --force` | Force installation even if compatibility checks fail. Sets FORCE_INSTALL=1 for the installation script. |
| `--json` | Print resolved metadata URL and exit. |
| `--download-only` | Download metadata resources without running installation. |
| `--python-executable` | For packages that include an optional wheel, install it into this Python environment. |

## Arguments

| Name | Description |
| --- | --- |
| `TARGET` | (required) |

## Full Help

```text
Usage: sima-cli neat install [OPTIONS] TARGET

  Install a Neat artifact package from TARGET.

  TARGET supports REPO, REPO/folder, REPO@branch, REPO/folder@branch:spec,
  REPO@latest, or REPO@githash. If no branch or spec is provided, latest main
  is used.

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
                                  checks fail. Sets FORCE_INSTALL=1 for the
                                  installation script.
  --json                          Print resolved metadata URL and exit.
  --download-only                 Download metadata resources without running
                                  installation.
  --python-executable FILE        Install an optional wheel into this customer
                                  Python environment.
  --help                          Show this message and exit.
```

## Windows PCIe host package

The Windows PCIe host package uses Windows Installer for native files. Python
support is optional and is installed with pip only when an application Python
environment is selected explicitly:

```powershell
sima-cli neat install core/pciehost/windows/amd64@3.0 `
  --python-executable .\.venv\Scripts\python.exe
```

Omit `--python-executable` for a native-only installation. Repeating the same
command repairs or upgrades the native package and verifies a matching
`pyneatpcie` installation without reinstalling it. If the MSI succeeds but pip
fails, the native installation remains installed and the command can be retried
with the same Python environment.

Before invoking Windows Installer, the CLI checks manifest-declared OpenSSH,
Platform service, GStreamer, and selected-Python prerequisites. After the native
packages install, it runs the declared host verification command and installs
the package-matched customer skills. Provision card SSH once after installation:

```powershell
& "C:\Program Files\SimaPCIeHost\bin\pcie-setup.cmd"
```

Windows removes the native package through Installed apps or `msiexec`. Remove
the Python package separately from the selected environment with
`python -m pip uninstall pyneatpcie`.
