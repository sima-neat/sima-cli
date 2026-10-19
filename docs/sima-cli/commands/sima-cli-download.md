# `sima-cli download`

Download a file or a whole folder from a given URL.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli download [OPTIONS] URL
```

## Options

| Name | Description |
| --- | --- |
| `-d, --dest` | Target download directory |

## Arguments

| Name | Description |
| --- | --- |
| `URL` | (required) |

## Large files and interrupted transfers

The shared HTTP downloader writes sequentially in 1 MiB chunks. The chunk size
bounds the transfer buffer; it is independent of the storage device's block size.
Downloads use a sibling `.part` file and `.part.json` resume receipt. A successful
transfer is flushed and atomically renamed to the requested filename on the same
filesystem. An existing destination is preserved if the replacement fails.

Interrupted partials resume only when the server supplies a matching strong ETag.
The client validates the returned byte range and restarts when the server ignores
the range request. Without a matching validator, the partial is downloaded again.
Legacy incomplete files under the final filename are also downloaded again;
existing files matching the reported size retain the previous skip behavior.
Size checking is not a cryptographic integrity check.

Vulcan manifest artifact downloads also stream in 1 MiB chunks, verify supplied
size and SHA-256 metadata during the write, and publish only after validation.
Failed Vulcan partials are removed and retried from the beginning. The separate
GitHub-release downloader is unchanged.

Allow enough space on the destination filesystem for the download and any later
extraction. Replacing an existing file temporarily requires space for both copies.
On systems with RAM-backed temporary directories, a temporary destination consumes
RAM rather than eMMC space. These changes reduce buffering overhead and memory use;
they do not change TLS cipher selection or enable parallel transfers.

## Full Help

```text
Usage: sima-cli download [OPTIONS] URL

  Download a file or a whole folder from a given URL.

Options:
  -d, --dest PATH  Target download directory
  --help           Show this message and exit.
```
