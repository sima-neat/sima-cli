# `sima-cli sdk doctor network`

Diagnose Ubuntu/Linux host networking for Neat SDK Insight ports.

Parent command: [`sima-cli sdk doctor`](./sima-cli-sdk-doctor.md)

## Usage

```bash
sima-cli sdk doctor network [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--devkit` | DevKit IP to use for route and reachability diagnostics. |
| `--container` | Neat SDK container name. Required when multiple Neat SDK containers exist. |
| `--collect` | Create a read-only support bundle with sanitized network, Docker, and Insight diagnostics. |
| `--output` | Output .tar.gz file or directory for --collect. Defaults to ./sima-sdk-network-doctor-<timestamp>.tar.gz. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sdk doctor network [OPTIONS]

  Diagnose Ubuntu/Linux host networking for Neat SDK Insight ports.

Options:
  --devkit TEXT     DevKit IP to use for route and reachability diagnostics.
  --container TEXT  Neat SDK container name. Required when multiple Neat SDK
                    containers exist.
  --collect         Create a read-only support bundle with sanitized network,
                    Docker, and Insight diagnostics.
  --output PATH     Output .tar.gz file or directory for --collect. Defaults
                    to ./sima-sdk-network-doctor-<timestamp>.tar.gz.
  --help            Show this message and exit.
```
