from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import urllib.parse
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from uuid import uuid4

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

ORGANIZATION = "sima-neat"
REPOSITORY = "models"
STAGING_BASE_URL = "https://models-registry.stg.neat.sima.ai"
TRANSIENT_STATUS_CODES = (429, 502, 503, 504)
SUPPORTED_BENCHMARK_SCHEMAS = (1, 2, 3, 4, 5)


class ModelRegistryError(RuntimeError):
    pass


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ModelRegistryError("Model Registry base URL is empty.")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelRegistryError("Model Registry base URL must be an HTTP(S) URL.")
    return normalized


def resolve_base_url(staging: bool = False, override: Optional[str] = None) -> str:
    configured = override or os.environ.get("SIMA_MODELS_REGISTRY_BASE_URL")
    if configured:
        return normalize_base_url(configured)
    if staging:
        return STAGING_BASE_URL
    production = os.environ.get("SIMA_MODELS_REGISTRY_PRODUCTION_URL")
    if production:
        return normalize_base_url(production)
    raise ModelRegistryError(
        "The production Model Registry endpoint is not configured. "
        "Use --stg or set SIMA_MODELS_REGISTRY_PRODUCTION_URL."
    )


def _new_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=TRANSIENT_STATUS_CODES,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Accept": "application/json", "User-Agent": "sima-cli-models"})
    return session


def _api_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if message and code:
                return f"{message} ({code})"
            if message:
                return str(message)
    return response.reason or f"HTTP {response.status_code}"


def _path_url(base_url: str, *parts: str) -> str:
    encoded = [urllib.parse.quote(str(part).strip("/"), safe="._-~") for part in parts]
    return "/".join([base_url, *encoded])


def _run_sort_key(run: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(run.get("finished_at") or run.get("created_at") or ""),
        str(run.get("created_at") or ""),
        str(run.get("id") or ""),
    )


def _build_profile_id(metadata: Dict[str, Any]) -> str:
    return str(metadata.get("build_profile_id") or metadata.get("variant_id") or "").strip()


