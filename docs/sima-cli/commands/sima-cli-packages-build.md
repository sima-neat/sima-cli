# `sima-cli packages build`

Build package metadata.json from an artifacts folder.

Parent command: [`sima-cli packages`](./sima-cli-packages.md)

## Usage

```bash
sima-cli packages build [OPTIONS] ARTIFACTS_FOLDER
```

## Options

| Name | Description |
| --- | --- |
| `--name` | Package name. Defaults to gh:<org>/<repo> for GitHub repos. |
| `--version` | Package version. Defaults to exact git tag or short commit hash. |
| `--description` | Package description. Defaults to the GitHub repo description when available. |
| `--install-script` | Install script file inside ARTIFACTS_FOLDER, or a single-line shell command. (required) |
| `--selectables` | Optional resources in 'name1:file1;name2:file2' format. |
| `--exclude` | Exclude artifact files whose relative path or filename contains this text. May be repeated. |
| `--variant` | Optional metadata variant name. Writes metadata-<variant>.json instead of metadata.json. |
| `--download-compatible-files-only` | Add download-compatible-files-only so installers download only wheel files compatible with the current platform. |
| `--host-platform` | Host OS compatibility as a comma-separated list. Supported values: linux, ubuntu, mac, windows. May be repeated. |
| `--board-platform` | Board compatibility as COMPAT[,COMPAT...][@VERSION_SPEC], for example modalix, modalix@==2.1.1, or modalix@>=2.1.0,<=2.1.2. May be repeated. |
| `--palette-platform` | Mark the package as compatible with Palette SDK containers. Optionally pass an exact SDK version, for example --palette-platform 2.0.0. |

## Arguments

| Name | Description |
| --- | --- |
| `ARTIFACTS_FOLDER` | (required) |

## Full Help

```text
Usage: sima-cli packages build [OPTIONS] ARTIFACTS_FOLDER

  Build package metadata.json from an artifacts folder.

Options:
  --name TEXT                     Package name. Defaults to gh:<org>/<repo>
                                  for GitHub repos.
  --version TEXT                  Package version. Defaults to exact git tag
                                  or short commit hash.
  --description TEXT              Package description. Defaults to the GitHub
                                  repo description when available.
  --install-script TEXT           Install script file inside ARTIFACTS_FOLDER,
                                  or a single-line shell command.  [required]
  --selectables TEXT              Optional resources in
                                  'name1:file1;name2:file2' format.
  --exclude TEXT                  Exclude artifact files whose relative path
                                  or filename contains this text. May be
                                  repeated.
  --variant TEXT                  Optional metadata variant name. Writes
                                  metadata-<variant>.json instead of
                                  metadata.json.
  --download-compatible-files-only
                                  Add download-compatible-files-only so
                                  installers download only wheel files
                                  compatible with the current platform.
  --host-platform TEXT            Host OS compatibility as a comma-separated
                                  list. Supported values: linux, ubuntu, mac,
                                  windows. May be repeated.
  --board-platform TEXT           Board compatibility as
                                  COMPAT[,COMPAT...][@VERSION_SPEC], for
                                  example modalix, modalix@==2.1.1, or
                                  modalix@>=2.1.0,<=2.1.2. May be repeated.
  --palette-platform              Mark the package as compatible with Palette
                                  SDK containers. Optionally pass an exact SDK
                                  version, for example --palette-platform
                                  2.0.0.
  --help                          Show this message and exit.
```
