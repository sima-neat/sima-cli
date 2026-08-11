# `sima-cli sdk ros2`

Launch the ROS 2 SDK workspace environment.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk ros2 [OPTIONS] [CMD]...
```

## Options

None.

## Arguments

| Name | Description |
| --- | --- |
| `CMD` | Optional passthrough command and arguments. All remaining tokens are joined and executed inside the selected SDK container with `bash -lc`; if omitted, sima-cli opens an interactive login shell. (accepts zero or more values) |

## Full Help

```text
Usage: sima-cli sdk ros2 [OPTIONS] [CMD]...

  Launch the ROS 2 SDK workspace environment.

  Use ``sima-cli sdk -v VERSION ros2`` to select among multiple installed
  versions. CMD is executed in the container; without CMD an interactive login
  shell is opened as the mapped host user.

  Examples:
      sima-cli sdk ros2
      sima-cli sdk -v latest ros2 ros2 --help

Options:
  --help  Show this message and exit.
```
