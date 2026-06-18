# `sima-cli sdk network rollback`

Best-effort rollback for Linux SDK network setup/repair changes.

Parent command: [`sima-cli sdk network`](./sima-cli-sdk-network.md)

Rollback removes only SDK-specific Linux host network rules that `sima-cli` can
match exactly. It does not reset NetworkManager sharing, delete Docker networks,
flush firewall tables, restore previous IPv6 profile values, or change
`net.ipv4.ip_forward`.

By default, rollback runs as a dry run. Pass `--apply` to remove the listed
rules.

When a persistent SDK network repair profile is installed, `--apply` explains
what the profile does and asks whether to remove it. Pass
`--remove-persistent-profile` to remove it without prompting, for example in
automation.

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
