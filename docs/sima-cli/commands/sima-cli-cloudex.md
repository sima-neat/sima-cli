# `sima-cli cloudex`

Connect to allocated DevKits through CloudEx.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli cloudex [OPTIONS] COMMAND [ARGS]...
```

## Options

None.

## Arguments

None.

## Subcommands

- [`sima-cli cloudex connect`](./sima-cli-cloudex-connect.md): Connect to an allocated DevKit using a securely supplied key.
- [`sima-cli cloudex devices`](./sima-cli-cloudex-devices.md): Show NeatOps device availability and your allocation IDs.
- [`sima-cli cloudex disconnect`](./sima-cli-cloudex-disconnect.md): Disconnect one or all CloudEx sessions.
- [`sima-cli cloudex list`](./sima-cli-cloudex-list.md): List locally recorded CloudEx connections.
- [`sima-cli cloudex login`](./sima-cli-cloudex-login.md): Sign in to NeatOps in your browser on this computer.
- [`sima-cli cloudex logout`](./sima-cli-cloudex-logout.md): Revoke and remove this CLI's NeatOps credentials; keep device leases.
- [`sima-cli cloudex release`](./sima-cli-cloudex-release.md): Release your NeatOps lease and disconnect its local CloudEx sessions.
- [`sima-cli cloudex request`](./sima-cli-cloudex-request.md): Request a NeatOps device, wait for preparation, and connect automatically.
- [`sima-cli cloudex update`](./sima-cli-cloudex-update.md): Install or update the native CloudEx forwarder.

## Full Help

```text
Usage: sima-cli cloudex [OPTIONS] COMMAND [ARGS]...

  Connect to allocated DevKits through CloudEx.

Options:
  --help  Show this message and exit.

Commands:
  connect     Connect to an allocated DevKit using a securely supplied key.
  devices     Show NeatOps device availability and your allocation IDs.
  disconnect  Disconnect one or all CloudEx sessions.
  list        List locally recorded CloudEx connections.
  login       Sign in to NeatOps in your browser on this computer.
  logout      Revoke and remove this CLI's NeatOps credentials; keep...
  release     Release your NeatOps lease and disconnect its local CloudEx...
  request     Request a NeatOps device, wait for preparation, and connect...
  update      Install or update the native CloudEx forwarder.
```
