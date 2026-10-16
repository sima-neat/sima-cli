from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
import zipfile
from concurrent.futures import CancelledError, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests
from click.testing import CliRunner

from sima_cli.models.client import (
    ModelRegistryError, RegistryClient, _DownloadCancellation, _artifact_matches, _download_lock, _manifest_files,
)
from sima_cli.models.commands import models_group


def _artifact(name, content, **fields):
    return {
        "name": name, "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content), "object_status": "available", **fields,
    }


def _manifest():
    metadata = {
        "model_id": "qwen3_5_2b", "build_profile_id": "w4a16", "package_id": "default",
    }
    payloads = {
        "config": b'{"lm_cfg": {}}', "elf": b"example ELF", "embeddings": b"example embeddings",
    }
    artifacts = [_artifact(name, content) for name, content in payloads.items()]
    paths = ["devkit/vlm_config.json", "elf_files/language.elf", "npy_files/embeddings.npy"]
    manifest = {
        **metadata,
        "files": [
            {"path": path, "artifact": item["name"], "size_bytes": item["size_bytes"], "sha256": item["sha256"]}
            for path, item in zip(paths, artifacts)
        ],
    }
    return metadata, manifest, artifacts, payloads


@pytest.fixture
def deployment():
    metadata, manifest, artifacts, payloads = _manifest()
    state = SimpleNamespace(
        metadata=metadata, manifest=manifest, artifacts=artifacts, payloads=payloads,
        requests=[], expired=set(), unavailable=set(), manifest_corrupt=False, manifest_content=None,
        root_name="package", root_type="model-package", catalog=None,
        media_type="application/json", layout="directory", filename=None,
    )
    run = {
        "id": "package-run", "run_type": "model_package", "status": "completed", "metadata": metadata,
        "git_ref": "refs/heads/feature/quant", "commit_sha": "a" * 40,
    }
    state.run = run

    def summary():
        content = state.manifest_content if state.manifest_content is not None else json.dumps(manifest).encode()
        package = _artifact(
            state.root_name, content, artifact_type=state.root_type, media_type=state.media_type,
            s3_key=state.filename,
            metadata={"layout": state.layout, **({"deployment_size_bytes": sum(map(len, payloads.values()))}
                                                 if state.layout == "directory" else {})},
        )
        return {"run": run, "artifacts": [package, *artifacts]}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            name = path.rsplit("/", 1)[-1]
            state.requests.append(path)
            status = 200
            if path == "/v1/repositories/sima-neat/models/catalogs/current":
                content = json.dumps({"catalog": {"document": state.catalog}}).encode()
            elif path == f"/v1/runs/{run['id']}":
                content = json.dumps(summary()).encode()
            elif path.startswith(f"/v1/runs/{run['id']}/artifacts/"):
                content = json.dumps({"artifact": {"download_url": f"{state.base_url}/objects/{name}"}}).encode()
            elif name in state.expired:
                state.expired.remove(name)
                status, content = 403, b"Expired"
            elif name in state.unavailable:
                status, content = 503, b"Unavailable"
            elif name == state.root_name:
                content = state.manifest_content if state.manifest_content is not None else json.dumps(manifest).encode()
                if state.manifest_corrupt:
                    content = b"X" + content[1:]
            else:
                content = payloads[name]
            self.send_response(status)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    with requests.Session() as session:
        session.trust_env = False
        state.client = RegistryClient(state.base_url, session=session)
        state.summary = summary
        yield state
    server.shutdown()
    server.server_close()
    thread.join()


def _download(state, destination, force=False):
    summary = state.summary()
    return state.client.download_directory(
        "package-run", summary["artifacts"][0], summary["artifacts"], destination, state.metadata, force=force,
    )


def _contents(root):
    return {path for path in root.iterdir() if not path.name.endswith(".lock")}


