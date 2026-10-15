"""
sima-cli MCP integration.

Exposes sima-cli's DevKit remote-execution capabilities as Model Context
Protocol (MCP) tools so that any coding agent (Claude Code, Cursor, Codex, ...)
can connect to sima-cli and run commands on a SiMa DevKit board over SSH.

PoC scope: a stdio MCP server (`sima-cli mcp serve`) exposing
`discover_devices`, `get_board_info` and `run_command`, plus a Claude
integration path (`sima-cli mcp install`).
"""

from sima_cli.mcp.commands import register_mcp_commands

__all__ = ["register_mcp_commands"]
