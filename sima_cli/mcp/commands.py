"""
`sima-cli mcp` command group: run sima-cli as an MCP server and register it
with coding agents.

    sima-cli mcp serve      Start the stdio MCP server (this is what agents launch).
    sima-cli mcp install    Register the server in Claude and/or Codex MCP config.
    sima-cli mcp status      Show server availability and per-agent registration.
"""

import os

import click

from sima_cli.mcp.agents import AGENTS, SERVER_KEY, get_agent


def _mcp_available() -> bool:
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        return False
    return True


def register_mcp_commands(main):
    @main.group(name="mcp")
    @click.pass_context
    def mcp_group(ctx):
        """Run sima-cli as an MCP server so coding agents can drive the DevKit."""
        pass

    @mcp_group.command("serve")
    @click.option(
        "--transport",
        type=click.Choice(["stdio"]),
        default="stdio",
        show_default=True,
        help="Transport for the MCP server (PoC supports stdio only).",
    )
    def serve(transport):
        """Start the MCP server exposing DevKit remote-execution tools.

        Communicates over stdio; intended to be launched by a coding agent, not
        run by hand. Tools: discover_devices, get_board_info, run_command.

        Host-only: the server drives a DevKit *from* a host over SSH, so it is
        not supported when run on a SiMa board itself.
        """
        from sima_cli.utils.env import is_sima_board

        if is_sima_board():
            click.echo(
                "❌ 'sima-cli mcp serve' is host-only; it drives a DevKit from a host "
                "over SSH and isn't supported on the DevKit itself.\n"
                "   Run it on your workstation. On-device support may come later.",
                err=True,
            )
            raise SystemExit(1)

        if not _mcp_available():
            click.echo(
                "❌ MCP support requires the optional 'mcp' dependency (Python 3.10+).\n"
                "   Install it with:  pip install 'sima-cli[mcp]'",
                err=True,
            )
            raise SystemExit(1)

        from sima_cli.mcp.server import build_server

        # Diagnostics go to stderr only — stdout is the protocol stream.
        click.echo(f"🔌 Starting sima-cli MCP server ({transport})…", err=True)
        server = build_server()
        server.run(transport=transport)

    @mcp_group.command("install")
    @click.option(
        "--agent",
        type=click.Choice([*AGENTS, "all"]),
        default="claude",
        show_default=True,
        help="Coding agent(s) to register sima-cli with.",
    )
    @click.option(
        "--scope",
        type=click.Choice(["project", "user"]),
        default="project",
        show_default=True,
        help="project=./.mcp.json (shareable) · user=~/.claude.json. "
        "Ignored for agents with a single user-global config (e.g. Codex).",
    )
    def install(agent, scope):
        """Register sima-cli as an MCP server in the coding agent's config."""
        names = list(AGENTS) if agent == "all" else [agent]
        for name in names:
            impl = get_agent(name)
            if not impl.honors_scope and scope == "project":
                click.echo(
                    f"ℹ️  {name} config is user-global; --scope is ignored for {name}."
                )
            config_path, existed = impl.install(scope)
            verb = "Updated" if existed else "Registered"
            click.echo(f"✅ {verb} '{SERVER_KEY}' for {name} in {config_path}")

        click.echo(
            "ℹ️  Restart the agent (or reload its MCP servers) to pick up the change."
        )
        if not _mcp_available():
            click.echo(
                "⚠️  The 'mcp' dependency (Python 3.10+) isn't installed yet; the server "
                "won't start until you run:  pip install 'sima-cli[mcp]'"
            )

    @mcp_group.command("status")
    @click.option(
        "--scope",
        type=click.Choice(["project", "user"]),
        default="project",
        show_default=True,
        help="Scope to check for scope-aware agents (others are user-global).",
    )
    def status(scope):
        """Show MCP availability and per-agent registration."""
        from sima_cli.mcp.server import _user, _strict_host_key, _KEY_ENV

        click.echo(f"MCP SDK installed : {'yes' if _mcp_available() else 'no'}")
        click.echo(
            f"DevKit SSH user   : {_user()}"
            f"{' (from $SIMA_DEVKIT_USER)' if os.environ.get('SIMA_DEVKIT_USER') else ' (unprivileged default)'}"
        )
        key_file = os.environ.get(_KEY_ENV)
        if key_file:
            click.echo(f"DevKit auth       : key file ($SIMA_DEVKIT_KEY={key_file})")
        else:
            pw_src = "from $SIMA_DEVKIT_PASSWORD" if os.environ.get("SIMA_DEVKIT_PASSWORD") else "built-in default"
            click.echo(f"DevKit auth       : password ({pw_src})")
        click.echo(
            f"Host key check    : {'strict (known hosts only)' if _strict_host_key() else 'trust-on-first-use'}"
        )
        for name, impl in AGENTS.items():
            state = "registered" if impl.is_registered(scope) else "not registered"
            click.echo(f"{name + ' config':<17} : {impl.config_path(scope)} ({state})")

    @mcp_group.command("available")
    def available():
        """List the coding-agent backends sima-cli can register the MCP server with."""
        click.echo("Available agent backends for the SiMa MCP server:")
        for name in AGENTS:
            click.echo(f"  - {click.style(name, bold=True)}")
