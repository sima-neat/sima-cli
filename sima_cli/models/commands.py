from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import click

from .client import (
    ModelRegistryError,
    RegistryClient,
    artifact_filename,
    find_latest_run,
    resolve_base_url,
    safe_output_path,
    select_model_artifact,
)
from .rendering import (
    echo_json,
    model_card,
    render_branches,
    render_model_card,
    select_branch,
    select_run,
)


def _staging_option(function):
    return click.option(
        "--stg",
        "--staging",
        "staging",
        is_flag=True,
        default=False,
        help="Use the staging Model Registry.",
    )(function)


def _client(ctx: click.Context, staging: bool) -> RegistryClient:
    group_staging = bool((ctx.obj or {}).get("models_staging"))
    base_url = resolve_base_url(staging=staging or group_staging)
    return RegistryClient(base_url)


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _taxonomy(client: RegistryClient) -> Optional[Dict[str, Any]]:
    try:
        return client.taxonomy()
    except ModelRegistryError:
        return None


def _download_run(
    client: RegistryClient,
    run: Dict[str, Any],
    output: Path,
    force: bool,
) -> Dict[str, Any]:
    run_id = str(run.get("id") or "")
    if not run_id:
        raise ModelRegistryError("The selected registry run has no ID.")
    summary = client.run(run_id)
    artifacts = summary.get("artifacts") or []
    artifact = select_model_artifact(artifacts)
    metadata = (summary.get("run") or run).get("metadata") or {}
    model_id = str(metadata.get("model_id") or "")
    variant_id = str(metadata.get("build_profile_id") or metadata.get("variant_id") or "")
    signed = client.artifact_download(run_id, str(artifact.get("name") or ""))
    filename = artifact_filename({**artifact, **signed})
    destination = safe_output_path(output, model_id, variant_id, filename)
    downloaded = client.download(
        run_id,
        artifact,
        destination,
        force=force,
        initial_download=signed,
    )
    return {
        "run_id": run_id,
        "model_id": model_id,
        "variant_id": variant_id,
        "build_profile_id": variant_id,
        "artifact": artifact.get("name"),
        "filename": filename,
        "path": str(downloaded),
        "size_bytes": artifact.get("size_bytes"),
        "sha256": artifact.get("sha256"),
    }


def _selected_model_detail(
    client: RegistryClient, selected: Dict[str, Any]
) -> Dict[str, Any]:
    selected_metadata = selected.get("metadata") or {}
    component_run_ids = selected_metadata.get("component_run_ids") or {}
    if isinstance(component_run_ids, dict) and component_run_ids:
        components = sorted(
            (
                (str(component), str(run_id))
                for component, run_id in component_run_ids.items()
                if str(component) and str(run_id)
            ),
            key=lambda item: (item[0] != "model", item[0]),
        )
        details = [(component, client.detail(run_id)) for component, run_id in components]
        package_summary = client.run(str(selected.get("id") or ""))
    else:
        details = [
            (
                str(selected_metadata.get("component_id") or "model"),
                client.detail(str(selected.get("id") or "")),
            )
        ]
        package_summary = details[0][1].get("summary") or {}

    primary = details[0][1]
    registry_run = ((primary.get("summary") or {}).get("run") or {})
    registry_metadata = registry_run.get("metadata") or {}
    summary = {
        **(primary.get("summary") or {}),
        "run": {
            **selected,
            **registry_run,
            "metadata": {
                **registry_metadata,
                **selected_metadata,
                "variant_id": (
                    selected_metadata.get("build_profile_id")
                    or selected_metadata.get("variant_id")
                ),
            },
            "model_categories": (
                selected.get("model_categories")
                or registry_run.get("model_categories")
                or {}
            ),
        },
        "artifacts": package_summary.get("artifacts") or [],
    }
    return {
        "summary": summary,
        "provenance": primary.get("provenance") or {},
        "tests": {
            "test_results": [
                {**test, "component_id": test.get("component_id") or component}
                for component, detail in details
                for test in (detail.get("tests") or {}).get("test_results") or []
            ]
        },
        "metrics": {
            "measurements": [
                {**metric, "component_id": metric.get("component_id") or component}
                for component, detail in details
                for metric in (detail.get("metrics") or {}).get("measurements") or []
            ]
        },
        "benchmark": next(
            (detail.get("benchmark") for _, detail in details if detail.get("benchmark")),
            None,
        ),
        "accuracy": next(
            (detail.get("accuracy") for _, detail in details if detail.get("accuracy")),
            None,
        ),
    }


