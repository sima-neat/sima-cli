# `sima-cli mcp status`

Show MCP availability and per-agent registration.

Parent command: [`sima-cli mcp`](./sima-cli-mcp.md)

## Usage

```bash
sima-cli mcp status [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--scope` | Scope to check for scope-aware agents (others are user-global). (default: project) |

## Arguments

None.

## Full Help

```text
Usage: sima-cli mcp status [OPTIONS]

  Show MCP availability and per-agent registration.

Options:
  --scope [project|user]  Scope to check for scope-aware agents (others are
                          user-global).  [default: project]
  --help                  Show this message and exit.
```
