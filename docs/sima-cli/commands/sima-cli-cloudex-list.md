# `sima-cli cloudex list`

List locally recorded CloudEx connections.

Parent command: [`sima-cli cloudex`](./sima-cli-cloudex.md)

## Usage

```bash
sima-cli cloudex list [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--benchmark` | Stress-test RTT and maximum bidirectional throughput for each connection. |
| `--duration` | Throughput measurement window in seconds. (default: 10) |

## Arguments

None.

## Full Help

```text
Usage: sima-cli cloudex list [OPTIONS]

  List locally recorded CloudEx connections.

Options:
  --benchmark               Stress-test RTT and maximum bidirectional
                            throughput for each connection.
  --duration INTEGER RANGE  Throughput measurement window in seconds.
                            [default: 10; 1<=x<=60]
  --help                    Show this message and exit.
```
