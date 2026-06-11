# `sima-cli packages show`

Show metadata or post-install instructions for a package.

Parent command: [`sima-cli packages`](./sima-cli-packages.md)

## Usage

```bash
sima-cli packages show [OPTIONS] PACKAGE
```

## Options

| Name | Description |
| --- | --- |
| `--version, -v` | Specify a version when multiple matches exist. If omitted, the latest match is shown. |

## Arguments

| Name | Description |
| --- | --- |
| `PACKAGE` | (required) |

## Full Help

```text
Usage: sima-cli packages show [OPTIONS] PACKAGE

  Show metadata or post-install instructions for a package.

Options:
  -v, --version TEXT  Specify a version when multiple matches exist. If
                      omitted, the latest match is shown.
  --help              Show this message and exit.

  PACKAGE supports partial and case-insensitive matching. If multiple matches
  are found, a summary table of all matching packages will be shown.
```