def test_download_command_recreates_deployment_folder(deployment, tmp_path, monkeypatch):
    state = deployment
    monkeypatch.setattr("sima_cli.models.commands.RegistryClient", lambda *_args: state.client)
    monkeypatch.setattr(state.client, "catalog_models", lambda *_args: [state.summary()["run"]])
    result = CliRunner().invoke(models_group, [
        "--stg", "download", "--id", state.metadata["model_id"],
        "--variant", state.metadata["build_profile_id"], "--output", str(tmp_path), "--json",
    ])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    destination = tmp_path / state.metadata["model_id"] / state.metadata["build_profile_id"]
    assert report["path"] == str(destination)
    assert report["file_count"] == 3
    assert report["size_bytes"] == sum(map(len, state.payloads.values()))
    assert report["manifest_sha256"] == state.summary()["artifacts"][0]["sha256"]
    for entry in state.manifest["files"]:
        assert (destination / entry["path"]).read_bytes() == state.payloads[entry["artifact"]]
    assert json.loads((destination / "manifest.json").read_text()) == state.manifest
    assert _contents(destination.parent) == {destination}
    assert report["downloaded_files"] == 3
    assert report["reused_files"] == 0


@pytest.mark.parametrize("catalog_kind,root_kind,layout", [
    ("approved", "package", "directory"), ("approved", "component", "directory"),
    ("feature", "package", "directory"),
    ("approved", "package", "archive"), ("approved", "component", "archive"),
    ("feature", "package", "archive"),
])
def test_catalog_deployment_downloads(
    deployment, tmp_path, monkeypatch, catalog_kind, root_kind, layout,
):
    state = deployment
    state.layout = layout
    if layout == "archive":
        state.metadata.update(model_id="resnet_50", build_profile_id="w8a8")
        content = io.BytesIO()
        if root_kind == "package":
            with zipfile.ZipFile(content, "w") as archive:
                archive.writestr("model.elf", b"vision model")
            state.media_type, state.filename = "application/zip", "resnet_50_w8a8_default.zip"
        else:
            with tarfile.open(fileobj=content, mode="w:gz") as archive:
                info = tarfile.TarInfo("model.elf")
                info.size = len(b"vision model")
                archive.addfile(info, io.BytesIO(b"vision model"))
            state.media_type, state.filename = "application/gzip", "resnet_50_w8a8_mpk.tar.gz"
        state.manifest_content = content.getvalue()
    if root_kind == "component":
        state.root_name, state.root_type = "llima" if layout == "directory" else "mpk", "model-pack"
        state.run.update(id="compile-run", run_type="model_compile")
        state.metadata.pop("package_id")
        state.manifest.pop("package_id")
        state.metadata["component_id"] = "model"
    root = {
        "role": f"{root_kind}-model", f"{root_kind}_id": "default" if root_kind == "package" else "model",
        "run_id": state.run["id"], "artifact_name": state.root_name, "layout": layout,
        "format": "llima" if layout == "directory" else "zip" if root_kind == "package" else "mpk",
    }
    roots = [root]
    if root_kind == "package":
        roots.insert(0, {
            "role": "compile-model", "component_id": "model", "run_id": "unused-component",
            "artifact_name": "llima" if layout == "directory" else "mpk", "layout": layout,
            "format": "llima" if layout == "directory" else "mpk",
        })
    model = {
        "id": state.metadata["model_id"], "builds": [], "packages": [],
        "build_on_demand": catalog_kind == "approved" or layout == "directory",
    }
    if catalog_kind == "approved":
        model["approved_deployments"] = {state.metadata["build_profile_id"]: {
            "deployment_id": "11111111-1111-4111-8111-111111111111", "manifest_digest": "b" * 64,
            "artifacts": roots,
        }}
    else:
        state.metadata["component_run_ids"] = {"model": "component-run"}
        model["packages"] = [{
            "run_id": state.run["id"], "package_id": "default",
            "build_profile_id": state.metadata["build_profile_id"],
            "component_run_ids": state.metadata["component_run_ids"],
        }]
    state.catalog = {"models": [model]}
    branch = "develop" if catalog_kind == "approved" else "feature/quant"
    selected = state.client.catalog_models(branch)[0]
    assert selected["git_ref"] == "refs/heads/feature/quant"
    assert selected["commit_sha"] == "a" * 40
    assert selected["size_bytes"] == (sum(map(len, state.payloads.values()))
                                       if layout == "directory" else len(state.manifest_content))
    assert selected["artifact_name"] == state.root_name
    monkeypatch.setattr("sima_cli.models.commands.RegistryClient", lambda *_args: state.client)
    result = CliRunner().invoke(models_group, [
        "download", "--branch", branch, "--id", state.metadata["model_id"],
        "--variant", state.metadata["build_profile_id"], "--stg", "--output", str(tmp_path), "--json",
    ])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["run_id"] == state.run["id"]
    assert report["artifact"] == state.root_name
    if layout == "directory":
        assert report["downloaded_files"] == 3
        for item in state.manifest["files"]:
            assert (Path(report["path"]) / item["path"]).read_bytes() == state.payloads[item["artifact"]]
    else:
        assert Path(report["path"]).name == state.filename
        assert Path(report["path"]).read_bytes() == state.manifest_content
    assert not any("/runs/unused-component" in path for path in state.requests)


