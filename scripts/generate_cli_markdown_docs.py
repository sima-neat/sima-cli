#!/usr/bin/env python3
"""Generate Markdown reference documentation for sima-cli commands."""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import click


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "sima-cli"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class CommandDoc:
    path: List[str]
    command: click.Command
    parent_path: Optional[List[str]]

    @property
    def full_name(self) -> str:
        return " ".join(self.path)

    @property
    def slug(self) -> str:
        return slugify(self.full_name)

    @property
    def filename(self) -> str:
        return f"{self.slug}.md"


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "command"


def markdown_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def first_help_line(command: click.Command) -> str:
    help_text = (command.help or "").strip()
    if not help_text:
        return ""
    return help_text.splitlines()[0].strip()


def make_context(path: List[str], command: click.Command) -> click.Context:
    from sima_cli.cli import main

    root_ctx = click.Context(main, info_name="sima-cli")
    if len(path) == 1:
        return root_ctx

    parent = root_ctx
    current: click.Command = main
    for name in path[1:-1]:
        if not isinstance(current, click.Group):
            break
        current = current.get_command(parent, name)
        parent = click.Context(current, info_name=name, parent=parent)

    return click.Context(command, info_name=path[-1], parent=parent)


def command_children(doc: CommandDoc) -> List[CommandDoc]:
    command = doc.command
    if not isinstance(command, click.Group):
        return []

    ctx = make_context(doc.path, command)
    children: List[CommandDoc] = []
    for name in command.list_commands(ctx):
        child = command.get_command(ctx, name)
        if child is None or getattr(child, "hidden", False):
            continue
        children.append(CommandDoc(doc.path + [name], child, doc.path))
    return children


def collect_commands() -> List[CommandDoc]:
    from sima_cli.cli import main

    root = CommandDoc(["sima-cli"], main, None)
    docs: List[CommandDoc] = []
    queue = [root]
    while queue:
        doc = queue.pop(0)
        docs.append(doc)
        queue.extend(command_children(doc))
    return docs


def argument_description(param: click.Argument) -> str:
    if param.type is click.UNPROCESSED and param.nargs == -1:
        return (
            "Optional passthrough command and arguments. All remaining tokens are joined "
            "and executed inside the selected SDK container with `bash -lc`; if omitted, "
            "sima-cli opens an interactive login shell."
        )
    return ""


def render_param_table(params: Iterable[click.Parameter], param_type: type) -> str:
    rows = []
    for param in params:
        if not isinstance(param, param_type):
            continue
        if isinstance(param, click.Option):
            name = ", ".join(param.opts + param.secondary_opts)
            description = getattr(param, "help", "") or ""
        else:
            name = param.human_readable_name
            description = argument_description(param)
        details = []
        if getattr(param, "required", False):
            details.append("required")
        if isinstance(param, click.Argument) and param.nargs == -1:
            details.append("accepts zero or more values")
        elif isinstance(param, click.Argument) and param.nargs != 1:
            details.append(f"accepts {param.nargs} values")
        if isinstance(param, click.Option) and param.show_default and param.default not in (None, False):
            details.append(f"default: {param.default}")
        if details:
            description = f"{description} ({'; '.join(details)})".strip()
        rows.append((name, description))

    if not rows:
        return "None.\n"

    text = "| Name | Description |\n| --- | --- |\n"
    for name, description in rows:
        text += f"| `{markdown_escape(name)}` | {markdown_escape(description)} |\n"
    return text


def relative_link(from_doc: CommandDoc, to_doc: CommandDoc) -> str:
    if from_doc.filename == to_doc.filename:
        return f"./{to_doc.filename}"
    return f"./{to_doc.filename}"


