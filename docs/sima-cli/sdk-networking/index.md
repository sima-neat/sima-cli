# Neat SDK Networking Setup

The Neat SDK, also known as the Neat Development Environment, runs inside a Docker container. `sima-cli sdk setup` creates the container, prepares the host workspace mount, starts Insight when enabled, and publishes the container ports that the host browser and a DevKit use during development.

This guide explains the networking pieces that matter most when using the SDK with a Modalix DevKit.

## Network model

A typical SDK setup has three participants:

- Host computer: runs Docker, `sima-cli`, the SDK container, and your browser.
- SDK container: runs the Neat Development Environment and optional Insight services.
- DevKit: connects to the host over the local network or a direct Ethernet/shared-network link.

The supported SDK container network is `simasdkbridge`. Do not start the SDK container directly from Docker or the VS Code Dev Containers extension when you need DevKit and Insight networking. Use `sima-cli sdk setup` so the container, port mappings, workspace sharing, and Insight configuration are created together.

## What setup configures

When you run setup with DevKit integration, `sima-cli` configures:

- Docker network: creates or reuses `simasdkbridge`.
- SDK container ports: publishes Insight UI, video, RTSP, metadata, WebRTC, and web SSH ports to the host.
- Insight port map: writes the generated port mapping to the SDK workspace configuration.
- Workspace sharing: configures host-to-DevKit workspace access when `--devkit` is used.
- DevKit Internet access: routes the DevKit through the host shared-network link so the DevKit can reach package repositories and download dependencies when required.
- Linux shared-network routing: on Ubuntu/Linux shared-network links, applies scoped forwarding/NAT rules when they are needed.

Use:

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

## Persistent Linux shared-network repair

On some Ubuntu hosts, NetworkManager shared networking can recreate firewall chains when the cable reconnects or the host reboots. In that case, setup may repair the current session but warn that the repair is not persistent.

For an interactive setup, `sima-cli` asks before installing a persistent NetworkManager dispatcher profile.

For automation, opt in explicitly:

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```

The persistent profile only applies to the detected shared-network path. It is not installed by `-y` alone.

## DevKit Internet access

When the DevKit is connected through the recommended Linux/macOS shared-network link, it relies on the host computer for Internet access. This matters when the DevKit needs to download packages, install dependencies, or reach external services during setup and development.

If DevKit commands fail while trying to download dependencies, verify the host has working Internet access and that the shared-network path is still active. On Linux, the network doctor can identify shared-network forwarding issues:

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

## Insight and published ports

Insight uses generated host ports. Defaults are used when available, but `sima-cli` may allocate non-default ports if a port is already in use.

To inspect the active mapping from inside the SDK shell:

```bash
neat --json
```

Look for `exposedPorts` and the `insight.webUiUrl` entry. Use those values when configuring DevKit streams, RTSP sources, browser access, or application output sinks.

## Related pages

- [Troubleshoot SDK networking](troubleshooting.md)
- [Rollback SDK network changes](rollback.md)
- [`sima-cli sdk setup`](../commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk doctor network`](../commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](../commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](../commands/sima-cli-sdk-network-rollback.md)
