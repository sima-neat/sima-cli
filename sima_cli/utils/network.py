import requests

# This module wraps common network calls like GET and HEAD
# to centralize error handling, auth injection, and logging if needed.

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
