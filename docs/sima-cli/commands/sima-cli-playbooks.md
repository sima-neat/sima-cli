# `sima-cli playbooks`

Install and manage playbooks (Codex/Claude).

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli playbooks [OPTIONS] COMMAND [ARGS]...
```

## Options

None.

## Arguments

None.

## Subcommands

- [`sima-cli playbooks apply`](./sima-cli-playbooks-apply.md): Apply an installed rule playbook to the current git repository.
- [`sima-cli playbooks delete`](./sima-cli-playbooks-delete.md): Delete one installed playbook by id, or all with --all.
- [`sima-cli playbooks describe`](./sima-cli-playbooks-describe.md): Show an installed playbook's manifest and document content.
- [`sima-cli playbooks install`](./sima-cli-playbooks-install.md): Install one or more playbooks from SOURCE.
- [`sima-cli playbooks list`](./sima-cli-playbooks-list.md): List installed playbooks.
- [`sima-cli playbooks remove`](./sima-cli-playbooks-remove.md): Alias for delete.
- [`sima-cli playbooks update`](./sima-cli-playbooks-update.md): Update one installed playbook, or all if KIT_ID is omitted.

## Full Help

```text
Usage: sima-cli playbooks [OPTIONS] COMMAND [ARGS]...

  Install and manage playbooks (Codex/Claude).

Options:
  --help  Show this message and exit.

Commands:
  apply     Apply an installed rule playbook to the current git repository.
  delete    Delete one installed playbook by id, or all with --all.
  describe  Show an installed playbook's manifest and document content.
  install   Install one or more playbooks from SOURCE.
  list      List installed playbooks.
  remove    Alias for delete.
  update    Update one installed playbook, or all if KIT_ID is omitted.
```