@pytest.mark.parametrize("name", ["package", "elf"])
def test_directory_refreshes_expired_signed_urls(deployment, tmp_path, name):
    deployment.expired.add(name)
    _download(deployment, tmp_path / "model")
    assert deployment.requests.count(f"/v1/runs/package-run/artifacts/{name}") == 2


def test_repeat_download_reuses_all_files_without_network_transfers(deployment, tmp_path):
    destination = tmp_path / "model"
    _download(deployment, destination)
    deployment.requests.clear()

    result = _download(deployment, destination)

    assert result["reused_files"] == 3
    assert result["downloaded_files"] == 0
    assert deployment.requests == []
    assert _contents(tmp_path) == {destination}


@pytest.mark.parametrize("damage", ["missing", "same_size_corruption"])
def test_repeat_download_repairs_only_invalid_files(deployment, tmp_path, damage):
    destination = tmp_path / "model"
    _download(deployment, destination)
    damaged = destination / "elf_files/language.elf"
    if damage == "missing":
        damaged.unlink()
    else:
        damaged.write_bytes(b"example BAD")
    deployment.requests.clear()

    result = _download(deployment, destination)

    assert result["reused_files"] == 2
    assert result["downloaded_files"] == 1
    assert damaged.read_bytes() == deployment.payloads["elf"]
    assert deployment.requests == ["/v1/runs/package-run/artifacts/elf", "/objects/elf"]


def test_new_build_requires_force_and_reuses_unchanged_files(deployment, tmp_path):
    destination = tmp_path / "model"
    _download(deployment, destination)
    content = b"updated ELF"
    updated = _artifact("elf", content)
    deployment.payloads["elf"] = content
    next(item for item in deployment.artifacts if item["name"] == "elf").update(updated)
    next(item for item in deployment.manifest["files"] if item["artifact"] == "elf").update({
        "sha256": updated["sha256"], "size_bytes": updated["size_bytes"],
    })
    deployment.requests.clear()

    with pytest.raises(ModelRegistryError, match="different deployment"):
        _download(deployment, destination)
    assert deployment.requests == []
    result = _download(deployment, destination, force=True)
    assert result["reused_files"] == 2
    assert result["downloaded_files"] == 1
    assert {path for path in deployment.requests if path.startswith("/objects/")} == {
        "/objects/package", "/objects/elf",
    }
    assert (destination / "elf_files/language.elf").read_bytes() == content


@pytest.mark.parametrize("corrupt_cache", [False, True])
def test_retry_reuses_completed_files_and_rechecks_cached_checksums(deployment, tmp_path, monkeypatch, corrupt_cache):
    destination = tmp_path / "model"
    deployment.unavailable.add("elf")
    completed = threading.Barrier(3, timeout=3)
    download = deployment.client.download

    def finish_good_files_first(run_id, artifact, *args, **kwargs):
        if artifact["name"] == "elf":
            completed.wait()
            return download(run_id, artifact, *args, **kwargs)
        result = download(run_id, artifact, *args, **kwargs)
        if artifact["name"] != "package":
            completed.wait()
        return result

    monkeypatch.setattr(deployment.client, "download", finish_good_files_first)
    with pytest.raises(ModelRegistryError, match="HTTP 503"):
        _download(deployment, destination)
    cached = tmp_path / ".model.download/deployment"
    assert not destination.exists()
    assert (cached / "devkit/vlm_config.json").is_file()
    assert (cached / "npy_files/embeddings.npy").is_file()
    assert not (cached / "elf_files/language.elf").exists()
    assert not list(cached.parent.glob("*.part"))
    if corrupt_cache:
        (cached / "npy_files/embeddings.npy").write_bytes(b"x" * len(deployment.payloads["embeddings"]))

    monkeypatch.setattr(deployment.client, "download", download)
    deployment.unavailable.clear()
    deployment.requests.clear()
    result = _download(deployment, destination)
    assert result["reused_files"] == (1 if corrupt_cache else 2)
    expected_downloads = {"/objects/elf"}
    if corrupt_cache:
        expected_downloads.add("/objects/embeddings")
    assert {path for path in deployment.requests if path.startswith("/objects/")} == expected_downloads
    assert _contents(tmp_path) == {destination}


