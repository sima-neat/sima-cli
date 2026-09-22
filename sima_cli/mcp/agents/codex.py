"""Codex MCP integration — TOML config at ``$CODEX_HOME/config.toml``.

Codex has a single user-global config, so ``scope`` is ignored. We merge by
replacing/appending only our ``[mcp_servers.sima-devkit]`` table (regex, no TOML
writer dependency), preserving the rest of the file and staying idempotent.
"""

import json
import os
import re
from pathlib import Path
from typing import Tuple

from sima_cli.mcp.agents.base import AgentIntegration, SERVER_KEY, server_entry


class CodexAgent(AgentIntegration):
    name = "codex"
    honors_scope = False

    # Matches our table from its header up to the next top-level table or EOF.
    _BLOCK_RE = re.compile(
        r"(?ms)^\[mcp_servers\." + re.escape(SERVER_KEY) + r"\]\s*\n.*?(?=^\[|\Z)"
    )

    def config_path(self, scope: str = "") -> Path:
        codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        return codex_home / "config.toml"

    def _block(self) -> str:
        # json.dumps emits valid TOML basic strings/arrays.
        entry = server_entry()
        args = ", ".join(json.dumps(a) for a in entry["args"])
        env = ", ".join(f"{k} = {json.dumps(v)}" for k, v in entry["env"].items())
        return (
            f"[mcp_servers.{SERVER_KEY}]\n"
            f'command = {json.dumps(entry["command"])}\n'
            f"args = [{args}]\n"
            f"env = {{ {env} }}\n"
        )

    def install(self, scope: str = "") -> Tuple[Path, bool]:
        path = self.config_path(scope)
        block = self._block()
        existing = path.read_text() if path.exists() else ""
        existed = bool(self._BLOCK_RE.search(existing))
        if existed:
            new_text = self._BLOCK_RE.sub(block, existing, count=1)
        elif existing and not existing.endswith("\n"):
            new_text = existing + "\n\n" + block
        elif existing:
            new_text = existing + "\n" + block
        else:
            new_text = block
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text)
        return path, existed

    def is_registered(self, scope: str = "") -> bool:
        path = self.config_path(scope)
        if not path.exists():
            return False
        return bool(self._BLOCK_RE.search(path.read_text()))
