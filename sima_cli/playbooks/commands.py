import click
from datetime import datetime, timezone
from pathlib import Path
import re
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
import subprocess

from .manager import SkillError, SkillManager


def _playbook_kind(item_type: str) -> str:
    normalized = str(item_type or "skill").strip().lower()
    return "rule" if normalized in {"rule", "rules", "policy", "policies"} else "skill"


def _print_validation_warning(console: Console, action: str, sid: str, reason: str) -> None:
    message = f"Skipped playbook {action} for {sid}: {reason}"
    console.print(
        Panel(
            Text(message, style="yellow"),
            border_style="yellow",
            title="[yellow]Validation Warning[/yellow]",
        )
    )


def _humanize_elapsed(iso_dt: str) -> str:
    if not iso_dt or iso_dt == "unknown":
        return "unknown"
    try:
        normalized = iso_dt.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt.astimezone(timezone.utc)
        seconds = max(0, int(delta.total_seconds()))
        hours = seconds // 3600
        days = seconds // 86400
        weeks = seconds // (86400 * 7)
        years = seconds // (86400 * 365)
        if hours < 24:
            unit, value = "hour", max(1, hours)
        elif days < 7:
            unit, value = "day", days
        elif weeks < 52:
            unit, value = "week", weeks
        else:
            unit, value = "year", years
        suffix = "" if value == 1 else "s"
        return f"{value} {unit}{suffix} ago"
    except Exception:
        return "unknown"


def _resolve_repo_root() -> Path:
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise click.ClickException(
            "Current directory is not inside a git repository. "
            "playbooks apply only works on git-tracked repositories."
        ) from None
    return Path(root)


def _build_managed_rule_block(rule_id: str, entry: dict, body: str) -> str:
    source = str(entry.get("source") or "unknown").replace('"', "'")
    version = str(entry.get("version") or "unknown").replace('"', "'")
    commit = str(entry.get("scm_short_hash") or "-").replace('"', "'")
    applied_at = datetime.now(timezone.utc).isoformat().replace('"', "'")
    start = (
        f'<!-- SIMA-PLAYBOOK:START id="{rule_id}" type="rule" '
        f'source="{source}" version="{version}" commit="{commit}" applied_at="{applied_at}" | '
        "DO NOT EDIT THIS LINE -->"
    )
    end = f'<!-- SIMA-PLAYBOOK:END id="{rule_id}" | DO NOT EDIT THIS LINE -->'
    content = body.rstrip()
    return f"{start}\n{content}\n{end}\n"


