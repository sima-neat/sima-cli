# `sima-cli sdk`

Manage and launch SiMa SDK 2.0 container environments (Beta).

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli sdk [OPTIONS] COMMAND [ARGS]...
```

## Options

| Name | Description |
| --- | --- |
| `-v, --version` | Filter SDK containers by version tag (e.g. latest_master). |

## Arguments

None.

## Subcommands

- [`sima-cli sdk elxr`](./sima-cli-sdk-elxr.md): Launch the eLxr SDK tool environment.
- [`sima-cli sdk ls`](./sima-cli-sdk-ls.md): List installed and running SiMa SDK containers.
- [`sima-cli sdk model`](./sima-cli-sdk-model.md): Launch the Model SDK tool environment.
- [`sima-cli sdk mpk`](./sima-cli-sdk-mpk.md): Access MPK CLI toolset container for managing and building pipelines along with the device manager.
- [`sima-cli sdk neat`](./sima-cli-sdk-neat.md): Launch the Neat SDK tool environment.
- [`sima-cli sdk remove`](./sima-cli-sdk-remove.md): Remove SDK containers and images.
- [`sima-cli sdk run`](./sima-cli-sdk-run.md): Run a .sima hybrid script with local + container commands.
- [`sima-cli sdk setup`](./sima-cli-sdk-setup.md): Initialize SDK environment and select components to start.
- [`sima-cli sdk start`](./sima-cli-sdk-start.md): Select and start one or more SDK containers.
- [`sima-cli sdk stop`](./sima-cli-sdk-stop.md): Stop one or more running SDK containers.
- [`sima-cli sdk yocto`](./sima-cli-sdk-yocto.md): Launch the Yocto SDK tool environment.

## Full Help

```text
Usage: sima-cli sdk [OPTIONS] COMMAND [ARGS]...

  Manage and launch SiMa SDK 2.0 container environments (Beta).

  This group provides access to the full SDK 2.0 toolchain, including setup,
  container orchestration, tool-specific shells (MPK, model, Yocto, Neat,
  eLxr), and hybrid `.sima` script execution. These commands are intended for
  SDK 2.0+ users only.

  \c Host platforms only.

  Typical Use Cases

      • Setting up a full SDK toolchain

      • Starting one or more SDK containers

      • Stopping or removing SDK containers and cached images

      • Launching MPK, model, Yocto, Neat, or eLxr shells

Options:
  -v, --version TEXT  Filter SDK containers by version tag (e.g.
                      latest_master).
  --help              Show this message and exit.

Commands:
  elxr    Launch the eLxr SDK tool environment.
  ls      List installed and running SiMa SDK containers.
  model   Launch the Model SDK tool environment.
  mpk     Access MPK CLI toolset container for managing and building...
  neat    Launch the Neat SDK tool environment.
  remove  Remove SDK containers and images.
  run     Run a .sima hybrid script with local + container commands.
  setup   Initialize SDK environment and select components to start.
  start   Select and start one or more SDK containers.
  stop    Stop one or more running SDK containers.
  yocto   Launch the Yocto SDK tool environment.
```
