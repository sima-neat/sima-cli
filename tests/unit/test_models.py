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
        "artifact_type": "model-package",
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
    session = FakeSession(
        [
            FakeResponse(payload={"runs": [_run("one")], "has_more": True, "next_cursor": "next"}),
            FakeResponse(payload={"runs": [_run("two")], "has_more": False, "next_cursor": None}),
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
                            {"component_id": "backbone", "run_id": "backbone"},
                            {"component_id": "transformer", "run_id": "transformer"},
                        ],
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
    session = FakeSession([FakeResponse(payload=catalog)])

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
    assert find_latest_run(models, "rf_detr_small", "modalix_int8_bf16") == package
    assert session.calls[0][1]["params"] == {"git_ref": "refs/heads/develop"}


def test_client_rejects_catalog_without_models():
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession([FakeResponse(payload={"catalog": {"document": {}}})]),
    )

    with pytest.raises(ModelRegistryError, match="current model catalog"):
        client.catalog_models("develop")


def test_client_treats_missing_benchmark_as_normal():
    client = RegistryClient(
        "https://registry.example",
        session=FakeSession([FakeResponse(status_code=404, payload={"error": {}})]),
    )
    assert client.benchmark("run") is None


def test_select_model_artifact_requires_one_available_model_package():
    artifact = {
        "name": "resnet_int8_mpk.tar.gz",
        "artifact_type": "model-package",
        "object_status": "available",
    }
    assert select_model_artifact([artifact]) == artifact
    with pytest.raises(ModelRegistryError, match="no available model-package"):
        select_model_artifact([{"name": "log", "artifact_type": "log"}])


def test_safe_output_path_rejects_path_traversal(tmp_path):
    with pytest.raises(ModelRegistryError, match="unsafe model ID"):
        safe_output_path(tmp_path, "../model", "int8", "model.tar.gz")


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
    assert list(tmp_path.iterdir()) == []


def test_model_card_handles_supported_and_unsupported_benchmarks():
    detail = {
        "summary": {
            "run": _run(),
            "artifacts": [],
        },
        "provenance": {
            "inputs": [{"metadata": {"model_card": "https://huggingface.co/example/model"}}],
            "parameters": {"parameters": {"compile_parameters": {"precision": "INT8"}}},
        },
        "tests": {"test_results": []},
        "metrics": {"measurements": []},
        "benchmark": {
            "result": {
                "schema_version": 3,
                "status": "passed",
                "metrics": {
                    "latency_avg_ms": {
                        "status": "measured",
                        "value": 3.5,
                        "unit": "ms",
                        "aggregation": "mean",
                    }
                },
                "configuration": {"samples": 100},
                "compatibility": {
                    "actual_platform_version": "2.1.3",
                    "actual_neat_version": "2.1.0",
                },
                "trials": [{"trial": 1}],
                "environment": {
                    "observations": {
                        "start": {"temperatures_c": [{"celsius": 40.0}]},
                        "end": {"temperatures_c": [{"celsius": 42.0}]},
                    }
                },
            }
        },
    }

    card = model_card(detail)
    assert card["compiler"]["precision"] == "INT8"
    assert card["model_card"] == "https://huggingface.co/example/model"
    assert card["github_model_folder"] == (
        f"https://github.com/sima-neat/models/tree/{'a' * 40}/models/resnet_50"
    )
    assert card["benchmark"]["measurements"]["latency_avg_ms"]["value"] == 3.5
    assert card["benchmark"]["temperature_c"]["maximum"] == 42.0

    detail["benchmark"]["result"]["schema_version"] = 99
    assert model_card(detail)["benchmark"]["status"] == "unsupported schema"


