# `sima-cli sdk network repair`

Apply scoped Ubuntu/Linux host network repair for Neat SDK Insight paths.

Parent command: [`sima-cli sdk network`](./sima-cli-sdk-network.md)

## Usage

```bash
sima-cli sdk network repair [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--devkit` | DevKit IPv4 address to use for route and shared-network repair. Required. |
| `--container` | Neat SDK container name. Required when multiple Neat SDK containers exist. |
| `--persist` | Install/update a persistent NetworkManager dispatcher hook after applying runtime repair. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sdk network repair [OPTIONS]

  Apply scoped Ubuntu/Linux host network repair for Neat SDK Insight paths.

Options:
  --devkit TEXT     DevKit IPv4 address to use for route and shared-network
                    repair. Required.
  --container TEXT  Neat SDK container name. Required when multiple Neat SDK
                    containers exist.
  --persist         Install/update a persistent NetworkManager dispatcher hook
                    after applying runtime repair.
  --help            Show this message and exit.
```
