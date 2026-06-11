# `sima-cli playbooks install`

Install one or more playbooks from SOURCE.

Parent command: [`sima-cli playbooks`](./sima-cli-playbooks.md)

## Usage

```bash
sima-cli playbooks install [OPTIONS] SOURCE
```

## Options

| Name | Description |
| --- | --- |
| `--force` | Overwrite an already-installed playbook with the same id. |

## Arguments

| Name | Description |
| --- | --- |
| `SOURCE` | (required) |

## Full Help

```text
Usage: sima-cli playbooks install [OPTIONS] SOURCE

  Install one or more playbooks from SOURCE.

  SOURCE can be:   - Local folder/archive path   - http(s) archive URL   -
  gh:owner/repo[/path][@ref]   - bb:owner/repo[/path][@ref]   -
  art:https://...

Options:
  --force  Overwrite an already-installed playbook with the same id.
  --help   Show this message and exit.
```
