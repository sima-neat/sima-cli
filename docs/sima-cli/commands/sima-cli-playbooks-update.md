# `sima-cli playbooks update`

Update one installed playbook, or all if KIT_ID is omitted.

Parent command: [`sima-cli playbooks`](./sima-cli-playbooks.md)

## Usage

```bash
sima-cli playbooks update [OPTIONS] [KIT_ID]
```

## Options

| Name | Description |
| --- | --- |
| `--skills` | Update only skill playbooks. |
| `--rules` | Update only rule playbooks. |

## Arguments

| Name | Description |
| --- | --- |
| `KIT_ID` |  |

## Full Help

```text
Usage: sima-cli playbooks update [OPTIONS] [KIT_ID]

  Update one installed playbook, or all if KIT_ID is omitted.

Options:
  --skills  Update only skill playbooks.
  --rules   Update only rule playbooks.
  --help    Show this message and exit.
```
