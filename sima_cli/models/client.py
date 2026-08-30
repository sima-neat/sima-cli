from __future__ import annotations

import hashlib
import os
import urllib.parse
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ORGANIZATION = "sima-neat"
REPOSITORY = "models"
STAGING_BASE_URL = "https://models-registry.stg.neat.sima.ai"
TRANSIENT_STATUS_CODES = (429, 502, 503, 504)
SUPPORTED_BENCHMARK_SCHEMAS = (1, 2, 3)


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


def latest_runs_by_model_variant(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for run in runs:
        metadata = run.get("metadata")
        if not isinstance(metadata, dict):
            continue
        model_id = str(metadata.get("model_id") or "").strip()
        variant_id = str(metadata.get("variant_id") or "").strip()
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
            str(run.get("metadata", {}).get("variant_id", "")).lower(),
        ),
    )


def find_latest_run(
    runs: Sequence[Dict[str, Any]], model_id: str, variant_id: str
) -> Dict[str, Any]:
    for run in latest_runs_by_model_variant(runs):
        metadata = run.get("metadata", {})
        if metadata.get("model_id") == model_id and metadata.get("variant_id") == variant_id:
            return run
    raise ModelRegistryError(
        f"No completed model build found for model {model_id!r}, variant {variant_id!r}."
    )


def select_model_artifact(artifacts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    available = [
        artifact
        for artifact in artifacts
        if artifact.get("object_status") in {None, "available"}
        and artifact.get("artifact_type") == "model-pack"
    ]
    if not available:
        raise ModelRegistryError("The selected run has no available model-pack artifact.")
    preferred = [item for item in available if str(item.get("name", "")).endswith("_mpk.tar.gz")]
    candidates = preferred or available
    if len(candidates) != 1:
        names = ", ".join(sorted(str(item.get("name", "")) for item in candidates))
        raise ModelRegistryError(f"The selected run has multiple model-pack artifacts: {names}.")
    return candidates[0]


def artifact_filename(artifact: Dict[str, Any]) -> str:
    metadata = artifact.get("metadata")
    configured = metadata.get("filename") if isinstance(metadata, dict) else None
    s3_key = str(artifact.get("s3_key") or "")
    filename = str(configured or (PurePosixPath(s3_key).name if s3_key else artifact.get("name") or ""))
    if not filename or Path(filename).name != filename or filename in {".", ".."}:
        raise ModelRegistryError("The registry returned an unsafe artifact filename.")
    return filename


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

    def runs(self, branch: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        if not branch.strip():
            raise ModelRegistryError("Branch is required.")
        path = ("v1", "repositories", ORGANIZATION, REPOSITORY, "runs")
        params: Dict[str, Any] = {
            "status": "completed",
            "run_type": "model_compile",
            "git_ref": f"refs/heads/{branch}",
            "latest_per_model_compiler": "true",
            "limit": 200,
        }
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
            runs.extend(item for item in page if isinstance(item, dict))
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise ModelRegistryError("The registry returned an invalid pagination cursor.")
            seen_cursors.add(cursor)
            params["cursor"] = cursor
        return runs

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
        }

    def artifact_download(self, run_id: str, name: str) -> Dict[str, Any]:
        payload = self._get_json(("v1", "runs", run_id, "artifacts", name))
        artifact = payload.get("artifact") if payload else None
        if not isinstance(artifact, dict) or not artifact.get("download_url"):
            raise ModelRegistryError("The registry did not return an artifact download URL.")
        return artifact

    def _download_once(self, url: str, partial: Path, expected_size: int, expected_sha: str) -> None:
        try:
            response = self.session.get(url, stream=True, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ModelRegistryError(f"Artifact download failed: {exc}") from exc
        if response.status_code in {401, 403}:
            raise PermissionError("The presigned artifact URL expired or was rejected.")
        if response.status_code >= 400:
            raise ModelRegistryError(
                f"Artifact download failed with HTTP {response.status_code}: {_api_error_message(response)}"
            )

        digest = hashlib.sha256()
        size = 0
        try:
            with partial.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except (OSError, requests.RequestException) as exc:
            raise ModelRegistryError(f"Could not write downloaded artifact: {exc}") from exc

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
        partial = destination.with_name(f".{destination.name}.part")
        try:
            for attempt in range(2):
                signed = initial_download if attempt == 0 and initial_download else self.artifact_download(run_id, name)
                try:
                    self._download_once(str(signed["download_url"]), partial, expected_size, expected_sha)
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


def safe_output_path(root: Path, model_id: str, variant_id: str, artifact_name: str) -> Path:
    for label, value in (("model ID", model_id), ("variant", variant_id)):
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ModelRegistryError(f"The registry returned an unsafe {label}.")
    if not artifact_name or Path(artifact_name).name != artifact_name:
        raise ModelRegistryError("The registry returned an unsafe artifact filename.")
    return root / model_id / variant_id / artifact_name
