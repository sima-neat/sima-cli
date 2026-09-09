"""SDK-owned version pins for optional browser VS Code extensions."""

import json
import re
import shlex
import subprocess
from contextlib import contextmanager
from typing import Dict, List, Mapping

from InquirerPy import inquirer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


SDK_EXTENSION_MANIFEST = "/etc/sima-neat/vscode-extensions.json"
# Compatibility fallback for SDK images released before the manifest contract.
DEFAULT_EXTENSION_VERSIONS = {
    "openai.chatgpt": "26.5825.51511",
    "anthropic.claude-code": "2.1.266",
}
_EXTENSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*\.[A-Za-z0-9][A-Za-z0-9-]*")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")


EXTENSION_CHOICES = (
    {"name": "Neat Extension", "value": "neat"},
    {"name": "Codex Extension", "value": "codex"},
    {"name": "Claude Extension", "value": "claude"},
)


def select_browser_extensions(auto_install: bool, allow_prompt: bool) -> List[str]:
    """Choose optional extensions without prompting in automation mode."""
    if not auto_install and not allow_prompt:
        return []
    print("Neat Extension adds Neat SDK tools to VS Code, while Codex Extension "
          "and Claude Extension provide AI coding assistance.")
    if auto_install:
        return [choice["value"] for choice in EXTENSION_CHOICES]
    return inquirer.checkbox(
        message="Select VS Code extensions to install:",
        choices=[dict(choice) for choice in EXTENSION_CHOICES],
        instruction="Space to select, Enter to confirm; leave all unchecked to skip",
    ).execute() or []


@contextmanager
def extension_install_progress(labels: List[str], console: Console):
    """Keep interactive installs visibly active without inventing percentages."""
    if not console.is_interactive:
        yield
        return
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}", markup=False),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        refresh_per_second=4,
    ) as progress:
        progress.add_task("Installing " + ", ".join(labels) + " extensions", total=None)
        yield


def load_extension_versions(container: str) -> Dict[str, str]:
    """Use SDK pins when present; only a missing file permits fallback pins."""
    script = (
        "import pathlib, sys\n"
        "try:\n"
        "    print(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "except FileNotFoundError:\n"
        "    sys.exit(44)\n"
    )
    result = subprocess.run(
        ["docker", "exec", container, "python3", "-c", script, SDK_EXTENSION_MANIFEST],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode == 44:
        print("ℹ️  SDK extension manifest is absent; using sima-cli compatibility pins.")
        return dict(DEFAULT_EXTENSION_VERSIONS)
    if result.returncode != 0:
        raise ValueError(f"Cannot read {SDK_EXTENSION_MANIFEST}: {result.stderr.strip()}")
    try:
        manifest = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid JSON in {SDK_EXTENSION_MANIFEST}: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        raise ValueError(f"{SDK_EXTENSION_MANIFEST} must declare schema_version 1")
    versions = manifest.get("extensions")
    if not isinstance(versions, dict):
        raise ValueError(f"{SDK_EXTENSION_MANIFEST} must contain an extensions object")
    for extension_id in DEFAULT_EXTENSION_VERSIONS:
        version = versions.get(extension_id)
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError(f"{SDK_EXTENSION_MANIFEST} needs an exact version for {extension_id}")
    print(f"ℹ️  Using SDK extension pins from {SDK_EXTENSION_MANIFEST}.")
    return {extension_id: versions[extension_id] for extension_id in DEFAULT_EXTENSION_VERSIONS}


def resolve_extension_target(target: str, versions: Mapping[str, str]) -> str:
    """Allow exact overrides, resolving bare known IDs through the SDK pins."""
    target = target.strip()
    if not target:
        return ""
    extension_id, separator, version = target.partition("@")
    if not _EXTENSION_ID.fullmatch(extension_id):
        raise ValueError(f"Invalid VS Code extension ID: {extension_id!r}")
    if not separator:
        version = versions.get(extension_id, "")
    if not _VERSION.fullmatch(version):
        raise ValueError(f"Use an exact publisher.extension@version for {target!r}")
    return f"{extension_id}@{version}"


def extension_install_command(server: str, extensions_dir: str, label: str, target: str) -> str:
    """Skip only an exact installed version; the CLI handles upgrades/downgrades."""
    command = f"{shlex.quote(server)} --extensions-dir {shlex.quote(extensions_dir)}"
    return (
        f"echo {shlex.quote(f'Installing {label} extension: {target}')}; "
        f"if {command} --list-extensions --show-versions | grep -Fxi -- {shlex.quote(target)} >/dev/null; then "
        f"echo {shlex.quote(f'{label} extension already installed: {target}')}; "
        "else "
        "neat_extension_attempt=1; "
        "while :; do "
        f"if {command} --install-extension {shlex.quote(target)} --force --accept-server-license-terms; then "
        "break; "
        "else neat_extension_status=$?; fi; "
        "if [ \"$neat_extension_attempt\" -ge 3 ]; then "
        f"echo {shlex.quote(f'Failed to install {label} extension {target} after 3 attempts.')} >&2; "
        "exit \"$neat_extension_status\"; "
        "fi; "
        f"echo {shlex.quote(f'Retrying {label} extension {target} after installation failure...')}; "
        "sleep \"$((neat_extension_attempt * 2))\"; "
        "neat_extension_attempt=$((neat_extension_attempt + 1)); "
        "done; "
        "fi"
    )


# OpenVSCode 1.109.5 skips an exact-version CLI install when that version already
# exists, even with --force, leaving an existing unpinned extension unpinned.
# Its per-extension auto-update pin lives in the profile's extensions.json.
# Verify installation and atomically set that metadata without disabling updates
# for unrelated extensions. Run as the mapped user, before restarting the server.
PIN_EXTENSIONS_SCRIPT = """\
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

profile = Path(sys.argv[1]) / 'extensions.json'
requested = json.loads(sys.argv[2])
if not requested:
    sys.exit(0)
entries = json.loads(profile.read_text(encoding='utf-8'))
changed = False
for extension_id, version in requested.items():
    matches = [entry for entry in entries
               if entry['identifier']['id'].lower() == extension_id.lower()]
    if len(matches) != 1 or matches[0]['version'] != version:
        raise RuntimeError('Installed extension does not match requested pin: '
                           + extension_id + '@' + version)
    metadata = matches[0].setdefault('metadata', {})
    if metadata.get('pinned') is not True:
        metadata['pinned'] = True
        changed = True
if changed:
    fd, temporary = tempfile.mkstemp(prefix='.sima-cli-pins-', dir=str(profile.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), stat.S_IMODE(profile.stat().st_mode))
            json.dump(entries, handle, indent=2)
            handle.write('\\n')
        os.replace(temporary, profile)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
for extension_id, version in requested.items():
    print('Verified pinned extension: ' + extension_id + '@' + version)
"""


def pin_extensions_command(extensions_dir: str, targets: Mapping[str, str]) -> str:
    """Build the verification command for the selected exact version targets."""
    return " ".join(shlex.quote(arg) for arg in (
        "python3", "-c", PIN_EXTENSIONS_SCRIPT, extensions_dir, json.dumps(dict(targets)),
    ))
