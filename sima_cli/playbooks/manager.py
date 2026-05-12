from datetime import datetime, timezone
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

import requests
import yaml

from sima_cli.__version__ import __version__
from sima_cli.utils.env import get_environment_type


PLAYBOOK_MANIFEST_CANDIDATES = [
    "playbook.yaml",
    "playbook.yml",
]

SKILL_MANIFEST_CANDIDATES = [
    "sima-skill.yaml",
    "sima-skill.yml",
    "skill.yaml",
    "skill.yml",
]

RULE_MANIFEST_CANDIDATES = [
    "sima-rule.yaml",
    "sima-rule.yml",
    "rule.yaml",
    "rule.yml",
]

MANIFEST_CANDIDATES = [
    *PLAYBOOK_MANIFEST_CANDIDATES,
    *SKILL_MANIFEST_CANDIDATES,
    *RULE_MANIFEST_CANDIDATES,
    "manifest.json",
]


@dataclass
class SourceRef:
    raw: str
    scheme: str
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    path: str = ""
    ref: str = "main"
    url: Optional[str] = None
    scm_short_hash: Optional[str] = None
    scm_published_at: Optional[str] = None


class SkillError(RuntimeError):
    pass


class SkillRegistry:
    def __init__(self, registry_path: Optional[Path] = None):
        base = Path(os.environ.get("SIMA_CLI_HOME", str(Path.home() / ".sima-cli")))
        if registry_path is None:
            self.registry_path = base / "playbooks" / "registry.json"
            legacy_registry_path = base / "skills" / "registry.json"
            if not self.registry_path.exists() and legacy_registry_path.exists():
                self.registry_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_registry_path, self.registry_path)
        else:
            self.registry_path = registry_path
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> Dict[str, dict]:
        if not self.registry_path.exists():
            return {"skills": {}}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SkillError(f"Skills registry is corrupted: {self.registry_path}") from exc
        if not isinstance(data, dict):
            return {"skills": {}}
        if "skills" not in data or not isinstance(data["skills"], dict):
            data["skills"] = {}
        return data

    def save(self) -> None:
        self.registry_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def set_skill(self, skill_id: str, payload: dict) -> None:
        self._data["skills"][skill_id] = payload
        self.save()

    def remove_skill(self, skill_id: str) -> bool:
        if skill_id not in self._data["skills"]:
            return False
        del self._data["skills"][skill_id]
        self.save()
        return True

    def get_skill(self, skill_id: str) -> Optional[dict]:
        return self._data["skills"].get(skill_id)

    def all_skills(self) -> Dict[str, dict]:
        return dict(self._data["skills"])


