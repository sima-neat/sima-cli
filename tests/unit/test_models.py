from __future__ import annotations

import hashlib
import json
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from sima_cli.cli import main
from sima_cli.models import rendering
from sima_cli.models.client import (
    STAGING_BASE_URL,
    ModelRegistryError,
    _download_lock,
    RegistryClient,
    artifact_filename,
    find_latest_run,
    latest_runs_by_model_variant,
    resolve_base_url,
    safe_output_path,
    select_model_artifact,
)
from sima_cli.models.commands import models_group
from sima_cli.models.rendering import model_card, render_model_card


def _run(
    run_id="run-1",
    model_id="resnet_50",
    variant_id="int8",
    finished_at="2026-08-30T12:00:00Z",
    compiler="2.1.3",
):
    return {
        "id": run_id,
        "repository": "sima-neat/models",
        "run_type": "model_compile",
        "status": "completed",
        "git_ref": "refs/heads/develop",
        "commit_sha": "a" * 40,
        "created_at": finished_at,
        "finished_at": finished_at,
        "toolchain_name": "model-compiler",
        "toolchain_version": compiler,
        "target_platform": "modalix",
        "metadata": {"model_id": model_id, "variant_id": variant_id},
    }


def _package_summary(run):
    return {"run": run, "artifacts": [{
        "name": "package", "artifact_type": "model-package", "object_status": "available",
        "size_bytes": 123, "sha256": "c" * 64, "media_type": "application/gzip",
    }]}


FINGERPRINT = "f" * 64
PYNEAT = "0.6.0+feat-benchmark-sentinel-telemetry.82d758ef18ee"
# Trimmed from the Models repository's artifact-browser/fixtures/model-report-*.json.
PERFORMANCE_REPORT = {
    "schema": "sima.model-report",
    "schema_version": 1,
    "stage": "performance",
    "runtime": {"pyneat": PYNEAT, "build": {}},
    "workload": "Model.benchmark with default options",
    "metrics": {
        "throughput_fps": 886.925587921827,
        "latency_avg_ms": 2.4093143350000013,
        "avg_power_watts": 11.316666666666666,
        "energy_per_inference_joules": 0.01276,
    },
    "telemetry": {"status": "ok"},
    "components": {"model": {"execution_plan": {}, "layer_stats": []}},
}
ACCURACY_REPORT = {
    "schema": "sima.model-report",
    "schema_version": 1,
    "stage": "accuracy",
    "runtime": {"pyneat": PYNEAT, "build": {}},
    "datasets": {
        "imagenet-2012-mini": {
            "split": "val",
            "evaluated_samples": 1000,
            "available_samples": 1000,
            "metrics": {"top5_accuracy": 0.9318, "top1_accuracy": 0.7652},
            "details": {"inference_seconds": 41.3},
        }
    },
}


def _test_result(suite, summary, name="model", status="passed"):
    """A /v1/runs/{id}/test-results entry as the registry returns it."""
    return {
        "id": f"{suite}-{name}", "suite": suite, "name": name, "version": "1", "status": status,
        "duration_ms": None, "attempt": 1, "failure_classification": None, "summary": summary,
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"", reason=""):
        self.status_code = status_code
        self._payload = payload
        self._content = content
        self.reason = reason

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    def iter_content(self, chunk_size=0):
        del chunk_size
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_models_group_is_hidden_but_directly_invokable(monkeypatch):
    monkeypatch.setattr("sima_cli.cli.check_for_update", lambda *_: False)
    runner = CliRunner()

    help_result = runner.invoke(main, ["--help"])
    direct_result = runner.invoke(main, ["models", "--help"])

    assert help_result.exit_code == 0, help_result.output
    assert "\n  models " not in help_result.output
    assert direct_result.exit_code == 0, direct_result.output
    assert "branches" in direct_result.output
    assert "download" in direct_result.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["models", "--stg", "branches", "--json"],
        ["models", "--stg", "list", "--json"],
        [
            "models",
            "--stg",
            "download",
            "--id",
            "resnet_50",
            "--variant",
            "int8",
            "--json",
        ],
    ],
)
def test_top_level_models_json_keeps_root_diagnostics_on_stderr(arguments):
    run = _run()
    artifact = {
        "name": "mpk",
        "artifact_type": "model-pack",
        "size_bytes": 5,
        "sha256": hashlib.sha256(b"model").hexdigest(),
    }
    signed = {
        "download_url": "https://download.example/model",
        "s3_key": "models/run/resnet_50_int8_mpk.tar.gz",
    }
    fake_client = Mock()
    fake_client.branches.return_value = [{"name": "main"}]
    fake_client.catalog_models.return_value = [run]
    fake_client.run.return_value = {"run": run, "artifacts": [artifact]}
    fake_client.artifact_download.return_value = signed
    fake_client.download.return_value = Path(
        "models/resnet_50/int8/resnet_50_int8_mpk.tar.gz"
    )

    with patch(
        "sima_cli.cli.check_for_update",
        side_effect=lambda *_: print("update diagnostic"),
    ), patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(main, arguments)

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)
    assert "update diagnostic" not in result.stdout
    assert "Environment:" not in result.stdout
    assert "update diagnostic" in result.stderr
    assert "Environment:" in result.stderr


