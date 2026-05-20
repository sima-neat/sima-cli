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


AVAILABLE_ENVIRONMENTS = {"dev"}


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
    resolved_environment = (environment or "production").lower()

    if resolved_environment not in AVAILABLE_ENVIRONMENTS:
        raise click.ClickException(
            f"Vulcan {resolved_environment} environment is not yet available to use. "
            "Please use --env dev for now."
        )

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


@click.group(name="vulcan", help="Discover and download Vulcan build artifacts.", hidden=True)
@click.option(
    "--env",
    "environment",
    type=click.Choice(sorted(ENV_BASE_URLS), case_sensitive=False),
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
def vulcan_group(ctx, environment, base_url):
    ctx.ensure_object(dict)
    ctx.obj["vulcan_environment"] = environment.lower() if environment else None
    ctx.obj["vulcan_base_url"] = base_url


@vulcan_group.command("download")
@click.argument("repo", required=False)
@click.argument("ref", required=False)
@click.option(
    "--env",
    "environment",
    type=click.Choice(sorted(ENV_BASE_URLS), case_sensitive=False),
    default=None,
    help="Artifact environment. Overrides `sima-cli vulcan --env`.",
)
@click.option(
    "--base-url",
    default=None,
    envvar="SIMA_VULCAN_BASE_URL",
    help="Override the artifact base URL. Overrides `sima-cli vulcan --base-url`.",
)
@click.option(
    "-o",
    "--output",
    default="vulcan-downloads",
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
def download(ctx, repo, ref, environment, base_url, output, artifact_patterns, json_output):
    """Download artifacts for REPO and branch or tag REF."""
    resolved_environment = (
        environment
        or ctx.obj.get("vulcan_environment")
        or "production"
    ).lower()
    resolved_base_url = base_url or ctx.obj.get("vulcan_base_url")

    if resolved_environment not in AVAILABLE_ENVIRONMENTS:
        raise click.ClickException(
            f"Vulcan {resolved_environment} environment is not yet available to use. "
            "Please use --env dev for now."
        )

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


@vulcan_group.command("install")
@click.argument("target")
@click.option(
    "--env",
    "environment",
    type=click.Choice(sorted(ENV_BASE_URLS), case_sensitive=False),
    default=None,
    help="Artifact environment. Overrides `sima-cli vulcan --env`.",
)
@click.option(
    "--base-url",
    default=None,
    envvar="SIMA_VULCAN_BASE_URL",
    help="Override the artifact base URL. Overrides `sima-cli vulcan --base-url`.",
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
def install(ctx, target, environment, base_url, install_dir, package_type, force, json_output):
    """Install a Vulcan package from TARGET.

    TARGET supports REPO, REPO@branch, REPO@branch:spec, REPO@latest, or
    REPO@githash. If no branch or spec is provided, latest main is used.
    """
    return install_vulcan_package(
        target=target,
        environment=environment or ctx.obj.get("vulcan_environment") or "production",
        base_url=base_url or ctx.obj.get("vulcan_base_url"),
        package_type=package_type,
        install_dir=install_dir,
        force=force,
        json_output=json_output,
    )


def register_vulcan_commands(main):
    main.add_command(vulcan_group)