def _upsert_managed_rule_block(existing: str, rule_id: str, block: str) -> str:
    pattern = re.compile(
        rf'<!-- SIMA-PLAYBOOK:START id="{re.escape(rule_id)}".*?-->\n.*?\n<!-- SIMA-PLAYBOOK:END id="{re.escape(rule_id)}".*?-->\n?',
        flags=re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(block, existing, count=1)
        return updated if updated.endswith("\n") else f"{updated}\n"

    base = existing.rstrip()
    if not base:
        return block
    return f"{base}\n\n{block}"


def _select_rule_from_installed(entries: dict) -> str:
    rule_ids = sorted(k for k, v in entries.items() if _playbook_kind(v.get("type", "skill")) == "rule")
    if not rule_ids:
        raise click.ClickException("No installed rule playbooks were found.")
    try:
        from InquirerPy import inquirer
    except ImportError:
        click.echo("Available installed rule playbooks:")
        for idx, rid in enumerate(rule_ids, start=1):
            click.echo(f"  {idx}. {rid}")
        click.echo("  0. Cancel")
        choice = click.prompt("Select a rule to apply", type=click.IntRange(0, len(rule_ids)), default=0)
        if choice == 0:
            click.echo("❌ Apply cancelled")
            raise click.Abort()
        return rule_ids[choice - 1]

    choices = [{"name": rid, "value": rid} for rid in rule_ids]
    choices.append({"name": "❌ Cancel", "value": "cancel"})
    try:
        selected = inquirer.select(
            message="Select a rule playbook to apply:",
            choices=choices,
            default=rule_ids[0],
        ).execute()
    except KeyboardInterrupt:
        click.echo("❌ Apply cancelled")
        raise click.Abort() from None

    if selected == "cancel":
        click.echo("❌ Apply cancelled")
        raise click.Abort()
    return selected


@click.group(name="playbooks", help="Install and manage playbooks (Codex/Claude).")
def playbook_group():
    pass


@playbook_group.command("install")
@click.argument("source")
@click.option("--force", is_flag=True, help="Overwrite an already-installed playbook with the same id.")
def install_skills(source: str, force: bool):
    """
    Install one or more playbooks from SOURCE.

    SOURCE can be:
      - Local folder/archive path
      - http(s) archive URL
      - gh:owner/repo[/path][@ref]
      - bb:owner/repo[/path][@ref]
      - art:https://...
    """
    mgr = SkillManager()
    try:
        installed = mgr.install(source, force=force)
    except SkillError as exc:
        raise click.ClickException(str(exc)) from exc

    console = Console()
    for skipped in mgr.last_install_skips():
        sid = skipped.get("id", "unknown")
        reason = skipped.get("reason", "invalid markdown")
        _print_validation_warning(console, "install", sid, reason)

    installed_entries = mgr.list_installed()
    for sid in installed:
        payload = installed_entries.get(sid, {})
        kind = _playbook_kind(payload.get("type", "skill"))
        click.secho(f"✅ Installed playbook ({kind}): {sid}", fg="green")

    summary = mgr.last_install_summary()
    summary_table = Table(title="Install Summary")
    summary_table.add_column("Metric", style="bold cyan")
    summary_table.add_column("Count", style="green")
    summary_table.add_row("detected", str(summary.get("detected", 0)))
    summary_table.add_row("valid", str(summary.get("valid", 0)))
    summary_table.add_row(
        "[red]discarded[/red]",
        f"[red]{summary.get('discarded', 0)}[/red]",
    )
    console.print(summary_table)


@playbook_group.command("list")
def list_skills():
    """List installed playbooks."""
    mgr = SkillManager()
    entries = mgr.list_installed()

    console = Console()
    table = Table(title="SiMa.ai Playbooks")
    table.add_column("Playbook", style="bold cyan")
    table.add_column("Kind", style="blue")
    table.add_column("Version", style="green")
    table.add_column("Agents", style="yellow")
    table.add_column("Source", style="white")
    table.add_column("Commit", style="magenta")
    table.add_column("Published", style="white")
    table.add_column("Updated on this machine", style="dim")

    if not entries:
        console.print("[yellow]No playbooks installed.[/yellow]")
        return

    for skill_id, payload in sorted(entries.items()):
        kind = _playbook_kind(payload.get("type", "skill"))
        table.add_row(
            skill_id,
            kind,
            str(payload.get("version", "")),
            ", ".join(payload.get("agents", [])),
            str(payload.get("source", "")),
            str(payload.get("scm_short_hash") or "-"),
            str(payload.get("scm_published_at") or "-"),
            str(payload.get("updated_at", "")),
        )

    console.print(table)


def _run_delete_playbook(kit_id: str, remove_all: bool, yes: bool):
    mgr = SkillManager()
    if remove_all and kit_id:
        raise click.ClickException("Specify either KIT_ID or --all, not both.")
    if not remove_all and not kit_id:
        raise click.ClickException("Provide KIT_ID or use --all.")

    if remove_all:
        entries = mgr.list_installed()
        if not entries:
            click.echo("ℹ️  No playbooks installed.")
            return
        if not yes:
            confirmed = click.confirm(
                "Delete all installed playbooks from all installed locations?",
                default=False,
            )
            if not confirmed:
                click.echo("ℹ️  Delete cancelled.")
                return
        removed = 0
        for sid in sorted(entries.keys()):
            mgr.uninstall(sid)
            click.echo(f"✅ Removed playbook: {sid}")
            removed += 1
        click.echo(f"✅ Removed {removed} playbooks.")
        return

    if not yes:
        confirmed = click.confirm(
            f"Delete playbook '{kit_id}' from all installed locations?",
            default=False,
        )
        if not confirmed:
            click.echo("ℹ️  Delete cancelled.")
            return
    try:
        mgr.uninstall(kit_id)
    except SkillError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"✅ Removed playbook: {kit_id}")


