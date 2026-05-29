import os
import click
import getpass
import requests
import json
import subprocess
import shutil
import base64
import hashlib
import webbrowser

from typing import Optional
from http.cookiejar import MozillaCookieJar
from rich.console import Console
from rich.panel import Panel
from sima_cli.__version__ import __version__
from sima_cli.utils.env import is_sima_board
from sima_cli.auth.auth0 import (
    decode_jwt_payload,
    extract_email,
    access_token_has_doc_access,
    get_or_refresh_tokens,
    get_cached_access_token,
    load_tokens,
)

HOME_DIR = os.path.expanduser("~/.sima-cli")
COOKIE_JAR_PATH = os.path.join(HOME_DIR, ".sima-cli-cookies.txt")
ACCESS_REQUEST_STATE_PATH = os.path.join(HOME_DIR, ".access-requests.json")
LEGACY_SIMA_DOCKER_CONFIG_DIR = os.path.join(HOME_DIR, "docker-config")
SNAP_SIMA_DOCKER_CONFIG_DIR = os.path.join(os.path.expanduser("~"), "snap", "docker", "common", ".sima-cli", "docker-config")
HOST_DOCKER_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".docker", "config.json")
HOST_DOCKER_CONTEXTS_DIR = os.path.join(os.path.expanduser("~"), ".docker", "contexts")

# Base URLs depending on environment
# Detect staging or production environment
is_staging = False
if os.getenv("USE_STAGING_DEV_PORTAL", "false").lower() in ("1", "true", "yes"):
    DEV_PORTAL = "https://discourse-dev.sima.ai"
    DOCS_PORTAL = "https://docs-dev.sima.ai"
    is_staging = True
else:
    DEV_PORTAL = "https://developer.sima.ai"
    DOCS_PORTAL = "https://docs.sima.ai"

# Derived endpoints
LOGIN_URL = f"{DEV_PORTAL}/session"
DEV_PORTAL_LOGIN_URL = f"{DEV_PORTAL}/login"
DUMMY_CHECK_URL = f"{DOCS_PORTAL}/pkg_downloads/validation"
ACCESS_REQUEST_FORM_URL = "https://www2.sima.ai/l/1041271/2025-05-05/37bndg"
USER_INFO_CLAIM = "https://auth.sima.ai/user_info"
_ACCESS_REQUEST_HANDLED = False
console = Console()


