from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from InquirerPy import inquirer


ENV_BASE_URLS = {
    "dev": "https://artifacts.neat.paconsultings.com",
    "staging": "https://artifacts.stg.neat.sima.ai",
    "production": "https://artifacts.neat.sima.ai",
}

DEFAULT_REPOSITORIES = [
    "apps",
    "core",
    "insight",
    "internals",
    "llima",
    "sima-cli",
]

DEFAULT_METADATA_FILENAME = "metadata.json"


class VulcanArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadResult:
    environment: str
    base_url: str
    repository: str
    ref: str
    ref_key: str
    latest_tag: str
    manifest_url: str
    output_dir: Path
    files: Tuple[Path, ...]


@dataclass(frozen=True)
class InstallMetadataResult:
    environment: str
    base_url: str
    repository: str
    ref: str
    ref_key: str
    requested_spec: str
    resolved_spec: str
    metadata_url: str


class ArtifactClient:
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()

    def read_bytes(self, url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
        try:
            response = self.session.get(url, timeout=60, headers=headers)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise VulcanArtifactError(f"GET {url} failed: {exc}") from exc

    def read_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        return self.read_bytes(url, headers=headers).decode("utf-8")

    def read_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        try:
            return json.loads(self.read_text(url, headers=headers))
        except json.JSONDecodeError as exc:
            raise VulcanArtifactError(f"GET {url} returned invalid JSON: {exc}") from exc


def normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise VulcanArtifactError("Artifact base URL is empty.")
    return value


def join_url(base_url: str, *parts: str) -> str:
    base = normalize_base_url(base_url)
    encoded_parts = [
        urllib.parse.quote(part.strip("/"), safe="/._-+~")
        for part in parts
        if part.strip("/")
    ]
    return "/".join([base, *encoded_parts])


def ref_key(ref: str) -> str:
    value = ref.strip()
    if not value:
        raise VulcanArtifactError("Branch or tag is empty.")
    return urllib.parse.quote(value, safe="")


def repository_choices() -> List[str]:
    configured = os.environ.get("SIMA_VULCAN_REPOS", "").strip()
    if configured:
        return sorted({item.strip() for item in configured.split(",") if item.strip()})
    return sorted(DEFAULT_REPOSITORIES)


def load_branch_choices(client: ArtifactClient, base_url: str, repository: str) -> List[Dict[str, str]]:
    branches_url = join_url(base_url, repository, "branches.json")
    payload = client.read_json(branches_url)
    branches = payload.get("branches")
    if not isinstance(branches, list):
        raise VulcanArtifactError(f"{branches_url} does not contain a branches list.")

    normalized = []
    for item in branches:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        key = str(item.get("key", "")).strip() or ref_key(name)
        if name:
            normalized.append({"name": name, "key": key})

    if not normalized:
        raise VulcanArtifactError(f"{branches_url} does not list any branches.")
    return sorted(normalized, key=lambda item: item["name"].lower())


def select_from_menu(title: str, choices: Sequence[str]) -> str:
    if not choices:
        raise VulcanArtifactError(f"No choices available for {title}.")
    if not sys.stdin.isatty():
        raise VulcanArtifactError(f"{title} was not specified and stdin is not interactive.")

    menu_choices = list(choices) + ["Cancel"]
    try:
        selected = inquirer.fuzzy(
            message=f"Select {title.lower()}:",
            choices=menu_choices,
            max_height="70%",
            instruction="(Type or use ↑↓)",
            qmark="👉",
        ).execute()
    except KeyboardInterrupt as exc:
        raise VulcanArtifactError("Selection cancelled.") from exc

    if selected in {None, "Cancel"}:
        raise VulcanArtifactError("Selection cancelled.")
    return str(selected)


def resolve_repository(repository: Optional[str]) -> str:
    if repository:
        return repository.strip()
    return select_from_menu("Repositories", repository_choices())


def resolve_ref(
    client: ArtifactClient,
    base_url: str,
    repository: str,
    requested_ref: Optional[str],
) -> Tuple[str, str]:
    if requested_ref:
        value = requested_ref.strip()
        return value, ref_key(value)

    branches = load_branch_choices(client, base_url, repository)
    selected = select_from_menu("Branches", [item["name"] for item in branches])
    for item in branches:
        if item["name"] == selected:
            return item["name"], item["key"]
    raise VulcanArtifactError(f"Selected branch was not found: {selected}")


def read_latest_tag(client: ArtifactClient, base_url: str, repository: str, key: str) -> str:
    latest_url = join_url(base_url, repository, key, "latest.tag")
    latest_tag = client.read_text(latest_url).strip()
    if not latest_tag:
        raise VulcanArtifactError(f"{latest_url} is empty.")
    return latest_tag


def github_ref_short_sha(client: ArtifactClient, repository: str, ref: str) -> str:
    repo_part = urllib.parse.quote(repository.strip(), safe="")
    ref_part = urllib.parse.quote(ref.strip(), safe="")
    url = f"https://api.github.com/repos/sima-neat/{repo_part}/commits/{ref_part}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = client.read_json(url, headers=headers)
    if not isinstance(payload, dict):
        raise VulcanArtifactError(f"{url} did not return a JSON object.")

    sha = str(payload.get("sha", "")).strip()
    if not sha:
        raise VulcanArtifactError(f"{url} did not include a commit sha.")
    return sha[:12]


def parse_install_target(target: str) -> Tuple[str, str, str]:
    value = target.strip()
    if not value:
        raise VulcanArtifactError("Install target is empty.")

    repository, separator, ref_spec = value.partition("@")
    repository = repository.strip()
    if not repository:
        raise VulcanArtifactError("Install target repository is empty.")

    if not separator or not ref_spec.strip():
        return repository, "main", "latest"

    ref_spec = ref_spec.strip().strip("/")
    if not ref_spec:
        return repository, "main", "latest"

    if ":" in ref_spec:
        ref, spec = ref_spec.rsplit(":", 1)
        ref = ref.strip()
        spec = spec.strip()
        if not ref or not spec:
            raise VulcanArtifactError(
                "Install target must use repo@branch:spec when specifying both branch and spec."
            )
        return repository, ref, spec

    if ref_spec == "latest" or _looks_like_commit_spec(ref_spec):
        return repository, "main", ref_spec

    return repository, ref_spec, "latest"


def _looks_like_commit_spec(value: str) -> bool:
    if not 7 <= len(value) <= 40:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def metadata_filename(package_type: Optional[str] = None) -> str:
    if package_type is None:
        return DEFAULT_METADATA_FILENAME
    normalized = package_type.strip()
    if not normalized:
        return DEFAULT_METADATA_FILENAME
    if not all(char.isalnum() or char in "._-" for char in normalized):
        raise VulcanArtifactError("metadata type must contain only letters, numbers, dots, underscores, or hyphens")
    return f"metadata-{normalized}.json"


def resolve_install_metadata_url(
    *,
    environment: str,
    target: str,
    base_url: Optional[str] = None,
    package_type: Optional[str] = None,
    client: Optional[ArtifactClient] = None,
) -> InstallMetadataResult:
    client = client or ArtifactClient()
    resolved_base_url = normalize_base_url(base_url or ENV_BASE_URLS[environment])
    repository, ref_name, requested_spec = parse_install_target(target)
    key = ref_key(ref_name)
    if requested_spec == "latest":
        try:
            resolved_spec = read_latest_tag(client, resolved_base_url, repository, key)
        except VulcanArtifactError:
            if ref_name == "main":
                raise
            resolved_spec = github_ref_short_sha(client, repository, ref_name)
    else:
        resolved_spec = requested_spec
    metadata_url = join_url(resolved_base_url, repository, key, resolved_spec, metadata_filename(package_type))
    return InstallMetadataResult(
        environment=environment,
        base_url=resolved_base_url,
        repository=repository,
        ref=ref_name,
        ref_key=key,
        requested_spec=requested_spec,
        resolved_spec=resolved_spec,
        metadata_url=metadata_url,
    )


def read_manifest(client: ArtifactClient, base_url: str, repository: str, key: str) -> Tuple[str, Dict[str, Any]]:
    manifest_url = join_url(base_url, repository, key, "manifest.json")
    payload = client.read_json(manifest_url)
    if not isinstance(payload, dict):
        raise VulcanArtifactError(f"{manifest_url} did not return a JSON object.")
    return manifest_url, payload


def manifest_artifacts(manifest: Dict[str, Any], patterns: Sequence[str]) -> List[Dict[str, Any]]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise VulcanArtifactError("manifest.json does not contain an artifacts list.")

    selected = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        key = str(item.get("s3_key", "")).strip()
        if not path or not key:
            continue
        if patterns and not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            continue
        selected.append(item)

    if not selected:
        detail = f" matching {', '.join(patterns)}" if patterns else ""
        raise VulcanArtifactError(f"manifest.json does not contain downloadable artifacts{detail}.")
    return selected


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(raw: str) -> Path:
    rel_path = Path(raw)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise VulcanArtifactError(f"Unsafe artifact path in manifest: {rel_path}")
    return rel_path


def download_artifacts(
    client: ArtifactClient,
    base_url: str,
    manifest: Dict[str, Any],
    artifacts: Iterable[Dict[str, Any]],
    output_dir: Path,
) -> List[Path]:
    written = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for artifact in artifacts:
        rel_path = _safe_relative_path(str(artifact["path"]))
        url = join_url(base_url, str(artifact["s3_key"]))
        destination = output_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(client.read_bytes(url))

        expected_sha = str(artifact.get("sha256", "")).strip()
        if expected_sha:
            actual_sha = sha256_file(destination)
            if actual_sha != expected_sha:
                destination.unlink(missing_ok=True)
                raise VulcanArtifactError(
                    f"SHA256 mismatch for {destination}: expected {expected_sha}, got {actual_sha}"
                )

        expected_size = artifact.get("size")
        if isinstance(expected_size, int) and expected_size >= 0:
            actual_size = destination.stat().st_size
            if actual_size != expected_size:
                destination.unlink(missing_ok=True)
                raise VulcanArtifactError(
                    f"Size mismatch for {destination}: expected {expected_size}, got {actual_size}"
                )

        written.append(destination)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    written.append(manifest_path)
    return written


def write_latest_tag(output_dir: Path, latest_tag: str) -> Path:
    path = output_dir / "latest.tag"
    path.write_text(latest_tag + "\n", encoding="utf-8")
    return path


def warn_manifest_mismatch(manifest: Dict[str, Any], latest_tag: str) -> Optional[str]:
    commit = str(manifest.get("commit", "")).strip()
    if commit and not commit.startswith(latest_tag):
        return f"latest.tag ({latest_tag}) does not match manifest commit ({commit})."
    return None


def result_to_json(result: DownloadResult) -> Dict[str, Any]:
    return {
        "environment": result.environment,
        "base_url": result.base_url,
        "repository": result.repository,
        "ref": result.ref,
        "ref_key": result.ref_key,
        "latest_tag": result.latest_tag,
        "manifest_url": result.manifest_url,
        "output_dir": str(result.output_dir),
        "files": [str(path) for path in result.files],
    }


def download_vulcan_artifacts(
    *,
    environment: str,
    repository: Optional[str],
    ref: Optional[str],
    output: str,
    artifact_patterns: Sequence[str] = (),
    base_url: Optional[str] = None,
    client: Optional[ArtifactClient] = None,
) -> Tuple[DownloadResult, Optional[str]]:
    client = client or ArtifactClient()
    resolved_base_url = normalize_base_url(base_url or ENV_BASE_URLS[environment])
    resolved_repository = resolve_repository(repository)
    ref_name, key = resolve_ref(client, resolved_base_url, resolved_repository, ref)
    latest_tag = read_latest_tag(client, resolved_base_url, resolved_repository, key)
    manifest_url, manifest = read_manifest(client, resolved_base_url, resolved_repository, key)
    warning = warn_manifest_mismatch(manifest, latest_tag)

    output_root = Path(output).expanduser()
    output_dir = output_root / environment / resolved_repository / key / latest_tag
    artifacts = manifest_artifacts(manifest, artifact_patterns)
    files = download_artifacts(client, resolved_base_url, manifest, artifacts, output_dir)
    files.append(write_latest_tag(output_dir, latest_tag))

    return (
        DownloadResult(
            environment=environment,
            base_url=resolved_base_url,
            repository=resolved_repository,
            ref=ref_name,
            ref_key=key,
            latest_tag=latest_tag,
            manifest_url=manifest_url,
            output_dir=output_dir,
            files=tuple(files),
        ),
        warning,
    )
