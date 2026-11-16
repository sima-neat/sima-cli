# `sima-cli sdk network repair`

Apply scoped Ubuntu/Linux host network repair for Palette SDK Insight paths.

Parent command: [`sima-cli sdk network`](./sima-cli-sdk-network.md)

## Usage

```bash
sima-cli sdk network repair [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--devkit` | DevKit IPv4 address to use for route and shared-network repair. Required. (required) |
| `--container` | Palette SDK container name. Required when multiple Palette SDK containers exist. |
| `--persist` | Install/update a persistent NetworkManager dispatcher hook after applying runtime repair. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sdk network repair [OPTIONS]

  Apply scoped Ubuntu/Linux host network repair for Palette SDK Insight paths.

Options:
  --devkit TEXT     DevKit IPv4 address to use for route and shared-network
                    repair. Required.  [required]
  --container TEXT  Palette SDK container name. Required when multiple Palette
                    SDK containers exist.
  --persist         Install/update a persistent NetworkManager dispatcher hook
                    after applying runtime repair.
  --help            Show this message and exit.
```
