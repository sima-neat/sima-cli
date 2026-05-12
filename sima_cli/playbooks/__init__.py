"""Skills management for sima-cli."""

from .commands import register_playbook_commands

# Backward-compatible export for existing imports.
register_agent_kit_commands = register_playbook_commands

__all__ = ["register_playbook_commands", "register_agent_kit_commands"]
