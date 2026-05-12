import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sima_cli.playbooks.manager import SkillManager, SkillRegistry


class TestSkillsManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.registry_path = self.root / "registry.json"
        self.registry = SkillRegistry(registry_path=self.registry_path)
        self.manager = SkillManager(registry=self.registry)

        self.codex_home = self.root / "codex-home"
        self.claude_home = self.root / "claude-home"
        os.environ["CODEX_HOME"] = str(self.codex_home)
        os.environ["CLAUDE_HOME"] = str(self.claude_home)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("CODEX_HOME", None)
        os.environ.pop("CLAUDE_HOME", None)
        os.environ.pop("SIMA_CLI_HOME", None)

    def test_default_registry_path_uses_playbooks_and_migrates_legacy_skills_registry(self):
        os.environ["SIMA_CLI_HOME"] = str(self.root / "sima-home")
        legacy_registry = Path(os.environ["SIMA_CLI_HOME"]) / "skills" / "registry.json"
        legacy_registry.parent.mkdir(parents=True, exist_ok=True)
        legacy_registry.write_text(
            '{"skills": {"repo-defaults": {"id": "repo-defaults", "type": "rule"}}}',
            encoding="utf-8",
        )

        registry = SkillRegistry()

        expected_registry = Path(os.environ["SIMA_CLI_HOME"]) / "playbooks" / "registry.json"
        self.assertEqual(registry.registry_path, expected_registry)
        self.assertTrue(expected_registry.exists())
        self.assertIn("repo-defaults", registry.all_skills())

    def _create_skill(self, base: Path, skill_name: str = "netops") -> Path:
        skill = base / skill_name
        (skill / "common").mkdir(parents=True)
        (skill / "targets" / "codex").mkdir(parents=True)
        (skill / "targets" / "claude").mkdir(parents=True)

        (skill / "common" / "SKILL.md").write_text("Common {{AGENT}}\n", encoding="utf-8")
        (skill / "targets" / "codex" / "openai.yaml").write_text("model: gpt\n", encoding="utf-8")
        (skill / "targets" / "claude" / "anthropic.yaml").write_text("model: claude\n", encoding="utf-8")

        (skill / "playbook.yaml").write_text(
            """
id: netops
version: 1.2.3
agents: [codex, claude]
compatibility:
  env_types: [host]
  env_subtypes: [mac]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        return skill

    def _create_rule(self, base: Path, rule_name: str = "repo-defaults") -> Path:
        rule = base / rule_name
        rule.mkdir(parents=True, exist_ok=True)
        (rule / "AGENTS.md").write_text("# AGENTS.md\n\nUse rg.\n", encoding="utf-8")
        (rule / "playbook.yaml").write_text(
            """
id: repo-defaults
type: rule
version: 0.1.0
agents: [codex, claude]
compatibility:
  env_types: [host]
  env_subtypes: [mac]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        return rule

    def _create_nested_skill(
        self,
        base: Path,
        rel_path: str,
        skill_id: str,
        *,
        env_type: str = "host",
        env_subtype: str = "mac",
    ) -> Path:
        skill = base / rel_path
        (skill / "common").mkdir(parents=True, exist_ok=True)
        (skill / "targets" / "codex").mkdir(parents=True, exist_ok=True)
        (skill / "common" / "SKILL.md").write_text(f"{skill_id} {{AGENT}}\n", encoding="utf-8")
        (skill / "targets" / "codex" / "openai.yaml").write_text("model: gpt\n", encoding="utf-8")
        (skill / "playbook.yaml").write_text(
            f"""
id: {skill_id}
version: 0.1.0
agents: [codex]
compatibility:
  env_types: [{env_type}]
  env_subtypes: [{env_subtype}]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        return skill

    def test_parse_repo_source_with_ref_and_path(self):
        src = self.manager.parse_source("gh:sima-neat/skillset/sima-cli@v1.0.0")
        self.assertEqual(src.scheme, "gh")
        self.assertEqual(src.repo_owner, "sima-neat")
        self.assertEqual(src.repo_name, "skillset")
        self.assertEqual(src.path, "sima-cli")
        self.assertEqual(src.ref, "v1.0.0")

    def test_parse_repo_source_accepts_trailing_slash(self):
        gh_src = self.manager.parse_source("gh:lihyin/skills/")
        self.assertEqual(gh_src.scheme, "gh")
        self.assertEqual(gh_src.repo_owner, "lihyin")
        self.assertEqual(gh_src.repo_name, "skills")
        self.assertEqual(gh_src.path, "")
        self.assertEqual(gh_src.ref, "main")

        bb_src = self.manager.parse_source("bb:lihyin/skills/")
        self.assertEqual(bb_src.scheme, "bb")
        self.assertEqual(bb_src.repo_owner, "lihyin")
        self.assertEqual(bb_src.repo_name, "skills")
        self.assertEqual(bb_src.path, "")
        self.assertEqual(bb_src.ref, "main")

    def test_repo_clone_url_normalizes_github_token_prefix(self):
        src = self.manager.parse_source("gh:sima-neat/skillset")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "Bearer ghp_abc123"}):
            clone_url = self.manager._repo_clone_url(src)
        self.assertIn("x-access-token:ghp_abc123@", clone_url)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "token ghp_abc123"}):
            clone_url = self.manager._repo_clone_url(src)
        self.assertIn("x-access-token:ghp_abc123@", clone_url)

    @patch("sima_cli.playbooks.manager.shutil.which", return_value="/usr/bin/git")
    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    @patch("sima_cli.playbooks.manager.subprocess.run")
    def test_install_from_github_subfolder_uses_git_checkout(self, mock_run, _mock_env, _mock_which):
        repo_root = self.root / "repo-src"
        skill_path = self._create_skill(repo_root, skill_name="skills")

        def _run_side_effect(cmd, capture_output=True, text=True, check=False):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if len(cmd) >= 6 and cmd[0] == "git" and cmd[3] == "rev-parse":
                result.stdout = "a1b2c3d\n"
                return result
            if len(cmd) >= 6 and cmd[0] == "git" and cmd[3] == "log":
                result.stdout = "2026-04-21T10:11:12Z\n"
                return result
            # Create cloned tree when clone command is issued.
            if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "clone":
                clone_target = Path(cmd[-1])
                clone_target.mkdir(parents=True, exist_ok=True)
                target_skill = clone_target / "skills"
                target_skill.mkdir(parents=True, exist_ok=True)
                for entry in skill_path.iterdir():
                    if entry.is_dir():
                        import shutil

                        shutil.copytree(entry, target_skill / entry.name)
                    else:
                        import shutil

                        shutil.copy2(entry, target_skill / entry.name)
            return result

        mock_run.side_effect = _run_side_effect
        installed = self.manager.install("gh:sima-neat/core/skills")
        self.assertEqual(installed, ["netops"])
        entry = self.registry.get_skill("netops")
        self.assertEqual(entry.get("scm_short_hash"), "a1b2c3d")
        self.assertEqual(entry.get("scm_published_at"), "2026-04-21T10:11:12Z")

    @patch("sima_cli.playbooks.manager.shutil.which", return_value="/usr/bin/git")
    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    @patch("sima_cli.playbooks.manager.subprocess.run")
    def test_install_from_github_repo_root_recursively_discovers_skills(
        self, mock_run, _mock_env, _mock_which
    ):
        repo_root = self.root / "repo-src"
        self._create_nested_skill(repo_root, "sima/onnx-afe-pipeline", "onnx-afe-pipeline")
        self._create_nested_skill(repo_root, "sima/tools/calibration", "calibration-tools")
        self._create_nested_skill(
            repo_root, "sima/internal/linux-only", "linux-only-skill", env_subtype="linux"
        )

        def _run_side_effect(cmd, capture_output=True, text=True, check=False):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "clone":
                clone_target = Path(cmd[-1])
                clone_target.mkdir(parents=True, exist_ok=True)
                for entry in repo_root.iterdir():
                    if entry.is_dir():
                        import shutil

                        shutil.copytree(entry, clone_target / entry.name)
                    else:
                        import shutil

                        shutil.copy2(entry, clone_target / entry.name)
            return result

        mock_run.side_effect = _run_side_effect
        installed = self.manager.install("gh:lihyin/skills/")
        self.assertEqual(set(installed), {"onnx-afe-pipeline", "calibration-tools"})

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_install_local_skill_applies_agent_overlays(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        skill_path = self._create_skill(source_root)

        installed = self.manager.install(str(skill_path))
        self.assertEqual(installed, ["netops"])

        codex_dest = self.codex_home / "skills" / "netops"
        claude_dest = self.claude_home / "skills" / "netops"

        self.assertTrue((codex_dest / "SKILL.md").exists())
        self.assertTrue((codex_dest / "openai.yaml").exists())
        self.assertFalse((codex_dest / "anthropic.yaml").exists())

        self.assertTrue((claude_dest / "SKILL.md").exists())
        self.assertTrue((claude_dest / "anthropic.yaml").exists())
        self.assertFalse((claude_dest / "openai.yaml").exists())

        skill_entry = self.registry.get_skill("netops")
        self.assertEqual(skill_entry["version"], "1.2.3")
        self.assertEqual(set(skill_entry["agents"]), {"codex", "claude"})

        rendered = (codex_dest / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("codex", rendered)

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_install_local_rule_to_sima_storage(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        rule_path = self._create_rule(source_root)
        os.environ["SIMA_CLI_HOME"] = str(self.root / "sima-home")

        installed = self.manager.install(str(rule_path))
        self.assertEqual(installed, ["repo-defaults"])

        rule_dest = Path(os.environ["SIMA_CLI_HOME"]) / "playbooks" / "rules" / "repo-defaults"
        self.assertTrue((rule_dest / "AGENTS.md").exists())
        self.assertTrue((rule_dest / "playbook.yaml").exists())

        rule_entry = self.registry.get_skill("repo-defaults")
        self.assertEqual(rule_entry["type"], "rule")
        self.assertEqual(rule_entry["installed_paths"]["rules"], str(rule_dest))

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "linux"))
    def test_install_skips_incompatible_skill(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        skill_path = self._create_skill(source_root)

        with self.assertRaisesRegex(Exception, "No compatible skills"):
            self.manager.install(str(skill_path))

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_install_skips_skill_with_invalid_markdown_frontmatter(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        skill_path = self._create_skill(source_root)
        (skill_path / "common" / "SKILL.md").write_text(
            "---\nname: netops\nargument-hint: [FOO=bar] [BAZ=qux]\n---\n\nBody\n",
            encoding="utf-8",
        )

        installed = self.manager.install(str(skill_path))
        self.assertEqual(installed, [])
        self.assertFalse((self.codex_home / "skills" / "netops").exists())
        skips = self.manager.last_install_skips()
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0]["id"], "netops")
        self.assertIn("invalid YAML frontmatter", skips[0]["reason"])
        self.assertEqual(
            self.manager.last_install_summary(),
            {"detected": 1, "valid": 0, "discarded": 1},
        )

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_install_summary_counts_detected_valid_and_discarded(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        valid_skill = self._create_nested_skill(source_root, "sima/valid", "valid-skill")
        invalid_skill = self._create_nested_skill(source_root, "sima/invalid", "invalid-skill")
        (invalid_skill / "common" / "SKILL.md").write_text(
            "---\nname: invalid-skill\nargument-hint: [BROKEN=a] [BROKEN=b]\n---\n\nBody\n",
            encoding="utf-8",
        )

        installed = self.manager.install(str(source_root))

        self.assertEqual(installed, ["valid-skill"])
        self.assertEqual(
            self.manager.last_install_summary(),
            {"detected": 2, "valid": 1, "discarded": 1},
        )

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_update_reinstalls_from_registry_source(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        skill_path = self._create_skill(source_root)

        self.manager.install(str(skill_path))

        # Change source skill and run update.
        (skill_path / "common" / "SKILL.md").write_text("Updated {{AGENT}}\n", encoding="utf-8")
        self.manager.update("netops")

        codex_dest = self.codex_home / "skills" / "netops" / "SKILL.md"
        self.assertIn("Updated codex", codex_dest.read_text(encoding="utf-8"))

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_update_skips_skill_when_markdown_becomes_invalid(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        skill_path = self._create_skill(source_root)
        self.manager.install(str(skill_path))

        (skill_path / "common" / "SKILL.md").write_text(
            "---\nname: netops\nargument-hint: [BROKEN=a] [BROKEN=b]\n---\n\nBody\n",
            encoding="utf-8",
        )
        updated = self.manager.update("netops")

        self.assertEqual(updated, [])
        skips = self.manager.last_update_skips()
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0]["id"], "netops")
        self.assertIn("invalid YAML frontmatter", skips[0]["reason"])

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_describe_returns_human_readable_yaml_and_skill_content(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        skill_path = self._create_skill(source_root)
        self.manager.install(str(skill_path))

        details = self.manager.describe("netops")

        self.assertIn("id: netops", details["manifest_yaml"])
        self.assertIn("version: 1.2.3", details["manifest_yaml"])
        self.assertIn("Common codex", details["skill_markdown"])

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_describe_rule_returns_agents_markdown(self, _mock_env):
        source_root = self.root / "source"
        source_root.mkdir(parents=True)
        rule_path = self._create_rule(source_root)
        os.environ["SIMA_CLI_HOME"] = str(self.root / "sima-home")
        self.manager.install(str(rule_path))

        details = self.manager.describe("repo-defaults")
        self.assertEqual(details["document_name"], "AGENTS.md")
        self.assertIn("AGENTS.md", details["document_markdown"])

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_update_skips_when_remote_hash_unchanged_for_scm_skill(self, _mock_env):
        self.registry.set_skill(
            "onnx-afe-pipeline",
            {
                "id": "onnx-afe-pipeline",
                "source": "gh:paconsultings/skills/sima/onnx-afe-pipeline@v0.1.1-test",
                "scm_short_hash": "99cdb21",
                "installed_paths": {},
            },
        )

        with patch.object(self.manager, "_resolve_remote_scm_short_hash", return_value="99cdb21"), patch.object(
            self.manager, "install"
        ) as mock_install:
            updated = self.manager.update("onnx-afe-pipeline")

        self.assertEqual(updated, [])
        mock_install.assert_not_called()

    @patch("sima_cli.playbooks.manager.get_environment_type", return_value=("host", "mac"))
    def test_update_reinstalls_when_remote_hash_changes_for_scm_skill(self, _mock_env):
        self.registry.set_skill(
            "onnx-afe-pipeline",
            {
                "id": "onnx-afe-pipeline",
                "source": "gh:paconsultings/skills/sima/onnx-afe-pipeline@v0.1.1-test",
                "scm_short_hash": "99cdb21",
                "installed_paths": {},
            },
        )

        with patch.object(self.manager, "_resolve_remote_scm_short_hash", return_value="aabbccd"), patch.object(
            self.manager, "install"
        ) as mock_install:
            updated = self.manager.update("onnx-afe-pipeline")

        self.assertEqual(updated, ["onnx-afe-pipeline"])
        mock_install.assert_called_once_with(
            "gh:paconsultings/skills/sima/onnx-afe-pipeline@v0.1.1-test",
            force=True,
        )


if __name__ == "__main__":
    unittest.main()
