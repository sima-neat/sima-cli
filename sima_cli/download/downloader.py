import os
import requests
from urllib.parse import urlparse
from tqdm import tqdm
from typing import List
from sima_cli.utils.config import get_auth_token
from sima_cli.auth.login import login
from sima_cli.install.github_assets import download_github_asset


DOCS_HOSTS = {"docs.sima.ai", "docs-dev.sima.ai"}


def _url_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _is_docs_url(url: str) -> bool:
    return _url_host(url) in DOCS_HOSTS


def _is_github_release_asset(url: str) -> bool:
    parsed_url = urlparse(url)
    return parsed_url.hostname == "github.com" and "/releases/download/" in parsed_url.path


def _list_directory_files(url: str, internal: bool = False) -> List[str]:
    """
    Attempt to list files in a server-hosted directory with index browsing enabled.

    Args:
        url (str): Base URL to the folder.
        internal (bool): Whether the resource is internal (requires token).

    Returns:
        List[str]: List of full file URLs.

    Raises:
        RuntimeError: If listing fails or HTML cannot be parsed.
    """
    try:
        headers = {}
        token = get_auth_token(internal)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if internal:
            session = requests.Session()
            session.trust_env = False
            response = session.get(url, headers=headers, timeout=10)
        else:
            response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        if "text/html" not in response.headers.get("Content-Type", ""):
            raise RuntimeError("Directory listing not supported (non-HTML response).")

        import re
        hrefs = re.findall(r'href="([^"?/][^"?]*)"', response.text)
        files = [href for href in hrefs if href not in ("../", "") and not href.endswith("/")]

        if not files:
            raise RuntimeError("No files found or listing is not permitted.")

        return [url.rstrip("/") + "/" + fname for fname in files]

    except Exception as e:
        raise RuntimeError(f"Failed to list folder '{url}': {e}")


def download_file_from_url(url: str, dest_folder: str = ".", internal: bool = False) -> str:
    """
    Download a file from a direct URL with resume and skip support.

    Args:
        url (str): The full URL to download.
        dest_folder (str): The folder to save the downloaded file.
        internal (bool): Whether this is internal resource on Artifactory

    Returns:
        str: Path to the downloaded file.

    Raises:
        Exception: if download fails.
    """
    parsed_url = urlparse(url)
    file_name = os.path.basename(parsed_url.path)
    if not file_name:
        raise ValueError("Cannot determine file name from URL.")

    os.makedirs(dest_folder, exist_ok=True)
    dest_path = os.path.join(dest_folder, file_name)

    resume_header = {}
    headers = {}
    mode = 'wb'
    existing_size = 0

    try:
        if internal:
            auth_token = get_auth_token(internal=True)
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            session = requests.Session()
            session.trust_env = False
            request_fn = session.get
            head_fn = session.head
        elif _is_docs_url(url):
            session = login('external')
            request_fn = session.get
            head_fn = session.head
        elif _is_github_release_asset(url):
            token = os.getenv("GITHUB_TOKEN", None)
            return download_github_asset(url, token=token)
        else:
            session = requests.Session()
            request_fn = session.get
            head_fn = session.head            

        # HEAD request to get total file size
        head = head_fn(url, headers=headers, timeout=10)
        head.raise_for_status()
        total_size = int(head.headers.get('content-length', 0))

        # Check for existing file
        if os.path.exists(dest_path):
            existing_size = os.path.getsize(dest_path)

            if existing_size == total_size:
                print(f"✔  File already exists and is complete: {file_name}")
                return dest_path
            elif existing_size < total_size:
                resume_header['Range'] = f'bytes={existing_size}-'
                mode = 'ab'
                headers['Range'] = resume_header['Range']
            else:
                existing_size = 0
                mode = 'wb'

        # Begin download with appropriate handler
        with request_fn(url, stream=True, headers=headers, timeout=30) as r:
            r.raise_for_status()

            content_length = int(r.headers.get('content-length', 0))
            final_total = existing_size + content_length

            with open(dest_path, mode) as f, tqdm(
                desc=f"⬇️  Downloading {file_name}",
                total=final_total,
                initial=existing_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024
            ) as bar:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        bar.update(len(chunk))

    except Exception as e:
        raise RuntimeError(f"Download failed: {e}")
    

    return dest_path

def check_url_available(url: str, internal: bool = False) -> bool:
    """
    Perform a HEAD request to check if a resource is available.

    Args:
        url (str): The full URL to check.
        internal (bool): Whether this is an internal resource on Artifactory.

    Returns:
        bool: True if the resource is available (status 200–399), False otherwise.
    """
    headers = {}
    try:
        if internal:
            auth_token = get_auth_token(internal=True)
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            session = requests.Session()
            session.trust_env = False  # Ignore .netrc and other env-based config
            head_fn = session.head
        elif _is_docs_url(url):
            session = login('external')
            head_fn = session.head
        else:
            session = requests.Session()
            head_fn = session.head

        resp = head_fn(url, headers=headers, timeout=10, allow_redirects=True)
        # Consider any 2xx or 3xx as "available"
        return 200 <= resp.status_code < 400

    except Exception as e:
        print(f"⚠️ HEAD check failed for {url}: {e}")
        return False

def download_folder_from_url(url: str, dest_folder: str = ".", internal: bool = False) -> List[str]:
    """
    Download all files listed in a remote folder (server must support listing).

    Args:
        url (str): Folder URL.
        dest_folder (str): Local folder to save downloads.
        internal (bool): Whether this is internal resource on Artifactory

    Returns:
        List[str]: Paths to all downloaded files.
    """
    file_urls = _list_directory_files(url, internal=internal)
    downloaded_paths = []

    for file_url in file_urls:
        try:
            downloaded_path = download_file_from_url(file_url, dest_folder, internal=internal)
            downloaded_paths.append(downloaded_path)
        except Exception as e:
            print(f"⚠ Skipped {file_url}: {e}")

    return downloaded_paths
