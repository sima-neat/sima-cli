"""Unit tests for the sima-cli MCP PoC layer (no hardware, no live MCP transport)."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - older interpreters
    tomllib = None

try:
    import mcp.server.fastmcp  # noqa: F401  - MCP SDK requires Python 3.10+
    _HAVE_MCP_SDK = True
except Exception:  # pragma: no cover - SDK absent on 3.8/3.9 CI
    _HAVE_MCP_SDK = False

# Tests that build the FastMCP server need the optional 'mcp' dependency.
requires_mcp = unittest.skipUnless(_HAVE_MCP_SDK, "requires the mcp SDK (Python 3.10+)")

import click
from click.testing import CliRunner

from sima_cli.mcp import commands as mcp_commands
from sima_cli.mcp import server as mcp_server
from sima_cli.mcp.safety import evaluate_command
from sima_cli.mcp.agents import AGENTS, AgentIntegration, ClaudeAgent, CodexAgent, get_agent


def _build_cli():
    @click.group()
    def root():
        pass

    mcp_commands.register_mcp_commands(root)
    return root


def _tool(name):
    """Return the underlying callable for a registered FastMCP tool."""
    server = mcp_server.build_server()
    return server._tool_manager._tools[name].fn


class TestServerTools(unittest.TestCase):
    """The tool callables wrap update/remote.py; exercise them with the SSH layer mocked."""

    @requires_mcp
    def test_run_command_success(self):
        with patch("sima_cli.mcp.server._connect") as connect, \
             patch("sima_cli.update.remote.run_remote_command_capture",
                   return_value=(0, "hello\n", "")) as cap:
            run_command = _tool("run_command")
            result = run_command(ip="1.2.3.4", command="echo hello")

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "hello\n")
        self.assertFalse(result["timed_out"])
        connect.assert_called_once_with("1.2.3.4")
        # command passed through unchanged
        self.assertEqual(cap.call_args.args[1], "echo hello")

    @requires_mcp
    def test_run_command_blocks_before_connecting(self):
        # A blocked command must never open an SSH session.
        with patch("sima_cli.update.remote.init_ssh_session") as init_ssh, \
             patch("sima_cli.update.remote.run_remote_command_capture") as cap:
            run_command = _tool("run_command")
            result = run_command(ip="1.2.3.4", command="rm -rf *")

        self.assertTrue(result["blocked"])
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("blocked by sima-cli MCP policy", result["stderr"])
        init_ssh.assert_not_called()
        cap.assert_not_called()

    @requires_mcp
    def test_run_command_connect_failure_is_graceful(self):
        with patch("sima_cli.mcp.server._connect",
                   side_effect=OSError("timed out")):
            run_command = _tool("run_command")
            result = run_command(ip="10.255.255.1", command="echo hi")

        self.assertEqual(result["exit_code"], -1)
        self.assertIn("timed out", result["stderr"])

    @requires_mcp
    def test_get_board_info_uses_connect_and_probe(self):
        with patch("sima_cli.mcp.server._connect") as connect, \
             patch("sima_cli.mcp.server._probe_board_info",
                   return_value=("mlsoc", "1.6.0", "modalix", True, "yocto")) as probe:
            get_board_info = _tool("get_board_info")
            info = get_board_info(ip="1.2.3.4")

        connect.assert_called_once_with("1.2.3.4")
        probe.assert_called_once()
        self.assertEqual(info["board_type"], "mlsoc")
        self.assertEqual(info["fwtype"], "yocto")
        self.assertTrue(info["full_image"])
        self.assertTrue(info["reachable"])

    @requires_mcp
    def test_get_board_info_unreachable_is_graceful(self):
        with patch("sima_cli.mcp.server._connect", side_effect=OSError("no route")):
            get_board_info = _tool("get_board_info")
            info = get_board_info(ip="10.255.255.1")

        self.assertFalse(info["reachable"])
        self.assertEqual(info["board_type"], "")
        self.assertIn("no route", info["error"])

    @requires_mcp
    def test_discover_probes_each_device_via_connect(self):
        arp = [{"ip": "1.2.3.4", "mac": "aa"}, {"ip": "5.6.7.8", "mac": "bb"}]

        def fake_connect(ip, timeout=None):
            if ip == "5.6.7.8":
                raise OSError("down")
            return MagicMock()

        with patch("sima_cli.discover.discover.get_sima_devices_from_arp", return_value=arp), \
             patch("sima_cli.mcp.server._connect", side_effect=fake_connect) as connect, \
             patch("sima_cli.mcp.server._probe_board_info",
                   return_value=("mlsoc", "1.6.0", "modalix", False, "elxr")):
            discover = _tool("discover_devices")
            devices = discover()

        self.assertEqual(connect.call_count, 2)
        # Discovery must use the shorter per-probe timeout, not the default.
        for call in connect.call_args_list:
            self.assertEqual(call.kwargs.get("timeout"), mcp_server._DISCOVERY_CONNECT_TIMEOUT)
        reachable = {d["ip"]: d for d in devices}
        self.assertTrue(reachable["1.2.3.4"]["reachable"])
        self.assertEqual(reachable["1.2.3.4"]["board_type"], "mlsoc")
        self.assertFalse(reachable["5.6.7.8"]["reachable"])
        self.assertNotIn("board_type", reachable["5.6.7.8"])

    def test_truncate_caps_output(self):
        big = "x" * (mcp_server._MAX_OUTPUT_CHARS + 500)
        out = mcp_server._truncate(big)
        self.assertLess(len(out), len(big))
        self.assertIn("truncated", out)

    def test_password_env_override(self):
        with patch.dict(os.environ, {mcp_server._PASSWORD_ENV: "secret"}):
            self.assertEqual(mcp_server._password(), "secret")

    def test_default_unprivileged_identity(self):
        # By default the MCP server connects as the unprivileged sima/edgeai user.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(mcp_server._USER_ENV, None)
            os.environ.pop(mcp_server._PASSWORD_ENV, None)
            self.assertEqual(mcp_server._user(), "sima")
            self.assertEqual(mcp_server._password(), "edgeai")

    def test_user_env_override(self):
        with patch.dict(os.environ, {mcp_server._USER_ENV: "devuser"}):
            self.assertEqual(mcp_server._user(), "devuser")

    def test_privileged_user_override_is_rejected(self):
        # SIMA_DEVKIT_USER=root (or other privileged identities) must be refused,
        # not silently honoured, so the unprivileged guarantee can't be bypassed.
        for name in ("root", "ROOT", "  root  ", "toor"):
            with patch.dict(os.environ, {mcp_server._USER_ENV: name}):
                with self.assertRaises(mcp_server.PrivilegedUserError):
                    mcp_server._user()

    def test_privileged_user_blocks_connection(self):
        # _connect resolves the user first, so a root override never opens an SSH
        # session as root — it raises before paramiko is touched.
        fake_paramiko = MagicMock()
        with patch.dict(os.environ, {mcp_server._USER_ENV: "root"}), \
             patch.dict("sys.modules", {"paramiko": fake_paramiko}):
            with self.assertRaises(mcp_server.PrivilegedUserError):
                mcp_server._connect("1.2.3.4")
        fake_paramiko.SSHClient.return_value.connect.assert_not_called()

    def _fake_connect(self, env=None):
        """Run _connect with paramiko mocked; return (fake_paramiko, connect_kwargs)."""
        fake_paramiko = MagicMock()
        with patch.dict(os.environ, env or {}, clear=False):
            for var in (
                mcp_server._USER_ENV,
                mcp_server._PASSWORD_ENV,
                mcp_server._KEY_ENV,
                mcp_server._STRICT_HOST_KEY_ENV,
            ):
                if not env or var not in env:
                    os.environ.pop(var, None)
            with patch.dict("sys.modules", {"paramiko": fake_paramiko}):
                mcp_server._connect("1.2.3.4")
        _, kwargs = fake_paramiko.SSHClient.return_value.connect.call_args
        return fake_paramiko, kwargs

    def test_connect_uses_unprivileged_user_and_no_keys(self):
        fake_paramiko, kwargs = self._fake_connect()
        client = fake_paramiko.SSHClient.return_value
        self.assertEqual(kwargs["username"], "sima")
        self.assertEqual(kwargs["password"], "edgeai")
        # Must not fall back to a (possibly privileged) SSH-agent key.
        self.assertFalse(kwargs["allow_agent"])
        self.assertFalse(kwargs["look_for_keys"])
        # Known hosts loaded (verify changed keys) and TOFU policy by default.
        client.load_system_host_keys.assert_called_once()
        fake_paramiko.AutoAddPolicy.assert_called_once()
        fake_paramiko.RejectPolicy.assert_not_called()

    def test_connect_strict_host_key_uses_reject_policy(self):
        fake_paramiko, _ = self._fake_connect({mcp_server._STRICT_HOST_KEY_ENV: "1"})
        fake_paramiko.RejectPolicy.assert_called_once()
        fake_paramiko.AutoAddPolicy.assert_not_called()

    def test_connect_key_auth_skips_password(self):
        _, kwargs = self._fake_connect({mcp_server._KEY_ENV: "/home/u/.ssh/id_devkit"})
        self.assertEqual(kwargs["key_filename"], "/home/u/.ssh/id_devkit")
        self.assertNotIn("password", kwargs)
        self.assertTrue(kwargs["allow_agent"])
        self.assertTrue(kwargs["look_for_keys"])


class TestInstallAndStatus(unittest.TestCase):
    def test_install_project_scope_writes_mcp_json(self):
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            result = runner.invoke(root, ["mcp", "install", "--scope", "project"])
            self.assertEqual(result.exit_code, 0, result.output)
            data = json.loads(Path(".mcp.json").read_text())
            entry = data["mcpServers"]["sima-devkit"]
            self.assertEqual(entry["command"], "sima-cli")
            self.assertEqual(entry["args"], ["mcp", "serve"])
            self.assertEqual(entry["env"]["SIMA_CLI_SUPPRESS_ENV_BANNER"], "1")

    def test_install_preserves_existing_servers(self):
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            Path(".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
            result = runner.invoke(root, ["mcp", "install", "--scope", "project"])
            self.assertEqual(result.exit_code, 0, result.output)
            servers = json.loads(Path(".mcp.json").read_text())["mcpServers"]
            self.assertIn("other", servers)
            self.assertIn("sima-devkit", servers)

    def test_install_rejects_malformed_json(self):
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            Path(".mcp.json").write_text("{not json")
            result = runner.invoke(root, ["mcp", "install", "--scope", "project"])
            self.assertNotEqual(result.exit_code, 0)

    def test_status_reports_registration(self):
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            runner.invoke(root, ["mcp", "install", "--scope", "project"])
            result = runner.invoke(root, ["mcp", "status", "--scope", "project"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("registered", result.output)

    def test_status_lists_unregistered_agents(self):
        # With nothing installed, every agent must still be listed as "not registered".
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            codex_home = Path("codex_home").resolve()
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                result = runner.invoke(root, ["mcp", "status", "--scope", "project"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("not registered", result.output)
            for name in AGENTS:
                self.assertIn(f"{name} config", result.output)

    def test_available_lists_all_agents(self):
        runner = CliRunner()
        root = _build_cli()
        result = runner.invoke(root, ["mcp", "available"])
        self.assertEqual(result.exit_code, 0, result.output)
        for name in AGENTS:
            self.assertIn(name, result.output)

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_install_codex_writes_toml(self):
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            codex_home = Path("codex_home").resolve()
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                result = runner.invoke(root, ["mcp", "install", "--agent", "codex"])
                self.assertEqual(result.exit_code, 0, result.output)
                data = tomllib.loads((codex_home / "config.toml").read_text())
            entry = data["mcp_servers"]["sima-devkit"]
            self.assertEqual(entry["command"], "sima-cli")
            self.assertEqual(entry["args"], ["mcp", "serve"])
            self.assertEqual(entry["env"]["SIMA_CLI_CHECK_FOR_UPDATE"], "0")

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_install_codex_preserves_and_is_idempotent(self):
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            codex_home = Path("codex_home").resolve()
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                '[profiles.default]\nmodel = "gpt"\n\n'
                '[mcp_servers.other]\ncommand = "x"\n'
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                runner.invoke(root, ["mcp", "install", "--agent", "codex"])
                runner.invoke(root, ["mcp", "install", "--agent", "codex"])  # re-install
                text = (codex_home / "config.toml").read_text()
                data = tomllib.loads(text)
            # existing config preserved, our block present exactly once
            self.assertIn("profiles", data)
            self.assertIn("other", data["mcp_servers"])
            self.assertIn("sima-devkit", data["mcp_servers"])
            self.assertEqual(text.count("[mcp_servers.sima-devkit]"), 1)

    def test_install_all_registers_both(self):
        runner = CliRunner()
        root = _build_cli()
        with runner.isolated_filesystem():
            codex_home = Path("codex_home").resolve()
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                result = runner.invoke(root, ["mcp", "install", "--agent", "all"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertTrue(Path(".mcp.json").exists())
                self.assertTrue((codex_home / "config.toml").exists())


class TestServeGating(unittest.TestCase):
    def test_serve_rejected_on_devkit(self):
        runner = CliRunner()
        root = _build_cli()
        with patch("sima_cli.utils.env.is_sima_board", return_value=True), \
             patch("sima_cli.mcp.server.build_server") as build:
            result = runner.invoke(root, ["mcp", "serve"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("host-only", result.output)
        build.assert_not_called()  # rejected before the server is built


class TestAgents(unittest.TestCase):
    def test_registry_contains_known_agents(self):
        self.assertIsInstance(get_agent("claude"), ClaudeAgent)
        self.assertIsInstance(get_agent("codex"), CodexAgent)
        for impl in AGENTS.values():
            self.assertIsInstance(impl, AgentIntegration)
            self.assertTrue(impl.name)

    def test_scope_awareness(self):
        self.assertTrue(get_agent("claude").honors_scope)
        self.assertFalse(get_agent("codex").honors_scope)

    def test_abstract_base_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            AgentIntegration()  # missing abstractmethods

    def test_each_agent_implements_contract(self):
        required = {"config_path", "install", "is_registered"}
        for impl in AGENTS.values():
            for method in required:
                self.assertTrue(callable(getattr(impl, method)), f"{impl.name}.{method}")


class TestSafety(unittest.TestCase):
    BLOCKED = [
        "rm -rf *",
        "rm -rf /",
        "rm -r /tmp/foo",
        "rm -fr ~",
        "rm *",
        "rm /etc/hosts",
        "sudo reboot",
        "su - root",
        "doas reboot",
        # privilege escalation via absolute path / wrapper must not bypass the gate:
        "/usr/bin/sudo id",
        "/bin/su - root",
        "/usr/bin/pkexec reboot",
        "/usr/bin/doas reboot",
        "env sudo reboot",                              # wrapper form
        "uptime && /usr/bin/sudo reboot",               # absolute path in a later segment
        "echo 1 > /sys/class/leds/x/brightness",
        "echo performance >> /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
        "tee /proc/sys/vm/drop_caches",
        "dd if=/dev/zero of=/dev/mmcblk0",
        "sysctl -w kernel.panic=1",
        "mkfs.ext4 /dev/sda1",
        "uptime; rm -rf /var",
        "true && sudo sh",
        ":(){ :|:& };:",
        "sh -c 'rm -rf /'",
        "bash -c \"sudo reboot\"",
        # bypasses closed in this change:
        "echo rm -rf / | sh",                           # pipe into a shell
        "bash /tmp/script.sh",                          # shell with a script, no -c
        "find / -delete",
        "find /tmp -name x -exec rm {} ;",
        "dd if=/dev/zero of=/tmp/x",                    # dd blocked regardless of target
        "shred -u /etc/hosts",
        "truncate -s 0 /var/log/syslog",
        "wipefs -a /dev/mmcblk0",
        "echo bad > /etc/hosts",                        # redirect to a system path
        "sysctl kernel.panic=1",                        # write without -w
    ]

    ALLOWED = [
        "uname -a",
        "cat /sys/class/thermal/thermal_zone0/temp",   # reading /sys is fine
        "ls -la /tmp",
        "rm /tmp/onefile.log",                          # non-recursive, explicit file
        "rm -f /tmp/onefile.log",
        "cat /proc/cpuinfo",
        "df -h && free -m",
        "systemctl status docker",
        "sh -c 'ls -la'",                               # screenable inline command
        "find /tmp -name '*.log'",                      # find without -delete/-exec
        "sysctl -a",                                    # read all params
        "echo hello > /tmp/out.txt",                    # redirect to a normal path
    ]

    def test_blocked_commands(self):
        for cmd in self.BLOCKED:
            allowed, reason = evaluate_command(cmd)
            self.assertFalse(allowed, f"should be blocked: {cmd!r}")
            self.assertTrue(reason, f"missing reason for: {cmd!r}")

    def test_allowed_commands(self):
        for cmd in self.ALLOWED:
            allowed, reason = evaluate_command(cmd)
            self.assertTrue(allowed, f"should be allowed: {cmd!r} (reason={reason})")

    def test_empty_command_is_allowed(self):
        self.assertEqual(evaluate_command("   "), (True, None))

    def test_absolute_path_privilege_escalation_is_blocked(self):
        # The basename check must catch escalation regardless of how it's spelled.
        for cmd in (
            "/usr/bin/sudo id",
            "/bin/su - root",
            "/usr/bin/pkexec reboot",
            "/usr/bin/doas reboot",
            "env sudo reboot",
        ):
            allowed, reason = evaluate_command(cmd)
            self.assertFalse(allowed, f"should be blocked: {cmd!r}")
            self.assertIn("privilege escalation", reason)


if __name__ == "__main__":
    unittest.main()
