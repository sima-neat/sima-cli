# `sima-cli sdk doctor network`

Diagnose Ubuntu/Linux host networking for Palette SDK Insight ports.

Parent command: [`sima-cli sdk doctor`](./sima-cli-sdk-doctor.md)

## Usage

```bash
sima-cli sdk doctor network [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--devkit` | DevKit IP to use for route and reachability diagnostics. |
| `--container` | Palette SDK container name. Required when multiple Palette SDK containers exist. |
| `--collect` | Create a read-only support bundle with sanitized network, Docker, and Insight diagnostics. |
| `--output` | Output .tar.gz file or directory for --collect. Defaults to ./sima-sdk-network-doctor-<timestamp>.tar.gz. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sdk doctor network [OPTIONS]

  Diagnose Ubuntu/Linux host networking for Palette SDK Insight ports.

Options:
  --devkit TEXT     DevKit IP to use for route and reachability diagnostics.
  --container TEXT  Palette SDK container name. Required when multiple Palette
                    SDK containers exist.
  --collect         Create a read-only support bundle with sanitized network,
                    Docker, and Insight diagnostics.
  --output PATH     Output .tar.gz file or directory for --collect. Defaults
                    to ./sima-sdk-network-doctor-<timestamp>.tar.gz.
  --help            Show this message and exit.
```
