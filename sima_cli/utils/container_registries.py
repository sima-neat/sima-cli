import click
import subprocess
import os

from sima_cli.utils.config import get_auth_token, get_auth_username, set_auth_username
from sima_cli.utils.config_loader import load_resource_config, artifactory_url
from sima_cli.utils.artifactory import validate_token
from sima_cli.auth.devportal import (
    resolve_public_registry,
    ensure_docker_available,
    docker_login_with_token,
    get_sima_docker_env,
)
from sima_cli.utils.docker import check_and_start_docker


def _resolve_internal_docker_username(internal_token: str) -> str:
    username = get_auth_username(internal=True)
    if username:
        return username

    # Backward compatibility path: old config may have token but not username.
    try:
        cfg = load_resource_config()
        auth_cfg = cfg.get("internal", {}).get("auth", {})
        validate_path = auth_cfg.get("validate_url")
        base_url = artifactory_url()
        if validate_path and base_url:
            validate_url = f"{base_url}/{validate_path}"
            is_valid, discovered_user = validate_token(internal_token, validate_url)
            if is_valid and discovered_user:
                set_auth_username(discovered_user, internal=True)
                return discovered_user
    except Exception:
        pass

    return os.getenv("ARTIFACTORY_USER") or "sima_cli"

def _pull_container_from_registry(registry_url: str, image_ref: str) -> str:
    """
    Pulls container image from given registry and returns its local reference.
    """
    if ensure_docker_available():
        full_image = f"{registry_url.rstrip('/')}/{image_ref}"
        click.echo(f"📦 Pulling container image: {full_image}")
        proc = subprocess.run(
            ["docker", "pull", full_image],
            check=False,
            env=get_sima_docker_env(),
        )
        if proc.returncode != 0:
            # Re-run once with captured output strictly for better diagnostics.
            diag = subprocess.run(
                ["docker", "pull", full_image],
                check=False,
                env=get_sima_docker_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            raise subprocess.CalledProcessError(
                proc.returncode,
                proc.args,
                output=diag.stdout,
                stderr=diag.stderr,
            )
        return full_image


def _pull_error_text(err: subprocess.CalledProcessError) -> str:
    return ((err.stderr or "") + "\n" + (err.output or "")).strip()


def _is_dns_resolution_error(err: subprocess.CalledProcessError) -> bool:
    text = _pull_error_text(err).lower()
    return any(
        marker in text
        for marker in (
            "server misbehaving",
            "temporary failure in name resolution",
            "no such host",
            "i/o timeout",
            "lookup ",
            "name or service not known",
        )
    )

def docker_logout_from_registry(registry: str = "artifacts.eng.sima.ai"):
    """
    Logout from the specified Docker registry.
    Removes stored credentials (even if managed by a credential helper).
    Safe to call multiple times — no error if already logged out.
    """
    if ensure_docker_available():
        click.echo(f"🐳 Logging out of Docker registry")

        try:
            proc = subprocess.run(
                ["docker", "logout", registry],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=get_sima_docker_env(),
            )

            if proc.returncode == 0:
                click.echo(proc.stdout.decode().strip() or f"✅ Logged out from {registry}")
            else:
                msg = proc.stderr.decode().strip() or proc.stdout.decode().strip()
                if "not logged in" in msg.lower():
                    click.echo(f"ℹ️  Already logged out from {registry}")
                else:
                    raise click.ClickException(f"Docker logout failed: {msg}")

        except Exception as e:
            raise click.ClickException(f"⚠️  Unexpected error during Docker logout: {e}")


def _select_artifactory_version(image_name: str) -> str:
    """
    Query available tags for an image from SiMa Artifactory and prompt user to select one.

    Args:
        image_name (str): The image name under sima-docker (e.g., 'modelsdk').

    Returns:
        str: The user-selected tag (e.g., 'latest_develop').

    Raises:
        click.ClickException: If no tags are found or query fails.
    """
    import requests
    from InquirerPy import inquirer

    click.echo(f"🔍 Querying available versions for {image_name} from Artifactory...")

    # Retrieve internal auth token, if available
    token = get_auth_token(internal=True)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if not token:
        click.secho("⚠️  Not authorized to access Artifactory; please run `sima-cli -i login` with your Identity Token.", fg='yellow')
        exit(-1)

    tags_url = (
        f"https://artifacts.eng.sima.ai/artifactory/api/docker/"
        f"sima-docker/v2/{image_name}/tags/list"
    )

    try:
        session = requests.Session()
        session.trust_env = False
        resp = session.get(tags_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise click.ClickException(
                f"❌ Failed to query tags for '{image_name}': {resp.status_code} {resp.text}"
            )

        tags = sorted(resp.json().get("tags", []))
        if not tags:
            raise click.ClickException(f"❌ No tags found for image '{image_name}'")

        # Interactive tag selection
        return inquirer.fuzzy(
            message=f"Select a version for {image_name}:",
            choices=tags,
            default="latest" if "latest" in tags else '',
        ).execute()

    except requests.exceptions.RequestException as e:
        raise click.ClickException(f"❌ Network error while querying Artifactory: {e}")


def install_from_cr(resource_spec: str, internal: bool = False) -> str:
    """
    Install a component from a container registry resource.

    Args:
        resource_spec (str): Resource string in the form:
            cr:<image>[:tag] or cr:<image>@<digest>
            ghcr:<owner>/<image>[:tag] or ghcr:<owner>/<image>@<digest>
        internal (bool): Whether to use SiMa internal Artifactory registry.

    Examples:
        install_from_cr("cr:modelsdk:latest_develop", internal=True)
        install_from_cr("cr:modelsdk@sha256:abcd1234", internal=False)
        install_from_cr("ghcr:simaai/my-image:latest", internal=False)
    """
    if not ensure_docker_available():
        click.echo("⚠️  Docker not available; skipping container installation.")
        return ""

    if not check_and_start_docker():
        click.echo("⚠️  Unable to start Docker on this platform.")
        return ""

    scheme = None
    if resource_spec.startswith("cr:"):
        scheme = "cr"
        resource_spec = resource_spec[3:].strip()
    elif resource_spec.startswith("ghcr:"):
        scheme = "ghcr"
        resource_spec = resource_spec[5:].strip()
    else:
        raise click.ClickException("❌ Unsupported container resource format. Use 'cr:' or 'ghcr:'.")

    # Parse image and version/digest
    if "@" in resource_spec:
        image_name, version = resource_spec.split("@", 1)
        separator = "@"
    elif ":" in resource_spec:
        image_name, version = resource_spec.split(":", 1)
        separator = ":"
    else:
        image_name, version, separator = resource_spec, None, ":"

    # Normalize optional ghcr.io/ prefix if user included it.
    image_parts = image_name.split("/", 1)
    if scheme == "ghcr" and len(image_parts) == 2 and image_parts[0].lower() == "ghcr.io":
        image_name = image_parts[1]

    if scheme == "ghcr":
        if internal:
            click.secho("ℹ️  '--internal' does not apply to ghcr:. Pulling from public ghcr.io.", fg="yellow")
        registry_url = "ghcr.io"
    else:
        # Resolve registry, default to Artifactory, if external resolve again.
        registry_url = "artifacts.eng.sima.ai/sima-docker"

        if not internal:
            try:
                token, registry_url = resolve_public_registry("ecr")
                if not token or not registry_url:
                    click.secho("⚠️  Failed to resolve container registry or token is missing.", fg="yellow")
                    return None

                success = docker_login_with_token("sima_cli", token, registry_url)
                if success:
                    crtype = 'internal' if internal else 'SiMa.ai'
                    click.secho(f"✅ Logged in to {crtype} container registry", fg="green")
                else:
                    click.secho(f"❌ Docker login to container registry failed", fg="red")

            except Exception as e:
                click.secho(f"❌ Unexpected error during container login: {e}", fg="red")
                return None
        
    # If internal and version not specified, prompt for version
    if scheme == "cr" and internal and version is None:
        version = _select_artifactory_version(image_name)

    # Compose final ref
    full_image_ref = f"{registry_url}/{image_name}{separator}{version or 'latest'}"
    internal_username = None

    # Auto-login if internal and not logged in
    if scheme == "cr" and internal and not get_auth_token(internal=internal):
        click.echo(
            f"⚠️  No internal token found; please login as "
            + click.style("sima-cli -i login", fg="cyan", bold=True)
        )
        return
    elif scheme == "cr" and internal:
        # Ensure the sima-cli Docker profile exists even for users upgrading from
        # older versions that previously used global docker login.
        try:
            internal_token = get_auth_token(internal=True)
            if not internal_token:
                click.echo(
                    f"⚠️  No internal token found; please login as "
                    + click.style("sima-cli -i login", fg="cyan", bold=True)
                )
                return
            internal_username = _resolve_internal_docker_username(internal_token)
            docker_login_with_token(internal_username, internal_token, registry_url)
            click.secho("✅ Internal container auth profile refreshed", fg="green")
        except Exception as e:
            click.secho(f"❌ Failed to prepare internal container auth profile: {e}", fg="red")
            return

    # Pull image
    try:
        registry_url = registry_url.replace('https://', '')
        pulled_ref = _pull_container_from_registry(
            registry_url, f"{image_name}{separator}{version or 'latest'}"
        )

        if pulled_ref:
            click.echo(f"✅ Successfully pulled container: {pulled_ref}")
    
    except subprocess.CalledProcessError as e:
        if _is_dns_resolution_error(e):
            detail = _pull_error_text(e) or str(e)
            if internal and "artifacts.eng.sima.ai" in registry_url:
                raise click.ClickException(
                    "❌ Docker pull failed due to DNS/network resolution to internal Artifactory.\n"
                    "↳ This is not an auth issue. Verify VPN connectivity and DNS routing for artifacts.eng.sima.ai.\n"
                    f"↳ Docker error: {detail}"
                )
            raise click.ClickException(
                "❌ Docker pull failed due to DNS/network resolution.\n"
                f"↳ Docker error: {detail}"
            )

        # Token may have expired, or auth profile may be stale/missing.
        # Refresh auth once and retry pull.
        retry_error = None
        if scheme == "cr":
            click.secho("⚠️  Docker pull failed. Refreshing registry auth and retrying once...", fg="yellow")
            try:
                if internal:
                    internal_token = get_auth_token(internal=True)
                    if not internal_token:
                        raise click.ClickException("Missing internal token. Please run `sima-cli -i login`.")
                    if not internal_username:
                        internal_username = _resolve_internal_docker_username(internal_token)
                    docker_login_with_token(internal_username, internal_token, registry_url)
                else:
                    token, resolved_registry = resolve_public_registry("ecr")
                    if not token or not resolved_registry:
                        raise click.ClickException("Failed to refresh ECR token/registry endpoint.")
                    registry_url = resolved_registry.replace("https://", "")
                    docker_login_with_token("sima_cli", token, resolved_registry)

                pulled_ref = _pull_container_from_registry(
                    registry_url, f"{image_name}{separator}{version or 'latest'}"
                )
                if pulled_ref:
                    click.echo(f"✅ Successfully pulled container after auth refresh: {pulled_ref}")
                    return full_image_ref
            except Exception as retry_e:
                retry_error = retry_e

        if retry_error is not None:
            retry_text = _pull_error_text(retry_error) if isinstance(retry_error, subprocess.CalledProcessError) else str(retry_error)
            first_text = _pull_error_text(e) or str(e)
            raise click.ClickException(
                f"❌ Docker pull failed: {first_text}\n"
                f"↳ Retry after auth refresh failed: {retry_text}"
            )

        raise click.ClickException(f"❌ Docker pull failed: {_pull_error_text(e) or str(e)}")

    return full_image_ref
