import json

import click

from .artifacts import (
    ENV_BASE_URLS,
    VulcanArtifactError,
    download_vulcan_artifacts,
    result_to_json,
)


@click.group(name="vulcan", help="Discover and download Vulcan build artifacts.")
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


def register_vulcan_commands(main):
    main.add_command(vulcan_group)
