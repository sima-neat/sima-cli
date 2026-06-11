# `sima-cli packages`

Manage sima-cli package registry (list, inspect, clean, etc.)

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli packages [OPTIONS] COMMAND [ARGS]...
```

## Options

None.

## Arguments

None.

## Subcommands

- [`sima-cli packages build`](./sima-cli-packages-build.md): Build package metadata.json from an artifacts folder.
- [`sima-cli packages list`](./sima-cli-packages-list.md): List all packages in the local registry.
- [`sima-cli packages show`](./sima-cli-packages-show.md): Show metadata or post-install instructions for a package.

## Full Help

```text
Usage: sima-cli packages [OPTIONS] COMMAND [ARGS]...

  Manage sima-cli package registry (list, inspect, clean, etc.)

Options:
  --help  Show this message and exit.

Commands:
  build  Build package metadata.json from an artifacts folder.
  list   List all packages in the local registry.
  show   Show metadata or post-install instructions for a package.
```
