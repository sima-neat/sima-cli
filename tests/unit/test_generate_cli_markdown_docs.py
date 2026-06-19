from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import click

from scripts import generate_cli_markdown_docs as docs_generator


def _command_docs():
    root = click.Group(help="Root command.")
    child = click.Command("child", help="Child command.")
    root.add_command(child)
    root_doc = docs_generator.CommandDoc(["sima-cli"], root, None)
    child_doc = docs_generator.CommandDoc(["sima-cli", "child"], child, ["sima-cli"])
    return [root_doc, child_doc]


def test_write_docs_preserves_manual_guides_and_links_them_from_index():
    with TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "docs" / "sima-cli"
        manual_dir = output_dir / "sdk-networking"
        manual_dir.mkdir(parents=True)
        manual_page = manual_dir / "index.md"
        manual_page.write_text(
            "# Neat SDK Networking Setup\n\n"
            "Understand SDK, Docker, Insight, and DevKit networking.\n",
            encoding="utf-8",
        )

        with patch.object(docs_generator, "collect_commands", return_value=_command_docs()), patch.object(
            docs_generator,
            "read_readme_section",
            return_value="## Installation\n\nInstall sima-cli.",
        ):
            docs_generator.write_docs(output_dir)

        assert manual_page.exists()
        index_text = (output_dir / "index.md").read_text(encoding="utf-8")
        assert "## Guides" in index_text
        assert "[Neat SDK Networking Setup](sdk-networking/index.md)" in index_text
        assert "Understand SDK, Docker, Insight, and DevKit networking." in index_text


def test_write_docs_preserves_existing_guides_section_verbatim():
    with TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "docs" / "sima-cli"
        output_dir.mkdir(parents=True)
        guides_section = (
            "## Guides\n\n"
            "| Guide | Description |\n"
            "| --- | --- |\n"
            "| [Custom Guide](custom/index.md) | Keep this hand-written description. |\n"
        )
        (output_dir / "index.md").write_text(
            "# Existing\n\n"
            "## Installation\n\n"
            "Old install text.\n\n"
            f"{guides_section}\n"
            "## Top-Level Commands\n\n"
            "Old generated content.\n",
            encoding="utf-8",
        )

        with patch.object(docs_generator, "collect_commands", return_value=_command_docs()), patch.object(
            docs_generator,
            "read_readme_section",
            return_value="## Installation\n\nInstall sima-cli.",
        ):
            docs_generator.write_docs(output_dir)

        index_text = (output_dir / "index.md").read_text(encoding="utf-8")
        assert guides_section.strip() in index_text


def test_compare_dirs_ignores_manual_extra_files():
    with TemporaryDirectory() as tmpdir:
        expected = Path(tmpdir) / "expected"
        actual = Path(tmpdir) / "actual"
        (expected / "commands").mkdir(parents=True)
        (actual / "commands").mkdir(parents=True)
        (actual / "sdk-networking").mkdir(parents=True)

        (expected / "index.md").write_text("generated index\n", encoding="utf-8")
        (expected / "commands" / "sima-cli.md").write_text("generated command\n", encoding="utf-8")
        (actual / "index.md").write_text("generated index\n", encoding="utf-8")
        (actual / "commands" / "sima-cli.md").write_text("generated command\n", encoding="utf-8")
        (actual / "sdk-networking" / "index.md").write_text("manual guide\n", encoding="utf-8")

        assert docs_generator.compare_dirs(expected, actual) == []