def test_reuse_falls_back_to_copy_when_hard_links_are_unavailable(deployment, tmp_path, monkeypatch):
    destination = tmp_path / "model"
    _download(deployment, destination)
    deployment.requests.clear()

    def no_hard_links(*_args):
        raise OSError("hard links unavailable")

    monkeypatch.setattr("sima_cli.models.client.os.link", no_hard_links)
    result = _download(deployment, destination)
    assert result["reused_files"] == 3
    assert deployment.requests == []
    assert (destination / "elf_files/language.elf").read_bytes() == deployment.payloads["elf"]


def test_size_mismatch_does_not_read_the_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "model.elf"
    path.write_bytes(b"x")

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("a file with the wrong size should not be hashed")

    monkeypatch.setattr(Path, "open", unexpected_read)
    assert not _artifact_matches(path, _artifact("elf", b"expected"))


def test_cancellation_closes_active_response_and_stops_streaming(tmp_path, monkeypatch):
    started, closed = threading.Event(), threading.Event()

    class BlockingResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def iter_content(self, **_kwargs):
            started.set()
            assert closed.wait(timeout=2)
            yield b"x"

        def close(self):
            closed.set()

    client = RegistryClient("https://registry.example")
    monkeypatch.setattr(client.session, "get", lambda *_args, **_kwargs: BlockingResponse())
    cancellation = _DownloadCancellation()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client._download_once, "https://download.example", tmp_path / "partial", 1,
            hashlib.sha256(b"x").hexdigest(), cancellation=cancellation,
        )
        assert started.wait(timeout=2)
        cancellation.cancel()
        with pytest.raises(CancelledError):
            future.result(timeout=1)
    assert closed.is_set()


def test_same_deployment_downloads_cannot_run_concurrently(deployment, tmp_path):
    destination = tmp_path / "model"
    with _download_lock(destination):
        with pytest.raises(ModelRegistryError, match="Another download"):
            _download(deployment, destination)
    assert deployment.requests == []
    _download(deployment, destination)


def test_backup_cleanup_failure_is_a_warning_after_success(deployment, tmp_path, monkeypatch, caplog):
    destination = tmp_path / "model"
    _download(deployment, destination)
    from sima_cli.models import client as client_module

    rmtree = client_module.shutil.rmtree

    def fail_backup_cleanup(path, *args, **kwargs):
        if str(path).endswith(".previous"):
            raise PermissionError("backup cleanup denied")
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(client_module.shutil, "rmtree", fail_backup_cleanup)
    result = _download(deployment, destination)
    assert result["file_count"] == 3
    assert "backup cleanup denied" in caplog.text
    assert (destination / "elf_files/language.elf").is_file()