def test_resolve_base_url_requires_configured_production(monkeypatch):
    monkeypatch.delenv("SIMA_MODELS_REGISTRY_BASE_URL", raising=False)
    monkeypatch.delenv("SIMA_MODELS_REGISTRY_PRODUCTION_URL", raising=False)

    assert resolve_base_url(staging=True) == STAGING_BASE_URL
    with pytest.raises(ModelRegistryError, match="production Model Registry endpoint"):
        resolve_base_url()


def test_base_url_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("SIMA_MODELS_REGISTRY_BASE_URL", "https://registry.example/")
    assert resolve_base_url(staging=True) == "https://registry.example"


def test_latest_runs_collapses_compiler_builds_deterministically():
    old = _run(run_id="old", finished_at="2026-08-29T12:00:00Z", compiler="2.1.2")
    newest = _run(run_id="new", finished_at="2026-08-30T12:00:00Z", compiler="2.1.3")
    other = _run(run_id="other", model_id="yolo", variant_id="bf16")

    result = latest_runs_by_model_variant([newest, other, old])

    assert [item["id"] for item in result] == ["new", "other"]
    assert find_latest_run([old, newest], "resnet_50", "int8")["id"] == "new"


def test_find_latest_run_reports_missing_model():
    with pytest.raises(ModelRegistryError, match="No completed model build"):
        find_latest_run([], "missing", "int8")


def test_find_latest_run_prefers_default_promoted_package():
    packages = []
    for package_id in ("extras", "default"):
        run = _run(run_id=package_id)
        run["run_type"] = "model_package"
        run["metadata"]["package_id"] = package_id
        packages.append(run)

    assert find_latest_run(packages, "resnet_50", "int8")["id"] == "default"


def test_client_paginates_runs_and_preserves_search_filters():
    first = _run("one")
    second = _run("two")
    first["git_ref"] = "refs/heads/feature/models v2"
    second["git_ref"] = "refs/heads/feature/models v2"
    session = FakeSession(
        [
            FakeResponse(
                payload={"runs": [first], "has_more": True, "next_cursor": "next"}
            ),
            FakeResponse(
                payload={"runs": [second], "has_more": False, "next_cursor": None}
            ),
        ]
    )
    client = RegistryClient("https://registry.example", session=session)

    assert [item["id"] for item in client.runs("feature/models v2", query="yolo drone")] == [
        "one",
        "two",
    ]
    assert session.calls[0][1]["params"]["git_ref"] == "refs/heads/feature/models v2"
    assert session.calls[0][1]["params"]["q"] == "yolo drone"
    assert session.calls[1][1]["params"]["cursor"] == "next"
    assert session.calls[1][1]["params"]["q"] == "yolo drone"
    assert "latest_per_model_compiler" in session.calls[0][1]["params"]


def test_client_rejects_repeated_pagination_cursor():
    page = {"runs": [], "has_more": True, "next_cursor": "same"}
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession([FakeResponse(payload=page), FakeResponse(payload=page)]),
    )

    with pytest.raises(ModelRegistryError, match="pagination cursor"):
        client.runs("develop")


def test_client_resolves_promoted_grouped_model_package_from_catalog():
    catalog = {
        "catalog": {
            "document": {
                "catalog_revision": "b" * 40,
                "models": [
                    {
                        "id": "rf_detr_small",
                        "display_name": "RF-DETR Small 512x512",
                        "description": "Grouped object detector.",
                        "categories": {"tasks": ["object_detection"]},
                        "builds": [
                            {"build_profile_id": "modalix_int8_bf16", "component_id": "backbone", "run_id": "backbone"},
                            {"build_profile_id": "modalix_int8_bf16", "component_id": "transformer", "run_id": "transformer"},
                        ],
                        "benchmark_fingerprints": {"modalix_int8_bf16": {"performance": FINGERPRINT}},
                        "packages": [
                            {
                                "run_id": "package-run",
                                "package_id": "default",
                                "build_profile_id": "modalix_int8_bf16",
                                "component_run_ids": {
                                    "backbone": "backbone",
                                    "transformer": "transformer",
                                },
                                "size_bytes": 123,
                                "artifact_sha256": "c" * 64,
                            }
                        ],
                    }
                ],
            }
        }
    }
    package_run = _run(
        "package-run", model_id="rf_detr_small", variant_id="modalix_int8_bf16"
    )
    package_run["run_type"] = "model_package"
    package_run["metadata"].update(
        {
            "package_id": "default",
            "component_run_ids": {
                "backbone": "backbone",
                "transformer": "transformer",
            },
        }
    )
    session = FakeSession(
        [
            FakeResponse(payload=catalog),
            FakeResponse(payload=_package_summary(package_run)),
        ]
    )

    models = RegistryClient("https://registry.example", session=session).catalog_models(
        "develop", query="RF-DETR object detection"
    )

    assert len(models) == 1
    package = models[0]
    assert package["id"] == "package-run"
    assert package["run_type"] == "model_package"
    assert package["metadata"]["variant_id"] == "modalix_int8_bf16"
    assert package["metadata"]["component_run_ids"] == {
        "backbone": "backbone",
        "transformer": "transformer",
    }
    assert package["benchmark_fingerprints"] == {"performance": FINGERPRINT}
    assert find_latest_run(models, "rf_detr_small", "modalix_int8_bf16") == package
    assert session.calls[0][1]["params"] == {"git_ref": "refs/heads/develop"}
    assert len(session.calls) == 2
    assert session.calls[1][0].endswith("/v1/runs/package-run")


