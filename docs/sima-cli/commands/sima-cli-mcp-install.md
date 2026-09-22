# `sima-cli mcp install`

Register sima-cli as an MCP server in the coding agent's config.

Parent command: [`sima-cli mcp`](./sima-cli-mcp.md)

## Usage

```bash
sima-cli mcp install [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--agent` | Coding agent(s) to register sima-cli with. (default: claude) |
| `--scope` | project=./.mcp.json (shareable) · user=~/.claude.json. Ignored for agents with a single user-global config (e.g. Codex). (default: project) |

## Arguments

None.

## Full Help

```text
Usage: sima-cli mcp install [OPTIONS]

  Register sima-cli as an MCP server in the coding agent's config.

Options:
  --agent [claude|codex|all]  Coding agent(s) to register sima-cli with.
                              [default: claude]
  --scope [project|user]      project=./.mcp.json (shareable) ·
                              user=~/.claude.json. Ignored for agents with a
                              single user-global config (e.g. Codex).
                              [default: project]
  --help                      Show this message and exit.
```
