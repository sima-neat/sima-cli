import unittest
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner

from sima_cli.playbooks.commands import playbook_group, register_playbook_commands
from sima_cli.playbooks.manager import SkillError


class TestSkillsCommands(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_install_command_calls_manager_with_source(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.install.return_value = ["onnx-afe-pipeline", "calibration-tools"]
        mock_manager.last_install_skips.return_value = []
        mock_manager.last_install_summary.return_value = {"detected": 2, "valid": 2, "discarded": 0}
        mock_manager.list_installed.return_value = {
            "onnx-afe-pipeline": {"type": "skill"},
            "calibration-tools": {"type": "rule"},
        }
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["install", "gh:lihyin/skills/"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("✅ Installed playbook (skill): onnx-afe-pipeline", result.output)
        self.assertIn("✅ Installed playbook (rule): calibration-tools", result.output)
        self.assertIn("Install Summary", result.output)
        self.assertIn("detected", result.output)
        self.assertIn("discarded", result.output)
        self.assertIn("2", result.output)
        self.assertIn("0", result.output)
        mock_manager.install.assert_called_once_with("gh:lihyin/skills/", force=False)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_install_command_supports_force_flag(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.install.return_value = ["onnx-afe-pipeline"]
        mock_manager.last_install_skips.return_value = []
        mock_manager.last_install_summary.return_value = {"detected": 1, "valid": 1, "discarded": 0}
        mock_manager.list_installed.return_value = {"onnx-afe-pipeline": {"type": "skill"}}
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["install", "--force", "bb:paconsultings/skills/"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("✅ Installed playbook (skill): onnx-afe-pipeline", result.output)
        self.assertIn("Install Summary", result.output)
        self.assertIn("detected", result.output)
        self.assertIn("valid", result.output)
        self.assertIn("discarded", result.output)
        mock_manager.install.assert_called_once_with("bb:paconsultings/skills/", force=True)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_install_command_prints_warning_for_skipped_invalid_markdown(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.install.return_value = []
        mock_manager.last_install_skips.return_value = [
            {"id": "onnx-afe-pipeline", "reason": "invalid YAML frontmatter in SKILL.md"}
        ]
        mock_manager.last_install_summary.return_value = {"detected": 1, "valid": 0, "discarded": 1}
        mock_manager.list_installed.return_value = {}
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["install", "gh:lihyin/skills/"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Skipped playbook install for onnx-afe-pipeline", result.output)
        self.assertIn("invalid YAML frontmatter in", result.output)
        self.assertIn("SKILL.md", result.output)
        self.assertIn("Install Summary", result.output)
        self.assertIn("detected", result.output)
        self.assertIn("valid", result.output)
        self.assertIn("discarded", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_install_command_surfaces_skill_error_as_click_exception(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.install.side_effect = SkillError("No compatible skills were installed for the current environment.")
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["install", "gh:lihyin/skills/"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Error: No compatible skills were installed for the current environment.", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_describe_command_displays_manifest_and_skill_markdown(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.describe.return_value = {
            "entry": {
                "id": "onnx-afe-pipeline",
                "name": "ONNX AFE Pipeline",
                "version": "0.1.0",
                "agents": ["codex", "claude"],
            },
            "manifest_yaml": "id: onnx-afe-pipeline\nversion: 0.1.0\n",
            "skill_markdown": "# ONNX AFE Pipeline\n\nBuild and validate.\n",
        }
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["describe", "onnx-afe-pipeline"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Manifest (YAML)", result.output)
        self.assertIn("id: onnx-afe-pipeline", result.output)
        self.assertIn("SKILL.md", result.output)
        self.assertIn("# ONNX AFE Pipeline", result.output)
        mock_manager.describe.assert_called_once_with("onnx-afe-pipeline")

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_list_command_displays_type_column(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {
            "netops": {"type": "skill", "version": "1.0.0", "agents": ["codex"]},
            "repo-rules": {"type": "rule", "version": "0.1.0", "agents": ["claude"]},
        }
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["list"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Playbook", result.output)
        self.assertIn("Kind", result.output)
        self.assertIn("netops", result.output)
        self.assertIn("repo-ru", result.output)
        self.assertIn("skill", result.output)
        self.assertIn("rule", result.output)

    def test_registers_playbook_command(self):
        @click.group()
        def root():
            pass

        register_playbook_commands(root)
        self.assertIn("playbooks", root.commands)
        self.assertIn("remove", root.commands["playbooks"].commands)
        self.assertIn("delete", root.commands["playbooks"].commands)
        self.assertNotIn("skills", root.commands)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_update_command_prints_warning_for_skipped_invalid_markdown(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.update.return_value = []
        mock_manager.last_update_skips.return_value = [
            {"id": "onnx-afe-pipeline", "reason": "invalid YAML frontmatter in SKILL.md"}
        ]
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["update", "onnx-afe-pipeline"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Skipped playbook update for onnx-afe-pipeline", result.output)
        self.assertIn("invalid YAML frontmatter in", result.output)
        self.assertIn("SKILL.md", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_update_with_rules_filter_updates_only_rules(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {
            "netops": {"type": "skill"},
            "repo-rules": {"type": "rule"},
        }
        mock_manager.update.return_value = ["repo-rules"]
        mock_manager.last_update_skips.return_value = []
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["update", "--rules"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("✅ Updated playbook: repo-rules", result.output)
        mock_manager.update.assert_called_once_with("repo-rules")

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_update_with_skills_filter_updates_only_skills(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {
            "netops": {"type": "skill"},
            "repo-rules": {"type": "rule"},
        }
        mock_manager.update.return_value = ["netops"]
        mock_manager.last_update_skips.return_value = []
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["update", "--skills"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("✅ Updated playbook: netops", result.output)
        mock_manager.update.assert_called_once_with("netops")

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_update_with_filter_and_non_matching_kit_id(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {"netops": {"type": "skill"}}
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["update", "netops", "--rules"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("does not match the requested kind filter", result.output)
        mock_manager.update.assert_not_called()

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_delete_requires_confirmation_by_default(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["delete", "netops"], input="n\n")

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Delete cancelled", result.output)
        mock_manager.uninstall.assert_not_called()

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_delete_with_yes_skips_confirmation(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["delete", "--yes", "netops"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("✅ Removed playbook: netops", result.output)
        mock_manager.uninstall.assert_called_once_with("netops")

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_remove_all_with_yes_removes_every_playbook(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {
            "netops": {"type": "skill"},
            "repo-rules": {"type": "rule"},
        }
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["remove", "--all", "--yes"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("✅ Removed playbook: netops", result.output)
        self.assertIn("✅ Removed playbook: repo-rules", result.output)
        self.assertIn("✅ Removed 2 playbooks.", result.output)
        self.assertEqual(mock_manager.uninstall.call_count, 2)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_remove_all_requires_no_kit_id(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["remove", "--all", "netops"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Specify either KIT_ID or --all, not both.", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    def test_delete_requires_kit_id_or_all(self, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager

        result = self.runner.invoke(playbook_group, ["delete"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Provide KIT_ID or use --all.", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    @patch("sima_cli.playbooks.commands.subprocess.check_output")
    def test_apply_fails_when_not_in_git_repo(self, mock_check_output, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {"repo-policy": {"type": "rule"}}
        mock_manager.describe.return_value = {
            "entry": {"id": "repo-policy", "type": "rule"},
            "document_markdown": "# AGENTS.md\n",
        }
        mock_manager_cls.return_value = mock_manager
        mock_check_output.side_effect = subprocess.CalledProcessError(128, ["git"])

        result = self.runner.invoke(playbook_group, ["apply", "repo-policy"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("only works on git-tracked repositories", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    @patch("sima_cli.playbooks.commands._select_rule_from_installed", return_value="repo-policy")
    @patch("sima_cli.playbooks.commands.subprocess.check_output")
    def test_apply_without_id_prompts_and_writes_agents_md(
        self, mock_check_output, _mock_select_rule, mock_manager_cls
    ):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {
            "repo-policy": {
                "id": "repo-policy",
                "type": "rule",
                "source": "gh:paconsultings/agent-kit",
                "version": "0.1.0",
                "scm_short_hash": "abc1234",
            },
            "netops-skill": {"id": "netops-skill", "type": "skill"},
        }
        mock_manager.describe.return_value = {
            "entry": {"id": "repo-policy", "type": "rule"},
            "document_markdown": "# AGENTS.md\n\nUse rg.\n",
        }
        mock_manager_cls.return_value = mock_manager

        with self.runner.isolated_filesystem() as tmpdir:
            mock_check_output.return_value = f"{tmpdir}\n"
            result = self.runner.invoke(playbook_group, ["apply"], input="y\n")

            self.assertEqual(result.exit_code, 0, msg=result.output)
            agents_md = Path(tmpdir) / "AGENTS.md"
            self.assertTrue(agents_md.exists())
            content = agents_md.read_text(encoding="utf-8")
            self.assertIn('SIMA-PLAYBOOK:START id="repo-policy"', content)
            self.assertIn("Use rg.", content)
            self.assertIn('SIMA-PLAYBOOK:END id="repo-policy"', content)
            self.assertIn("✅ Applied rule playbook: repo-policy", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    @patch("sima_cli.playbooks.commands.subprocess.check_output")
    def test_apply_rejects_non_rule_playbook(self, mock_check_output, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {"netops-skill": {"type": "skill"}}
        mock_manager_cls.return_value = mock_manager
        mock_check_output.return_value = "/tmp\n"

        result = self.runner.invoke(playbook_group, ["apply", "netops-skill"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("is not a rule and cannot be applied", result.output)

    @patch("sima_cli.playbooks.commands.SkillManager")
    @patch("sima_cli.playbooks.commands.subprocess.check_output")
    def test_apply_replaces_existing_managed_block(self, mock_check_output, mock_manager_cls):
        mock_manager = MagicMock()
        mock_manager.list_installed.return_value = {
            "repo-policy": {
                "id": "repo-policy",
                "type": "rule",
                "source": "gh:paconsultings/agent-kit",
                "version": "0.1.1",
                "scm_short_hash": "def5678",
            }
        }
        mock_manager.describe.return_value = {
            "entry": {"id": "repo-policy", "type": "rule"},
            "document_markdown": "NEW CONTENT\n",
        }
        mock_manager_cls.return_value = mock_manager

        with self.runner.isolated_filesystem() as tmpdir:
            mock_check_output.return_value = f"{tmpdir}\n"
            agents_md = Path(tmpdir) / "AGENTS.md"
            agents_md.write_text(
                'intro\n\n<!-- SIMA-PLAYBOOK:START id="repo-policy" type="rule" source="old" version="0.1.0" commit="old" applied_at="x" -->\nOLD CONTENT\n<!-- SIMA-PLAYBOOK:END id="repo-policy" -->\n',
                encoding="utf-8",
            )

            result = self.runner.invoke(playbook_group, ["apply", "repo-policy", "--yes"])

            self.assertEqual(result.exit_code, 0, msg=result.output)
            content = agents_md.read_text(encoding="utf-8")
            self.assertIn("intro", content)
            self.assertIn("NEW CONTENT", content)
            self.assertNotIn("OLD CONTENT", content)
            self.assertEqual(content.count('SIMA-PLAYBOOK:START id="repo-policy"'), 1)


if __name__ == "__main__":
    unittest.main()