def test_directory_downloads_four_files_concurrently(deployment, tmp_path, monkeypatch):
    for index in range(5):
        name, content = f"extra-{index}", b"extra ELF"
        item = _artifact(name, content)
        deployment.artifacts.append(item)
        deployment.payloads[name] = content
        deployment.manifest["files"].append({
            "path": f"elf_files/{name}.elf", "artifact": name,
            "size_bytes": item["size_bytes"], "sha256": item["sha256"],
        })
    barrier, lock = threading.Barrier(4), threading.Lock()
    active, peak = 0, 0
    download = deployment.client.download

    def concurrent_download(run_id, artifact, *args, **kwargs):
        nonlocal active, peak
        if artifact["name"] == "package":
            return download(run_id, artifact, *args, **kwargs)
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=5)
            return download(run_id, artifact, *args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(deployment.client, "download", concurrent_download)
    _download(deployment, tmp_path / "model")
    assert peak == 4


@pytest.mark.parametrize("failure, message", [
    ("file_checksum", "SHA-256 mismatch"), ("manifest_checksum", "SHA-256 mismatch"),
    ("transfer", "HTTP 503"), ("invalid_json", "not valid JSON"),
])
def test_failed_directory_download_preserves_existing_model(deployment, tmp_path, failure, message):
    destination = tmp_path / "model"
    destination.mkdir()
    (destination / "old.elf").write_bytes(b"old model")
    if failure == "file_checksum":
        deployment.payloads["elf"] = b"example BAD"
    elif failure == "manifest_checksum":
        deployment.manifest_corrupt = True
    elif failure == "transfer":
        deployment.unavailable.add("elf")
    else:
        deployment.manifest_content = b"{"

    with pytest.raises(ModelRegistryError, match=message):
        _download(deployment, destination, force=True)
    assert (destination / "old.elf").read_bytes() == b"old model"
    assert list(destination.iterdir()) == [destination / "old.elf"]
    expected = {destination}
    if failure in {"file_checksum", "transfer"}:
        expected.add(tmp_path / ".model.download")
    assert _contents(tmp_path) == expected


def test_force_replaces_directory_without_leaving_stale_files(deployment, tmp_path):
    destination = tmp_path / "model"
    destination.mkdir()
    (destination / "obsolete.elf").write_bytes(b"old model")
    _download(deployment, destination, force=True)
    assert not (destination / "obsolete.elf").exists()
    assert (destination / "elf_files/language.elf").read_bytes() == deployment.payloads["elf"]
    assert _contents(tmp_path) == {destination}


def test_force_restores_old_directory_if_installation_fails(deployment, tmp_path, monkeypatch):
    destination = tmp_path / "model"
    destination.mkdir()
    (destination / "old.elf").write_bytes(b"old model")
    rename = Path.rename

    def fail_install(path, target):
        if path.name == "deployment":
            raise OSError("installation failed")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_install)
    with pytest.raises(ModelRegistryError, match="installation failed"):
        _download(deployment, destination, force=True)
    assert (destination / "old.elf").read_bytes() == b"old model"
    assert _contents(tmp_path) == {destination, tmp_path / ".model.download"}
    monkeypatch.setattr(Path, "rename", rename)
    deployment.requests.clear()
    result = _download(deployment, destination, force=True)
    assert result["reused_files"] == 3
    assert deployment.requests == []


def test_interruption_after_backup_rename_restores_existing_deployment(deployment, tmp_path, monkeypatch):
    destination = tmp_path / "model"
    destination.mkdir()
    (destination / "old.elf").write_bytes(b"old model")
    rename = Path.rename

    def interrupt_after_backup(path, target):
        result = rename(path, target)
        if path == destination:
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(Path, "rename", interrupt_after_backup)
    with pytest.raises(KeyboardInterrupt):
        _download(deployment, destination, force=True)
    assert (destination / "old.elf").read_bytes() == b"old model"
    assert not list(tmp_path.glob("*.previous"))
    monkeypatch.setattr(Path, "rename", rename)
    deployment.requests.clear()
    assert _download(deployment, destination, force=True)["reused_files"] == 3
    assert deployment.requests == []


def test_interrupted_download_cleans_up_and_preserves_existing_model(deployment, tmp_path, monkeypatch):
    destination = tmp_path / "model"
    destination.mkdir()
    (destination / "old.elf").write_bytes(b"old model")
    download = deployment.client.download

    def interrupted_download(run_id, artifact, *args, **kwargs):
        if artifact["name"] == "elf":
            raise KeyboardInterrupt()
        return download(run_id, artifact, *args, **kwargs)

    monkeypatch.setattr(deployment.client, "download", interrupted_download)
    with pytest.raises(KeyboardInterrupt):
        _download(deployment, destination, force=True)
    assert (destination / "old.elf").read_bytes() == b"old model"
    assert _contents(tmp_path) == {destination, tmp_path / ".model.download"}


def test_existing_destination_is_rejected_before_network_access(deployment, tmp_path):
    destination = tmp_path / "model"
    destination.mkdir()
    with pytest.raises(ModelRegistryError, match="already exists"):
        _download(deployment, destination)
    assert deployment.requests == []