@pytest.mark.parametrize("failure", [None, "missing_root", "unavailable_root", "wrong_profile", "wrong_root_type"])
@pytest.mark.parametrize(("selection_field", "branch"), [
    ("approved_deployments", "develop"),
    ("branch_deployments", "feature/model"),
])
def test_client_uses_exact_selected_root(selection_field, branch, failure):
    run = _run("selected-run", model_id="qwen3_5_0_8b", variant_id="w4a16")
    run.update(run_type="model_package", git_ref="refs/heads/feature/model")
    run["metadata"].update(package_id="default", component_run_ids={"model": "candidate-build"})
    artifact = {
        "name": "package", "artifact_type": "model-package", "object_status": "available",
        "size_bytes": 100, "sha256": "c" * 64, "media_type": "application/json",
        "metadata": {"layout": "directory", "deployment_size_bytes": 10000},
    }
    other = {**artifact, "name": "other-root"}
    if failure == "missing_root":
        artifact["name"] = "not-the-selected-root"
    elif failure == "unavailable_root":
        artifact["object_status"] = "missing"
    elif failure == "wrong_profile":
        run["metadata"]["variant_id"] = "w8a16"
    elif failure == "wrong_root_type":
        artifact["artifact_type"] = "model-pack"
    catalog = {"catalog": {"document": {"models": [{
        "id": "qwen3_5_0_8b", "display_name": "Qwen3.5-0.8B", "build_on_demand": True,
        "packages": [{"run_id": "newer-unapproved", "build_profile_id": "w4a16"}],
        "builds": [{"build_profile_id": "w4a16", "component_id": "model", "run_id": "catalog-build"}],
        "benchmark_fingerprints": {"w4a16": {"performance": FINGERPRINT}},
        selection_field: {"w4a16": {
            "deployment_id": "11111111-1111-4111-8111-111111111111", "manifest_digest": "b" * 64,
            "artifacts": [{
                "role": "package-default", "package_id": "default", "run_id": "selected-run",
                "artifact_name": "package", "layout": "directory", "format": "llima",
            }],
        }},
    }]}}}
    if selection_field == "branch_deployments":
        catalog["catalog"]["document"]["models"][0]["approved_deployments"] = {"w4a16": {"artifacts": [{
            "package_id": "default", "run_id": "inherited-approval", "artifact_name": "package",
        }]}}
    session = FakeSession([
        FakeResponse(payload=catalog),
        FakeResponse(payload={"run": run, "artifacts": [other, artifact]}),
    ])
    client = RegistryClient("https://registry.example", session=session)
    if failure:
        with pytest.raises(ModelRegistryError, match="no available.*artifact|does not match"):
            client.catalog_models(branch)
    else:
        selected = find_latest_run(client.catalog_models(branch), "qwen3_5_0_8b", "w4a16")
        assert selected["id"] == "selected-run"
        assert selected["artifact_name"] == "package"
        assert selected["git_ref"] == "refs/heads/feature/model"
        assert selected["size_bytes"] == 10000
        assert selected["sha256"] == "c" * 64
        # The fingerprint measured the catalog's build, not this deployment's.
        assert selected["benchmark_fingerprints"] == {}
    assert len(session.calls) == 2
    assert session.calls[0][1]["params"] == {"git_ref": f"refs/heads/{branch}"}
    assert session.calls[1][0].endswith("/v1/runs/selected-run")


def test_client_keeps_inherited_catalog_packages_on_feature_branch():
    catalog = {
        "catalog": {
            "document": {
                "catalog_revision": "b" * 40,
                "models": [
                    {
                        "id": "resnet_50",
                        "display_name": "ResNet-50",
                        "packages": [
                            {
                                "run_id": "develop-package",
                                "package_id": "default",
                                "build_profile_id": "modalix_int8",
                                "component_run_ids": {"model": "develop-compile"},
                            }
                        ],
                    }
                ],
            }
        }
    }
    inherited_package = _run("develop-package", variant_id="modalix_int8")
    inherited_package["run_type"] = "model_package"
    inherited_package["metadata"].update(
        {"package_id": "default", "component_run_ids": {"model": "develop-compile"}}
    )
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession(
            [
                FakeResponse(payload=catalog),
                FakeResponse(payload=_package_summary(inherited_package)),
            ]
        ),
    )

    selected = client.catalog_models("feature/readme-only")[0]
    assert selected["id"] == "develop-package"
    assert selected["git_ref"] == "refs/heads/develop"


