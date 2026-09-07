# `sima-cli mcp`

Run sima-cli as an MCP server so coding agents can drive the DevKit.

Parent command: [`sima-cli`](./sima-cli.md)

## Usage

```bash
sima-cli mcp [OPTIONS] COMMAND [ARGS]...
```

## Options

None.

## Arguments

None.

## Subcommands

- [`sima-cli mcp available`](./sima-cli-mcp-available.md): List the coding-agent backends sima-cli can register the MCP server with.
- [`sima-cli mcp install`](./sima-cli-mcp-install.md): Register sima-cli as an MCP server in the coding agent's config.
- [`sima-cli mcp serve`](./sima-cli-mcp-serve.md): Start the MCP server exposing DevKit remote-execution tools.
- [`sima-cli mcp status`](./sima-cli-mcp-status.md): Show MCP availability and per-agent registration.

## Full Help

```text
Usage: sima-cli mcp [OPTIONS] COMMAND [ARGS]...

  Run sima-cli as an MCP server so coding agents can drive the DevKit.

Options:
  --help  Show this message and exit.

Commands:
  available  List the coding-agent backends sima-cli can register the MCP...
  install    Register sima-cli as an MCP server in the coding agent's...
  serve      Start the MCP server exposing DevKit remote-execution tools.
  status     Show MCP availability and per-agent registration.
```
