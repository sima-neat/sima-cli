from __future__ import annotations

import json
import re
import sys
import urllib.parse
from typing import Any, Dict, List, Optional, Sequence

import click
from InquirerPy import inquirer
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from tabulate import tabulate

from .client import (
    ORGANIZATION,
    REPOSITORY,
    ModelRegistryError,
    SUPPORTED_BENCHMARK_SCHEMAS,
    find_latest_run,
)


BENCHMARK_LABELS = {
    "energy_joules": "Total energy",
    "model_load_ms": "Model load time",
    "latency_avg_ms": "Average latency",
    "latency_p50_ms": "P50 latency",
    "latency_p95_ms": "P95 latency",
    "throughput_fps": "Throughput",
    "avg_power_watts": "Average power",
    "mla_memory_peak_mib": "MLA memory peak",
    "mla_memory_final_mib": "MLA memory final",
    "mla_memory_baseline_mib": "MLA memory baseline",
    "mla_memory_peak_delta_mib": "MLA memory peak delta",
    "time_to_first_inference_ms": "Time to first inference",
    "energy_per_inference_joules": "Energy per inference",
}


def echo_json(payload: Any) -> None:
    click.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def render_branches(branches: Sequence[Dict[str, Any]]) -> None:
    rows = [
        [branch.get("name", ""), branch.get("latest_run_at", "-"), branch.get("run_count", "-")]
        for branch in branches
    ]
    if not rows:
        click.echo("No model branches are available.")
        return
    click.echo(tabulate(rows, headers=["Branch", "Latest run", "Runs"], tablefmt="simple"))


def _run_label(run: Dict[str, Any]) -> str:
    metadata = run.get("metadata") or {}
    model_id = metadata.get("model_id", "unknown")
    variant_id = metadata.get("build_profile_id") or metadata.get("variant_id", "unknown")
    if run.get("run_type") == "model_package":
        package_id = metadata.get("package_id") or "default"
        return f"{model_id} / {variant_id} — full package ({package_id})"
    compiler = " ".join(
        value for value in (run.get("toolchain_name"), run.get("toolchain_version")) if value
    )
    target = run.get("target_platform") or "unknown target"
    return f"{model_id} / {variant_id} — {compiler or 'unknown compiler'} — {target}"


