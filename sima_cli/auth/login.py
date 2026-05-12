import os
import click
from InquirerPy import inquirer

from sima_cli.utils.config import set_auth_token, set_auth_username
from sima_cli.utils.config_loader import load_resource_config, artifactory_url
from sima_cli.utils.artifactory import exchange_identity_token, validate_token
from sima_cli.auth.devportal import login_external, docker_login_with_token
from sima_cli.auth.auth0 import get_or_refresh_tokens


def login(method: str = "external"):
    """
    Dispatch login based on the specified method.

    Args:
        method (str): 'external' (public developer portal) or 'internal' (Artifactory).
    """
    try:
        if method == "internal":
            return login_internal()
        else:
            return login_external()

    except Exception as e:
        click.secho(f"❌ Unable to login: {e}", fg="red")
        return None

def login_internal():
    """
    Internal login using a manually provided identity token.

    Flow:
    1. Prompt for identity token.
    2. Validate the token using the configured validation URL.
    3. If valid, exchange it for a short-lived access token.
    4. Save the short-lived token to local config.
    5. Also calls docker to login
    """

    cfg = load_resource_config()
    auth_cfg = cfg.get("internal", {}).get("auth", {})
    base_url = artifactory_url()
    validate_url = auth_cfg.get("validate_url")
    internal_url = auth_cfg.get("internal_url")
    validate_url = f"{base_url}/{validate_url}"
    exchange_url = f"{base_url}/{internal_url}"

    if not validate_url or not exchange_url:
        click.echo("❌ Missing 'validate_url' or 'internal_url' in internal auth config.")
        click.echo("👉 Please check ~/.sima-cli/resources_internal.yaml")
        return

    # Prefer environment variable to avoid prompting in CI/automation.
    identity_token = os.getenv("ARTIFACTORY_ID_TOKEN")
    if identity_token:
        click.echo("🔐 Using ARTIFACTORY_ID_TOKEN from environment.")
    else:
        # Prompt for identity token
        click.echo("🔐 Paste your Artifactory identity token below.")
        identity_token = click.prompt("Identity Token", hide_input=True)

    if not identity_token or len(identity_token.strip()) < 10:
        return click.echo("❌ Invalid or empty token.")

    # Step 1: Validate the identity token
    is_valid, username = validate_token(identity_token, validate_url)
    if not is_valid:
        return click.echo("❌ Token validation failed. Please check your identity token.")

    click.echo(f"✅ Identity token is valid for {username}")

    # Step 2: Exchange for a short-lived access token (default: 30 days)
    access_token, user_name = exchange_identity_token(identity_token, exchange_url, expires_in=2592000)

    if not access_token:
        return click.echo("❌ Failed to acquire short-lived access token.")

    # Step 3: Save token + username to internal auth config
    set_auth_token(access_token, internal=True)
    if user_name:
        set_auth_username(user_name, internal=True)
    click.echo(f"💾 Short-lived access token saved successfully for {user_name} (valid for 30 days).")

    try:
        docker_username = user_name or username or os.getenv("ARTIFACTORY_USER")
        if not docker_username:
            click.echo("⚠️  Artifactory username is unavailable; Docker auth may fail. Set ARTIFACTORY_USER and run `sima-cli -i login` again.")
            docker_username = "sima_cli"
        docker_login_with_token(docker_username, access_token)
    except Exception as e:
        click.echo(f"⚠️  Docker credential setup failed: {e}")