def test_client_excludes_native_package_not_promoted_in_catalog():
    catalog = {
        "catalog": {
            "document": {
                "catalog_revision": "b" * 40,
                "models": [{"id": "resnet_50", "packages": []}],
            }
        }
    }
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession(
            [
                FakeResponse(payload=catalog),
            ]
        ),
    )

    assert client.catalog_models("feature/model") == []


def test_client_keeps_promoted_package_when_newer_unpromoted_rerun_exists():
    catalog = {
        "catalog": {
            "document": {
                "catalog_revision": "b" * 40,
                "models": [
                    {
                        "id": "resnet_50",
                        "packages": [
                            {
                                "run_id": "promoted-package",
                                "package_id": "default",
                                "build_profile_id": "modalix_int8",
                                "component_run_ids": {"model": "feature-compile"},
                            }
                        ],
                    }
                ],
            }
        }
    }
    promoted = _run("promoted-package", variant_id="modalix_int8", finished_at="2026-08-30T12:01:00Z")
    promoted.update(run_type="model_package", git_ref="refs/heads/feature/model")
    promoted["metadata"].update(package_id="default", component_run_ids={"model": "feature-compile"})
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession(
            [
                FakeResponse(payload=catalog),
                FakeResponse(payload=_package_summary(promoted)),
            ]
        ),
    )

    assert [run["id"] for run in client.catalog_models("feature/model")] == [
        "promoted-package"
    ]
    assert len(client.session.calls) == 2
    assert client.session.calls[1][0].endswith("/v1/runs/promoted-package")


def test_client_rejects_catalog_without_models():
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession([FakeResponse(payload={"catalog": {"document": {}}})]),
    )

    with pytest.raises(ModelRegistryError, match="current model catalog"):
        client.catalog_models("develop")


def test_whole_model_result_without_fingerprint_makes_no_request():
    session = FakeSession([])
    assert RegistryClient("https://registry.example", session=session).whole_model_result("accuracy", None) is None
    assert session.calls == []


def test_whole_model_result_treats_unmeasured_fingerprint_as_normal():
    session = FakeSession([FakeResponse(status_code=404, payload={"error": {"code": "fingerprint_not_found"}})])
    client = RegistryClient("https://registry.example", session=session)

    assert client.whole_model_result("performance", FINGERPRINT) is None
    assert session.calls[0][0] == "https://registry.example/v1/repositories/sima-neat/models/resolve"
    assert session.calls[0][1]["params"] == {"run_type": "model_performance", "fingerprint": FINGERPRINT}


def test_whole_model_result_returns_the_model_test_of_the_resolved_run():
    model_result = _test_result("accuracy", ACCURACY_REPORT)
    session = FakeSession([
        FakeResponse(payload={"run": {"id": "accuracy-run", "run_type": "model_accuracy", "status": "completed"}}),
        FakeResponse(payload={"run_id": "accuracy-run", "test_results": [
            _test_result("accuracy", {"schema_version": 2}, name="coco_accuracy"),
            model_result,
            _test_result("performance", PERFORMANCE_REPORT),
        ]}),
    ])

    result = RegistryClient("https://registry.example", session=session).whole_model_result("accuracy", FINGERPRINT)

    assert result == model_result
    assert session.calls[1][0] == "https://registry.example/v1/runs/accuracy-run/test-results"


def test_select_model_artifact_requires_one_available_model_package():
    artifact = {
        "name": "resnet_int8_mpk.tar.gz",
        "artifact_type": "model-package",
        "object_status": "available",
    }
    assert select_model_artifact([artifact]) == artifact
    with pytest.raises(ModelRegistryError, match="no available model-package"):
        select_model_artifact([{"name": "log", "artifact_type": "log"}])


@pytest.mark.parametrize("name,variant", [
    ("../model", "int8"), ("../model", None), ("model/child", None),
    ("model\\child", None), ("C:drive", None), ("model\x00", None), ("model.", None),
])
def test_safe_output_path_rejects_path_traversal(tmp_path, name, variant):
    with pytest.raises(ModelRegistryError, match="unsafe model ID"):
        safe_output_path(tmp_path, name, variant)


def test_artifact_filename_uses_real_s3_object_name():
    assert artifact_filename(
        {
            "name": "mpk",
            "s3_key": "models/run/resnet_50_modalix_int8_mpk.tar.gz",
        }
    ) == "resnet_50_modalix_int8_mpk.tar.gz"