def test_directory_symlink_is_rejected_even_with_force(deployment, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    destination = tmp_path / "model"
    destination.symlink_to(target, target_is_directory=True)
    with pytest.raises(ModelRegistryError, match="not a directory"):
        _download(deployment, destination, force=True)
    assert destination.is_symlink()
    assert list(target.iterdir()) == []
    assert deployment.requests == []


def test_reuse_rejects_symlinked_parent_directories(deployment, tmp_path):
    destination = tmp_path / "model"
    _download(deployment, destination)
    external = tmp_path / "external"
    (destination / "elf_files").rename(external)
    (destination / "elf_files").symlink_to(external, target_is_directory=True)
    deployment.requests.clear()
    with pytest.raises(ModelRegistryError, match="symlinked parent"):
        _download(deployment, destination)
    assert (external / "language.elf").read_bytes() == deployment.payloads["elf"]
    assert (destination / "elf_files").is_symlink()
    assert deployment.requests == []


def test_failed_new_download_leaves_no_directory(deployment, tmp_path):
    deployment.payloads["elf"] = b"bad model"
    with pytest.raises(ModelRegistryError, match="size mismatch"):
        _download(deployment, tmp_path / "model")
    assert not (tmp_path / "model").exists()
    assert _contents(tmp_path) == {tmp_path / ".model.download"}
    assert not list((tmp_path / ".model.download").glob("*.part"))


def test_invalid_manifest_is_rejected_before_downloading_files(deployment, tmp_path):
    deployment.manifest["files"][0]["path"] = "../escape"
    with pytest.raises(ModelRegistryError, match="unsafe file path"):
        _download(deployment, tmp_path / "model")
    assert deployment.requests == ["/v1/runs/package-run/artifacts/package", "/objects/package"]
    assert _contents(tmp_path) == set()


@pytest.mark.parametrize("path", [
    "", ".", "../escape", "devkit/../../escape", "/absolute", "C:/drive", "file:stream",
    "devkit\\escape", "devkit//config", "devkit/./config", "nul\x00path", "manifest.json", "Manifest.json/child",
    "devkit/config.", "devkit/config ", "devkit./config",
])
def test_manifest_rejects_unsafe_paths(path):
    metadata, manifest, artifacts, _ = _manifest()
    manifest["files"][0]["path"] = path
    with pytest.raises(ModelRegistryError, match="unsafe file path"):
        _manifest_files(manifest, artifacts, metadata)


@pytest.mark.parametrize("paths", [
    ["devkit/config", "devkit/config"], ["devkit/Config", "devkit/config"],
    ["devkit", "devkit/config"], ["devkit/config", "devkit"],
])
def test_manifest_rejects_duplicate_or_conflicting_paths(paths):
    metadata, manifest, artifacts, _ = _manifest()
    for entry, path in zip(manifest["files"], paths):
        entry["path"] = path
    with pytest.raises(ModelRegistryError, match="duplicate|conflict"):
        _manifest_files(manifest, artifacts, metadata)


def test_temporary_download_names_do_not_restrict_deployment_paths(deployment, tmp_path):
    deployment.manifest["files"][0]["path"] = "elf_files/x"
    deployment.manifest["files"][1]["path"] = "elf_files/.x.part/child"
    result = _download(deployment, tmp_path / "model")
    assert result["downloaded_files"] == 3
    assert (tmp_path / "model/elf_files/x").read_bytes() == deployment.payloads["config"]
    assert (tmp_path / "model/elf_files/.x.part/child").read_bytes() == deployment.payloads["elf"]


@pytest.mark.parametrize("field,value", [
    ("model_id", "wrong"), ("build_profile_id", "wrong"), ("package_id", "wrong"),
    ("files", []), ("files", [None]), ("files", {}),
])
def test_manifest_rejects_wrong_identity_or_file_list(field, value):
    metadata, manifest, artifacts, _ = _manifest()
    manifest[field] = value
    with pytest.raises(ModelRegistryError):
        _manifest_files(manifest, artifacts, metadata)


@pytest.mark.parametrize("field,value", [
    ("artifact", "missing"), ("artifact", None), ("artifact", []),
    ("size_bytes", 999), ("size_bytes", True), ("size_bytes", -1), ("size_bytes", "14"),
    ("sha256", "x" * 64), ("sha256", "a" * 64),
])
def test_manifest_rejects_bad_references_and_integrity_fields(field, value):
    metadata, manifest, artifacts, _ = _manifest()
    manifest["files"][0][field] = value
    with pytest.raises(ModelRegistryError):
        _manifest_files(manifest, artifacts, metadata)


def test_manifest_rejects_unavailable_file_artifact():
    metadata, manifest, artifacts, _ = _manifest()
    artifacts[0]["object_status"] = "missing"
    with pytest.raises(ModelRegistryError, match="unavailable artifact"):
        _manifest_files(manifest, artifacts, metadata)