def latest_runs_by_model_variant(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for run in runs:
        metadata = run.get("metadata")
        if not isinstance(metadata, dict):
            continue
        model_id = str(metadata.get("model_id") or "").strip()
        variant_id = _build_profile_id(metadata)
        if not model_id or not variant_id:
            continue
        key = (model_id, variant_id)
        current = latest.get(key)
        if current is None or _run_sort_key(run) > _run_sort_key(current):
            latest[key] = run
    return sorted(
        latest.values(),
        key=lambda run: (
            str(run.get("metadata", {}).get("model_id", "")).lower(),
            _build_profile_id(run.get("metadata", {})).lower(),
        ),
    )


def find_latest_run(
    runs: Sequence[Dict[str, Any]], model_id: str, variant_id: str
) -> Dict[str, Any]:
    package_matches = [
        run
        for run in runs
        if run.get("run_type") == "model_package"
        and run.get("metadata", {}).get("model_id") == model_id
        and _build_profile_id(run.get("metadata", {})) == variant_id
    ]
    if package_matches:
        defaults = [
            run
            for run in package_matches
            if run.get("metadata", {}).get("package_id") == "default"
        ]
        if len(defaults) == 1:
            return defaults[0]
        if len(package_matches) == 1:
            return package_matches[0]
        package_ids = ", ".join(
            sorted(
                str(run.get("metadata", {}).get("package_id") or "unknown")
                for run in package_matches
            )
        )
        raise ModelRegistryError(
            f"Multiple packages found for model {model_id!r}, variant "
            f"{variant_id!r}: {package_ids}."
        )
    for run in latest_runs_by_model_variant(runs):
        metadata = run.get("metadata", {})
        if metadata.get("model_id") == model_id and _build_profile_id(metadata) == variant_id:
            return run
    raise ModelRegistryError(
        f"No completed model build found for model {model_id!r}, variant {variant_id!r}."
    )


def select_model_artifact(
    artifacts: Sequence[Dict[str, Any]], name: Optional[str] = None,
) -> Dict[str, Any]:
    available = [
        artifact
        for artifact in artifacts
        if artifact.get("object_status") in {None, "available"}
        and artifact.get("artifact_type") in ({"model-pack", "model-package"} if name else {"model-package"})
        and (name is None or artifact.get("name") == name)
    ]
    if not available:
        description = f"artifact {name!r}" if name else "model-package artifact"
        raise ModelRegistryError(f"The selected build has no available {description}.")
    if len(available) != 1:
        names = ", ".join(sorted(str(item.get("name", "")) for item in available))
        raise ModelRegistryError(f"The selected build has multiple model-package artifacts: {names}.")
    return available[0]


def artifact_filename(artifact: Dict[str, Any]) -> str:
    metadata = artifact.get("metadata")
    configured = metadata.get("filename") if isinstance(metadata, dict) else None
    s3_key = str(artifact.get("s3_key") or "")
    filename = str(configured or (PurePosixPath(s3_key).name if s3_key else artifact.get("name") or ""))
    if not filename or Path(filename).name != filename or filename in {".", ".."}:
        raise ModelRegistryError("The registry returned an unsafe artifact filename.")
    return filename


def _manifest_files(
    manifest: Any,
    artifacts: Sequence[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> List[Tuple[str, Dict[str, Any]]]:
    """Validate a deployment manifest against the package run's registered files."""
    if not isinstance(manifest, dict):
        raise ModelRegistryError("The deployment manifest must be a JSON object.")
    identity = {
        "model_id": metadata.get("model_id"),
        "build_profile_id": _build_profile_id(metadata),
    }
    if metadata.get("package_id"):
        identity["package_id"] = metadata["package_id"]
    for field, expected in identity.items():
        if not expected or manifest.get(field) != expected:
            raise ModelRegistryError(f"Deployment manifest {field} does not match the selected package.")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ModelRegistryError("The deployment manifest must contain a nonempty files list.")

    registered = {item.get("name"): item for item in artifacts}
    if len(registered) != len(artifacts):
        raise ModelRegistryError("The package has duplicate artifact names.")
    paths = set()
    files = []
    for entry in entries:
        path = entry.get("path") if isinstance(entry, dict) else None
        if (
            not isinstance(path, str)
            or not path
            or "\\" in path
            or ":" in path
            or "\x00" in path
            or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(part.endswith((" ", ".")) for part in path.split("/"))
            or path.split("/")[0].casefold() == "manifest.json"
        ):
            raise ModelRegistryError(f"The deployment manifest contains an unsafe file path: {path!r}.")
        normalized = path.casefold()
        if normalized in paths:
            raise ModelRegistryError(f"The deployment manifest contains a duplicate file path: {path!r}.")
        paths.add(normalized)

        name = entry.get("artifact")
        artifact = registered.get(name) if isinstance(name, str) else None
        if (
            not name
            or not isinstance(name, str)
            or Path(name).name != name
            or name in {".", ".."}
            or "\\" in name
            or artifact is None
            or artifact.get("artifact_type") != "model-file"
            or artifact.get("object_status") not in {None, "available"}
        ):
            raise ModelRegistryError(f"Deployment file {path!r} references an unavailable artifact: {name!r}.")
        if (
            name != "file-" + hashlib.sha256(path.encode()).hexdigest()
            or not isinstance(artifact.get("metadata"), dict)
            or artifact["metadata"].get("path") != path
        ):
            raise ModelRegistryError(f"Deployment file {path!r} has an inconsistent artifact name or path binding.")
        size, digest = entry.get("size_bytes"), entry.get("sha256")
        if (
            type(size) is not int
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in digest)
            or size != artifact.get("size_bytes")
            or digest.lower() != str(artifact.get("sha256") or "").lower()
        ):
            raise ModelRegistryError(f"Deployment file {path!r} has inconsistent size or SHA-256 metadata.")
        files.append((path, artifact))

    for path in paths:
        if any(parent.as_posix() in paths for parent in PurePosixPath(path).parents):
            raise ModelRegistryError(f"The deployment manifest contains conflicting file paths: {path!r}.")
    return files


def _check_directory_destination(destination: Path) -> None:
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ModelRegistryError(f"Destination is not a directory: {destination}.")


@contextmanager
def _download_lock(destination: Path) -> Iterator[None]:
    """Prevent two downloads from changing the same deployment or resume folder."""
    with destination.with_name(f".{destination.name}.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                if lock.seek(0, os.SEEK_END) == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ModelRegistryError(f"Another download is using {destination}. Retry when it finishes.") from exc
        yield


class _DownloadCancellation:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._responses = set()
        self._lock = threading.Lock()

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError("Model download cancelled.")

    @contextmanager
    def track(self, response: requests.Response) -> Iterator[None]:
        with self._lock:
            self.check()
            self._responses.add(response)
        try:
            yield
        finally:
            with self._lock:
                self._responses.discard(response)

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            responses = tuple(self._responses)
        for response in responses:
            try:
                response.close()
            except OSError:
                pass


def _artifact_matches(
    path: Path, artifact: Dict[str, Any], cancellation: Optional[_DownloadCancellation] = None,
) -> bool:
    """Check size before hashing existing bytes; never trust filenames or mtimes."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != artifact.get("size_bytes"):
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                if cancellation is not None:
                    cancellation.check()
                digest.update(chunk)
        return digest.hexdigest() == str(artifact.get("sha256") or "").lower()
    except OSError:
        return False


def _deployment_file(root: Path, relative: str) -> Path:
    if any((root / parent).is_symlink() for parent in PurePosixPath(relative).parents):
        raise ModelRegistryError(f"Deployment file {relative!r} has a symlinked parent directory.")
    return root / relative


def _reuse_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def _cleanup_directory(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not remove download directory %s: %s", path, exc)


class RegistryClient:
    def __init__(
        self,
        base_url: str,
        session: Optional[requests.Session] = None,
        timeout: Tuple[float, float] = (5.0, 60.0),
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.session = session or _new_session()
        self.timeout = timeout

    def _get_json(
        self,
        path_parts: Sequence[str],
        params: Optional[Dict[str, Any]] = None,
        allow_not_found: bool = False,
    ) -> Optional[Dict[str, Any]]:
        url = _path_url(self.base_url, *path_parts)
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ModelRegistryError(f"GET {url} failed: {exc}") from exc
        if allow_not_found and response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ModelRegistryError(
                f"GET {url} failed with HTTP {response.status_code}: {_api_error_message(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelRegistryError(f"GET {url} returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ModelRegistryError(f"GET {url} returned an unexpected response.")
        return payload

    def branches(self) -> List[Dict[str, Any]]:
        payload = self._get_json(
            ("v1", "repositories", ORGANIZATION, REPOSITORY, "branches")
        )
        branches = payload.get("branches") if payload else None
        if not isinstance(branches, list):
            raise ModelRegistryError("The registry did not return a branch catalog.")
        normalized = []
        for branch in branches:
            if isinstance(branch, str):
                normalized.append({"name": branch, "git_ref": f"refs/heads/{branch}"})
            elif isinstance(branch, dict) and branch.get("name"):
                normalized.append(branch)
        return sorted(normalized, key=lambda item: str(item["name"]).lower())

    def catalog_models(self, branch: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        if not branch.strip():
            raise ModelRegistryError("Branch is required.")
        payload = self._get_json(
            ("v1", "repositories", ORGANIZATION, REPOSITORY, "catalogs", "current"),
            params={"git_ref": f"refs/heads/{branch}"},
        )
        catalog = payload.get("catalog") if payload else None
        document = catalog.get("document") if isinstance(catalog, dict) else None
        models = document.get("models") if isinstance(document, dict) else None
        if not isinstance(models, list):
            raise ModelRegistryError("The registry did not return a current model catalog.")

        definitions = {
            str(model.get("id") or ""): model
            for model in models
            if isinstance(model, dict) and model.get("id")
        }
        approved = {
            (model_id, profile): selection
            for model_id, model in definitions.items()
            for profile, selection in (model.get("approved_deployments") or {}).items()
        }
        references = [
            (model_id, package["build_profile_id"], {**package, "artifact_name": "package"})
            for model_id, model in definitions.items()
            for package in (model.get("packages") or [])
            if isinstance(package, dict) and package.get("run_id")
            and (model_id, package.get("build_profile_id")) not in approved
        ]
        for (model_id, profile), selection in approved.items():
            roots = selection["artifacts"]
            packages = [root for root in roots if root.get("package_id")]
            roots = packages or [root for root in roots if root.get("component_id")]
            if not roots or (not packages and len(roots) != 1):
                raise ModelRegistryError(f"Approved model {model_id!r}, variant {profile!r} has no full deployment root.")
            references.extend((model_id, profile, root) for root in roots)

        query_terms = (query or "").strip().lower().split()
        selections: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            summaries = executor.map(self.run, [root["run_id"] for _, _, root in references])
            for (model_id, profile, root), summary in zip(references, summaries):
                run = summary.get("run") or {}
                metadata = run.get("metadata") or {}
                if (run.get("id") != root["run_id"] or run.get("status") != "completed"
                        or run.get("run_type") != ("model_package" if root.get("package_id") else "model_compile")
                        or metadata.get("model_id") != model_id or _build_profile_id(metadata) != profile
                        or (root.get("package_id") and metadata.get("package_id") != root["package_id"])
                        or (root.get("component_id") and metadata.get("component_id", "model") != root["component_id"])
                        or ("component_run_ids" in root and metadata.get("component_run_ids") != root["component_run_ids"])):
                    raise ModelRegistryError(f"Catalog artifact does not match model {model_id!r}, variant {profile!r}.")
                artifact = select_model_artifact(summary.get("artifacts") or [], root["artifact_name"])
                artifact_metadata = artifact.get("metadata") or {}
                model = definitions[model_id]
                searchable = " ".join(str(value) for value in (
                    model_id, profile, model.get("display_name"), model.get("description"),
                    model.get("categories"), branch,
                ) if value).lower()
                if query_terms and not all(term in searchable for term in query_terms):
                    continue
                selections.append({
                    **run, "artifact_name": root["artifact_name"],
                    "repository": run.get("repository") or f"{ORGANIZATION}/{REPOSITORY}",
                    "metadata": {
                        **metadata, "variant_id": profile, "build_profile_id": profile,
                        "display_name": model.get("display_name"), "description": model.get("description"),
                    },
                    "model_categories": model.get("categories") or {},
                    "size_bytes": artifact_metadata.get("deployment_size_bytes", artifact.get("size_bytes")),
                    "sha256": artifact.get("sha256"),
                })

        return sorted(
            selections,
            key=lambda run: (
                str(run.get("metadata", {}).get("model_id") or "").lower(),
                _build_profile_id(run.get("metadata", {})).lower(),
                str(run.get("metadata", {}).get("package_id") or "").lower(),
            ),
        )

    def _repository_runs(
        self,
        branch: str,
        run_type: str,
        query: Optional[str] = None,
        latest_per_model_compiler: bool = False,
    ) -> List[Dict[str, Any]]:
        if not branch.strip():
            raise ModelRegistryError("Branch is required.")
        git_ref = f"refs/heads/{branch}"
        path = ("v1", "repositories", ORGANIZATION, REPOSITORY, "runs")
        params: Dict[str, Any] = {
            "status": "completed",
            "run_type": run_type,
            "git_ref": git_ref,
            "limit": 200,
        }
        if latest_per_model_compiler:
            params["latest_per_model_compiler"] = "true"
        normalized_query = (query or "").strip()
        if normalized_query:
            params["q"] = normalized_query
        runs: List[Dict[str, Any]] = []
        seen_cursors = set()
        while True:
            payload = self._get_json(path, params=params)
            page = payload.get("runs") if payload else None
            if not isinstance(page, list):
                raise ModelRegistryError("The registry did not return a runs list.")
            runs.extend(
                item
                for item in page
                if isinstance(item, dict) and item.get("git_ref") == git_ref
            )
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise ModelRegistryError("The registry returned an invalid pagination cursor.")
            seen_cursors.add(cursor)
            params["cursor"] = cursor
        return runs

    def runs(self, branch: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._repository_runs(
            branch,
            "model_compile",
            query=query,
            latest_per_model_compiler=True,
        )

    def run(self, run_id: str) -> Dict[str, Any]:
        payload = self._get_json(("v1", "runs", run_id))
        return payload or {}

    def provenance(self, run_id: str) -> Dict[str, Any]:
        payload = self._get_json(("v1", "runs", run_id, "provenance"))
        return payload or {}

    def test_results(self, run_id: str) -> Dict[str, Any]:
        payload = self._get_json(("v1", "runs", run_id, "test-results"))
        return payload or {}

    def metrics(self, run_id: str) -> Dict[str, Any]:
        payload = self._get_json(("v1", "runs", run_id, "metrics"))
        return payload or {}

    def benchmark(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._get_json(
            ("v1", "runs", run_id, "benchmarks", "latest"), allow_not_found=True
        )

    def accuracy(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._get_json(
            ("v1", "runs", run_id, "accuracy", "latest"), allow_not_found=True
        )

    def taxonomy(self) -> Optional[Dict[str, Any]]:
        return self._get_json(
            ("v1", "repositories", ORGANIZATION, REPOSITORY, "taxonomies", "latest"),
            allow_not_found=True,
        )

    def detail(self, run_id: str) -> Dict[str, Any]:
        return {
            "summary": self.run(run_id),
            "provenance": self.provenance(run_id),
            "tests": self.test_results(run_id),
            "metrics": self.metrics(run_id),
            "benchmark": self.benchmark(run_id),
            "accuracy": self.accuracy(run_id),
        }

    def artifact_download(self, run_id: str, name: str) -> Dict[str, Any]:
        payload = self._get_json(("v1", "runs", run_id, "artifacts", name))
        artifact = payload.get("artifact") if payload else None
        if not isinstance(artifact, dict) or not artifact.get("download_url"):
            raise ModelRegistryError("The registry did not return an artifact download URL.")
        return artifact

    def _download_once(
        self,
        url: str,
        partial: Path,
        expected_size: int,
        expected_sha: str,
        progress: Optional[Callable[[int], None]] = None,
        cancellation: Optional[_DownloadCancellation] = None,
    ) -> None:
        if cancellation is not None:
            cancellation.check()
        try:
            response = self.session.get(url, stream=True, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ModelRegistryError(f"Artifact download failed: {exc}") from exc
        digest = hashlib.sha256()
        size = 0
        with response, (cancellation.track(response) if cancellation is not None else nullcontext()):
            if response.status_code in {401, 403}:
                raise PermissionError("The presigned artifact URL expired or was rejected.")
            if response.status_code >= 400:
                raise ModelRegistryError(
                    f"Artifact download failed with HTTP {response.status_code}: {_api_error_message(response)}"
                )
            try:
                with partial.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if cancellation is not None:
                            cancellation.check()
                        if not chunk:
                            continue
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        if progress is not None:
                            progress(len(chunk))
            except (OSError, requests.RequestException) as exc:
                if cancellation is not None:
                    cancellation.check()
                raise ModelRegistryError(f"Could not write downloaded artifact: {exc}") from exc

        if cancellation is not None:
            cancellation.check()
        if size != expected_size:
            raise ModelRegistryError(
                f"Downloaded artifact size mismatch: expected {expected_size} bytes, received {size}."
            )
        actual_sha = digest.hexdigest()
        if actual_sha.lower() != expected_sha.lower():
            raise ModelRegistryError(
                f"Downloaded artifact SHA-256 mismatch: expected {expected_sha}, received {actual_sha}."
            )

    def download(
        self,
        run_id: str,
        artifact: Dict[str, Any],
        destination: Path,
        force: bool = False,
        initial_download: Optional[Dict[str, Any]] = None,
        progress: Optional[Callable[[int], None]] = None,
        cancellation: Optional[_DownloadCancellation] = None,
        temporary_dir: Optional[Path] = None,
    ) -> Path:
        name = str(artifact.get("name") or "")
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ModelRegistryError("The registry returned an unsafe artifact filename.")
        try:
            expected_size = int(artifact["size_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelRegistryError("The registry did not provide a valid artifact size.") from exc
        expected_sha = str(artifact.get("sha256") or "")
        if len(expected_sha) != 64 or any(char not in "0123456789abcdefABCDEF" for char in expected_sha):
            raise ModelRegistryError("The registry did not provide a valid artifact SHA-256.")

        if destination.exists() and not force:
            raise ModelRegistryError(
                f"Destination already exists: {destination}. Use --force to replace it."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = (temporary_dir or destination.parent) / f".{uuid4().hex}.part"
        try:
            for attempt in range(2):
                if cancellation is not None:
                    cancellation.check()
                signed = initial_download if attempt == 0 and initial_download else self.artifact_download(run_id, name)
                try:
                    self._download_once(
                        str(signed["download_url"]), partial, expected_size, expected_sha, progress, cancellation,
                    )
                    break
                except PermissionError as exc:
                    if attempt == 1:
                        raise ModelRegistryError(str(exc)) from exc
            os.replace(str(partial), str(destination))
        except BaseException:
            try:
                partial.unlink()
            except FileNotFoundError:
                pass
            raise
        return destination

    def download_directory(
        self,
        run_id: str,
        artifact: Dict[str, Any],
        artifacts: Sequence[Dict[str, Any]],
        destination: Path,
        metadata: Dict[str, Any],
        force: bool = False,
    ) -> Dict[str, Any]:
        """Reuse verified files and retain completed downloads until installation succeeds."""
        _check_directory_destination(destination)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with _download_lock(destination):
                _check_directory_destination(destination)
                installed_manifest = destination / "manifest.json"
                same_deployment = _artifact_matches(installed_manifest, artifact)
                if destination.exists() and not same_deployment and not force:
                    raise ModelRegistryError(
                        f"Destination already exists with a different deployment: {destination}. "
                        "Use --force to replace it."
                    )
                cache = destination.with_name(f".{destination.name}.download")
                _check_directory_destination(cache)
                staged = cache / "deployment"
                _check_directory_destination(staged)
                manifest_path = staged / "manifest.json"
                if cache.exists() and not _artifact_matches(manifest_path, artifact):
                    shutil.rmtree(cache)
                cache.mkdir(mode=0o700, exist_ok=True)
                staged.mkdir(exist_ok=True)
                try:
                    if not _artifact_matches(manifest_path, artifact):
                        if same_deployment:
                            _reuse_file(installed_manifest, manifest_path)
                        else:
                            self.download(run_id, artifact, manifest_path, temporary_dir=cache)
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    files = _manifest_files(manifest, artifacts, metadata)
                except ValueError as exc:
                    _cleanup_directory(cache)
                    raise ModelRegistryError("The deployment manifest is not valid JSON.") from exc
                except BaseException:
                    _cleanup_directory(cache)
                    raise
                total = sum(item["size_bytes"] for _, item in files)
                cancellation = _DownloadCancellation()
                reused = 0
                with tqdm(
                    total=total, unit="B", unit_scale=True, desc="Downloading model",
                    file=sys.stderr, disable=not sys.stderr.isatty(),
                ) as progress, ThreadPoolExecutor(max_workers=4) as executor:
                    def download_one(path: str, item: Dict[str, Any]) -> bool:
                        cancellation.check()
                        target = _deployment_file(staged, path)
                        existing = _deployment_file(destination, path)
                        if _artifact_matches(target, item, cancellation):
                            progress.update(item["size_bytes"])
                            return True
                        if _artifact_matches(existing, item, cancellation):
                            _reuse_file(existing, target)
                            progress.update(item["size_bytes"])
                            return True
                        self.download(
                            run_id, item, target, force=True, progress=progress.update,
                            cancellation=cancellation, temporary_dir=cache,
                        )
                        return False

                    futures = [
                        executor.submit(download_one, path, item)
                        for path, item in sorted(files, key=lambda item: (-item[1]["size_bytes"], item[0]))
                    ]
                    try:
                        for completed, future in enumerate(as_completed(futures), 1):
                            reused += future.result()
                            progress.set_postfix_str(f"{completed}/{len(files)} files, {reused} reused")
                    except BaseException:
                        cancellation.cancel()
                        for future in futures:
                            future.cancel()
                        raise

                _check_directory_destination(destination)
                backup = destination.with_name(f".{destination.name}-{uuid4().hex}.previous")
                try:
                    if destination.exists():
                        destination.rename(backup)
                    staged.rename(destination)
                except BaseException:
                    if backup.exists() and not destination.exists():
                        backup.rename(destination)
                    raise
                if backup.exists():
                    _cleanup_directory(backup)
                _cleanup_directory(cache)
        except OSError as exc:
            raise ModelRegistryError(f"Could not download deployment to {destination}: {exc}") from exc
        return {
            "path": str(destination), "format": "directory",
            "file_count": len(files), "size_bytes": total,
            "reused_files": reused, "downloaded_files": len(files) - reused,
        }


def safe_output_path(
    root: Path, model_id: str, variant_id: str, artifact_name: Optional[str] = None,
) -> Path:
    for label, value in (("model ID", model_id), ("variant", variant_id)):
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ModelRegistryError(f"The registry returned an unsafe {label}.")
    destination = root / model_id / variant_id
    if artifact_name is None:
        return destination
    if not artifact_name or Path(artifact_name).name != artifact_name:
        raise ModelRegistryError("The registry returned an unsafe artifact filename.")
    return destination / artifact_name
