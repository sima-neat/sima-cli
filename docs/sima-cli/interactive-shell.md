# Interactive Shell

`sima-cli shell` starts an interactive REPL with a live, filterable command
menu. Type to narrow the menu, use ↑/↓ to pick a command, press Enter to run it,
and type `exit` (or press Ctrl-D) to leave. Anything you type is dispatched to
the same commands you would run as `sima-cli <command>` directly, so you do not
have to remember the exact command names.

While dispatching a command the shell suppresses the per-command update check and
the repeated environment banner, and it propagates `-i/--internal` for the whole
session.

## Which commands work inside the shell

Almost every `sima-cli` command runs normally inside the shell. A small number of
commands cannot, because they take over the terminal or re-exec the process; the
shell blocks those with a hint to run them directly instead.

### Supported inside `sima-cli shell`

| Command | Description |
| --- | --- |
| `appzoo` | Access sample apps from the App Zoo. |
| `bootimg` | Prepare a bootable image for the SiMa DevKit. |
| `device` | Discover and manage devices for MPK deployment. |
| `download` | Download a file or folder from a URL. |
| `install` | Install SiMa packages (including `install --neat` / `neat install`). |
| `login` / `logout` | Manage SiMa Developer Portal credentials. |
| `mla` | Machine Learning Accelerator utilities. |
| `modelzoo` | Access models from the Model Zoo. |
| `neat` | Discover, download, and install Neat build artifacts. |
| `nvme` | Perform NVMe operations on the Modalix DevKit. |
| `packages` | Manage the local sima-cli package registry. |
| `playbooks` | Install and manage coding-agent playbooks. |
| `sdcard` | Prepare SD card storage. |
| `sdk` | Manage and launch SDK container environments. |
| `update` | Update a SiMa DevKit or remote device. |

Shell-only helpers are also available: `exit` / `quit` to leave, and `:theme`
(`:theme dark` / `:theme light`) to switch the colour theme live.

### Unsupported inside `sima-cli shell` — run directly instead

These commands take over the terminal (TTY) or re-exec the process, neither of
which works from inside the REPL. The shell refuses them with a hint; leave the
shell (`exit` / Ctrl-D) and run them as a normal `sima-cli` command.

| Command | Why it cannot run in the shell | Run instead |
| --- | --- | --- |
| `serial` | Hijacks the TTY for the UART console. | `sima-cli serial` |
| `network` | Hijacks the TTY for interactive configuration. | `sima-cli network` |
| `selfupdate` | Re-execs the `sima-cli` process to update itself. | `sima-cli selfupdate` |
