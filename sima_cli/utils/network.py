from urllib.parse import urlparse

import requests

from sima_cli.utils.config_loader import artifactory_url

# This module wraps common network calls like GET and HEAD
# to centralize error handling, auth injection, and logging if needed.


def _origin(url):
    if not isinstance(url, str):
        return None
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.rstrip(".").lower(), parsed.port or default_port


def is_internal_artifactory_url(url):
    """Return whether *url* is on the configured internal Artifactory origin."""
    return _origin(url) == _origin(artifactory_url())


def configure_session_for_url(session, url):
    """Use proxies for public URLs and direct access for internal Artifactory.

    Public sessions retain Requests' standard HTTP_PROXY, HTTPS_PROXY,
    NO_PROXY, and REQUESTS_CA_BUNDLE handling.  Only the exact configured
    internal Artifactory origin bypasses that environment.
    """
    session.trust_env = not is_internal_artifactory_url(url)
    return session

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
