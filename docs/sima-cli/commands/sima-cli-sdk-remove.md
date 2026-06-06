# `sima-cli sdk remove`

Remove SDK containers and images.

Parent command: [`sima-cli sdk`](./sima-cli-sdk.md)

## Usage

```bash
sima-cli sdk remove [OPTIONS] [SDK]
```

## Options

| Name | Description |
| --- | --- |
| `-y, --yes` | Skip confirmation before removing SDK containers/images. |

## Arguments

| Name | Description |
| --- | --- |
| `SDK` |  |

## Full Help

```text
Usage: sima-cli sdk remove [OPTIONS] [SDK]

  Remove SDK containers and images. Example:     sima-cli sdk remove yocto
  sima-cli sdk -v latest_develop remove mpk -y

Options:
  -y, --yes  Skip confirmation before removing SDK containers/images.
  --help     Show this message and exit.
```
