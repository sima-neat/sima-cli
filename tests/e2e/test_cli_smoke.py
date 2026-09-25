import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.local_only]

ROOT = Path(__file__).resolve().parents[2]


def run_cli(tmp_path, *args):
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "SIMA_CLI_HOME": str(tmp_path / "sima-home"),
            "SIMA_CLI_CHECK_FOR_UPDATE": "0",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PYTHONPATH": str(ROOT),
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "sima_cli", *args],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_module_entrypoint_help_and_version(tmp_path):
    help_result = run_cli(tmp_path, "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "SiMa Developer Portal CLI Tool" in help_result.stdout
    assert "Commands:" in help_result.stdout

    version_result = run_cli(tmp_path, "--version")
    assert version_result.returncode == 0, version_result.stderr
    assert version_result.stdout.startswith("SiMa CLI version:")


def test_packages_build_writes_metadata(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (artifacts / "payload.txt").write_text("payload\n", encoding="utf-8")

    result = run_cli(
        tmp_path,
        "packages",
        "build",
        str(artifacts),
        "--name",
        "demo-package",
        "--version",
        "0.1.0",
        "--description",
        "Local e2e package fixture",
        "--install-script",
        "install.sh",
    )

    assert result.returncode == 0, result.stderr
    metadata_path = artifacts / "metadata.json"
    assert metadata_path.exists()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["name"] == "demo-package"
    assert metadata["version"] == "0.1.0"
    assert metadata["resources"] == ["install.sh", "payload.txt"]
    assert metadata["installation"]["script"] == "./install.sh"


def test_playbooks_install_from_local_skill_fixture(tmp_path):
    source = tmp_path / "playbooks" / "demo-skill"
    (source / "common").mkdir(parents=True)
    (source / "common" / "SKILL.md").write_text("Demo skill for {{AGENT}}\n", encoding="utf-8")
    (source / "playbook.yaml").write_text(
        """
id: demo-skill
version: 0.1.0
agents: [codex]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = run_cli(tmp_path, "playbooks", "install", str(source))

    assert result.returncode == 0, result.stderr
    assert "Installed playbook (skill): demo-skill" in result.stdout

    registry_path = tmp_path / "sima-home" / "playbooks" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert "demo-skill" in registry["skills"]

    installed_skill = tmp_path / "home" / ".codex" / "skills" / "demo-skill" / "SKILL.md"
    assert installed_skill.read_text(encoding="utf-8") == "Demo skill for codex\n"
