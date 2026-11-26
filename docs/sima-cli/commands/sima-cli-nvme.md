# `sima-cli nvme`

Perform NVMe operations on Modalix SoM and DVT DevKits with NVMe storage.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli nvme [OPTIONS] {format|remount}
```

## Options

None.

## Arguments

| Name | Description |
| --- | --- |
| `OPERATION` | (required) |

## Full Help

```text
Usage: sima-cli nvme [OPTIONS] {format|remount}

  Perform NVMe operations on Modalix SoM and DVT DevKits with NVMe storage.

  Available operations:

    format   - Format the NVMe drive and mount it to /media/nvme

    remount  - Remount the existing NVMe partition to /media/nvme

  Example:   sima-cli nvme format

    sima-cli nvme remount

Options:
  --help  Show this message and exit.
```
