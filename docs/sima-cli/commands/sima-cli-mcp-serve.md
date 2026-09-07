# `sima-cli mcp serve`

Start the MCP server exposing DevKit remote-execution tools.

Parent command: [`sima-cli mcp`](./sima-cli-mcp.md)

## Usage

```bash
sima-cli mcp serve [OPTIONS]
```

## Options

| Name | Description |
| --- | --- |
| `--transport` | Transport for the MCP server (PoC supports stdio only). (default: stdio) |

## Arguments

None.

## Full Help

```text
Usage: sima-cli mcp serve [OPTIONS]

  Start the MCP server exposing DevKit remote-execution tools.

  Communicates over stdio; intended to be launched by a coding agent, not run
  by hand. Tools: discover_devices, get_board_info, run_command.

  Host-only: the server drives a DevKit *from* a host over SSH, so it is not
  supported when run on a SiMa board itself.

Options:
  --transport [stdio]  Transport for the MCP server (PoC supports stdio only).
                       [default: stdio]
  --help               Show this message and exit.
```