def test_download_is_atomic_and_verifies_size_and_sha(tmp_path):
    content = b"verified model artifact"
    artifact = {
        "name": "resnet_int8_mpk.tar.gz",
        "artifact_type": "model-pack",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    session = FakeSession(
        [
            FakeResponse(payload={"artifact": {"download_url": "https://download.example/model"}}),
            FakeResponse(content=content),
        ]
    )
    destination = tmp_path / artifact["name"]

    result = RegistryClient("https://registry.example", session=session).download(
        "run-1", artifact, destination
    )

    assert result == destination
    assert destination.read_bytes() == content
    assert not (tmp_path / f".{artifact['name']}.part").exists()


def test_download_refreshes_an_expired_presigned_url_once(tmp_path):
    content = b"model"
    artifact = {
        "name": "model_mpk.tar.gz",
        "artifact_type": "model-pack",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    session = FakeSession(
        [
            FakeResponse(payload={"artifact": {"download_url": "https://download.example/expired"}}),
            FakeResponse(status_code=403, payload={}, reason="Forbidden"),
            FakeResponse(payload={"artifact": {"download_url": "https://download.example/fresh"}}),
            FakeResponse(content=content),
        ]
    )

    RegistryClient("https://registry.example", session=session).download(
        "run-1", artifact, tmp_path / artifact["name"]
    )

    assert len(session.calls) == 4


def test_download_preserves_existing_file_without_force(tmp_path):
    destination = tmp_path / "model_mpk.tar.gz"
    destination.write_bytes(b"existing")
    artifact = {
        "name": destination.name,
        "size_bytes": 1,
        "sha256": "a" * 64,
    }
    with pytest.raises(ModelRegistryError, match="already exists"):
        RegistryClient("https://registry.example", session=FakeSession([])).download(
            "run", artifact, destination
        )
    assert destination.read_bytes() == b"existing"


def test_checksum_failure_removes_partial_file(tmp_path):
    artifact = {
        "name": "model_mpk.tar.gz",
        "size_bytes": 5,
        "sha256": "a" * 64,
    }
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession(
            [
                FakeResponse(payload={"artifact": {"download_url": "https://download.example/model"}}),
                FakeResponse(content=b"model"),
            ]
        ),
    )

    with pytest.raises(ModelRegistryError, match="SHA-256 mismatch"):
        client.download("run", artifact, tmp_path / artifact["name"])
    assert list(tmp_path.iterdir()) == [tmp_path / f".{artifact['name']}.lock"]


def _detail(benchmark=None, accuracy=None):
    return {
        "summary": {"run": _run(), "artifacts": []},
        "provenance": {
            "inputs": [{"metadata": {"model_card": "https://huggingface.co/example/model"}}],
            "parameters": {"parameters": {"compile_parameters": {"precision": "INT8"}}},
        },
        "tests": {"test_results": []},
        "metrics": {"measurements": []},
        "benchmark": benchmark,
        "accuracy": accuracy,
    }


def test_model_card_reads_whole_model_reports():
    card = model_card(_detail(
        _test_result("performance", PERFORMANCE_REPORT, status="failed"),
        _test_result("accuracy", ACCURACY_REPORT),
    ))

    assert card["compiler"]["precision"] == "INT8"
    assert card["model_card"] == "https://huggingface.co/example/model"
    assert card["github_model_folder"] == (
        f"https://github.com/sima-neat/models/tree/{'a' * 40}/models/resnet_50"
    )
    assert card["benchmark"]["status"] == "failed"
    assert card["benchmark"]["pyneat_version"] == PYNEAT
    assert list(card["benchmark"]["measurements"]) == [
        "avg_power_watts", "energy_per_inference_joules", "latency_avg_ms", "throughput_fps",
    ]
    assert card["accuracy"]["status"] == "passed"
    assert card["accuracy"]["datasets"] == [{
        "id": "imagenet-2012-mini",
        "split": "val",
        "evaluated_samples": 1000,
        "available_samples": 1000,
        "metrics": {"top1_accuracy": 0.7652, "top5_accuracy": 0.9318},
    }]


@pytest.mark.parametrize("report", [
    {**PERFORMANCE_REPORT, "schema_version": 2},
    {**PERFORMANCE_REPORT, "schema": "sima.benchmark"},
    {**PERFORMANCE_REPORT, "stage": "accuracy"},
    {**PERFORMANCE_REPORT, "metrics": None},
    None,
])
def test_model_card_rejects_unsupported_reports(report):
    benchmark = model_card(_detail(_test_result("performance", report)))["benchmark"]
    assert benchmark["status"] == "unsupported format"
    assert benchmark["measurements"] == {}


def test_model_card_reports_unmeasured_stages():
    card = model_card(_detail())
    assert card["benchmark"]["status"] == "not measured"
    assert card["accuracy"] == {"status": "not measured", "pyneat_version": None, "datasets": []}


def test_model_card_rendering_uses_panels_and_report_metrics(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        rendering,
        "_console",
        lambda: Console(file=output, width=100, color_system=None),
    )
    card = model_card(_detail(
        _test_result("performance", PERFORMANCE_REPORT),
        _test_result("accuracy", ACCURACY_REPORT),
    ))
    card["artifacts"] = [
        {
            "name": "mpk",
            "artifact_type": "model-pack",
            "size_bytes": 31_469_497,
            "sha256": "c" * 64,
        }
    ]

    render_model_card(card)
    rendered = output.getvalue()

    assert "╭─ Model Card" in rendered
    assert "╭─ Artifacts" in rendered
    assert "30.0 MiB" in rendered
    assert "throughput_fps" in rendered
    assert "886.926" in rendered
    assert "imagenet-2012-mini / val" in rendered
    assert "1,000 / 1,000" in rendered
    assert "76.52%" in rendered
    assert "93.18%" in rendered
    assert "GitHub commit" not in rendered
    assert "Model folder" in rendered
    assert "/models/resnet_50" in rendered


def test_branches_accepts_staging_flag_before_or_after_subcommand():
    fake_client = Mock()
    fake_client.branches.return_value = [{"name": "develop"}]
    runner = CliRunner()
    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client) as client_type:
        first = runner.invoke(models_group, ["--stg", "branches"])
        second = runner.invoke(models_group, ["branches", "--stg"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert all(call.args[0] == STAGING_BASE_URL for call in client_type.call_args_list)


def test_list_json_is_non_interactive_and_returns_catalog_models():
    fake_client = Mock()
    fake_client.catalog_models.return_value = [_run("package")]
    runner = CliRunner()
    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client), patch(
        "sima_cli.models.commands.select_run"
    ) as select:
        result = runner.invoke(models_group, ["--stg", "list", "--branch", "develop", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["models"][0]["id"] == "package"
    select.assert_not_called()


def test_list_json_defaults_to_main_without_prompting():
    fake_client = Mock()
    fake_client.catalog_models.return_value = []

    with patch(
        "sima_cli.models.commands.RegistryClient", return_value=fake_client
    ), patch(
        "sima_cli.models.commands._is_interactive", return_value=True
    ), patch(
        "sima_cli.models.commands.select_branch"
    ) as select:
        result = CliRunner().invoke(models_group, ["--stg", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["branch"] == "main"
    fake_client.catalog_models.assert_called_once_with("main", query=None)
    fake_client.branches.assert_not_called()
    select.assert_not_called()


def test_noninteractive_list_requires_explicit_branch_when_omitted():
    fake_client = Mock()

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(models_group, ["--stg", "list"])

    assert result.exit_code == 1
    assert "Use --branch BRANCH" in result.output
    assert "Package    Target    Size" not in result.output
    fake_client.branches.assert_not_called()
    fake_client.catalog_models.assert_not_called()


def test_noninteractive_list_requires_json_instead_of_rendering_overview_table():
    fake_client = Mock()
    fake_client.catalog_models.return_value = [_run()]

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(
            models_group, ["--stg", "list", "--branch", "develop"]
        )

    assert result.exit_code == 1
    assert "Use --json to list models" in result.output
    assert "Package    Target    Size" not in result.output
    fake_client.catalog_models.assert_called_once_with("develop", query=None)


def test_model_variant_group_rejects_ambiguous_promoted_packages():
    packages = []
    for package_id in ("optimized", "portable"):
        run = _run(run_id=package_id, variant_id="modalix_int8")
        run["run_type"] = "model_package"
        run["metadata"]["package_id"] = package_id
        packages.append(run)
    with pytest.raises(ModelRegistryError, match="optimized, portable"):
        rendering.group_model_variants(packages)


def test_model_variant_group_prefers_the_default_package():
    packages = []
    for package_id in ("optimized", "default"):
        run = _run(run_id=package_id, variant_id="modalix_int8")
        run["run_type"] = "model_package"
        run["metadata"]["package_id"] = package_id
        packages.append(run)

    models = rendering.group_model_variants(packages)

    assert models[0]["variants"][0]["run"]["id"] == "default"


def test_interactive_selection_chooses_model_then_variant(monkeypatch):
    resnet = _run(run_id="resnet", variant_id="modalix_int8")
    resnet["metadata"]["display_name"] = "ResNet-50"
    yolo_bf16 = _run(
        run_id="yolo-bf16", model_id="yolo26_tiny", variant_id="modalix_bf16"
    )
    yolo_bf16["metadata"]["display_name"] = "YOLO26 Tiny"
    yolo_int8 = _run(
        run_id="yolo-int8", model_id="yolo26_tiny", variant_id="modalix_int8"
    )
    yolo_int8["metadata"]["display_name"] = "YOLO26 Tiny"
    prompts = []

    def fuzzy(**kwargs):
        prompts.append(kwargs)
        prompt = Mock()
        prompt.execute.return_value = (
            "YOLO26 Tiny (yolo26_tiny) — 2 variants"
            if len(prompts) == 1
            else "yolo26_tiny / modalix_int8 — model-compiler 2.1.3 — modalix"
        )
        return prompt

    monkeypatch.setattr(rendering.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(rendering.inquirer, "fuzzy", fuzzy)

    selected = rendering.select_run([yolo_int8, resnet, yolo_bf16])

    assert selected["id"] == "yolo-int8"
    assert [prompt["message"] for prompt in prompts] == [
        "Select a model:",
        "Select a variant:",
    ]
    assert prompts[0]["choices"][-1] == "Cancel"
    assert prompts[1]["choices"][-1] == "Cancel"


def test_interactive_branch_selection_uses_available_branch_names(monkeypatch):
    prompt = Mock()
    prompt.execute.return_value = "develop"
    fuzzy = Mock(return_value=prompt)
    monkeypatch.setattr(rendering.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(rendering.inquirer, "fuzzy", fuzzy)

    selected = rendering.select_branch(
        [{"name": "develop", "run_count": 335}, {"name": "main", "run_count": 11}]
    )

    assert selected == "develop"
    assert fuzzy.call_args.kwargs["message"] == "Select a branch:"
    assert fuzzy.call_args.kwargs["choices"] == [
        {"name": "develop", "value": "develop"},
        {"name": "main", "value": "main"},
        {"name": "Cancel", "value": None},
    ]


def test_list_accepts_short_branch_option():
    fake_client = Mock()
    fake_client.catalog_models.return_value = []

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(models_group, ["--stg", "list", "-b", "develop"])

    assert result.exit_code == 0, result.output
    fake_client.catalog_models.assert_called_once_with("develop", query=None)
    fake_client.branches.assert_not_called()


def test_list_prompts_for_branch_when_omitted():
    fake_client = Mock()
    fake_client.branches.return_value = [{"name": "develop"}, {"name": "main"}]
    fake_client.catalog_models.return_value = []

    with patch(
        "sima_cli.models.commands.RegistryClient", return_value=fake_client
    ), patch(
        "sima_cli.models.commands._is_interactive", return_value=True
    ), patch(
        "sima_cli.models.commands.select_branch", return_value="develop"
    ) as select:
        result = CliRunner().invoke(models_group, ["--stg", "list"])

    assert result.exit_code == 0, result.output
    fake_client.branches.assert_called_once_with()
    select.assert_called_once_with(fake_client.branches.return_value)
    fake_client.catalog_models.assert_called_once_with("develop", query=None)


def test_list_passes_query_to_registry_search():
    fake_client = Mock()
    fake_client.catalog_models.return_value = []

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(
            models_group,
            [
                "--stg",
                "list",
                "--branch",
                "main",
                "--query",
                "yolo object detection",
            ],
        )

    assert result.exit_code == 0, result.output
    fake_client.catalog_models.assert_called_once_with("main", query="yolo object detection")


def test_interactive_list_renders_selected_model_card_without_forcing_download():
    run = _run()
    fake_client = Mock()
    fake_client.catalog_models.return_value = [run]
    fake_client.detail.return_value = {
        "summary": {"run": run, "artifacts": []},
        "provenance": {},
        "tests": {"test_results": []},
        "metrics": {"measurements": []},
    }
    fake_client.whole_model_result.return_value = None
    fake_client.taxonomy.return_value = None

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client), patch(
        "sima_cli.models.commands._is_interactive", return_value=True
    ), patch("sima_cli.models.commands.select_run", return_value=run), patch(
        "sima_cli.models.commands.click.confirm", return_value=False
    ):
        result = CliRunner().invoke(
            models_group, ["--stg", "list", "--branch", "develop"]
        )

    assert result.exit_code == 0, result.output
    assert "resnet_50" in result.output
    assert "Benchmark" in result.output
    assert "not measured" in result.output
    assert "Package    Target    Size" not in result.output
    fake_client.detail.assert_called_once_with("run-1")
    fake_client.download.assert_not_called()


def test_interactive_package_selection_loads_whole_model_results_for_its_profile():
    package = _run(run_id="package-run", variant_id="modalix_int8")
    package["run_type"] = "model_package"
    package["metadata"].update(
        {
            "build_profile_id": "modalix_int8",
            "package_id": "default",
            "component_run_ids": {"model": "compile-run"},
        }
    )
    package["benchmark_fingerprints"] = {"performance": FINGERPRINT}
    compile_run = _run(run_id="compile-run", variant_id="modalix_int8")
    fake_client = Mock(spec=RegistryClient)
    fake_client.catalog_models.return_value = [package]
    fake_client.run.return_value = {"run": package, "artifacts": []}
    fake_client.detail.return_value = {
        "summary": {"run": compile_run, "artifacts": []},
        "provenance": {},
        "tests": {"test_results": []},
        "metrics": {"measurements": []},
    }
    fake_client.whole_model_result.side_effect = lambda stage, fingerprint: (
        _test_result("performance", PERFORMANCE_REPORT) if fingerprint else None
    )
    fake_client.taxonomy.return_value = None

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client), patch(
        "sima_cli.models.commands._is_interactive", return_value=True
    ), patch("sima_cli.models.commands.select_run", return_value=package), patch(
        "sima_cli.models.commands.click.confirm", return_value=False
    ):
        result = CliRunner().invoke(
            models_group, ["--stg", "list", "--branch", "develop"]
        )

    assert result.exit_code == 0, result.output
    assert "latency_avg_ms" in result.output
    assert "not measured" in result.output
    assert [call.args for call in fake_client.whole_model_result.call_args_list] == [
        ("performance", FINGERPRINT),
        ("accuracy", None),
    ]
    fake_client.detail.assert_called_once_with("compile-run")
    fake_client.run.assert_called_once_with("package-run")


def test_default_command_reports_unconfigured_production(monkeypatch):
    monkeypatch.delenv("SIMA_MODELS_REGISTRY_BASE_URL", raising=False)
    monkeypatch.delenv("SIMA_MODELS_REGISTRY_PRODUCTION_URL", raising=False)

    result = CliRunner().invoke(models_group, ["branches"])

    assert result.exit_code == 1
    assert "production Model Registry endpoint is not configured" in result.output


def test_download_command_resolves_and_downloads_selected_model(tmp_path):
    run = _run(run_id="package-run", model_id="rf_detr_small")
    run["run_type"] = "model_package"
    run["metadata"] = {
        "model_id": "rf_detr_small",
        "build_profile_id": "modalix_int8_bf16",
        "package_id": "default",
        "component_run_ids": {
            "backbone": "backbone-run",
            "transformer": "transformer-run",
        },
    }
    content = b"model"
    artifact = {
        "name": "package",
        "artifact_type": "model-package",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    fake_client = Mock()
    fake_client.catalog_models.return_value = [run]
    fake_client.run.return_value = {"run": run, "artifacts": [artifact]}
    signed = {
        "download_url": "https://download.example/model",
        "s3_key": (
            "models/develop/revision/rf_detr_small/modalix_int8_bf16/packages/"
            "default/rf_detr_small_modalix_int8_bf16_default.zip"
        ),
    }
    fake_client.artifact_download.return_value = signed
    expected = (
        tmp_path
        / "rf_detr_small"
        / "modalix_int8_bf16"
        / "rf_detr_small_modalix_int8_bf16_default.zip"
    )
    fake_client.download.return_value = expected

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(
            models_group,
            [
                "--stg",
                "download",
                "--id",
                "rf_detr_small",
                "--variant",
                "modalix_int8_bf16",
                "--output",
                str(tmp_path),
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["path"] == str(expected)
    assert payload["build_profile_id"] == "modalix_int8_bf16"
    assert payload["filename"] == "rf_detr_small_modalix_int8_bf16_default.zip"
    fake_client.catalog_models.assert_called_once_with("main")
    fake_client.download.assert_called_once_with(
        "package-run", artifact, expected, force=False, initial_download=signed
    )


def test_download_accepts_short_branch_option(tmp_path):
    run = _run()
    artifact = {
        "name": "mpk",
        "artifact_type": "model-pack",
        "size_bytes": 5,
        "sha256": hashlib.sha256(b"model").hexdigest(),
    }
    signed = {
        "download_url": "https://download.example/model",
        "s3_key": "models/run/resnet_50_int8_mpk.tar.gz",
    }
    fake_client = Mock()
    fake_client.catalog_models.return_value = [run]
    fake_client.run.return_value = {"run": run, "artifacts": [artifact]}
    fake_client.artifact_download.return_value = signed
    destination = tmp_path / "resnet_50" / "int8" / "resnet_50_int8_mpk.tar.gz"
    fake_client.download.return_value = destination

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(
            models_group,
            [
                "--stg",
                "download",
                "-b",
                "develop",
                "--id",
                "resnet_50",
                "--variant",
                "int8",
                "--output",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output
    fake_client.catalog_models.assert_called_once_with("develop")


@pytest.mark.parametrize("failure", [False, True])
def test_archive_retry_reclaims_own_orphan_partial(tmp_path, failure):
    destination = tmp_path / "model.zip"
    destination.write_bytes(b"previous working archive")
    partial = tmp_path / ".model.zip.part"
    partial.write_bytes(b"orphan from a terminated process" * 100)
    unrelated = tmp_path / ".other.zip.part"
    unrelated.write_bytes(b"another download")
    content = b"verified archive"
    artifact = {"name": "package", "size_bytes": len(content),
                "sha256": "a" * 64 if failure else hashlib.sha256(content).hexdigest()}
    session = FakeSession([
        FakeResponse(payload={"artifact": {"download_url": "https://download.example/model"}}),
        FakeResponse(content=content),
    ])
    client = RegistryClient("https://registry.example", session=session)
    if failure:
        with pytest.raises(ModelRegistryError, match="SHA-256 mismatch"):
            client.download("run", artifact, destination, force=True)
        assert destination.read_bytes() == b"previous working archive"
    else:
        client.download("run", artifact, destination, force=True)
        assert destination.read_bytes() == content
    assert not partial.exists()
    assert unrelated.read_bytes() == b"another download"
    assert list(tmp_path.glob("*.part")) == [unrelated]


def test_archive_download_lock_preserves_active_partial(tmp_path):
    destination = tmp_path / "model.zip"
    partial = tmp_path / ".model.zip.part"
    partial.write_bytes(b"active transfer")
    session = FakeSession([])
    artifact = {"name": "package", "size_bytes": 1, "sha256": "a" * 64}
    with _download_lock(destination):
        with pytest.raises(ModelRegistryError, match="Another download"):
            RegistryClient("https://registry.example", session=session).download("run", artifact, destination)
    assert partial.read_bytes() == b"active transfer"
    assert session.calls == []