class SkillManager:
    def __init__(self, registry: Optional[SkillRegistry] = None):
        self.registry = registry or SkillRegistry()
        self._last_install_skips: List[dict] = []
        self._last_update_skips: List[dict] = []
        self._last_install_summary: Dict[str, int] = {
            "detected": 0,
            "valid": 0,
            "discarded": 0,
        }

    def list_installed(self) -> Dict[str, dict]:
        return self.registry.all_skills()

    def last_install_skips(self) -> List[dict]:
        return list(self._last_install_skips)

    def last_update_skips(self) -> List[dict]:
        return list(self._last_update_skips)

    def last_install_summary(self) -> Dict[str, int]:
        return dict(self._last_install_summary)

    def describe(self, skill_id: str) -> dict:
        entry = self.registry.get_skill(skill_id)
        if not entry:
            raise SkillError(f"Skill '{skill_id}' is not installed.")

        manifest = self._load_installed_manifest(entry, skill_id)
        item_type = str(entry.get("type") or manifest.get("type") or "skill").strip().lower()
        document_name, document_markdown = self._load_installed_document(entry, item_type)
        if document_markdown is None:
            expected = "AGENTS.md" if item_type == "rule" else "SKILL.md"
            raise SkillError(
                f"Skill '{skill_id}' is installed but {expected} is missing from installed paths."
            )

        manifest_yaml = yaml.safe_dump(
            manifest,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        return {
            "entry": entry,
            "manifest": manifest,
            "manifest_yaml": manifest_yaml,
            "document_name": document_name,
            "document_markdown": document_markdown,
            # Backward compatibility for existing command code.
            "skill_markdown": document_markdown,
        }

    def parse_source(self, source: str) -> SourceRef:
        src = source.strip()

        local_path = Path(src).expanduser()
        if local_path.exists():
            return SourceRef(raw=source, scheme="local", path=str(local_path.resolve()))

        if src.startswith("gh:"):
            return self._parse_repo_source(src, "gh")
        if src.startswith("bb:"):
            return self._parse_repo_source(src, "bb")
        if src.startswith("art:"):
            url = src[4:]
            if not url.startswith(("http://", "https://")):
                raise SkillError("art: source must include full http(s) URL")
            return SourceRef(raw=source, scheme="art", url=url)

        if src.startswith(("http://", "https://")):
            return SourceRef(raw=source, scheme="http", url=src)

        raise SkillError(
            "Unsupported source. Use local path, http(s) URL, or gh:/bb:/art: spec."
        )

    def _parse_repo_source(self, source: str, scheme: str) -> SourceRef:
        payload = source[len(scheme) + 1 :]
        if not payload:
            raise SkillError(f"Invalid {scheme}: source format")

        if "@" in payload:
            pre, ref = payload.rsplit("@", 1)
            ref = ref.strip() or "main"
        else:
            pre, ref = payload, "main"

        parts = [p for p in pre.split("/") if p]
        if len(parts) < 2:
            raise SkillError(f"Invalid {scheme}: source. Expected '{scheme}:owner/repo[/path][@ref]'.")

        owner = parts[0]
        repo = parts[1]
        subpath = "/".join(parts[2:])

        return SourceRef(
            raw=source,
            scheme=scheme,
            repo_owner=owner,
            repo_name=repo,
            path=subpath,
            ref=ref,
        )

    def install(self, source: str, *, force: bool = False, dry_run: bool = False) -> List[str]:
        self._last_install_skips = []
        self._last_install_summary = {"detected": 0, "valid": 0, "discarded": 0}
        parsed = self.parse_source(source)
        with tempfile.TemporaryDirectory(prefix="sima-skills-") as tmp:
            root = self._materialize_source(parsed, Path(tmp))
            skill_roots = self._discover_skill_roots(root)
            if not skill_roots:
                raise SkillError(f"No agent-kit directories found in source: {source}")

            installed_ids: List[str] = []
            env_type, env_subtype = get_environment_type()
            for skill_root in skill_roots:
                manifest = self._load_manifest(skill_root)
                if not self._is_compatible(manifest, env_type, env_subtype):
                    continue
                self._last_install_summary["detected"] += 1
                skill_id = manifest["id"]
                document_error = self._validate_markdown_document(skill_root, manifest)
                if document_error:
                    self._last_install_summary["discarded"] += 1
                    self._last_install_skips.append(
                        {
                            "id": skill_id,
                            "type": manifest.get("type", "skill"),
                            "path": str(skill_root),
                            "reason": document_error,
                        }
                    )
                    continue
                self._last_install_summary["valid"] += 1
                if dry_run:
                    installed_ids.append(skill_id)
                    continue
                if self.registry.get_skill(skill_id) and not force:
                    raise SkillError(
                        f"Skill '{skill_id}' is already installed. Use --force to overwrite."
                    )
                payload = self._install_item(skill_root, manifest, parsed)
                self.registry.set_skill(skill_id, payload)
                installed_ids.append(skill_id)

            if not installed_ids and not dry_run and not self._last_install_skips:
                raise SkillError(
                    "No compatible skills were installed for the current environment."
                )
            return installed_ids

    def uninstall(self, skill_id: str) -> None:
        entry = self.registry.get_skill(skill_id)
        if not entry:
            raise SkillError(f"Skill '{skill_id}' is not installed.")

        for path in self._iter_installed_roots(entry):
            if path.exists() and path.is_dir():
                shutil.rmtree(path)

        self.registry.remove_skill(skill_id)

    def update(self, skill_id: Optional[str] = None) -> List[str]:
        self._last_update_skips = []
        updated: List[str] = []
        installed = self.registry.all_skills()
        targets = [skill_id] if skill_id else list(installed.keys())

        for target in targets:
            entry = installed.get(target)
            if not entry:
                if skill_id:
                    raise SkillError(f"Skill '{skill_id}' is not installed.")
                continue
            src = entry.get("source")
            if not src:
                raise SkillError(f"Skill '{target}' has no source recorded in registry.")
            parsed = self.parse_source(src)

            # For SCM sources, skip reinstall when upstream ref resolves to the
            # same commit hash already installed on this machine.
            if parsed.scheme in {"gh", "bb"}:
                previous_hash = (entry.get("scm_short_hash") or "").strip()
                remote_hash = self._resolve_remote_scm_short_hash(parsed)
                if previous_hash and remote_hash and previous_hash == remote_hash:
                    continue

            installed_ids = self.install(src, force=True)
            if not isinstance(installed_ids, list):
                updated.append(target)
            elif target in installed_ids:
                updated.append(target)
            for skipped in self.last_install_skips():
                skipped_payload = dict(skipped)
                skipped_payload["target"] = target
                self._last_update_skips.append(skipped_payload)

        return updated

    def _materialize_source(self, source: SourceRef, workdir: Path) -> Path:
        if source.scheme == "local":
            local = Path(source.path)
            if local.is_dir():
                return local
            if local.suffix in {".gz", ".tgz", ".tar", ".zip"}:
                return self._extract_archive(local, workdir)
            raise SkillError(f"Unsupported local source type: {local}")

        if source.scheme in {"http", "art"}:
            if not source.url:
                raise SkillError("Missing URL for source")
            archive = self._download(source.url, workdir)
            return self._extract_archive(archive, workdir)

        if source.scheme in {"gh", "bb"}:
            # Prefer direct SCM checkout for repo/subfolder sources.
            if shutil.which("git"):
                return self._materialize_repo_via_git(source, workdir)

            # Fallback to archive mode when git is unavailable.
            url = self._repo_archive_url(source)
            archive = self._download_repo_archive(source, url, workdir)
            extracted = self._extract_archive(archive, workdir)
            if source.path:
                target = extracted / source.path
                if not target.exists() or not target.is_dir():
                    raise SkillError(
                        f"Path '{source.path}' does not exist in {source.scheme}:{source.repo_owner}/{source.repo_name}@{source.ref}"
                    )
                return target
            return extracted

        raise SkillError(f"Unsupported source scheme: {source.scheme}")

    def _download(self, url: str, workdir: Path) -> Path:
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "skills.tar.gz"
        target = workdir / filename

        resp = requests.get(url, stream=True, timeout=30)
        if resp.status_code >= 400:
            raise SkillError(f"Failed to download source ({resp.status_code}): {url}")

        with open(target, "wb") as fp:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fp.write(chunk)

        return target

    def _download_repo_archive(self, source: SourceRef, url: str, workdir: Path) -> Path:
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "skills.tar.gz"
        target = workdir / filename

        token = self._normalize_auth_token(os.getenv("GITHUB_TOKEN", "")) if source.scheme == "gh" else ""
        bb_user = os.getenv("BITBUCKET_USERNAME", "").strip()
        bb_app_password = os.getenv("BITBUCKET_APP_PASSWORD", "").strip()
        bb_token = os.getenv("BITBUCKET_TOKEN", "").strip()

        headers = {}
        auth = None
        if source.scheme == "gh" and token:
            headers["Authorization"] = f"Bearer {token}"
        elif source.scheme == "bb":
            if bb_user and bb_app_password:
                auth = (bb_user, bb_app_password)
            elif bb_token:
                headers["Authorization"] = f"Bearer {bb_token}"

        resp = requests.get(url, headers=headers, auth=auth, stream=True, timeout=30)
        if resp.status_code >= 400:
            raise SkillError(f"Failed to download source ({resp.status_code}): {url}")

        with open(target, "wb") as fp:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fp.write(chunk)

        return target

    def _extract_archive(self, archive_path: Path, workdir: Path) -> Path:
        target_dir = workdir / "src"
        target_dir.mkdir(parents=True, exist_ok=True)

        name = archive_path.name.lower()
        if name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(target_dir)
        elif name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(target_dir)
        else:
            raise SkillError(f"Unsupported archive type: {archive_path}")

        dirs = [p for p in target_dir.iterdir() if p.is_dir()]
        if len(dirs) == 1:
            return dirs[0]
        return target_dir

    def _repo_archive_url(self, source: SourceRef) -> str:
        owner = source.repo_owner
        repo = source.repo_name
        ref = source.ref
        if source.scheme == "gh":
            return f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{ref}"
        if source.scheme == "bb":
            return f"https://bitbucket.org/{owner}/{repo}/get/{ref}.tar.gz"
        raise SkillError(f"Unsupported repo source: {source.scheme}")

    def _repo_clone_url(self, source: SourceRef) -> str:
        owner = source.repo_owner
        repo = source.repo_name
        if source.scheme == "gh":
            token = self._normalize_auth_token(os.getenv("GITHUB_TOKEN", ""))
            if token:
                return f"https://x-access-token:{quote(token, safe='')}@github.com/{owner}/{repo}.git"
            return f"https://github.com/{owner}/{repo}.git"
        if source.scheme == "bb":
            bb_user = os.getenv("BITBUCKET_USERNAME", "").strip()
            bb_app_password = os.getenv("BITBUCKET_APP_PASSWORD", "").strip()
            bb_token = os.getenv("BITBUCKET_TOKEN", "").strip()
            if bb_user and bb_app_password:
                user = quote(bb_user, safe="")
                pwd = quote(bb_app_password, safe="")
                return f"https://{user}:{pwd}@bitbucket.org/{owner}/{repo}.git"
            if bb_token:
                return f"https://x-token-auth:{quote(bb_token, safe='')}@bitbucket.org/{owner}/{repo}.git"
            return f"https://bitbucket.org/{owner}/{repo}.git"
        raise SkillError(f"Unsupported repo source: {source.scheme}")

    def _mask_sensitive_url(self, value: str) -> str:
        parsed = urlsplit(value)
        if "@" not in parsed.netloc:
            return value
        _creds, host = parsed.netloc.rsplit("@", 1)
        return urlunsplit((parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment))

    def _normalize_auth_token(self, token: str) -> str:
        value = (token or "").strip()
        if not value:
            return ""
        lower = value.lower()
        if lower.startswith("bearer "):
            return value.split(" ", 1)[1].strip()
        if lower.startswith("token "):
            return value.split(" ", 1)[1].strip()
        return value

    def _resolve_remote_scm_short_hash(self, source: SourceRef) -> Optional[str]:
        if source.scheme not in {"gh", "bb"}:
            return None
        if not shutil.which("git"):
            return None

        clone_url = self._repo_clone_url(source)
        proc = subprocess.run(
            ["git", "ls-remote", clone_url, source.ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return None

        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            full_hash = line.split()[0].strip()
            if full_hash:
                return full_hash[:7]
        return None

    def _materialize_repo_via_git(self, source: SourceRef, workdir: Path) -> Path:
        repo_dir = workdir / "repo"
        clone_url = self._repo_clone_url(source)
        safe_clone_url = self._mask_sensitive_url(clone_url)

        clone_cmd = ["git", "clone", "--depth", "1", clone_url, str(repo_dir)]
        proc = subprocess.run(clone_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            raise SkillError(f"Failed to clone source repo: {safe_clone_url}\n{stderr}")

        checkout_cmd = ["git", "-C", str(repo_dir), "checkout", source.ref]
        checkout = subprocess.run(checkout_cmd, capture_output=True, text=True)
        if checkout.returncode != 0:
            fetch_cmd = ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", source.ref]
            fetch = subprocess.run(fetch_cmd, capture_output=True, text=True)
            if fetch.returncode != 0:
                stderr = (checkout.stderr or fetch.stderr or "").strip()
                raise SkillError(
                    f"Failed to resolve ref '{source.ref}' in {safe_clone_url}\n{stderr}"
                )
            head_checkout = subprocess.run(
                ["git", "-C", str(repo_dir), "checkout", "FETCH_HEAD"],
                capture_output=True,
                text=True,
            )
            if head_checkout.returncode != 0:
                stderr = (head_checkout.stderr or "").strip()
                raise SkillError(
                    f"Failed to checkout fetched ref '{source.ref}' in {clone_url}\n{stderr}"
                )

        self._hydrate_scm_metadata(source, repo_dir)

        if source.path:
            target = repo_dir / source.path
            if not target.exists() or not target.is_dir():
                raise SkillError(
                    f"Path '{source.path}' does not exist in {source.scheme}:{source.repo_owner}/{source.repo_name}@{source.ref}"
                )
            return target

        return repo_dir

    def _hydrate_scm_metadata(self, source: SourceRef, repo_dir: Path) -> None:
        short_hash = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if short_hash.returncode == 0:
            value = (short_hash.stdout or "").strip()
            if value:
                source.scm_short_hash = value

        published = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            check=False,
        )
        if published.returncode == 0:
            value = (published.stdout or "").strip()
            if value:
                source.scm_published_at = value

    def _discover_skill_roots(self, root: Path) -> List[Path]:
        if not root.exists() or not root.is_dir():
            return []

        if self._looks_like_skill_root(root):
            return [root]

        found: List[Path] = []
        skip_dir_names = {"common", "targets", "agent", "agents", ".git"}
        for current_root, dirnames, _filenames in os.walk(root, topdown=True):
            current = Path(current_root)
            if current == root:
                dirnames[:] = sorted(d for d in dirnames if d not in skip_dir_names)
                continue

            if self._looks_like_skill_root(current):
                found.append(current)
                # Do not descend into a matched skill root.
                dirnames[:] = []
                continue

            dirnames[:] = sorted(d for d in dirnames if d not in skip_dir_names)

        unique: List[Path] = []
        seen = set()
        for path in found:
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _looks_like_skill_root(self, path: Path) -> bool:
        if (path / "SKILL.md").exists() or (path / "AGENTS.md").exists():
            return True
        for candidate in MANIFEST_CANDIDATES:
            if (path / candidate).exists():
                return True
        return False

    def _validate_markdown_document(self, skill_root: Path, manifest: dict) -> Optional[str]:
        item_type = str(manifest.get("type") or "skill").strip().lower()
        if item_type == "rule":
            expected = "AGENTS.md"
            candidates = [skill_root / "AGENTS.md"]
        else:
            expected = "SKILL.md"
            candidates = [skill_root / "SKILL.md", skill_root / "common" / "SKILL.md"]

        doc_path = next((p for p in candidates if p.exists()), None)
        if doc_path is None:
            return f"missing required {expected}"

        content = doc_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None

        match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", content, flags=re.DOTALL)
        if not match:
            return "invalid frontmatter block: missing closing '---'"

        frontmatter = match.group(1)
        try:
            loaded = yaml.safe_load(frontmatter)
        except yaml.YAMLError as exc:
            return f"invalid YAML frontmatter in {expected}: {exc}"
        if loaded is not None and not isinstance(loaded, dict):
            return f"invalid YAML frontmatter in {expected}: expected mapping/object"
        return None

    def _load_manifest(self, skill_root: Path) -> dict:
        manifest = None
        for candidate in MANIFEST_CANDIDATES:
            file_path = skill_root / candidate
            if not file_path.exists():
                continue
            if file_path.suffix in {".yaml", ".yml"}:
                manifest = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            else:
                manifest = json.loads(file_path.read_text(encoding="utf-8"))
            break

        if manifest is None:
            manifest = {}

        manifest_type = str(manifest.get("type") or "").strip().lower()
        if not manifest_type:
            if any((skill_root / c).exists() for c in RULE_MANIFEST_CANDIDATES) or (
                (skill_root / "AGENTS.md").exists() and not (skill_root / "SKILL.md").exists()
            ):
                manifest_type = "rule"
            else:
                manifest_type = "skill"
        if manifest_type not in {"skill", "rule"}:
            raise SkillError(f"Invalid type '{manifest_type}' in {skill_root}")

        skill_id = manifest.get("id") or manifest.get("name") or skill_root.name
        skill_id = re.sub(r"[^a-zA-Z0-9._-]", "-", str(skill_id)).strip("-")
        if not skill_id:
            raise SkillError(f"Invalid skill id for root: {skill_root}")

        version = str(manifest.get("version") or "0.0.0")
        agents = manifest.get("agents") or ["codex", "claude"]
        if isinstance(agents, dict):
            agents = list(agents.keys())
        if not isinstance(agents, list):
            raise SkillError(f"Invalid agents field in {skill_root}")

        normalized = {
            "id": skill_id,
            "type": manifest_type,
            "version": version,
            "name": str(manifest.get("name") or skill_id),
            "description": str(manifest.get("description") or ""),
            "agents": [str(a).lower() for a in agents],
            "compatibility": manifest.get("compatibility") or {},
            "raw": manifest,
        }
        return normalized

    def _is_compatible(self, manifest: dict, env_type: str, env_subtype: str) -> bool:
        comp = manifest.get("compatibility") or {}
        env_types = comp.get("env_types") or comp.get("environments") or []
        env_subtypes = comp.get("env_subtypes") or []

        if env_types and env_type not in env_types:
            return False
        if env_subtypes and env_subtype not in env_subtypes:
            return False

        min_cli = comp.get("min_cli_version")
        max_cli = comp.get("max_cli_version")
        if min_cli and self._cmp_version(__version__, str(min_cli)) < 0:
            return False
        if max_cli and self._cmp_version(__version__, str(max_cli)) > 0:
            return False

        return True

    def _cmp_version(self, lhs: str, rhs: str) -> int:
        def parse(v: str) -> Tuple[int, ...]:
            nums = re.findall(r"\d+", v)
            if not nums:
                return (0,)
            return tuple(int(n) for n in nums)

        l = parse(lhs)
        r = parse(rhs)
        if l < r:
            return -1
        if l > r:
            return 1
        return 0

    def _install_item(self, skill_root: Path, manifest: dict, source: SourceRef) -> dict:
        item_type = str(manifest.get("type") or "skill").strip().lower()
        if item_type == "rule":
            return self._install_rule(skill_root, manifest, source)
        return self._install_skill(skill_root, manifest, source)

    def _install_skill(self, skill_root: Path, manifest: dict, source: SourceRef) -> dict:
        skill_id = manifest["id"]
        installed_paths: Dict[str, str] = {}

        for agent in manifest["agents"]:
            if agent not in {"codex", "claude"}:
                continue
            dest_root = self._agent_skills_root(agent)
            dest = dest_root / skill_id
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)

            self._copy_common(skill_root, dest)
            self._apply_agent_overlay(skill_root, agent, dest)
            self._apply_template_substitutions(dest, agent)
            installed_paths[agent] = str(dest)

        if not installed_paths:
            raise SkillError(
                f"Skill '{skill_id}' does not target a supported agent (codex/claude)."
            )

        return {
            "id": skill_id,
            "type": str(manifest.get("type") or "skill"),
            "name": manifest["name"],
            "version": manifest["version"],
            "description": manifest["description"],
            "agents": list(installed_paths.keys()),
            "installed_paths": installed_paths,
            "source": source.raw,
            "source_ref": source.ref,
            "source_scheme": source.scheme,
            "scm_short_hash": source.scm_short_hash,
            "scm_published_at": source.scm_published_at,
            "compatibility": manifest.get("compatibility", {}),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def _install_rule(self, rule_root: Path, manifest: dict, source: SourceRef) -> dict:
        rule_id = manifest["id"]
        dest_root = self._rules_storage_root()
        dest = dest_root / rule_id
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        self._copy_tree_contents(rule_root, dest)

        return {
            "id": rule_id,
            "type": "rule",
            "name": manifest["name"],
            "version": manifest["version"],
            "description": manifest["description"],
            "agents": manifest.get("agents") or [],
            "installed_paths": {"rules": str(dest)},
            "source": source.raw,
            "source_ref": source.ref,
            "source_scheme": source.scheme,
            "scm_short_hash": source.scm_short_hash,
            "scm_published_at": source.scm_published_at,
            "compatibility": manifest.get("compatibility", {}),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def _agent_skills_root(self, agent: str) -> Path:
        if agent == "codex":
            codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            return codex_home / "skills"
        if agent == "claude":
            claude_home = Path(os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")))
            return claude_home / "skills"
        raise SkillError(f"Unsupported agent: {agent}")

    def _rules_storage_root(self) -> Path:
        sima_home = Path(os.environ.get("SIMA_CLI_HOME", str(Path.home() / ".sima-cli")))
        return sima_home / "playbooks" / "rules"

    def _copy_common(self, skill_root: Path, dest: Path) -> None:
        common_dir = skill_root / "common"
        if common_dir.exists() and common_dir.is_dir():
            self._copy_tree_contents(common_dir, dest)
            return

        for entry in skill_root.iterdir():
            if entry.name in {"targets", "agent", "agents"}:
                continue
            if entry.name in MANIFEST_CANDIDATES:
                continue
            target = dest / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)

    def _apply_agent_overlay(self, skill_root: Path, agent: str, dest: Path) -> None:
        overlay_paths = [
            skill_root / "targets" / agent,
            skill_root / "agents" / agent,
            skill_root / "agent" / agent,
        ]
        for overlay in overlay_paths:
            if overlay.exists() and overlay.is_dir():
                self._copy_tree_contents(overlay, dest)

    def _copy_tree_contents(self, source: Path, dest: Path) -> None:
        for entry in source.iterdir():
            target = dest / entry.name
            if entry.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(entry, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)

    def _apply_template_substitutions(self, dest: Path, agent: str) -> None:
        replacements = {
            "{{AGENT}}": agent,
            "{{SIMA_CLI_VERSION}}": __version__,
        }

        for path in dest.rglob("*.md"):
            content = path.read_text(encoding="utf-8")
            updated = content
            for key, value in replacements.items():
                updated = updated.replace(key, value)
            if updated != content:
                path.write_text(updated, encoding="utf-8")

    def _iter_installed_roots(self, entry: dict) -> List[Path]:
        installed_paths = entry.get("installed_paths", {})
        roots: List[Path] = []
        if isinstance(installed_paths, dict):
            for path in installed_paths.values():
                if not path:
                    continue
                roots.append(Path(path))
        elif isinstance(installed_paths, list):
            for path in installed_paths:
                if path:
                    roots.append(Path(path))
        elif isinstance(installed_paths, str):
            roots.append(Path(installed_paths))
        return roots

    def _load_installed_manifest(self, entry: dict, skill_id: str) -> dict:
        for root in self._iter_installed_roots(entry):
            for candidate in MANIFEST_CANDIDATES:
                manifest_path = root / candidate
                if not manifest_path.exists():
                    continue
                content = manifest_path.read_text(encoding="utf-8")
                if manifest_path.suffix in {".yaml", ".yml"}:
                    loaded = yaml.safe_load(content) or {}
                else:
                    loaded = json.loads(content or "{}")
                if isinstance(loaded, dict):
                    return loaded

        manifest = {
            "id": entry.get("id") or skill_id,
            "type": entry.get("type") or "skill",
            "name": entry.get("name") or entry.get("id") or skill_id,
            "version": str(entry.get("version") or "0.0.0"),
            "description": str(entry.get("description") or ""),
            "agents": entry.get("agents") or [],
            "compatibility": entry.get("compatibility") or {},
        }
        return manifest

    def _load_installed_document(self, entry: dict, item_type: str) -> Tuple[str, Optional[str]]:
        candidates = ["AGENTS.md"] if item_type == "rule" else ["SKILL.md"]
        fallback = ["AGENTS.md", "SKILL.md"] if item_type == "rule" else ["SKILL.md", "AGENTS.md"]
        for root in self._iter_installed_roots(entry):
            for name in [*candidates, *fallback]:
                doc = root / name
                if doc.exists():
                    return name, doc.read_text(encoding="utf-8")
        return (candidates[0], None)
