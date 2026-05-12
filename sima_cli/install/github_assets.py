import os
import re
import requests
from urllib.parse import urlparse
from pathlib import Path
import click


def download_github_asset(url: str, dest: str = None, token: str = None, filename: str = None) -> str:
    """
    Download a GitHub release asset (public or private).

    If dest is not provided, the file will be downloaded to the current directory.
    The output filename is preserved unless overridden.
    """

    # Default destination = current working directory
    dest = Path(dest) if dest else Path.cwd()

    # ---------------------------------------------
    # 1. Extract owner + repo
    # ---------------------------------------------
    m = re.match(
        r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/.+",
        url,
    )
    if not m:
        raise ValueError(f"Not a valid GitHub release asset URL: {url}")

    owner = m.group("owner")
    repo = m.group("repo")

    # ---------------------------------------------
    # 2. Extract asset_id OR tag+filename
    # ---------------------------------------------
    if "/releases/assets/" in url:
        asset_id = re.search(r"assets/(\d+)", url).group(1)
    else:
        filename = filename or os.path.basename(urlparse(url).path)
        tag = re.search(r"download/([^/]+)/", url).group(1)
        asset_id = _resolve_asset_id(owner, repo, tag, filename, token, url)

    # ---------------------------------------------
    # 3. Build API URL
    # ---------------------------------------------
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/assets/{asset_id}"

    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"token {token}" 

    click.echo(f"⬇️  Downloading GitHub asset: {owner}/{repo} (id={asset_id})")

    # ---------------------------------------------
    # 4. GET asset (API → redirects → S3)
    # ---------------------------------------------
    r = requests.get(api_url, headers=headers, stream=True, allow_redirects=True)
    r.raise_for_status()

    # If no override filename is given, infer from headers or URL
    if filename is None:
        filename = _guess_filename_from_headers(r, url)

    dest_path = dest / filename
    dest.mkdir(parents=True, exist_ok=True)

    # Stream download to file
    total = int(r.headers.get("Content-Length", 0))
    downloaded = 0

    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    click.echo(f"\r   {downloaded/total:6.2%} ({downloaded}/{total} bytes)", nl=False)

    click.echo(f"\n✅ Download complete: {dest_path}")
    return str(dest_path)



# -------------------------------------------------------------------------
# Resolve asset_id from tag + filename — works for public + private repos
# -------------------------------------------------------------------------
def _resolve_asset_id(owner, repo, tag, filename, token, original_url):
    headers = {"Authorization": f"token {token}"} if token else {}

    # Try: /releases/tags/<tag>
    tag_api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    r = requests.get(tag_api, headers=headers)

    if r.status_code == 200:
        release = r.json()
        for asset in release.get("assets", []):
            if asset["name"] == filename:
                return asset["id"]

    # Fallback: scan entire releases list
    list_api = f"https://api.github.com/repos/{owner}/{repo}/releases"

    r = requests.get(list_api, headers=headers)
    r.raise_for_status()

    releases = r.json()
    for rel in releases:
        if rel.get("tag_name") == tag:
            for asset in rel.get("assets", []):
                if asset["name"] == filename:
                    return asset["id"]

    # Final fallback: public repo without release metadata
    if token is None:
        click.echo("⚠️  No release metadata found — falling back to direct download")
        raise RuntimeError("DIRECT_DOWNLOAD_FALLBACK")

    raise FileNotFoundError(
        f"Asset '{filename}' not found for tag '{tag}' in {owner}/{repo}.\nURL: {original_url}"
    )


# -------------------------------------------------
# Fallback: Public direct download (no API auth)
# -------------------------------------------------
def _direct_public_download(url, dest, filename):
    filename = filename or os.path.basename(urlparse(url).path)
    dest_path = Path(dest) / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)

    return str(dest_path)


# -------------------------------------------------
# Helper: Extract filename from Content-Disposition
# -------------------------------------------------
def _guess_filename_from_headers(response, fallback_url):
    cd = response.headers.get("Content-Disposition", "")
    m = re.search(r'filename="([^"]+)"', cd)
    if m:
        return m.group(1)
    return os.path.basename(urlparse(fallback_url).path)
