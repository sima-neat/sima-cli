# `sima-cli modelzoo`

Access models from the Model Zoo.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli modelzoo [OPTIONS] COMMAND [ARGS]...
```

## Options

| Name | Description |
| --- | --- |
| `-v, --ver, --version` | SDK version (e.g. 1.7.0, 2.0.0). If not provided, the current GA version will be used. |
| `--boardtype` | Target board type (mlsoc or modalix). |

## Arguments

None.

## Subcommands

- [`sima-cli modelzoo describe`](./sima-cli-modelzoo-describe.md): Download a specific model.
- [`sima-cli modelzoo get`](./sima-cli-modelzoo-get.md): Download a specific model.
- [`sima-cli modelzoo list`](./sima-cli-modelzoo-list.md): List available models.

## Full Help

```text
Usage: sima-cli modelzoo [OPTIONS] COMMAND [ARGS]...

  Access models from the Model Zoo.

Options:
  -v, --ver, --version TEXT    SDK version (e.g. 1.7.0, 2.0.0). If not
                               provided, the current GA version will be used.
  --boardtype [mlsoc|modalix]  Target board type (mlsoc or modalix).
  --help                       Show this message and exit.

Commands:
  describe  Download a specific model.
  get       Download a specific model.
  list      List available models.
```
