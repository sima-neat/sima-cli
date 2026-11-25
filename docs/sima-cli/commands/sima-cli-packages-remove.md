# `sima-cli packages remove`

Remove one local registry receipt. A Windows MSI package is uninstalled first; other package files are not touched.

Parent command: [`sima-cli packages`](./sima-cli-packages.md)

## Usage

```bash
sima-cli packages remove [OPTIONS] PACKAGE
```

## Options

| Name | Description |
| --- | --- |
| `--version, -v` | Exact installed version whose registry receipt should be removed. (required) |
| `--yes, -y` | Skip the confirmation prompt. |

## Arguments

| Name | Description |
| --- | --- |
| `PACKAGE` | (required) |

## Full Help

```text
Usage: sima-cli packages remove [OPTIONS] PACKAGE

  Remove one local registry receipt. A Windows MSI package is uninstalled
  first; other package files are not touched.

Options:
  -v, --version TEXT  Exact installed version whose registry receipt should be
                      removed.  [required]
  -y, --yes           Skip the confirmation prompt.
  --help              Show this message and exit.
```