@click.group(name="models", help="Browse and download Model Registry artifacts.", hidden=True)
@_staging_option
@click.pass_context
def models_group(ctx: click.Context, staging: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["models_staging"] = staging


@models_group.command("branches")
@_staging_option
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON.")
@click.pass_context
def branches_command(ctx: click.Context, staging: bool, json_output: bool) -> None:
    """List branches containing registered models."""
    try:
        branches = _client(ctx, staging).branches()
    except ModelRegistryError as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        echo_json({"branches": branches})
    else:
        render_branches(branches)


@models_group.command("list")
@click.option(
    "-b",
    "--branch",
    help=(
        "Models repository branch. If omitted, select interactively; "
        "--json defaults to main."
    ),
)
@click.option(
    "-q",
    "--query",
    help=(
        "Search model names, variants, categories, or branch. "
        "Multiple words must all match."
    ),
)
@_staging_option
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON without prompting.")
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path("models"),
    show_default=True,
    help="Root directory for an interactively downloaded artifact.",
)
@click.option("--force", is_flag=True, help="Replace an existing downloaded artifact.")
@click.pass_context
def list_command(
    ctx: click.Context,
    branch: Optional[str],
    query: Optional[str],
    staging: bool,
    json_output: bool,
    output: Path,
    force: bool,
) -> None:
    """Browse registered models, or list them with --json."""
    try:
        client = _client(ctx, staging)
        if branch is None:
            if json_output:
                branch = "main"
            elif not _is_interactive():
                raise ModelRegistryError(
                    "Branch selection requires an interactive terminal. "
                    "Use --branch BRANCH."
                )
            else:
                branch = select_branch(client.branches())
        runs = client.catalog_models(branch, query=query)
        if json_output:
            echo_json({"branch": branch, "query": query, "models": runs})
            return
        if not runs:
            click.echo("No completed model builds are available on this branch.")
            return
        if not _is_interactive():
            raise ModelRegistryError(
                "Model selection requires an interactive terminal. Use --json to list models."
            )
        selected = select_run(runs)
        detail = _selected_model_detail(client, selected)
        card = model_card(detail, _taxonomy(client))
        render_model_card(card)
        auto_confirm = bool((ctx.find_root().obj or {}).get("yes"))
        if auto_confirm or click.confirm("Download this model artifact?", default=False):
            result = _download_run(client, selected, output, force)
            click.secho(f"Downloaded to {result['path']}", fg="green")
    except ModelRegistryError as exc:
        raise click.ClickException(str(exc)) from exc


@models_group.command("download")
@click.option(
    "-b",
    "--branch",
    default="main",
    show_default=True,
    help="Models repository branch.",
)
@click.option("--id", "model_id", required=True, help="Model ID.")
@click.option(
    "--variant",
    "variant_id",
    required=True,
    help="Model variant ID.",
)
@_staging_option
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path("models"),
    show_default=True,
    help="Root output directory.",
)
@click.option("--force", is_flag=True, help="Replace an existing downloaded artifact.")
@click.option("--json", "json_output", is_flag=True, help="Print a machine-readable result.")
@click.pass_context
def download_command(
    ctx: click.Context,
    branch: str,
    model_id: str,
    variant_id: str,
    staging: bool,
    output: Path,
    force: bool,
    json_output: bool,
) -> None:
    """Download the promoted full package for a model variant."""
    try:
        client = _client(ctx, staging)
        run = find_latest_run(client.catalog_models(branch), model_id, variant_id)
        result = _download_run(client, run, output, force)
    except ModelRegistryError as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        echo_json(result)
    else:
        click.secho(f"Downloaded to {result['path']}", fg="green")


def register_models_commands(parent: click.Group) -> None:
    parent.add_command(models_group)
