"""Claude (Claude Code) MCP integration — JSON config under ``mcpServers``."""

import json
from pathlib import Path
from typing import Tuple

import click

from sima_cli.mcp.agents.base import AgentIntegration, SERVER_KEY, server_entry


class ClaudeAgent(AgentIntegration):
    name = "claude"
    honors_scope = True

    def config_path(self, scope: str) -> Path:
        # project: ./.mcp.json (shareable, checked into the repo)
        # user:    ~/.claude.json (all of this user's Claude sessions)
        if scope == "user":
            return Path.home() / ".claude.json"
        return Path.cwd() / ".mcp.json"

    def install(self, scope: str) -> Tuple[Path, bool]:
        path = self.config_path(scope)
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text() or "{}")
            except json.JSONDecodeError:
                raise click.ClickException(
                    f"{path} exists but is not valid JSON; leaving it untouched."
                )
        servers = data.setdefault("mcpServers", {})
        existed = SERVER_KEY in servers
        servers[SERVER_KEY] = server_entry()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return path, existed

    def is_registered(self, scope: str) -> bool:
        path = self.config_path(scope)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError:
            return False
        return SERVER_KEY in data.get("mcpServers", {})