@playbook_group.command("delete")
@click.argument("kit_id", required=False)
@click.option("--all", "remove_all", is_flag=True, help="Remove all installed playbooks.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
def delete_skill(kit_id: str, remove_all: bool, yes: bool):
    """Delete one installed playbook by id, or all with --all."""
    _run_delete_playbook(kit_id, remove_all, yes)


@playbook_group.command("remove")
@click.argument("kit_id", required=False)
@click.option("--all", "remove_all", is_flag=True, help="Remove all installed playbooks.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
def remove_skill(kit_id: str, remove_all: bool, yes: bool):
    """Alias for delete."""
    _run_delete_playbook(kit_id, remove_all, yes)


@playbook_group.command("update")
@click.argument("kit_id", required=False)
@click.option("--skills", "only_skills", is_flag=True, help="Update only skill playbooks.")
@click.option("--rules", "only_rules", is_flag=True, help="Update only rule playbooks.")
def update_skills(kit_id: str, only_skills: bool, only_rules: bool):
    """Update one installed playbook, or all if KIT_ID is omitted."""
    mgr = SkillManager()
    all_skips = []

    if not only_skills and not only_rules:
        try:
            updated = mgr.update(kit_id)
        except SkillError as exc:
            raise click.ClickException(str(exc)) from exc
        all_skips = mgr.last_update_skips()
    else:
        entries = mgr.list_installed()
        selected_kinds = set()
        if only_skills:
            selected_kinds.add("skill")
        if only_rules:
            selected_kinds.add("rule")

        candidate_ids = [kit_id] if kit_id else sorted(entries.keys())
        filtered_ids = [
            sid
            for sid in candidate_ids
            if sid in entries and _playbook_kind(entries[sid].get("type", "skill")) in selected_kinds
        ]

        if kit_id and not filtered_ids:
            click.echo(f"ℹ️  Playbook '{kit_id}' does not match the requested kind filter.")
            return
        if not kit_id and not filtered_ids:
            click.echo("ℹ️  No playbooks match the requested kind filter.")
            return

        updated = []
        for sid in filtered_ids:
            try:
                result = mgr.update(sid)
            except SkillError as exc:
                raise click.ClickException(str(exc)) from exc
            if isinstance(result, list):
                updated.extend(result)
            all_skips.extend(mgr.last_update_skips())
        # Preserve order while deduplicating
        updated = list(dict.fromkeys(updated))

    console = Console()
    for skipped in all_skips:
        sid = skipped.get("id") or skipped.get("target") or "unknown"
        reason = skipped.get("reason", "invalid markdown")
        _print_validation_warning(console, "update", sid, reason)

    if not updated:
        click.echo("ℹ️  No playbook update needed.")
        return

    for sid in updated:
        click.echo(f"✅ Updated playbook: {sid}")


@playbook_group.command("describe")
@click.argument("kit_id")
def describe_skill(kit_id: str):
    """Show an installed playbook's manifest and document content."""
    mgr = SkillManager()
    try:
        details = mgr.describe(kit_id)
    except SkillError as exc:
        raise click.ClickException(str(exc)) from exc

    entry = details["entry"]
    console = Console()
    console.print(f"[bold cyan]{entry.get('name') or kit_id}[/bold cyan] ({kit_id})")
    console.print(f"Version: {entry.get('version', '')}")
    console.print(f"Agents: {', '.join(entry.get('agents', []))}")
    console.print()
    console.print("[bold]Manifest (YAML)[/bold]")
    console.print(Syntax(details["manifest_yaml"], "yaml", word_wrap=True))
    doc_name = details.get("document_name") or "SKILL.md"
    doc_body = details.get("document_markdown") or details.get("skill_markdown") or ""
    console.print(f"[bold]{doc_name}[/bold]")
    console.print(doc_body)


@playbook_group.command("apply")
@click.argument("kit_id", required=False)
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
def apply_rule(kit_id: str, yes: bool):
    """Apply an installed rule playbook to the current git repository."""
    mgr = SkillManager()
    entries = mgr.list_installed()
    selected_id = kit_id or _select_rule_from_installed(entries)

    entry = entries.get(selected_id)
    if not entry:
        raise click.ClickException(f"Playbook '{selected_id}' is not installed.")
    if _playbook_kind(entry.get("type", "skill")) != "rule":
        raise click.ClickException(f"Playbook '{selected_id}' is not a rule and cannot be applied.")

    details = mgr.describe(selected_id)
    body = details.get("document_markdown") or details.get("skill_markdown") or ""
    if not body.strip():
        raise click.ClickException(f"Playbook '{selected_id}' has no AGENTS.md content to apply.")

    repo_root = _resolve_repo_root()
    target = repo_root / "AGENTS.md"
    source = str(entry.get("source") or "unknown")
    version = str(entry.get("version") or "unknown")
    published = str(entry.get("scm_published_at") or "unknown")
    published_age = _humanize_elapsed(published)

    console = Console()
    metadata_text = (
        "Apply rule playbook:\n"
        f"  id: {selected_id}\n"
        f"  source: {source}\n"
        f"  version: {version}\n"
        f"  published: {published} ({published_age})\n"
        f"  target: {target}"
    )
    console.print(
        Panel(
            Text(metadata_text, style="green"),
            border_style="green",
            title="[green]Rule Apply Preview[/green]",
        )
    )
    if not yes and not click.confirm("Proceed with applying this rule to AGENTS.md?", default=False):
        click.echo("❌ Apply cancelled")
        return

    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    block = _build_managed_rule_block(selected_id, entry, body)
    updated = _upsert_managed_rule_block(existing, selected_id, block)
    target.write_text(updated, encoding="utf-8")
    click.secho(f"✅ Applied rule playbook: {selected_id}", fg="green")
    click.echo(f"📄 Updated file: {target}")


def register_playbook_commands(main):
    main.add_command(playbook_group)


# Backward-compatible internal alias
register_agent_kit_commands = register_playbook_commands
