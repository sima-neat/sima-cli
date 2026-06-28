"""
Agent integration interface for the sima-cli MCP server.

Each coding agent (Claude, Codex, …) stores MCP server registrations in its own
file and format. ``AgentIntegration`` is the abstract contract every agent
implementation fulfils so :mod:`sima_cli.mcp.commands` can register/query the
server without knowing those per-agent details.

The MCP server entry itself (command, args, clean-stdout env) is identical
across agents and defined here once.
"""

import abc
from pathlib import Path
from typing import Tuple

# Key/name the MCP server is registered under in every agent's config.
SERVER_KEY = "sima-devkit"

# Env baked into the launch entry so `sima-cli mcp serve` keeps stdout clean for
# the JSON-RPC stream (the root CLI callback would otherwise print to stdout).
SERVE_ENV = {
    "SIMA_CLI_SUPPRESS_ENV_BANNER": "1",
    "SIMA_CLI_CHECK_FOR_UPDATE": "0",
}


def server_entry() -> dict:
    """The MCP server launch entry, shared by all agents."""
    return {
        "command": "sima-cli",
        "args": ["mcp", "serve"],
        "env": dict(SERVE_ENV),
    }


class AgentIntegration(abc.ABC):
    """Registers the sima-cli MCP server in one coding agent's config."""

    #: Short agent identifier, e.g. ``"claude"`` — used as the CLI ``--agent`` value.
    name: str = ""

    #: Whether the agent distinguishes a project vs. user scope. Agents that only
    #: have a single user-global config set this False so the CLI can note that
    #: ``--scope`` is ignored for them.
    honors_scope: bool = True

    @abc.abstractmethod
    def config_path(self, scope: str) -> Path:
        """Return the config file this agent reads MCP servers from."""

    @abc.abstractmethod
    def install(self, scope: str) -> Tuple[Path, bool]:
        """Register (or update) the MCP server.

        Returns ``(config_path, already_existed)`` where ``already_existed`` is
        True when an entry was present before this call. Must preserve any other
        configuration in the file and be idempotent on repeat calls.
        """

    @abc.abstractmethod
    def is_registered(self, scope: str) -> bool:
        """Return True if the sima-cli MCP server is registered for this agent."""
