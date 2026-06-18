# Roll Back SDK Network Changes

Use rollback when you want to inspect or undo Linux host networking changes made by SDK setup or network repair.

Rollback is best effort. It removes the scoped rules that `sima-cli` can identify for the detected DevKit/shared-network path. It does not reset unrelated host networking, VPN configuration, Docker installation state, or user-managed firewall rules.

## Preview rollback actions

Rollback runs in dry-run mode unless `--apply` is provided:

```bash
sima-cli sdk network rollback --devkit <devkit-ip>
```

Review the table before applying changes.

## Apply rollback

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

This removes matching runtime forwarding/NAT rules that `sima-cli` added for the SDK bridge and DevKit shared-network path.

## Remove the persistent profile

If a persistent NetworkManager dispatcher profile was installed, rollback prompts before removing it. The profile reapplies SDK bridge forwarding when NetworkManager recreates a shared connection.

To remove it interactively:

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

To remove it non-interactively:

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply --remove-persistent-profile
```

Removing the persistent profile is safe when you want to fully undo SDK network repair. If you continue using the same Ubuntu shared-network DevKit connection, you may need to rerun repair later.

## After rollback

Run the doctor to confirm the current state:

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

If you want to recreate the SDK network configuration from scratch, run:

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

For automation that needs persistent shared-network repair, use:

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```
