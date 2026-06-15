import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner

from sima_cli.shell import shell as shell_mod


def _clear_dispatch_env():
    """Remove the env vars _dispatch toggles so each test starts clean."""
    for key in ("SIMA_CLI_CHECK_FOR_UPDATE", "SIMA_CLI_SUPPRESS_ENV_BANNER",
                "SIMA_CLI_INTERNAL"):
        os.environ.pop(key, None)


class TestDispatch(unittest.TestCase):
    def setUp(self):
        _clear_dispatch_env()
        self.addCleanup(_clear_dispatch_env)

    def test_runs_command_through_main(self):
        fake_main = MagicMock()
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch("sdk list")
        fake_main.main.assert_called_once_with(args=["sdk", "list"], standalone_mode=False)

    def test_empty_line_is_noop(self):
        fake_main = MagicMock()
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch("   ")
        fake_main.main.assert_not_called()

    def test_unparseable_line_does_not_dispatch(self):
        fake_main = MagicMock()
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch('foo "unterminated')
        fake_main.main.assert_not_called()

    def test_blocks_terminal_takeover_commands(self):
        fake_main = MagicMock()
        with patch("sima_cli.cli.main", fake_main):
            for cmd in ("serial", "network", "selfupdate"):
                shell_mod._dispatch(cmd)
        fake_main.main.assert_not_called()

    def test_blocks_takeover_command_with_arguments(self):
        fake_main = MagicMock()
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch("serial -b 9600")
        fake_main.main.assert_not_called()

    def test_applies_dispatch_env_during_call_and_restores(self):
        seen = {}

        def capture(*_a, **_k):
            seen["update"] = os.environ.get("SIMA_CLI_CHECK_FOR_UPDATE")
            seen["banner"] = os.environ.get("SIMA_CLI_SUPPRESS_ENV_BANNER")

        fake_main = MagicMock()
        fake_main.main.side_effect = capture
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch("sdk list")

        # The overrides are visible while the command runs ...
        self.assertEqual(seen["update"], "0")
        self.assertEqual(seen["banner"], "1")
        # ... and removed afterwards because they were not set before.
        self.assertIsNone(os.environ.get("SIMA_CLI_CHECK_FOR_UPDATE"))
        self.assertIsNone(os.environ.get("SIMA_CLI_SUPPRESS_ENV_BANNER"))

    def test_restores_preexisting_env_values(self):
        os.environ["SIMA_CLI_CHECK_FOR_UPDATE"] = "1"
        fake_main = MagicMock()
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch("sdk list")
        self.assertEqual(os.environ.get("SIMA_CLI_CHECK_FOR_UPDATE"), "1")

    def test_dispatch_env_restored_even_on_error(self):
        fake_main = MagicMock()
        fake_main.main.side_effect = RuntimeError("boom")
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch("sdk list")  # should not raise
        self.assertIsNone(os.environ.get("SIMA_CLI_CHECK_FOR_UPDATE"))
        self.assertIsNone(os.environ.get("SIMA_CLI_SUPPRESS_ENV_BANNER"))

    def test_swallows_system_exit(self):
        fake_main = MagicMock()
        fake_main.main.side_effect = SystemExit(2)
        with patch("sima_cli.cli.main", fake_main):
            shell_mod._dispatch("sdk list")  # should not raise

    def test_shows_click_exception(self):
        exc = click.ClickException("nope")
        fake_main = MagicMock()
        fake_main.main.side_effect = exc
        with patch("sima_cli.cli.main", fake_main), patch.object(exc, "show") as show:
            shell_mod._dispatch("sdk list")
        show.assert_called_once()


