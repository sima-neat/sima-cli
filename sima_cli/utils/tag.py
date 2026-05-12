import json
import os
import click
import tempfile
from typing import Optional
from sima_cli.download import download_file_from_url

def tag_resolver(tag: str, internal: bool = False) -> str:
    """
    Resolve a release tag ('ga', 'beta', 'alpha', 'qa') to its version number
    using the remote releases.json file.

    Args:
        tag (str): One of ['ga', 'beta', 'alpha', 'qa']
        internal (bool): Whether to use internal download credentials.

    Returns:
        str: The version string (e.g. '1.7.0') if found.

    Raises:
        ValueError: If the tag does not exist in the JSON or file download fails.
    """
    url = "https://docs.sima.ai/pkg_downloads/releases.json"
    valid_tags = {"ga", "beta", "alpha"}

    tag = tag.lower().strip()
    if tag not in valid_tags:
        raise ValueError(f"❌ Invalid tag '{tag}'. Must be one of: {', '.join(valid_tags)}")

    # Download the JSON file to a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            json_path = download_file_from_url(url, dest_folder=tmpdir, internal=internal)
            if not os.path.exists(json_path):
                raise FileNotFoundError("❌ releases.json was not downloaded successfully.")

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if tag not in data:
                raise ValueError(f"❌ Tag '{tag}' not found in releases.json.")

            version = data[tag]
            print(f"✅ Resolved tag '{tag}' → version {version}")
            return version

        except Exception as e:
            raise ValueError(f"❌ Failed to resolve tag '{tag}': {e}")


def resolve_version(ver: Optional[str], internal: bool = False) -> str:
    """
    Resolve a version string or tag into a final version.
    - If 'ver' is empty or None, defaults to resolving the 'ga' tag.
    - If 'ver' is one of ['ga', 'beta', 'alpha'], resolve via tag_resolver().
    - Otherwise, returns 'ver' as-is (already an explicit version).

    Args:
        ver (str | None): Version string or tag.
        internal (bool): Whether to use internal resource access.

    Returns:
        str: Final resolved version (e.g. '1.7.0').
    """
    try:
        if not ver:
            print("ℹ️  No version specified — defaulting to 'ga' tag.")
            return tag_resolver("ga", internal=internal)

        ver = ver.strip().lower()
        if ver in {"ga", "beta", "alpha", "qa"}:
            return tag_resolver(ver, internal=internal)
    except Exception as e:
        click.secho(f"❌ Failed to resolve tag: {e}", fg="red")
        exit(0)

    print(f"✅ Using explicit version: {ver}")
    return ver
