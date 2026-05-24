import json

import click

from .artifacts import (
    ENV_BASE_URLS,
    VulcanArtifactError,
    download_vulcan_artifacts,
    resolve_install_metadata_url,
    result_to_json,
)
from sima_cli.install.metadata_installer import install_from_metadata


ENV_ALIASES = {
    "stg": "staging",
    "stage": "staging",
    "prd": "production",
    "prod": "production",
}
ENV_METAVAR = "[dev|stg|staging|prd|prod|production]"

AVAILABLE_ENVIRONMENTS = {"dev", "staging"}


def normalize_environment(environment):
    if environment is None:
        return None
    value = environment.strip().lower()
    resolved = ENV_ALIASES.get(value, value)
    if resolved not in ENV_BASE_URLS:
        valid = ", ".join(["dev", "stg", "staging", "prd", "prod", "production"])
        raise click.BadParameter(f"must be one of: {valid}")
    return resolved


def _normalize_environment_option(ctx, param, value):
    return normalize_environment(value)


def _resolve_environment(environment, environment_flag, default="production"):
    resolved_environment = normalize_environment(environment) if environment else None
    resolved_flag = normalize_environment(environment_flag) if environment_flag else None
    if resolved_environment and resolved_flag and resolved_environment != resolved_flag:
        raise click.ClickException(
            f"Conflicting artifact environments: --env {resolved_environment} and shortcut {resolved_flag}."
        )
    return resolved_environment or resolved_flag or default


def _environment_shortcut_options(function):
    function = click.option("--prd", "--prod", "environment_flag", flag_value="production", help="Use production artifacts.")(function)
    function = click.option("--stg", "--staging", "environment_flag", flag_value="staging", help="Use staging artifacts.")(function)
    function = click.option("--dev", "environment_flag", flag_value="dev", default=None, help="Use dev artifacts.")(function)
    return function


def _ensure_available_environment(environment):
    if environment not in AVAILABLE_ENVIRONMENTS:
        raise click.ClickException(
            f"Artifact environment '{environment}' is not yet available to use. "
            "Please use --env dev or --env staging for now."
        )


def install_vulcan_package(
    *,
    target,
    environment,
    base_url=None,
    package_type=None,
    install_dir=".",
    force=False,
    json_output=False,
):
    resolved_environment = normalize_environment(environment) or "production"
    _ensure_available_environment(resolved_environment)

    try:
        result = resolve_install_metadata_url(
            environment=resolved_environment,
            target=target,
            base_url=base_url,
            package_type=package_type,
        )
    except VulcanArtifactError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(json.dumps({
            "environment": result.environment,
            "base_url": result.base_url,
            "repository": result.repository,
            "ref": result.ref,
            "ref_key": result.ref_key,
            "requested_spec": result.requested_spec,
            "resolved_spec": result.resolved_spec,
            "metadata_url": result.metadata_url,
        }, indent=2))
        return None

    click.echo(f"Environment: {result.environment}")
    click.echo(f"Repository:  {result.repository}")
    click.echo(f"Ref:         {result.ref}")
    click.echo(f"Spec:        {result.resolved_spec}")
    click.echo(f"Metadata:    {result.metadata_url}")
    return install_from_metadata(
        metadata_url=result.metadata_url,
        internal=False,
        install_dir=install_dir,
        force=force,
    )


def _set_artifact_context(ctx, environment, environment_flag, base_url):
    ctx.ensure_object(dict)
    ctx.obj["vulcan_environment"] = _resolve_environment(environment, environment_flag, default=None)
    ctx.obj["vulcan_base_url"] = base_url


@click.group(name="neat", help="Discover, download, and install Neat build artifacts.")
@_environment_shortcut_options
@click.option(
    "--env",
    "environment",
    metavar=ENV_METAVAR,
    callback=_normalize_environment_option,
    default=None,
    help="Artifact environment. Defaults to production.",
)
@click.option(
    "--base-url",
    default=None,
    envvar="SIMA_NEAT_BASE_URL",
    help="Override the artifact base URL.",
)
@click.pass_context
def neat_group(ctx, environment, environment_flag, base_url):
    _set_artifact_context(ctx, environment, environment_flag, base_url)