HEADERS = {
    "User-Agent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) sima-cli/{__version__} Chrome/137.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{DEV_PORTAL}/login",
    "Origin": f"{DEV_PORTAL}",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

def _handle_eula_flow(session: requests.Session, username: str, domain: str) -> bool:
    try:
        click.echo("\n📄 To continue, you must accept the End-User License Agreement (EULA).")
        click.echo("👉 Please sign in to Developer Portal in your browser and accept the EULA if prompted.")
        click.echo("👉 If you were not prompted with the EULA acceptance popup, try opening the page in an incognito browser.")
        try:
            opened = webbrowser.open(DEV_PORTAL_LOGIN_URL)
        except Exception:
            opened = False

        if opened:
            click.echo(f"\nOpening Developer Portal sign-in page: {DEV_PORTAL_LOGIN_URL}\n")
        else:
            click.echo(f"\nOpen this sign-in page manually: {DEV_PORTAL_LOGIN_URL}\n")

        if not click.confirm("✅ Have you signed in to Developer Portal and accepted the EULA?", default=True):
            click.echo("❌ EULA acceptance is required to continue.")
            return False

        # try login external workflow and force to retrieve the access token again
        return login_external(force=True, loginDocker=False)

    except Exception as e:
        click.echo(f"❌ Error during EULA flow: {e}")
        return False


def _first_non_empty(*values) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _extract_user_info(claims: dict) -> dict:
    user_info = claims.get(USER_INFO_CLAIM)
    if isinstance(user_info, dict):
        return user_info

    for value in claims.values():
        if isinstance(value, dict) and any(
            key in value for key in ("email", "company", "country", "industry")
        ):
            return value

    return {}


def _load_identity_claims() -> dict:
    tokens = load_tokens() or {}
    id_token = tokens.get("id_token")
    if not id_token:
        return {}
    return decode_jwt_payload(id_token)


def _build_access_request_payload(claims: dict, message: str) -> dict:
    user_info = _extract_user_info(claims)
    return {
        "message": message,
        "first_name": _first_non_empty(
            user_info.get("first_name"),
            user_info.get("given_name"),
            claims.get("given_name"),
        ),
        "last_name": _first_non_empty(
            user_info.get("last_name"),
            user_info.get("family_name"),
            claims.get("family_name"),
        ),
        "email": _first_non_empty(user_info.get("email"), extract_email(claims)),
        "company": _first_non_empty(user_info.get("company")),
        "country": _first_non_empty(user_info.get("country")),
        "account_type": "Prospect",
        "industry": _first_non_empty(user_info.get("industry")),
    }


def _access_request_key(claims: dict) -> str:
    return _first_non_empty(extract_email(claims), claims.get("sub"), claims.get("nickname"))


def _load_access_request_state() -> dict:
    if not os.path.exists(ACCESS_REQUEST_STATE_PATH):
        return {}
    try:
        with open(ACCESS_REQUEST_STATE_PATH, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_access_request_state(state: dict):
    os.makedirs(HOME_DIR, exist_ok=True)
    with open(ACCESS_REQUEST_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _has_submitted_access_request(claims: dict) -> bool:
    key = _access_request_key(claims)
    if not key:
        return False
    return bool(_load_access_request_state().get(key))


def _mark_access_request_submitted(claims: dict):
    key = _access_request_key(claims)
    if not key:
        return
    state = _load_access_request_state()
    state[key] = True
    _save_access_request_state(state)


def _logout_external_credentials():
    target_files = [".sima-cli-cookies.txt", ".sima-cli-csrf.json", ".tokens.json"]
    for filename in target_files:
        full_path = os.path.join(HOME_DIR, filename)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception as e:
                click.echo(f"⚠️ Failed to delete {full_path}: {e}", err=True)

    if os.path.isdir(LEGACY_SIMA_DOCKER_CONFIG_DIR):
        try:
            shutil.rmtree(LEGACY_SIMA_DOCKER_CONFIG_DIR)
        except Exception as e:
            click.echo(f"⚠️ Failed to delete {LEGACY_SIMA_DOCKER_CONFIG_DIR}: {e}", err=True)

    click.echo("✅ Logged out successfully.")


def _show_access_request_pending_message(already_submitted: bool = False):
    if already_submitted:
        click.secho("✅ Your access request has already been submitted.", fg="green")
    else:
        click.secho("✅ Your access request has been submitted.", fg="green")
    console.print(
        Panel(
            "\n".join(
                [
                    "SiMa is reviewing your request and will grant access shortly.",
                    "Please look out for an email from marketing@marketing.sima.ai.",
                    "Once access is granted, run `sima-cli login` again.",
                ]
            ),
            border_style="yellow",
            style="yellow",
            expand=False,
        )
    )


def _show_limited_access_pending_message():
    click.secho("✅ You are signed in with limited Developer Portal access.", fg="green")
    console.print(
        Panel(
            "\n".join(
                [
                    "SiMa is reviewing your account and will grant full access shortly.",
                    "Please look out for an email from marketing@marketing.sima.ai.",
                    "Once access is granted, run `sima-cli login` again.",
                ]
            ),
            border_style="yellow",
            style="yellow",
            expand=False,
        )
    )


def _show_access_request_info_panel():
    console.print(
        Panel(
            "\n".join(
                [
                    "Welcome to the SiMa.ai Developer Portal.",
                    "",
                    "To download digital assets from the Developer Portal, "
                    "SiMa's business team needs to grant access after you "
                    "provide a few additional details, including your project goal.",
                    "",
                    "Once approved, you will receive an email from "
                    "marketing@marketing.sima.ai. Please check your email "
                    "client's spam filter.",
                ]
            ),
            title="Developer Portal Access Request",
            border_style="yellow",
            expand=False,
        )
    )


def _submit_access_request() -> bool:
    global _ACCESS_REQUEST_HANDLED
    _ACCESS_REQUEST_HANDLED = True

    claims = _load_identity_claims()
    if not claims:
        click.secho(
            "⚠️  We could not read your identity information. Please run `sima-cli login` again.",
            fg="yellow",
        )
        return False

    if _has_submitted_access_request(claims):
        _show_access_request_pending_message(already_submitted=True)
        _logout_external_credentials()
        return False

    _show_access_request_info_panel()
    message = click.prompt(
        click.style("Please briefly describe your project", fg="yellow"),
        type=str,
    ).strip()
    payload = _build_access_request_payload(claims, message)

    try:
        response = requests.post(ACCESS_REQUEST_FORM_URL, data=payload, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        click.secho(f"❌ Failed to submit your access request: {e}", fg="red")
        return False

    _mark_access_request_submitted(claims)
    _show_access_request_pending_message()
    _logout_external_credentials()
    return False


def _is_session_valid(session: requests.Session) -> bool:
    try:
        response = session.get(DUMMY_CHECK_URL, allow_redirects=False)

        if response.status_code == 200:
            return True
        elif response.status_code == 302:
            location = response.headers.get("Location", "")
            if "show-eula-form=1" in location:
                return _handle_eula_flow(session, username="", domain="")
            elif 'show-request-form=1' in location:
                return _submit_access_request()

        return False
    except Exception as e:
        click.echo(f"❌ Error validating session: {e}")
        return False

def get_ecr_access_info(session: requests.Session) -> Optional[dict]:
    """
    Retrieve an ECR access token and proxy endpoint using the current authenticated session.

    Args:
        session (requests.Session): An authenticated session (with valid cookies).

    Returns:
        Optional[dict]: JSON response containing the ECR token info, or None if failed.
    """
    try:
        ecr_url = f"{DOCS_PORTAL}/auth/ecr-token"
        response = session.get(ecr_url, timeout=10)
        response.raise_for_status()

        data = response.json()
        if "authorizationToken" in data and "proxyEndpoint" in data:
            return data
        else:
            click.secho(
                "⚠️  Container registry token response missing 'authorizationToken'. "
                "Contact support@sima.ai for help.",
                fg="yellow",
            )
            return data
    except Exception as e:
        click.secho(f"❌ Failed to retrieve ECR token: {e}", fg="red")
        return None


def _load_cookie_jar(session: requests.Session):
    if os.path.exists(COOKIE_JAR_PATH):
        cj = MozillaCookieJar()
        cj.load(COOKIE_JAR_PATH, ignore_discard=True)
        session.cookies.update(cj)

def validate_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    _load_cookie_jar(session)

    # ✅ If an explicit access_token is provided, inject as cookie
    access_token = get_cached_access_token()
    if access_token:
        session.cookies.set(
            "sima_docs_at",           # cookie name
            access_token,             # cookie value
            domain='.sima.ai',        # optional domain restriction
            path="/",                 # root path
        )

    # Validate session
    if _is_session_valid(session):
        return session, True

    return session, False

def login_external(force=False, loginDocker=True):
    global _ACCESS_REQUEST_HANDLED
    _ACCESS_REQUEST_HANDLED = False

    if not force:
        session, valid = validate_session()
        if valid:
            return session
        if _ACCESS_REQUEST_HANDLED:
            return None
        
    tokens = get_or_refresh_tokens(force=force)
    if not tokens:
        return None
    if not access_token_has_doc_access(tokens):
        _show_limited_access_pending_message()
        return None

    session, valid = validate_session()
    if valid:
        if loginDocker:
            token, endpoint = resolve_public_registry()
            docker_login_with_token('sima_cli', token, endpoint)

        return session


def resolve_public_registry(name: str = 'ecr'):
    """
    Resolve a short registry alias to its corresponding public container
    registry endpoint and ensure authentication if required.

    Args:
        name (str): Short alias for a known registry (e.g. 'ecr').

    Returns:
        Optional[str]: The resolved registry endpoint, or None on failure.
    """
    name = name.lower().strip()

    if not ensure_docker_available():
        return None, None

    if name == "ecr":

        try:
            # Avoid recursive login->resolve_public_registry->login loops.
            session = login_external(loginDocker=False)
            if not session or not isinstance(session, requests.Session):
                click.secho("❌ No valid session found. Please login first using `sima-cli login`.", fg="red")
                return None, None

            ecr_info = get_ecr_access_info(session)
            if not ecr_info:
                click.secho("❌ Failed to retrieve container registry token information.", fg="red")
                return None, None

            token = ecr_info.get("authorizationToken")
            endpoint = ecr_info.get("proxyEndpoint")
            if not token or not endpoint:
                click.secho("⚠️  Missing 'authorizationToken' or 'proxyEndpoint' in container registry response.", fg="yellow")
                return None, None

            return token, endpoint

        except Exception as e:
            click.secho(f"❌ Unexpected error while resolving registry '{name}': {e}", fg="red")
            return None, None

    else:
        raise click.ClickException(f"❌ Unknown public registry alias: {name}")


def ensure_docker_available() -> bool:
    """
    Check if Docker CLI exists on PATH.

    - Returns True if Docker is available.
    - If missing, prints a gentle warning (only on non-SiMa hosts).
    - On SiMa boards (Modalix/Davinci), stays completely silent since Docker is optional.
    """
    if shutil.which("docker"):
        return True

    # Only warn on host systems, not on SiMa devkits
    if not is_sima_board():
        click.echo("⚠️  Docker CLI not found — container image pull and registry operations will be skipped until Docker is installed.")

    return False

def _normalize_registry_for_auth(registry: str) -> str:
    """
    Normalize a registry string to the host[:port] form used by Docker auth lookup.
    """
    value = (registry or "").strip()
    value = value.replace("https://", "").replace("http://", "")
    value = value.rstrip("/")
    if "/" in value:
        value = value.split("/", 1)[0]
    return value

def _docker_cli_is_snap() -> bool:
    docker_bin = shutil.which("docker") or ""
    resolved = os.path.realpath(docker_bin) if docker_bin else ""
    return (
        "/snap/" in docker_bin
        or "/snap/" in resolved
        or resolved.endswith("/usr/bin/snap")
    )

def _get_sima_docker_config_dir() -> str:
    override = os.getenv("SIMA_DOCKER_CONFIG_DIR")
    if override:
        return os.path.expanduser(override)
    if _docker_cli_is_snap():
        return SNAP_SIMA_DOCKER_CONFIG_DIR
    return LEGACY_SIMA_DOCKER_CONFIG_DIR

def _get_sima_docker_config_path() -> str:
    return os.path.join(_get_sima_docker_config_dir(), "config.json")

def _get_sima_docker_contexts_dir() -> str:
    return os.path.join(_get_sima_docker_config_dir(), "contexts")

def _migrate_legacy_docker_profile_if_needed():
    # For snap Docker, move auth data into a snap-allowed path.
    target_dir = _get_sima_docker_config_dir()
    target_cfg = _get_sima_docker_config_path()
    if target_dir == LEGACY_SIMA_DOCKER_CONFIG_DIR:
        return
    legacy_cfg = os.path.join(LEGACY_SIMA_DOCKER_CONFIG_DIR, "config.json")
    try:
        os.makedirs(target_dir, exist_ok=True)
        if os.path.exists(legacy_cfg) and not os.path.exists(target_cfg):
            shutil.copy2(legacy_cfg, target_cfg)
        legacy_ctx = os.path.join(LEGACY_SIMA_DOCKER_CONFIG_DIR, "contexts")
        target_ctx = _get_sima_docker_contexts_dir()
        if os.path.isdir(legacy_ctx):
            shutil.copytree(legacy_ctx, target_ctx, dirs_exist_ok=True)
    except Exception:
        # Best-effort migration; caller can continue with a fresh profile.
        pass

def get_sima_docker_env() -> dict:
    """
    Return subprocess env that forces Docker CLI to use sima-cli-specific config.
    """
    env = os.environ.copy()
    _migrate_legacy_docker_profile_if_needed()
    env["DOCKER_CONFIG"] = _get_sima_docker_config_dir()
    _sync_host_docker_contexts()
    # Preserve active context (critical on macOS Docker Desktop, which often uses
    # "desktop-linux" instead of the default unix:///var/run/docker.sock).
    if "DOCKER_CONTEXT" not in env or not env["DOCKER_CONTEXT"]:
        host_cfg = _load_host_docker_config()
        current_ctx = host_cfg.get("currentContext")
        if isinstance(current_ctx, str) and current_ctx.strip() and _context_exists_in_sima_profile(current_ctx.strip()):
            env["DOCKER_CONTEXT"] = current_ctx.strip()
    return env

def _sync_host_docker_contexts():
    """
    Keep docker context metadata in sync for alternate runtimes (e.g. Colima).
    """
    if not os.path.isdir(HOST_DOCKER_CONTEXTS_DIR):
        return
    try:
        os.makedirs(_get_sima_docker_config_dir(), exist_ok=True)
        shutil.copytree(HOST_DOCKER_CONTEXTS_DIR, _get_sima_docker_contexts_dir(), dirs_exist_ok=True)
    except Exception:
        # Non-fatal: caller will proceed without forcing context.
        pass

def _context_exists_in_sima_profile(context_name: str) -> bool:
    """
    Docker stores context metadata by sha256(name) under contexts/meta/<hash>/meta.json.
    """
    if context_name == "default":
        return True
    ctx_hash = hashlib.sha256(context_name.encode("utf-8")).hexdigest()
    meta_path = os.path.join(_get_sima_docker_contexts_dir(), "meta", ctx_hash, "meta.json")
    return os.path.isfile(meta_path)

def _load_host_docker_config() -> dict:
    try:
        if not os.path.exists(HOST_DOCKER_CONFIG_PATH):
            return {}
        with open(HOST_DOCKER_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}

def _load_sima_docker_config() -> dict:
    _migrate_legacy_docker_profile_if_needed()
    sima_docker_config_dir = _get_sima_docker_config_dir()
    sima_docker_config_path = _get_sima_docker_config_path()
    _sync_host_docker_contexts()
    os.makedirs(sima_docker_config_dir, exist_ok=True)
    if not os.path.exists(sima_docker_config_path):
        cfg = {"auths": {}}
        host_cfg = _load_host_docker_config()
        current_ctx = host_cfg.get("currentContext")
        if isinstance(current_ctx, str) and current_ctx.strip() and _context_exists_in_sima_profile(current_ctx.strip()):
            cfg["currentContext"] = current_ctx.strip()
        return cfg

    try:
        with open(sima_docker_config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
        cfg.setdefault("auths", {})
        if not isinstance(cfg["auths"], dict):
            cfg["auths"] = {}
        if not cfg.get("currentContext"):
            host_cfg = _load_host_docker_config()
            current_ctx = host_cfg.get("currentContext")
            if isinstance(current_ctx, str) and current_ctx.strip() and _context_exists_in_sima_profile(current_ctx.strip()):
                cfg["currentContext"] = current_ctx.strip()
        return cfg
    except Exception:
        cfg = {"auths": {}}
        host_cfg = _load_host_docker_config()
        current_ctx = host_cfg.get("currentContext")
        if isinstance(current_ctx, str) and current_ctx.strip() and _context_exists_in_sima_profile(current_ctx.strip()):
            cfg["currentContext"] = current_ctx.strip()
        return cfg

def _save_sima_docker_config(cfg: dict):
    sima_docker_config_dir = _get_sima_docker_config_dir()
    sima_docker_config_path = _get_sima_docker_config_path()
    os.makedirs(sima_docker_config_dir, exist_ok=True)
    try:
        os.chmod(sima_docker_config_dir, 0o700)
    except Exception:
        pass
    with open(sima_docker_config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(sima_docker_config_path, 0o600)
    except Exception:
        pass

def docker_login_with_token(username: str, token: str, registry: str = "artifacts.eng.sima.ai"):
    """
    Register auth in sima-cli-specific Docker config (no global `docker login`).

    - Automatically detects and decodes AWS ECR base64 tokens of the form 'QVdTOmV5...'
    - Ensures Docker is available before attempting login.
    - Avoids host credential helper issues by using ~/.sima-cli/docker-config/config.json.
    """
    if ensure_docker_available():
        # Decode if token looks like base64 (AWS ECR style)
        try:
            decoded = base64.b64decode(token).decode("utf-8")
            if decoded.startswith("AWS:"):
                # ECR token detected → force username to AWS
                password = decoded.split("AWS:", 1)[1]
                username = "AWS"
            else:
                password = token
        except Exception:
            # Not a valid base64 string — use raw token
            password = token

        registry_host = _normalize_registry_for_auth(registry)
        if not registry_host:
            raise click.ClickException("❌ Invalid container registry endpoint.")

        # Docker config auth format is base64("username:password")
        auth_b64 = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")

        cfg = _load_sima_docker_config()
        cfg.setdefault("auths", {})
        cfg["auths"][registry_host] = {"auth": auth_b64}
        # Also write exact raw registry key for compatibility with custom lookups.
        raw_registry = (registry or "").strip().rstrip("/")
        if raw_registry and raw_registry != registry_host:
            cfg["auths"][raw_registry] = {"auth": auth_b64}
        _save_sima_docker_config(cfg)

        # Keep env in current process so subsequent docker subprocess calls use this profile.
        os.environ["DOCKER_CONFIG"] = _get_sima_docker_config_dir()
        return password
