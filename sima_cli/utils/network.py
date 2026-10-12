import os
from pathlib import Path
from types import MethodType
from urllib.parse import urlparse, urlunparse

import requests
from requests.auth import AuthBase

from sima_cli.utils.config_loader import artifactory_url

PUBLIC_ARTIFACT_ORIGIN = "https://artifacts.neat.sima.ai"
CACHE_CA_BUNDLE = Path.home() / ".config" / "sima-cli" / "cache-ca.pem"

# This module wraps common network calls like GET and HEAD
# to centralize error handling, auth injection, and logging if needed.


def _origin(url):
    if not isinstance(url, str):
        return None
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        port = parsed.port or default_port
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.rstrip(".").lower(), port


def is_internal_artifactory_url(url):
    """Return whether *url* is on the configured internal Artifactory origin."""
    configured_origin = _origin(artifactory_url())
    return configured_origin is not None and _origin(url) == configured_origin


class _InternalArtifactoryNetrcAuth(AuthBase):
    """Apply .netrc credentials only to the configured Artifactory origin."""

    def __call__(self, request):
        if is_internal_artifactory_url(request.url):
            netrc_auth = requests.sessions.get_netrc_auth(request.url)
            if netrc_auth is not None:
                request.prepare_auth(netrc_auth)
        return request


def _merge_environment_settings(self, url, proxies, stream, verify, cert):
    settings = requests.sessions.Session.merge_environment_settings(
        self, url, proxies, stream, verify, cert
    )
    if is_internal_artifactory_url(url):
        settings["proxies"] = {}
    cache_url = os.getenv("SIMA_CLI_CACHE_URL", "").strip()
    if _origin(url) == _origin(cache_url) and CACHE_CA_BUNDLE.is_file():
        settings["verify"] = str(CACHE_CA_BUNDLE)
    return settings


def _rebuild_proxies(self, prepared_request, proxies):
    if is_internal_artifactory_url(prepared_request.url):
        prepared_request.headers.pop("Proxy-Authorization", None)
        return {}
    return requests.sessions.Session.rebuild_proxies(
        self, prepared_request, proxies
    )


def _rebuild_auth(self, prepared_request, response):
    """Retain explicit auth and restrict redirected .netrc auth to Artifactory."""
    headers = prepared_request.headers
    if "Authorization" in headers and self.should_strip_auth(
        response.request.url, prepared_request.url
    ):
        del headers["Authorization"]

    if "Authorization" not in headers and is_internal_artifactory_url(
        prepared_request.url
    ):
        netrc_auth = requests.sessions.get_netrc_auth(prepared_request.url)
        if netrc_auth is not None:
            prepared_request.prepare_auth(netrc_auth)


def configure_session_for_url(session, _url):
    """Apply destination-scoped proxy, CA, and .netrc policy.

    HTTP_PROXY, HTTPS_PROXY, NO_PROXY, and REQUESTS_CA_BUNDLE remain active.
    The exact internal Artifactory origin bypasses proxies on both initial and
    redirected requests and is the only origin allowed to use .netrc. Public
    redirect targets re-evaluate the proxy environment without ambient auth.
    The *_url* argument is retained for the existing call-site API; routing is
    evaluated from each prepared request rather than fixed to that first URL.
    """
    session.trust_env = True
    if session.auth is None:
        session.auth = _InternalArtifactoryNetrcAuth()
    if isinstance(session, requests.sessions.Session) and not getattr(
        session, "_sima_origin_routing", False
    ):
        session.merge_environment_settings = MethodType(
            _merge_environment_settings, session
        )
        session.rebuild_proxies = MethodType(_rebuild_proxies, session)
        session.rebuild_auth = MethodType(_rebuild_auth, session)
        session._sima_origin_routing = True
    return session


def rewrite_cacheable_url(url):
    """Route the public artifact CDN through the configured cache gateway."""
    parsed = urlparse(url or "")
    public = urlparse(PUBLIC_ARTIFACT_ORIGIN)
    if not (
        parsed.scheme == public.scheme
        and parsed.hostname == public.hostname
        and (parsed.port or 443) == 443
    ):
        return url

    mirror_value = os.getenv("SIMA_CLI_CACHE_URL", "").strip()
    if not mirror_value:
        return url
    mirror = urlparse(mirror_value)
    if (
        _origin(mirror_value) is None
        or mirror.scheme != "https"
        or mirror.username is not None
        or mirror.password is not None
        or mirror.path not in ("", "/")
        or mirror.query
        or mirror.fragment
    ):
        raise ValueError(
            "SIMA_CLI_CACHE_URL must be an HTTPS origin without "
            "credentials, a path, query parameters, or a fragment."
        )
    return urlunparse((
        mirror.scheme,
        mirror.netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))

def get(url, headers=None, timeout=10):
    """
    Wrapper around requests.get() with built-in error checking.
    
    Args:
        url (str): The target URL.
        headers (dict, optional): HTTP headers to include.
        timeout (int): Request timeout in seconds.

    Returns:
        requests.Response: The successful response object.

    Raises:
        HTTPError: If the response contains an HTTP error status.
    """
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response

def head(url, headers=None, timeout=10):
    """
    Wrapper around requests.head() with built-in error checking.
    
    Args:
        url (str): The target URL.
        headers (dict, optional): HTTP headers to include.
        timeout (int): Request timeout in seconds.

    Returns:
        requests.Response: The successful HEAD response.

    Raises:
        HTTPError: If the response contains an HTTP error status.
    """
    response = requests.head(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response
