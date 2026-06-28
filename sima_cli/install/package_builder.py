import datetime
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from sima_cli.install.compatibility import build_platform_specs


METADATA_FILENAME = "metadata.json"
DEFAULT_POST_MESSAGE = "[bold]Package installed successfully.[/bold]\n"


def _run_git(args: List[str], cwd: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _git_root(search_dir: Path) -> Optional[Path]:
    output = _run_git(["rev-parse", "--show-toplevel"], search_dir)
    if not output:
        return None
    return Path(output).resolve()


def _github_repo_from_remote(remote_url: str) -> Optional[Tuple[str, str]]:
    value = remote_url.strip()
    if value.endswith(".git"):
        value = value[:-4]

    if value.startswith("git@github.com:"):
        value = value[len("git@github.com:"):]
    elif value.startswith("https://github.com/"):
        value = value[len("https://github.com/"):]
    elif value.startswith("http://github.com/"):
        value = value[len("http://github.com/"):]
    else:
        return None

    parts = value.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def resolve_git_context(artifacts_folder: Path) -> Tuple[Optional[Path], Optional[Tuple[str, str]]]:
    for search_dir in (Path.cwd(), artifacts_folder):
        root = _git_root(search_dir)
        if not root:
            continue
        remote = _run_git(["remote", "get-url", "origin"], root) or ""
        return root, _github_repo_from_remote(remote)
    return None, None


def default_package_name(artifacts_folder: Path) -> str:
    git_root, github_repo = resolve_git_context(artifacts_folder)
    if github_repo:
        owner, repo = github_repo
        return "gh:{}/{}".format(owner, repo)
    if git_root:
        return git_root.name
    return Path.cwd().name


def default_version(artifacts_folder: Path) -> str:
    git_root, _github_repo = resolve_git_context(artifacts_folder)
    if git_root:
        tag = _run_git(["describe", "--tags", "--exact-match"], git_root)
        if tag:
            return tag
        commit = _run_git(["rev-parse", "--short", "HEAD"], git_root)
        if commit:
            return commit
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")


def github_repo_description(artifacts_folder: Path) -> str:
    _git_root, github_repo = resolve_git_context(artifacts_folder)
    if not github_repo:
        return ""

    owner, repo = github_repo
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer {}".format(token)

    try:
        response = requests.get(
            "https://api.github.com/repos/{}/{}".format(owner, repo),
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        description = response.json().get("description")
    except Exception:
        return ""
    return description if isinstance(description, str) else ""


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _format_size(total_bytes: int) -> str:
    units = (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024))
    if total_bytes <= 0:
        return "0KB"
    for suffix, factor in units:
        if total_bytes >= factor:
            value = total_bytes / factor
            text = "{:.1f}".format(value).rstrip("0").rstrip(".")
            return "{}{}".format(text, suffix)
    return "1KB"


def _matches_exclude_pattern(resource: str, patterns: Sequence[str]) -> bool:
    filename = Path(resource).name
    for pattern in patterns:
        if not pattern:
            continue
        if pattern.startswith(".") and "/" not in pattern and "\\" not in pattern:
            if filename.endswith(pattern):
                return True
            continue
        if pattern in resource or pattern in filename:
            return True
    return False


def collect_artifact_resources(
    artifacts_folder: Path,
    exclude_patterns: Optional[Sequence[str]] = None,
) -> Tuple[List[str], Dict[str, Path], int]:
    resources = []
    resource_paths = {}
    total_size = 0
    normalized_excludes = [pattern.strip() for pattern in (exclude_patterns or []) if pattern.strip()]

    for path in sorted(artifacts_folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(artifacts_folder).as_posix()
        if rel == METADATA_FILENAME or re.fullmatch(r"metadata-[A-Za-z0-9_.-]+\.json", rel):
            continue
        if _matches_exclude_pattern(rel, normalized_excludes):
            continue
        resources.append(rel)
        resource_paths[rel] = path
        total_size += path.stat().st_size

    return resources, resource_paths, total_size


def parse_selectables(selectables: Optional[str]) -> List[Tuple[str, str]]:
    if not selectables:
        return []

    parsed = []
    for raw_item in selectables.split(";"):
        item = raw_item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError("selectables entries must use 'name:file' format")
        name, resource = item.split(":", 1)
        name = name.strip()
        resource = resource.strip()
        if not name or not resource:
            raise ValueError("selectables entries must include both name and file")
        parsed.append((name, resource))
    return parsed


def build_selectable_resources(
    selectable_specs: List[Tuple[str, str]],
    resource_paths: Dict[str, Path],
) -> List[Dict[str, str]]:
    selectable_resources = []
    seen_resources = set()

    for name, resource in selectable_specs:
        if resource not in resource_paths:
            raise ValueError("selectable resource is not in artifacts-folder: {}".format(resource))
        if resource in seen_resources:
            raise ValueError("duplicate selectable resource: {}".format(resource))
        seen_resources.add(resource)
        selectable_resources.append({
            "name": name,
            "url": "",
            "resource": resource,
            "checksum": _sha256_file(resource_paths[resource]),
        })

    return selectable_resources


def resolve_install_script(artifacts_folder: Path, install_script: str) -> str:
    script = install_script.strip()
    if not script:
        raise ValueError("install-script cannot be empty")

    candidate_paths = []
    raw_path = Path(script).expanduser()
    if raw_path.is_absolute():
        candidate_paths.append(raw_path)
    else:
        candidate_paths.append(artifacts_folder / raw_path)
        candidate_paths.append(Path.cwd() / raw_path)

    artifacts_root = artifacts_folder.resolve()
    for candidate in candidate_paths:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        try:
            rel = resolved.relative_to(artifacts_root).as_posix()
        except ValueError:
            continue
        return "./{}".format(rel)

    return script


def build_metadata(
    artifacts_folder: Path,
    name: Optional[str] = None,
    version: Optional[str] = None,
    description: Optional[str] = None,
    install_script: str = "",
    selectables: Optional[str] = None,
    exclude: Optional[Sequence[str]] = None,
    download_compatible_files_only: bool = False,
    host_platforms: Optional[Sequence[str]] = None,
    host_arches: Optional[Sequence[str]] = None,
    board_platforms: Optional[Sequence[str]] = None,
    palette_platform: Optional[str] = None,
) -> Dict:
    artifacts_folder = artifacts_folder.expanduser().resolve()
    if not artifacts_folder.is_dir():
        raise ValueError("artifacts-folder is not a directory: {}".format(artifacts_folder))

    resources, resource_paths, total_size = collect_artifact_resources(artifacts_folder, exclude_patterns=exclude)
    if not resources:
        raise ValueError("artifacts-folder does not contain any artifact files")

    selectable_specs = parse_selectables(selectables)
    selectable_resources = build_selectable_resources(selectable_specs, resource_paths)
    selectable_resource_names = {entry["resource"] for entry in selectable_resources}
    resources = [resource for resource in resources if resource not in selectable_resource_names]
    checksums = {
        resource: _sha256_file(path)
        for resource, path in resource_paths.items()
        if resource not in selectable_resource_names
    }

    resolved_description = description
    if resolved_description is None:
        resolved_description = github_repo_description(artifacts_folder)

    size_text = _format_size(total_size)
    metadata = {
        "name": name or default_package_name(artifacts_folder),
        "version": version or default_version(artifacts_folder),
        "release": "",
        "description": resolved_description or "",
        "platforms": build_platform_specs(
            host_platforms=host_platforms,
            host_arches=host_arches,
            board_platforms=board_platforms,
            palette_platform=palette_platform,
        ),
        "resources": resources,
        "resources-checksum": checksums,
        "selectable-resources": selectable_resources,
        "size": {
            "download": size_text,
            "install": size_text,
        },
        "installation": {
            "script": resolve_install_script(artifacts_folder, install_script),
            "post-message": DEFAULT_POST_MESSAGE,
        },
    }
    if download_compatible_files_only:
        metadata["download-compatible-files-only"] = True
    return metadata


def metadata_filename(variant: Optional[str] = None) -> str:
    if variant is None:
        return METADATA_FILENAME

    normalized = variant.strip()
    if not normalized:
        return METADATA_FILENAME
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", normalized):
        raise ValueError("variant must contain only letters, numbers, dots, underscores, or hyphens")
    return "metadata-{}.json".format(normalized)


def write_metadata(artifacts_folder: Path, metadata: Dict, variant: Optional[str] = None) -> Path:
    output_path = artifacts_folder.expanduser().resolve() / metadata_filename(variant)
    output_path.write_text(json.dumps(metadata, indent=4) + "\n", encoding="utf-8")
    return output_path
