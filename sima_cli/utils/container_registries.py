import click
import subprocess
import os
import platform
import shutil
import sys

import requests

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


def _is_ghcr_auth_error(err: subprocess.CalledProcessError) -> bool:
    text = _pull_error_text(err).lower()
    return any(
        marker in text
        for marker in (
            "unauthorized",
            "authentication required",
            "denied",
            "forbidden",
            "status code 401",
            "status code 403",
            "unexpected status: 401",
            "unexpected status: 403",
        )
    )


def _normalize_github_token(token: str) -> str:
    value = (token or "").strip()
    lowered = value.lower()
    for prefix in ("bearer ", "token "):
        if lowered.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _github_username_for_token(token: str) -> str:
    username = os.getenv("GITHUB_ACTOR") or os.getenv("GITHUB_USER")
    if username:
        return username.strip()

    try:
        response = requests.get(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
        if response.status_code == 200:
            login = response.json().get("login")
            if isinstance(login, str) and login.strip():
                return login.strip()
    except requests.RequestException:
        pass

    return ""


def _run_quiet(command) -> bool:
    proc = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode == 0


def _install_github_cli() -> bool:
    if shutil.which("gh"):
        return True

    if not sys.stdin.isatty():
        click.echo(
            "❌ GitHub CLI is required for interactive authorization. "
            "Install it from https://cli.github.com/ or provide GITHUB_TOKEN."
        )
        return False

    if not click.confirm("🔧 GitHub CLI is not installed. Install it now?", default=True):
        click.echo(
            "ℹ️  Install GitHub CLI from https://cli.github.com/ and rerun the sima-cli command."
        )
        return False

    system = platform.system().lower()
    command = None
    if system == "darwin" and shutil.which("brew"):
        command = ["brew", "install", "gh"]
    elif system == "linux" and shutil.which("apt-get") and shutil.which("sudo"):
        click.echo("🔧 Installing GitHub CLI using the system package manager...")
        if not _run_quiet(["sudo", "apt-get", "update"]):
            click.echo("❌ Unable to refresh packages while installing GitHub CLI.")
            return False
        command = ["sudo", "apt-get", "install", "-y", "gh"]
    elif system == "windows" and shutil.which("winget"):
        command = ["winget", "install", "--id", "GitHub.cli", "--exact"]

    if command is None:
        click.echo(
            "❌ Automatic GitHub CLI installation is unavailable on this host. "
            "Install it from https://cli.github.com/ and rerun the sima-cli command."
        )
        return False

    click.echo("🔧 Installing GitHub CLI...")
    if not _run_quiet(command) or not shutil.which("gh"):
        click.echo(
            "❌ GitHub CLI installation did not complete. "
            "Install it from https://cli.github.com/ and rerun the sima-cli command."
        )
        return False

    click.secho("✅ GitHub CLI installed.", fg="green")
    return True


def _gh_output(*args) -> str:
    proc = subprocess.run(
        ["gh", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _github_credentials_from_gh():
    if not _install_github_cli():
        return "", ""

    if not _run_quiet(["gh", "auth", "status", "--hostname", "github.com"]):
        if not sys.stdin.isatty():
            click.echo(
                "❌ GitHub authorization is required. In a noninteractive environment, "
                "provide GITHUB_TOKEN with private-package access."
            )
            return "", ""

        click.echo("🌐 Starting one-time GitHub authorization...")
        login = subprocess.run(
            [
                "gh",
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--web",
                "--git-protocol",
                "https",
                "--scopes",
                "read:packages",
            ],
            check=False,
        )
        if login.returncode != 0:
            click.echo("❌ GitHub authorization was not completed.")
            return "", ""

    username = _gh_output("api", "user", "--jq", ".login")
    token = _normalize_github_token(
        _gh_output("auth", "token", "--hostname", "github.com")
    )
    return username, token


def _authorize_ghcr() -> bool:
    click.echo("🔐 This private image requires GitHub authorization.")

    token = _normalize_github_token(os.getenv("GITHUB_TOKEN", ""))
    username = _github_username_for_token(token) if token else ""
    if token and not username:
        click.echo(
            "❌ GITHUB_TOKEN is set, but its GitHub username could not be determined. "
            "Set GITHUB_USER or GITHUB_ACTOR and rerun the sima-cli command."
        )
        return False

    if not token:
        username, token = _github_credentials_from_gh()

    if not username or not token:
        return False

    docker_login_with_token(username, token, "ghcr.io")
    click.secho("✅ GitHub authorization configured for private images.", fg="green")
    return True


def _ghcr_access_error(err: subprocess.CalledProcessError) -> click.ClickException:
    detail = _pull_error_text(err) or str(err)
    return click.ClickException(
        "❌ GitHub Container Registry denied access after authorization.\n"
        "↳ Confirm that your GitHub account can read this private package.\n"
        "↳ PAT-based access requires read:packages, and the token may need organization SSO authorization.\n"
        "↳ GitHub may report an inaccessible private image or missing tag using the same response.\n"
        f"↳ Registry error: {detail}"
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
    if scheme == "ghcr" and image_name.startswith("ghcr.io/"):
        image_name = image_name[len("ghcr.io/"):]

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

        if scheme == "ghcr" and _is_ghcr_auth_error(e):
            if not _authorize_ghcr():
                raise click.ClickException(
                    "❌ GitHub authorization is required to install this private image.\n"
                    "↳ Run the sima-cli command again after completing GitHub CLI authorization, "
                    "or provide GITHUB_TOKEN for a noninteractive environment."
                )

            click.echo("📦 Retrying container image pull after GitHub authorization...")
            try:
                pulled_ref = _pull_container_from_registry(
                    registry_url, f"{image_name}{separator}{version or 'latest'}"
                )
                if pulled_ref:
                    click.echo(f"✅ Successfully pulled container after GitHub authorization: {pulled_ref}")
                    return full_image_ref
            except subprocess.CalledProcessError as retry_error:
                if _is_dns_resolution_error(retry_error):
                    detail = _pull_error_text(retry_error) or str(retry_error)
                    raise click.ClickException(
                        "❌ Container pull failed due to DNS/network resolution after GitHub authorization.\n"
                        f"↳ Registry error: {detail}"
                    )
                raise _ghcr_access_error(retry_error)

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