def render_command_page(doc: CommandDoc, docs_by_path: dict[str, CommandDoc]) -> str:
    ctx = make_context(doc.path, doc.command)
    summary = first_help_line(doc.command)
    children = command_children(doc)

    lines = [
        f"# `{doc.full_name}`",
        "",
    ]
    if summary:
        lines.extend([summary, ""])

    if doc.parent_path:
        parent_key = " ".join(doc.parent_path)
        parent = docs_by_path[parent_key]
        lines.extend([f"Parent command: [`{parent.full_name}`]({relative_link(doc, parent)})", ""])

    lines.extend([
        "## Usage",
        "",
        "```bash",
        ctx.get_usage().replace("Usage: ", "", 1).strip(),
        "```",
        "",
        "## Options",
        "",
        render_param_table(doc.command.params, click.Option).rstrip(),
        "",
        "## Arguments",
        "",
        render_param_table(doc.command.params, click.Argument).rstrip(),
        "",
    ])

    if children:
        lines.extend(["## Subcommands", ""])
        for child in children:
            child_summary = first_help_line(child.command)
            lines.append(f"- [`{child.full_name}`]({relative_link(doc, child)}): {child_summary}")
        lines.append("")

    lines.extend([
        "## Full Help",
        "",
        "```text",
        doc.command.get_help(ctx).rstrip(),
        "```",
        "",
    ])

    return "\n".join(lines)


def render_index(docs: List[CommandDoc]) -> str:
    root = docs[0]
    top_level = [doc for doc in docs if doc.parent_path == root.path]

    lines = [
        "# sima-cli Command Reference",
        "",
        "Generated Markdown reference documentation for the sima-cli command line interface.",
        "",
        "## Top-Level Commands",
        "",
        "| Command | Description |",
        "| --- | --- |",
    ]
    for doc in top_level:
        lines.append(
            f"| [`{doc.full_name}`](commands/{doc.filename}) | {markdown_escape(first_help_line(doc.command))} |"
        )

    lines.extend([
        "",
        "## Complete Command List",
        "",
    ])
    for doc in docs:
        lines.append(f"- [`{doc.full_name}`](commands/{doc.filename})")
    lines.append("")
    return "\n".join(lines)


def write_docs(output_dir: Path) -> None:
    docs = collect_commands()
    docs_by_path = {doc.full_name: doc for doc in docs}
    commands_dir = output_dir / "commands"

    if output_dir.exists():
        shutil.rmtree(output_dir)
    commands_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "index.md").write_text(render_index(docs), encoding="utf-8")
    for doc in docs:
        (commands_dir / doc.filename).write_text(render_command_page(doc, docs_by_path), encoding="utf-8")


def compare_dirs(expected: Path, actual: Path) -> List[str]:
    diffs: List[str] = []
    expected_files = sorted(path.relative_to(expected) for path in expected.rglob("*") if path.is_file())
    actual_files = sorted(path.relative_to(actual) for path in actual.rglob("*") if path.is_file())
    if expected_files != actual_files:
        diffs.append(f"Expected files {expected_files}, found {actual_files}")
        return diffs

    for rel_path in expected_files:
        expected_text = (expected / rel_path).read_text(encoding="utf-8").splitlines(keepends=True)
        actual_text = (actual / rel_path).read_text(encoding="utf-8").splitlines(keepends=True)
        if expected_text != actual_text:
            diff = "".join(
                difflib.unified_diff(
                    actual_text,
                    expected_text,
                    fromfile=str(rel_path),
                    tofile=f"generated/{rel_path}",
                )
            )
            diffs.append(diff)
    return diffs


def check_docs(output_dir: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="sima-cli-docs-") as tmp:
        generated = Path(tmp) / "docs"
        write_docs(generated)
        diffs = compare_dirs(generated, output_dir)
    if not diffs:
        print(f"Markdown CLI docs are up to date: {output_dir}")
        return 0
    print("Markdown CLI docs are out of date. Run:", file=sys.stderr)
    print(f"  python {Path(__file__).relative_to(ROOT)} --output {output_dir.relative_to(ROOT)}", file=sys.stderr)
    print("", file=sys.stderr)
    for diff in diffs[:5]:
        print(diff, file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Defaults to {DEFAULT_OUTPUT_DIR.relative_to(ROOT)}.",
    )
    parser.add_argument("--check", action="store_true", help="Fail if generated docs differ from files on disk.")
    args = parser.parse_args()

    output_dir = args.output
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    if args.check:
        return check_docs(output_dir)

    write_docs(output_dir)
    print(f"Generated Markdown CLI docs under {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