def group_model_variants(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        metadata = run.get("metadata") or {}
        model_id = str(metadata.get("model_id") or "").strip()
        variant_id = str(
            metadata.get("build_profile_id") or metadata.get("variant_id") or ""
        ).strip()
        if not model_id or not variant_id:
            continue
        model = grouped.setdefault(
            model_id,
            {
                "model_id": model_id,
                "display_name": metadata.get("display_name"),
                "variants": {},
            },
        )
        if not model.get("display_name") and metadata.get("display_name"):
            model["display_name"] = metadata["display_name"]
        model["variants"].setdefault(variant_id, []).append(run)

    models = []
    for model in grouped.values():
        variants = [
            {
                "variant_id": variant_id,
                "run": find_latest_run(
                    variant_runs, str(model["model_id"]), variant_id
                ),
            }
            for variant_id, variant_runs in model["variants"].items()
        ]
        models.append(
            {
                "model_id": model["model_id"],
                "display_name": model.get("display_name"),
                "variants": sorted(
                    variants, key=lambda item: item["variant_id"].lower()
                ),
            }
        )
    return sorted(models, key=lambda item: item["model_id"].lower())


def select_run(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    models = group_model_variants(runs)
    if not models:
        raise ModelRegistryError("No model builds are available to select.")
    if not sys.stdin.isatty():
        raise ModelRegistryError("Model selection requires an interactive terminal.")
    model_labels = []
    for model in models:
        display_name = str(model.get("display_name") or model["model_id"])
        identity = (
            f"{display_name} ({model['model_id']})"
            if display_name != model["model_id"]
            else display_name
        )
        count = len(model["variants"])
        model_labels.append(
            f"{identity} — {count} variant{'s' if count != 1 else ''}"
        )
    model_labels.append("Cancel")
    try:
        selected_model = inquirer.fuzzy(
            message="Select a model:",
            choices=model_labels,
            max_height="70%",
            instruction="(Type or use ↑↓)",
            qmark="👉",
        ).execute()
    except KeyboardInterrupt as exc:
        raise ModelRegistryError("Selection cancelled.") from exc
    if selected_model in {None, "Cancel"}:
        raise ModelRegistryError("Selection cancelled.")
    model = models[model_labels.index(selected_model)]

    variant_labels = [
        _run_label(variant["run"]) for variant in model["variants"]
    ]
    variant_labels.append("Cancel")
    try:
        selected_variant = inquirer.fuzzy(
            message="Select a variant:",
            choices=variant_labels,
            max_height="70%",
            instruction="(Type or use ↑↓)",
            qmark="👉",
        ).execute()
    except KeyboardInterrupt as exc:
        raise ModelRegistryError("Selection cancelled.") from exc
    if selected_variant in {None, "Cancel"}:
        raise ModelRegistryError("Selection cancelled.")
    return model["variants"][variant_labels.index(selected_variant)]["run"]


def _taxonomy_label(taxonomy: Dict[str, Any], section: str, value: Any) -> Any:
    entries = taxonomy.get(section) or {}
    entry = entries.get(value) if isinstance(entries, dict) else None
    return entry.get("display_name", value) if isinstance(entry, dict) else value


def _source_card(provenance: Dict[str, Any]) -> Optional[str]:
    for item in provenance.get("inputs") or []:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if isinstance(metadata, dict) and metadata.get("model_card"):
            return str(metadata["model_card"])
    return None


def _github_model_folder_url(run: Dict[str, Any], model_id: Any) -> Optional[str]:
    repository = str(run.get("repository") or "")
    commit = str(run.get("commit_sha") or "")
    model = str(model_id or "")
    expected_repository = f"{ORGANIZATION}/{REPOSITORY}"
    if (
        repository.lower() != expected_repository.lower()
        or re.fullmatch(r"[a-f0-9]{40}", commit, flags=re.IGNORECASE) is None
        or re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", model) is None
    ):
        return None
    base = (
        f"https://github.com/{urllib.parse.quote(ORGANIZATION)}/"
        f"{urllib.parse.quote(REPOSITORY)}"
    )
    return f"{base}/tree/{commit}/models/{urllib.parse.quote(model)}"


def _measured_benchmarks(benchmark: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not benchmark:
        return {"status": "not available", "measurements": {}}
    result = benchmark.get("result")
    if not isinstance(result, dict):
        return {"status": "not available", "measurements": {}}
    schema = result.get("schema_version")
    if schema not in SUPPORTED_BENCHMARK_SCHEMAS:
        return {
            "status": "unsupported schema",
            "schema_version": schema,
            "result_artifact_url": benchmark.get("result_artifact_url"),
            "measurements": {},
        }
    measurements = {}
    for key, metric in (result.get("metrics") or {}).items():
        if not isinstance(metric, dict):
            continue
        if metric.get("status") == "measured" and isinstance(metric.get("value"), (int, float)):
            measurements[key] = {
                "value": metric["value"],
                "unit": metric.get("unit", ""),
                "aggregation": metric.get("aggregation", ""),
            }
    environment = result.get("environment") or {}
    temperature_samples = environment.get("temperature_samples") or []
    if not temperature_samples:
        observations = environment.get("observations") or {}
        temperature_samples = [
            value for value in (observations.get("start"), observations.get("end")) if value
        ]
    temperatures = [
        reading.get("celsius")
        for sample in temperature_samples
        if isinstance(sample, dict)
        for reading in sample.get("temperatures_c") or []
        if isinstance(reading, dict) and isinstance(reading.get("celsius"), (int, float))
    ]
    return {
        "status": result.get("status", "available"),
        "schema_version": schema,
        "samples": (result.get("configuration") or {}).get("samples"),
        "platform_version": (result.get("compatibility") or {}).get("actual_platform_version"),
        "neat_version": (result.get("compatibility") or {}).get("actual_neat_version"),
        "trials": result.get("trials") or [],
        "temperature_c": {
            "minimum": min(temperatures),
            "maximum": max(temperatures),
            "sample_count": len(temperatures),
        }
        if temperatures
        else None,
        "measurements": measurements,
    }


def _accuracy_summary(accuracy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not accuracy:
        return {"status": "not available", "metrics": {}}
    result = accuracy.get("result")
    if not isinstance(result, dict):
        return {"status": "not available", "metrics": {}}
    metrics = result.get("metrics") or {}
    candidate = metrics.get("candidate") or {}
    reference = metrics.get("reference") or {}
    comparison = metrics.get("comparison") or {}
    dataset = result.get("dataset") or {}
    counts = result.get("counts") or {}
    evaluation = result.get("evaluation") or {}
    execution = result.get("execution") or {}
    return {
        "status": evaluation.get("status") or "available",
        "schema_version": result.get("schema_version"),
        "scope": result.get("report_scope"),
        "profile": evaluation.get("profile"),
        "dataset": " / ".join(
            str(value)
            for value in (dataset.get("id"), dataset.get("variant"), dataset.get("split"))
            if value
        ),
        "evaluated_samples": dataset.get("evaluated_samples"),
        "available_samples": dataset.get("available_samples"),
        "samples_per_second": execution.get("samples_per_second"),
        "metrics": {
            "top1_accuracy": {
                "candidate": candidate.get("top1_accuracy"),
                "reference": reference.get("top1_accuracy"),
                "delta": comparison.get("top1_delta"),
            },
            "top5_accuracy": {
                "candidate": candidate.get("top5_accuracy"),
                "reference": reference.get("top5_accuracy"),
                "delta": comparison.get("top5_delta"),
            },
            "top1_agreement": comparison.get("top1_agreement"),
        },
        "counts": counts,
    }


def model_card(detail: Dict[str, Any], taxonomy_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    summary = detail.get("summary") or {}
    run = summary.get("run") or {}
    metadata = run.get("metadata") or {}
    provenance = detail.get("provenance") or {}
    taxonomy_row = (taxonomy_payload or {}).get("taxonomy") or {}
    taxonomy = taxonomy_row.get("document") or {}
    categories = run.get("model_categories") or {}
    architecture = categories.get("architecture") or {}
    parameters = (provenance.get("parameters") or {}).get("parameters") or {}
    compile_parameters = parameters.get("compile_parameters") or {}
    github_model_folder = _github_model_folder_url(run, metadata.get("model_id"))
    return {
        "model_id": metadata.get("model_id"),
        "variant_id": metadata.get("variant_id"),
        "display_name": metadata.get("display_name") or metadata.get("model_name"),
        "description": metadata.get("description"),
        "model_card": metadata.get("model_card") or _source_card(provenance),
        "branch": (
            str(run.get("git_ref") or "")[len("refs/heads/") :]
            if str(run.get("git_ref") or "").startswith("refs/heads/")
            else str(run.get("git_ref") or "")
        ),
        "commit_sha": run.get("commit_sha"),
        "github_model_folder": github_model_folder,
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "finished_at": run.get("finished_at"),
        "compiler": {
            "name": run.get("toolchain_name"),
            "version": run.get("toolchain_version"),
            "target": run.get("target_platform"),
            "precision": compile_parameters.get("precision"),
        },
        "categories": {
            "model_type": _taxonomy_label(taxonomy, "model_types", categories.get("model_type")),
            "architecture": _taxonomy_label(taxonomy, "architectures", architecture.get("id")),
            "paradigm": _taxonomy_label(
                taxonomy, "architecture_paradigms", architecture.get("paradigm")
            ),
            "tasks": [
                _taxonomy_label(taxonomy, "tasks", item) for item in categories.get("tasks") or []
            ],
        },
        "artifacts": summary.get("artifacts") or [],
        "tests": (detail.get("tests") or {}).get("test_results") or [],
        "metrics": (detail.get("metrics") or {}).get("measurements") or [],
        "provenance": {
            "inputs": provenance.get("inputs") or [],
            "parameters": parameters,
        },
        "benchmark": _measured_benchmarks(detail.get("benchmark")),
        "accuracy": _accuracy_summary(detail.get("accuracy")),
    }


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "not available"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "not available"
    return str(value)


def _console() -> Console:
    return Console(file=click.get_text_stream("stdout"), highlight=False)


def _value_text(value: Any, style: str = "") -> Text:
    formatted = _format_value(value)
    if formatted == "not available":
        return Text(formatted, style="dim italic")
    return Text(formatted, style=style)


def _link_text(url: Any) -> Text:
    if not url:
        return _value_text(None)
    value = str(url)
    return Text(value, style=f"blue underline link {value}")


def _status_text(status: Any) -> Text:
    value = _format_value(status)
    normalized = value.lower()
    if normalized in {"passed", "completed", "available"}:
        style = "bold green"
    elif normalized in {"failed", "error"}:
        style = "bold red"
    elif normalized in {"not available", "unsupported schema"}:
        style = "yellow"
    else:
        style = "bold"
    return Text(value, style=style)


def _metadata_grid(rows: Sequence[Sequence[Any]]) -> Table:
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(style="bold cyan", no_wrap=True, width=16)
    table.add_column(ratio=1, overflow="fold")
    for label, value in rows:
        table.add_row(str(label), value if isinstance(value, Text) else _value_text(value))
    return table


def _format_bytes(value: Any) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "not available"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:,.0f} {unit}" if unit == "B" else f"{amount:,.1f} {unit}"
        amount /= 1024
    return f"{size:,} B"


def _format_measurement(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.6g}"
    return _format_value(value)


def _format_percentage(value: Any, signed: bool = False) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "not available"
    formatted = f"{value * 100:{'+' if signed else ''}.2f}".rstrip("0").rstrip(".")
    return f"{formatted}%"


def render_model_card(card: Dict[str, Any]) -> None:
    console = _console()
    compiler = card["compiler"]
    categories = card["categories"]
    display_name = card.get("display_name") or card.get("model_id") or "Model"
    title = Text(str(display_name), style="bold bright_white")
    variant = _value_text(card.get("variant_id"), "bold cyan")
    heading = Table.grid(expand=True)
    heading.add_column(ratio=1)
    heading.add_column(justify="right")
    heading.add_row(title, variant)

    model_card_url = card.get("model_card")
    model_card_text = _link_text(model_card_url)
    rows = [
        ["Model", card.get("model_id")],
        ["Description", card.get("description")],
        ["Model card", model_card_text],
        ["Branch", card.get("branch")],
        ["Commit", _value_text(card.get("commit_sha"), "yellow")],
        ["Model folder", _link_text(card.get("github_model_folder"))],
        ["Built", card.get("finished_at") or card.get("created_at")],
        [
            "Compiler",
            " ".join(str(v) for v in (compiler.get("name"), compiler.get("version")) if v),
        ],
        ["Target", compiler.get("target")],
        ["Precision", _value_text(compiler.get("precision"), "bold magenta")],
        ["Model type", categories.get("model_type")],
        ["Architecture", categories.get("architecture")],
        ["Paradigm", categories.get("paradigm")],
        ["Tasks", categories.get("tasks")],
    ]
    console.print()
    console.print(
        Panel(
            Group(heading, Text(""), _metadata_grid(rows)),
            title="Model Card",
            title_align="left",
            border_style="bright_blue",
            padding=(1, 2),
        )
    )

    artifact_table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
        header_style="bold bright_blue",
    )
    artifact_table.add_column("Name", style="bold cyan", no_wrap=True)
    artifact_table.add_column("Type", no_wrap=True)
    artifact_table.add_column("Size", justify="right", no_wrap=True)
    artifact_table.add_column("SHA-256", ratio=1, overflow="fold", style="dim")
    for item in card.get("artifacts") or []:
        artifact_table.add_row(
            _format_value(item.get("name")),
            _format_value(item.get("artifact_type")),
            _format_bytes(item.get("size_bytes")),
            _format_value(item.get("sha256")),
        )
    artifact_content = artifact_table if artifact_table.row_count else _value_text(None)
    console.print(Panel(artifact_content, title="Artifacts", title_align="left", border_style="blue"))

    validation_table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
        header_style="bold bright_blue",
    )
    validation_table.add_column("Suite", style="cyan")
    validation_table.add_column("Test", ratio=1)
    validation_table.add_column("Status", justify="right")
    for item in card.get("tests") or []:
        validation_table.add_row(
            _format_value(item.get("suite")),
            _format_value(item.get("name")),
            _status_text(item.get("status")),
        )
    validation_content = validation_table if validation_table.row_count else _value_text(None)
    console.print(
        Panel(validation_content, title="Validation", title_align="left", border_style="blue")
    )

    accuracy = card.get("accuracy") or {"status": "not available", "metrics": {}}
    accuracy_rows = [
        ["Status", _status_text(accuracy.get("status"))],
        ["Schema", accuracy.get("schema_version")],
        ["Scope", accuracy.get("scope")],
        ["Profile", accuracy.get("profile")],
        ["Dataset", accuracy.get("dataset")],
    ]
    evaluated = accuracy.get("evaluated_samples")
    available = accuracy.get("available_samples")
    if evaluated is not None or available is not None:
        accuracy_rows.append(["Samples", f"{evaluated or 0} / {available or 0}"])
    if accuracy.get("samples_per_second") is not None:
        accuracy_rows.append(
            ["Throughput", f"{_format_measurement(accuracy['samples_per_second'])} images/s"]
        )
    accuracy_parts = [_metadata_grid(accuracy_rows)]
    accuracy_table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
        header_style="bold bright_blue",
        padding=(0, 1),
    )
    accuracy_table.add_column("Metric", ratio=1)
    accuracy_table.add_column("Candidate", justify="right", style="bold cyan")
    accuracy_table.add_column("Reference", justify="right")
    accuracy_table.add_column("Delta", justify="right")
    accuracy_metrics = accuracy.get("metrics") or {}
    for key, label in (("top1_accuracy", "Top-1 accuracy"), ("top5_accuracy", "Top-5 accuracy")):
        metric = accuracy_metrics.get(key) or {}
        if any(metric.get(field) is not None for field in ("candidate", "reference", "delta")):
            accuracy_table.add_row(
                label,
                _format_percentage(metric.get("candidate")),
                _format_percentage(metric.get("reference")),
                _format_percentage(metric.get("delta"), signed=True),
            )
    agreement = accuracy_metrics.get("top1_agreement")
    if agreement is not None:
        accuracy_table.add_row(
            "Top-1 agreement", _format_percentage(agreement), "not applicable", "not applicable"
        )
    if accuracy_table.row_count:
        accuracy_parts.extend([Text(""), accuracy_table])
    console.print(
        Panel(Group(*accuracy_parts), title="Accuracy", title_align="left", border_style="blue")
    )

    benchmark = card["benchmark"]
    summary_rows = [
        ["Status", _status_text(benchmark.get("status"))],
        ["Schema", benchmark.get("schema_version")],
    ]
    if benchmark.get("status") != "unsupported schema":
        summary_rows.extend(
            [
            ["Samples", benchmark.get("samples")],
            ["Trials", len(benchmark.get("trials") or [])],
            ["Platform version", benchmark.get("platform_version")],
            ["Neat version", benchmark.get("neat_version")],
            ]
        )
        temperature = benchmark.get("temperature_c")
        if temperature:
            summary_rows.append(
                [
                    "Temperature",
                    f"{temperature['minimum']}–{temperature['maximum']} °C "
                    f"({temperature['sample_count']} readings)",
                ]
            )
    benchmark_parts = [_metadata_grid(summary_rows)]
    measurement_table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
        header_style="bold bright_blue",
        padding=(0, 1),
    )
    measurement_table.add_column("Measurement", ratio=1)
    measurement_table.add_column("Value", justify="right", style="bold cyan", no_wrap=True)
    measurement_table.add_column("Unit", style="dim", no_wrap=True)
    measurement_table.add_column("Aggregation", style="dim", no_wrap=True)
    for name, measurement in benchmark.get("measurements", {}).items():
        measurement_table.add_row(
            BENCHMARK_LABELS.get(name, name.replace("_", " ").title()),
            _format_measurement(measurement.get("value")),
            _format_value(measurement.get("unit")),
            _format_value(measurement.get("aggregation")),
        )
    if measurement_table.row_count:
        benchmark_parts.extend([Text(""), measurement_table])
    elif benchmark.get("status") == "unsupported schema":
        benchmark_parts.extend(
            [Text(""), Text("This benchmark schema is not supported by this sima-cli version.", style="yellow")]
        )
        if benchmark.get("result_artifact_url"):
            raw_url = str(benchmark["result_artifact_url"])
            benchmark_parts.append(Text(f"Raw result: {raw_url}", style=f"blue underline link {raw_url}"))
    console.print(
        Panel(Group(*benchmark_parts), title="Benchmark", title_align="left", border_style="blue")
    )
