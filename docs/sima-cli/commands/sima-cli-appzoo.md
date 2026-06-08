# `sima-cli appzoo`

Access sample apps from the App Zoo.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli appzoo [OPTIONS] COMMAND [ARGS]...
```

## Options

| Name | Description |
| --- | --- |
| `-v, --ver, --version` | SDK version (e.g. 1.7.0, 2.0.0). If not provided, you can select from available versions. |

## Arguments

None.

## Subcommands

- [`sima-cli appzoo clone`](./sima-cli-appzoo-clone.md): Clone the version specific appzoo.
- [`sima-cli appzoo describe`](./sima-cli-appzoo-describe.md): Download a specific model.
- [`sima-cli appzoo get`](./sima-cli-appzoo-get.md): Download a specific model.
- [`sima-cli appzoo list`](./sima-cli-appzoo-list.md): List available models.

## Full Help

```text
Usage: sima-cli appzoo [OPTIONS] COMMAND [ARGS]...

  Access sample apps from the App Zoo.

Options:
  -v, --ver, --version TEXT  SDK version (e.g. 1.7.0, 2.0.0). If not provided,
                             you can select from available versions.
  --help                     Show this message and exit.

Commands:
  clone     Clone the version specific appzoo.
  describe  Download a specific model.
  get       Download a specific model.
  list      List available models.
```
