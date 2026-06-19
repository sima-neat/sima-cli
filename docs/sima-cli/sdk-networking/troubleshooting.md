# Troubleshoot SDK Networking

Use the network doctor when the SDK container, Insight UI, DevKit SSH, RTSP, WebRTC video, or workspace sync does not behave as expected.

The doctor command is read-only. It inspects the host route, Docker container state, published ports, Insight port map, firewall state, and selected Linux networking details.

## Run a quick check

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

If more than one SDK container exists, pass the container name:

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --container <container-name>
```

## Collect a support bundle

When you need help from SiMa support, collect a bundle:

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect
```

To choose the output location:

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect --output ~/sima-sdk-network-doctor.tar.gz
```

The bundle includes sanitized host, Docker, route, firewall, NetworkManager, and Insight port-map diagnostics. It is intended for networking support. Do not manually add SSH keys, Docker credential files, browser cookies, or other secrets to the archive.

## Common findings

| Finding | Meaning | Recommended action |
| --- | --- | --- |
| `vpn-route` | The route to the DevKit goes through a VPN or tunnel interface. | Disconnect the VPN or adjust routing so the DevKit uses the physical DevKit-facing interface. |
| `missing-simasdkbridge` | The SDK container is running but is not attached to `simasdkbridge`. | Recreate or restart the SDK with `sima-cli sdk setup`. Avoid direct VS Code Dev Containers startup for DevKit workflows. |
| `host-network-mode` | The SDK container was started with Docker host networking. | Recreate the SDK with `sima-cli sdk setup`; host networking is not the supported Insight port model. |
| `port-map-mismatch` | Docker published ports do not match the generated Insight port map. | Recreate the SDK container so `sima-cli` can regenerate ports and the Insight configuration together. |
| `stale-port-bindings` | A stopped SDK container has saved Docker port bindings that are no longer available. | Remove/recreate the container through `sima-cli sdk setup`. |
| `nm-shared-iptables-blocking` | NetworkManager shared networking rejects SDK bridge traffic before Docker rules are reached. | Run `sima-cli sdk network repair --devkit <devkit-ip>`; add `--persist` if the fix must survive reconnects/reboots. |
| `container-devkit-reachability` | The SDK container could not confirm SSH or ping reachability to the DevKit. | Confirm the DevKit IP, cable/network path, DevKit SSH service, and host firewall. |

## DevKit dependency downloads fail

When the DevKit uses the recommended shared-network link, it depends on the host for Internet access. If package installation, dependency downloads, or external service access fail on the DevKit, check:

- The host computer can reach the Internet.
- The host shared-network interface is still active.
- VPN routing is not capturing the DevKit route.
- Linux forwarding/NAT rules are still present if NetworkManager recreated the shared connection.

Run:

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

If the doctor reports a NetworkManager shared-network blocking issue, run the repair command shown in the doctor output.

## Repair Linux shared-network routing

On Ubuntu/Linux hosts using NetworkManager shared networking, run:

```bash
sima-cli sdk network repair --devkit <devkit-ip>
```

To install the persistent dispatcher hook after applying runtime repair:

```bash
sima-cli sdk network repair --devkit <devkit-ip> --persist
```

The repair is scoped to the SDK bridge and the detected DevKit-facing shared-network path. It does not switch Docker to host networking and does not set the global `FORWARD` policy to `ACCEPT`.

## Verify after repair

Run the doctor again:

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

Then open the Insight URL reported by:

```bash
neat --json
```

If the browser cannot open Insight, verify that the `mainUI` host port is reachable from the host browser and that the SDK container was started by `sima-cli sdk setup`.
