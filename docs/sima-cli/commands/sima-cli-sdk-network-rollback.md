# `sima-cli sdk network rollback`

Best-effort rollback for Linux SDK network setup/repair changes.

Parent command: [`sima-cli sdk network`](./sima-cli-sdk-network.md)

## Usage

```bash
sima-cli sdk network rollback [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--devkit` | DevKit IP to use for route and shared-network rollback. |
| `--apply` | Apply rollback changes. Without this flag, rollback runs in dry-run mode. |
| `--remove-persistent-profile` | Remove the persistent NetworkManager dispatcher hook installed by SDK network repair. |

## Arguments

None.

## Full Help

```text
Usage: sima-cli sdk network rollback [OPTIONS]

  Best-effort rollback for Linux SDK network setup/repair changes.

Options:
  --devkit TEXT                DevKit IP to use for route and shared-network
                               rollback.
  --apply                      Apply rollback changes. Without this flag,
                               rollback runs in dry-run mode.
  --remove-persistent-profile  Remove the persistent NetworkManager dispatcher
                               hook installed by SDK network repair.
  --help                       Show this message and exit.
```
