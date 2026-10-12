# `sima-cli peripherals`

Discover the peripherals attached to this DevKit, read from SiMa Sentinel.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli peripherals [OPTIONS] COMMAND [ARGS]...
```

## Options

None.

## Arguments

None.

## Subcommands

- [`sima-cli peripherals describe`](./sima-cli-peripherals-describe.md): Show one peripheral: identity, device nodes, every mode and Sentinel's verdict on each.
- [`sima-cli peripherals list`](./sima-cli-peripherals-list.md): List connected peripherals with their type, connection, status and support.

## Full Help

```text
Usage: sima-cli peripherals [OPTIONS] COMMAND [ARGS]...

  Discover the peripherals attached to this DevKit, read from SiMa Sentinel.

  Cameras and microphones are listed with a stable ID and, for cameras,
  Sentinel's verdict on whether any of their modes can be used by Neat's
  CameraInput on this board. Discovery is read-only and safe to run beside a
  running application.

Options:
  --help  Show this message and exit.

Commands:
  describe  Show one peripheral: identity, device nodes, every mode and...
  list      List connected peripherals with their type, connection,...
```