def test_model_card_rendering_uses_panels_and_readable_metric_labels(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        rendering,
        "_console",
        lambda: Console(file=output, width=80, color_system=None),
    )
    card = {
        "model_id": "yolo26_tiny_drone",
        "variant_id": "modalix_int8_tessellation_mla",
        "display_name": "yolo26_tiny_drone",
        "description": None,
        "model_card": "https://github.com/sima-neat/models/pull/27",
        "branch": "develop",
        "commit_sha": "a" * 40,
        "github_model_folder": (
            f"https://github.com/sima-neat/models/tree/{'a' * 40}/"
            "models/yolo26_tiny_drone"
        ),
        "finished_at": "2026-08-30T12:00:00Z",
        "compiler": {
            "name": "sima-model-compiler",
            "version": "2.1.3",
            "target": "modalix",
            "precision": "int8",
        },
        "categories": {
            "model_type": "Vision",
            "architecture": "YOLO26",
            "paradigm": "CNN",
            "tasks": ["Object Detection"],
        },
        "artifacts": [
            {
                "name": "mpk",
                "artifact_type": "model-pack",
                "size_bytes": 31_469_497,
                "sha256": "c" * 64,
            }
        ],
        "tests": [{"suite": "compile", "name": "mpk-validation", "status": "passed"}],
        "benchmark": {
            "status": "passed",
            "schema_version": 3,
            "samples": 100,
            "trials": [{}, {}, {}],
            "platform_version": "2.1.3",
            "neat_version": "0.4.0",
            "temperature_c": None,
            "measurements": {
                "latency_avg_ms": {"value": 26.416, "unit": "ms", "aggregation": "mean"}
            },
        },
    }

    render_model_card(card)
    rendered = output.getvalue()

    assert "╭─ Model Card" in rendered
    assert "╭─ Artifacts" in rendered
    assert "30.0 MiB" in rendered
    assert "Average latency" in rendered
    assert "latency_avg_ms" not in rendered
    assert "GitHub commit" not in rendered
    assert "Model folder" in rendered
    assert "/models/yolo26_tiny_drone" in rendered


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


def test_list_renders_models_with_nested_variants():
    int8 = _run(run_id="int8", variant_id="modalix_int8")
    int8["metadata"]["display_name"] = "ResNet-50"
    int8["metadata"]["package_id"] = "default"
    int8["size_bytes"] = 1024
    bf16 = _run(run_id="bf16", variant_id="modalix_bf16")
    bf16["metadata"]["display_name"] = "ResNet-50"
    bf16["metadata"]["package_id"] = "default"
    bf16["size_bytes"] = 2048
    yolo = _run(
        run_id="yolo", model_id="yolo26_tiny", variant_id="modalix_int8"
    )
    yolo["metadata"]["display_name"] = "YOLO26 Tiny"
    fake_client = Mock()
    fake_client.catalog_models.return_value = [int8, yolo, bf16]

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(
            models_group, ["--stg", "list", "--branch", "develop"]
        )

    assert result.exit_code == 0, result.output
    assert "Model" in result.output
    assert "Variant" in result.output
    assert "ResNet-50 (resnet_50)" in result.output
    assert "2 variants" in result.output
    assert "├─ modalix_bf16" in result.output
    assert "└─ modalix_int8" in result.output
    assert "YOLO26 Tiny (yolo26_tiny)" in result.output
    assert result.output.index("ResNet-50") < result.output.index("YOLO26 Tiny")


def test_list_rejects_ambiguous_promoted_packages_for_a_variant():
    packages = []
    for package_id in ("optimized", "portable"):
        run = _run(run_id=package_id, variant_id="modalix_int8")
        run["run_type"] = "model_package"
        run["metadata"]["package_id"] = package_id
        packages.append(run)
    fake_client = Mock()
    fake_client.catalog_models.return_value = packages

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(models_group, ["--stg", "list"])

    assert result.exit_code == 1
    assert (
        "Multiple promoted packages found for model 'resnet_50', variant "
        "'modalix_int8': optimized, portable."
    ) in result.output


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


def test_list_accepts_short_branch_option():
    fake_client = Mock()
    fake_client.catalog_models.return_value = []

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(models_group, ["--stg", "list", "-b", "develop"])

    assert result.exit_code == 0, result.output
    fake_client.catalog_models.assert_called_once_with("develop", query=None)


def test_list_defaults_to_main_branch():
    fake_client = Mock()
    fake_client.catalog_models.return_value = []

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(models_group, ["--stg", "list"])

    assert result.exit_code == 0, result.output
    fake_client.catalog_models.assert_called_once_with("main", query=None)


def test_list_passes_query_to_registry_search():
    fake_client = Mock()
    fake_client.catalog_models.return_value = []

    with patch("sima_cli.models.commands.RegistryClient", return_value=fake_client):
        result = CliRunner().invoke(
            models_group,
            ["--stg", "list", "--query", "yolo object detection"],
        )

    assert result.exit_code == 0, result.output
    fake_client.catalog_models.assert_called_once_with("main", query="yolo object detection")


def test_interactive_list_renders_selected_model_card_without_forcing_download():
    run = _run()
    fake_client = Mock()
    fake_client.catalog_models.return_value = [run]
    fake_client.run.return_value = {"run": run, "artifacts": []}
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
    fake_client.run.assert_called_once_with("run-1")
    fake_client.download.assert_not_called()


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
        "artifact_type": "model-package",
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