@click.group(name="vulcan", help="Discover and download Vulcan build artifacts.", hidden=True)
@_environment_shortcut_options
@click.option(
    "--env",
    "environment",
    metavar=ENV_METAVAR,
    callback=_normalize_environment_option,
    default=None,
    help="Artifact environment. Defaults to production.",
)
@click.option(
    "--base-url",
    default=None,
    envvar="SIMA_VULCAN_BASE_URL",
    help="Override the artifact base URL.",
)
@click.pass_context
def vulcan_group(ctx, environment, environment_flag, base_url):
    _set_artifact_context(ctx, environment, environment_flag, base_url)


@click.command("download")
@click.argument("repo", required=False)
@click.argument("ref", required=False)
@_environment_shortcut_options
@click.option(
    "--env",
    "environment",
    metavar=ENV_METAVAR,
    callback=_normalize_environment_option,
    default=None,
    help="Artifact environment. Overrides the parent --env.",
)
@click.option(
    "--base-url",
    default=None,
    envvar="SIMA_NEAT_BASE_URL",
    help="Override the artifact base URL. Overrides the parent --base-url.",
)
@click.option(
    "-o",
    "--output",
    default="neat-downloads",
    show_default=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=str),
    help="Output directory.",
)
@click.option(
    "--artifact",
    "artifact_patterns",
    multiple=True,
    help="Artifact path glob to download from manifest.json. May be repeated.",
)
@click.option("--json", "json_output", is_flag=True, help="Print a machine-readable JSON summary.")
@click.pass_context
def download(ctx, repo, ref, environment, environment_flag, base_url, output, artifact_patterns, json_output):
    """Download artifacts for REPO and branch or tag REF."""
    resolved_environment = (
        _resolve_environment(environment, environment_flag, default=None)
        or ctx.obj.get("vulcan_environment")
        or "production"
    )
    resolved_base_url = base_url or ctx.obj.get("vulcan_base_url")
    _ensure_available_environment(resolved_environment)

    try:
        result, warning = download_vulcan_artifacts(
            environment=resolved_environment,
            repository=repo,
            ref=ref,
            output=output,
            artifact_patterns=artifact_patterns,
            base_url=resolved_base_url,
        )
    except VulcanArtifactError as exc:
        raise click.ClickException(str(exc)) from exc

    if warning:
        click.echo(f"Warning: {warning}", err=True)

    if json_output:
        click.echo(json.dumps(result_to_json(result), indent=2))
        return

    click.echo(f"Environment: {result.environment}")
    click.echo(f"Repository:  {result.repository}")
    click.echo(f"Ref:         {result.ref}")
    click.echo(f"Latest tag:  {result.latest_tag}")
    click.echo(f"Manifest:    {result.manifest_url}")
    click.echo(f"Output:      {result.output_dir}")
    click.echo("Files:")
    for path in result.files:
        click.echo(f"  {path}")


@click.command("install")
@click.argument("target")
@_environment_shortcut_options
@click.option(
    "--env",
    "environment",
    metavar=ENV_METAVAR,
    callback=_normalize_environment_option,
    default=None,
    help="Artifact environment. Overrides the parent --env.",
)
@click.option(
    "--base-url",
    default=None,
    envvar="SIMA_NEAT_BASE_URL",
    help="Override the artifact base URL. Overrides the parent --base-url.",
)
@click.option(
    "-d",
    "--install-dir",
    default=".",
    show_default=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=str),
    help="Directory where package resources are downloaded and installed.",
)
@click.option(
    "-t",
    "--type",
    "package_type",
    help="Install metadata variant metadata-<type>.json instead of metadata.json.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    help="Force installation even if compatibility checks fail.",
)
@click.option("--json", "json_output", is_flag=True, help="Print resolved metadata URL and exit.")
@click.pass_context
def install(ctx, target, environment, environment_flag, base_url, install_dir, package_type, force, json_output):
    """Install a Neat artifact package from TARGET.

    TARGET supports REPO, REPO@branch, REPO@branch:spec, REPO@latest, or
    REPO@githash. If no branch or spec is provided, latest main is used.
    """
    return install_vulcan_package(
        target=target,
        environment=(
            _resolve_environment(environment, environment_flag, default=None)
            or ctx.obj.get("vulcan_environment")
            or "production"
        ),
        base_url=base_url or ctx.obj.get("vulcan_base_url"),
        package_type=package_type,
        install_dir=install_dir,
        force=force,
        json_output=json_output,
    )


def register_vulcan_commands(main):
    neat_group.add_command(download)
    neat_group.add_command(install)
    main.add_command(neat_group)
    vulcan_group.add_command(download)
    vulcan_group.add_command(install)
    main.add_command(vulcan_group)
