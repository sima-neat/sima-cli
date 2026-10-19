import os
import json
import hashlib
import re
from pathlib import Path
from sima_cli.utils.streaming import DOWNLOAD_CHUNK_SIZE, publish_partial
from sima_cli.download.parallel import PARALLEL_MIN_SIZE, download_parallel
import requests
from urllib.parse import urlparse
from tqdm import tqdm
from typing import List
from sima_cli.utils.config import get_auth_token
from sima_cli.auth.login import login
from sima_cli.install.github_assets import download_github_asset

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

    partial = Path(dest_path + ".part")
    receipt = Path(dest_path + ".part.json")
    headers = {"Accept-Encoding": "identity"}
    existing_size = 0
    session = None

    try:
        if internal:
            auth_token = get_auth_token(internal=True)
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            session = requests.Session()
            session.trust_env = False
            request_fn = session.get
            head_fn = session.head
        elif 'https://docs.sima.ai' in url or 'https://docs-dev.sima.ai' in url:
            session = login('external')
            request_fn = session.get
            head_fn = session.head
        elif 'https://github.com' in url and "/releases/download/" in url:
            token = os.getenv("GITHUB_TOKEN", None)
            return download_github_asset(url, token=token)
        else:
            session = requests.Session()
            if parsed_url.hostname == 'artifacts.neat.sima.ai':
                session.trust_env = False
            request_fn = session.get
            head_fn = session.head            

        # Only resume a partial when a strong validator identifies its bytes.
        head = head_fn(url, headers=headers, timeout=10, allow_redirects=True)
        try:
            head.raise_for_status()
            raw_size = head.headers.get('content-length')
            total_size = int(raw_size) if raw_size is not None else None
            etag = head.headers.get('ETag', '')
            accepts_ranges = head.headers.get('Accept-Ranges', '').lower() == 'bytes'
        finally:
            head.close()
        if etag.startswith('W/'):
            etag = ''
        identity = {"url_sha256": hashlib.sha256(url.encode()).hexdigest(), "etag": etag}
        if os.path.exists(dest_path) and total_size is not None:
            if os.path.getsize(dest_path) == total_size:
                print(f"✔  File already exists and is complete: {file_name}")
                return dest_path
        if partial.exists() and etag:
            try:
                if json.loads(receipt.read_text()) == identity:
                    existing_size = partial.stat().st_size
            except (OSError, ValueError):
                pass
        if total_size is not None and existing_size >= total_size:
            existing_size = 0
        # Keep existing contiguous partials resumable. Parallelism is limited
        # to the public mirror; authenticated/custom sessions retain their path.
        if (not existing_size and parsed_url.scheme == 'https'
                and parsed_url.hostname == 'artifacts.neat.sima.ai'
                and not internal and total_size is not None
                and total_size >= PARALLEL_MIN_SIZE and etag and accepts_ranges):
            if download_parallel(url, dest_path, total_size, etag):
                partial.unlink(missing_ok=True)
                receipt.unlink(missing_ok=True)
                return dest_path
        if existing_size:
            headers['Range'] = f'bytes={existing_size}-'
            headers['If-Range'] = etag

        with request_fn(url, stream=True, headers=headers, timeout=30) as r:
            r.raise_for_status()
            if r.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError("Server ignored identity content encoding")
            raw_length = r.headers.get('content-length')
            content_length = int(raw_length) if raw_length is not None else None
            if r.status_code == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', r.headers.get('Content-Range', ''))
                if not existing_size or not match:
                    raise ValueError("Invalid partial download response")
                start, end, size = map(int, match.groups())
                if (start != existing_size or end != size - 1 or end < start
                        or (content_length is not None and content_length != end - start + 1)
                        or (total_size is not None and size != total_size)
                        or (r.headers.get('ETag') and r.headers['ETag'] != etag)):
                    raise ValueError("Inconsistent partial download response")
                final_total = size
            elif r.status_code == 200:
                # Range ignored, or If-Range detected a changed object: restart.
                existing_size = 0
                final_total = content_length if content_length is not None else total_size
            else:
                raise ValueError(f"Unexpected download status: {r.status_code}")
            response_etag = r.headers.get('ETag', '')
            if existing_size:
                response_etag = etag
            identity['etag'] = response_etag if not response_etag.startswith('W/') else ''
            # Truncate before updating the receipt so old bytes cannot acquire a new validator.
            with partial.open('ab' if existing_size else 'wb') as f, tqdm(
                desc=f"⬇️  Downloading {file_name}", total=final_total,
                initial=existing_size, unit='B', unit_scale=True, unit_divisor=1024
            ) as bar:
                receipt.write_text(json.dumps(identity), encoding='utf-8')
                size = existing_size
                for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
                        bar.update(len(chunk))
                        if final_total is not None and size > final_total:
                            raise ValueError("Download exceeds expected size")
                if final_total is not None and size != final_total:
                    raise ValueError("Download size mismatch")
                f.flush()
                os.fsync(f.fileno())
            publish_partial(partial, dest_path)
            receipt.unlink(missing_ok=True)

    except Exception as e:
        raise RuntimeError(f"Download failed: {e}")
    finally:
        if session is not None:
            session.close()
    

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
        elif 'https://docs.sima.ai' in url or 'https://docs-dev.sima.ai':
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