class TestShellCommand(unittest.TestCase):
    def setUp(self):
        _clear_dispatch_env()
        self.addCleanup(_clear_dispatch_env)

    def test_propagates_internal_for_the_session_and_restores(self):
        captured = {}

        def fake_rich(_theme):
            captured["internal"] = os.environ.get("SIMA_CLI_INTERNAL")

        with patch.object(shell_mod, "_rich_repl", side_effect=fake_rich), \
                patch.object(shell_mod, "_get_saved_theme", return_value="dark"), \
                patch.object(shell_mod, "_save_theme"):
            result = CliRunner().invoke(shell_mod.shell_cmd, [], obj={"internal": True})

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(captured["internal"], "1")
        # Not leaked back to the caller after the shell exits.
        self.assertIsNone(os.environ.get("SIMA_CLI_INTERNAL"))

    def test_does_not_set_internal_when_external(self):
        captured = {}

        def fake_rich(_theme):
            captured["internal"] = os.environ.get("SIMA_CLI_INTERNAL")

        with patch.object(shell_mod, "_rich_repl", side_effect=fake_rich), \
                patch.object(shell_mod, "_get_saved_theme", return_value="dark"), \
                patch.object(shell_mod, "_save_theme"):
            CliRunner().invoke(shell_mod.shell_cmd, [], obj={"internal": False})

        self.assertIsNone(captured["internal"])

    def test_falls_back_to_basic_repl_when_prompt_toolkit_missing(self):
        with patch.object(shell_mod, "_rich_repl", side_effect=ImportError), \
                patch.object(shell_mod, "_basic_repl") as basic, \
                patch.object(shell_mod, "_get_saved_theme", return_value="dark"), \
                patch.object(shell_mod, "_save_theme"):
            result = CliRunner().invoke(shell_mod.shell_cmd, [], obj={})

        self.assertEqual(result.exit_code, 0)
        basic.assert_called_once()


class TestEnvBannerSuppression(unittest.TestCase):
    def setUp(self):
        _clear_dispatch_env()
        self.addCleanup(_clear_dispatch_env)

    def _invoke_probe(self):
        from sima_cli.cli import main

        @main.command(name="__bannerprobe")
        def _probe():
            click.echo("ran")

        self.addCleanup(lambda: main.commands.pop("__bannerprobe", None))

        with patch("sima_cli.cli.check_for_update", return_value=False), \
                patch("sima_cli.cli.get_environment_type", return_value=("host", "x")):
            return CliRunner().invoke(main, ["__bannerprobe"])

    def test_banner_printed_for_normal_invocation(self):
        result = self._invoke_probe()
        self.assertIn("🔧 Environment", result.output)

    def test_banner_suppressed_when_flag_set(self):
        os.environ["SIMA_CLI_SUPPRESS_ENV_BANNER"] = "1"
        result = self._invoke_probe()
        self.assertNotIn("🔧 Environment", result.output)


class TestDocsGeneration(unittest.TestCase):
    def test_collect_commands_includes_shell(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "generate_cli_markdown_docs",
            root / "scripts" / "generate_cli_markdown_docs.py",
        )
        module = importlib.util.module_from_spec(spec)
        # Register before exec so dataclass annotation resolution can find the
        # module (it looks the dataclass's module up in sys.modules).
        sys.modules[spec.name] = module
        self.addCleanup(lambda: sys.modules.pop(spec.name, None))
        spec.loader.exec_module(module)

        names = {doc.full_name for doc in module.collect_commands()}
        self.assertIn("sima-cli shell", names)


class TestUpdateCheckWarning(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: os.environ.pop("SIMA_CLI_CHECK_FOR_UPDATE", None))

    def test_warns_only_when_user_disabled_via_environment(self):
        from sima_cli.utils import pkg_update_check as puc

        os.environ["SIMA_CLI_CHECK_FOR_UPDATE"] = "0"

        # User disabled it at launch -> the warning is printed.
        with patch.object(puc, "_UPDATE_CHECK_DISABLED_BY_USER", True), \
                patch("builtins.print") as printed:
            self.assertFalse(puc.check_for_update("sima-cli"))
        self.assertTrue(
            any("disabled update check" in str(c) for c in printed.call_args_list)
        )

        # Disabled internally (e.g. by the shell) -> no warning noise.
        with patch.object(puc, "_UPDATE_CHECK_DISABLED_BY_USER", False), \
                patch("builtins.print") as printed:
            self.assertFalse(puc.check_for_update("sima-cli"))
        self.assertFalse(
            any("disabled update check" in str(c) for c in printed.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
