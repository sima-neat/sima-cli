"""Coding-agent MCP integrations.

Each agent implements :class:`AgentIntegration`; ``AGENTS`` is the name→instance
registry the CLI uses. To support a new agent, add an implementation module and
register its instance here — the ``mcp install``/``status`` commands pick it up
automatically.
"""

from sima_cli.mcp.agents.base import (
    AgentIntegration,
    SERVER_KEY,
    SERVE_ENV,
    server_entry,
)
from sima_cli.mcp.agents.claude import ClaudeAgent
from sima_cli.mcp.agents.codex import CodexAgent

AGENTS = {agent.name: agent for agent in (ClaudeAgent(), CodexAgent())}


def get_agent(name: str) -> AgentIntegration:
    return AGENTS[name]


__all__ = [
    "AgentIntegration",
    "SERVER_KEY",
    "SERVE_ENV",
    "server_entry",
    "ClaudeAgent",
    "CodexAgent",
    "AGENTS",
    "get_agent",
]
