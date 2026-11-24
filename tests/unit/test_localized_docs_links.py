import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALIZED_DOCS = REPO_ROOT / "docs" / "i18n"
MARKDOWN_LINK = re.compile(r"]\(([^)#?]+\.md)(?:[?#][^)]*)?\)")


def test_localized_command_links_resolve_to_generated_docs():
    command_links = []
    broken_links = []

    for source in LOCALIZED_DOCS.rglob("*.md"):
        for target in MARKDOWN_LINK.findall(source.read_text(encoding="utf-8")):
            if "sima-cli/commands/" not in target:
                continue
            command_links.append((source, target))
            if not (source.parent / target).resolve().is_file():
                broken_links.append(f"{source.relative_to(REPO_ROOT)}: {target}")

    assert command_links, "expected translated pages to link to generated command docs"
    assert not broken_links, "broken translated command links:\n" + "\n".join(broken_links)


def test_localized_index_pages_list_every_command_the_english_index_lists():
    def commands(page):
        return sorted(
            Path(target).name
            for target in MARKDOWN_LINK.findall(page.read_text(encoding="utf-8"))
            if "commands/" in target
        )

    english = commands(REPO_ROOT / "docs" / "sima-cli" / "index.md")
    translated = sorted(LOCALIZED_DOCS.glob("*/sima-cli/index.md"))
    assert translated, "expected translated index pages"
    for page in translated:
        assert commands(page) == english, page.relative_to(REPO_ROOT)
