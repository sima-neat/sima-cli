import json
import re
import requests
from sima_cli.utils.config_loader import load_resource_config, artifactory_url
from sima_cli.utils.config import get_auth_token

ARTIFACTORY_BASE_URL = artifactory_url() + '/artifactory'

def elxr_firmware_path(board: str, version: str) -> str:
    """Return the Artifactory root for an eLxr build (3.0+ uses BSP)."""
    match = re.match(r"^(\d+)\.(\d+)(?=$|[._-])", version)
    uses_bsp = match is not None and tuple(map(int, match.groups())) >= (3, 0)
    return f"elxr/bsp/{board}" if uses_bsp else f"elxr/{board}"


def _list_available_firmware_versions_internal(board: str, match_keyword: str = None, flavor: str = 'headless', swtype: str = 'yocto'):
    if swtype == 'yocto':
        fw_path = f"{board}"
        aql_query = f"""
                    items.find({{
                        "repo": "soc-images",
                        "path": {{
                            "$match": "{fw_path}/*"
                        }},
                        "type": "folder"
                    }}).include("repo", "path", "name")
                    """.strip()
    elif swtype == 'elxr':
        # Keywords can be nonnumeric (e.g. "daily"), so search both layouts.
        paths = [f"elxr/{board}", f"elxr/bsp/{board}"]
        criteria = {
            "repo": "soc-images",
            "$and": [
                {"$or": [
                    {"path": {"$match": f"{path}/*/artifacts/palette"}}
                    for path in paths
                ]},
                {"$or": [
                    {"name": f"{board}-tftp-boot-palette.tar.gz"},
                    {"name": f"{board}-tftp-boot.tar.gz"},
                ]},
            ],
            "type": "file",
        }
        aql_query = (
            f'items.find({json.dumps(criteria)}).include("repo", "path", "name")'
        )
    else:
        raise ValueError(f"Unsupported swtype: {swtype}")

    aql_url = f"{ARTIFACTORY_BASE_URL}/api/search/aql"
    headers = {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {get_auth_token(internal=True)}"
    }

    session = requests.Session()
    session.trust_env = False
    response = session.post(aql_url, data=aql_query, headers=headers)

    if response.status_code == 401:
        print('❌ You are not authorized to access Artifactory, use `sima-cli -i login` with your Artifactory identity token to authenticate, then try the command again.')

    if response.status_code != 200:
        return None

    results = response.json().get("results", [])

    if swtype == 'yocto':
        # Reconstruct full paths and remove board prefix
        full_paths = {
            f"{item['path']}/{item['name']}".replace(fw_path + "/", "")
            for item in results
        }
        top_level_folders = sorted({path.split("/")[0] for path in full_paths})
    else:  # elxr
        versions = set()
        for item in results:
            root, version, artifacts, flavor_dir = item['path'].rsplit('/', 3)
            if (root == elxr_firmware_path(board, version)
                    and artifacts == 'artifacts' and flavor_dir == 'palette'):
                versions.add(version)
        top_level_folders = sorted(versions)

    if match_keyword:
        match_keyword = match_keyword.lower()
        top_level_folders = [
            f for f in top_level_folders if match_keyword in f.lower()
        ]

    return top_level_folders


def _list_available_firmware_versions_external(
    board: str,
    match_keyword: str = None,
    flavor: str = 'headless',
    swtype: str = 'yocto',
    update_type: str = 'standard',
):
    """
    Construct and return a list containing a single firmware download URL for a given board.
    
    If match_keyword is provided and matches a 'major.minor' version pattern (e.g., '1.6'),
    it is normalized to 'major.minor.patch' format (e.g., '1.6.0') to ensure consistent URL construction.

    Args:
        board (str): The name of the hardware board.
        match_keyword (str, optional): A version string to match (e.g., '1.6' or '1.6.0').
        flavor (str, optional): A string indicating firmware flavor - headless or full.
        swtype (str, optional): A string indicating firmware type - yocto or elxr.
        update_type (str, optional): Operation being prepared. ``bootimg``
            selects a writable disk image for eLxr; other operations select the
            netboot archive.

    Returns:
        list[str]: A list containing one formatted firmware download URL.
    """
    cfg = load_resource_config()
    download_url_base = cfg.get('public').get('download').get('download_url')

    if match_keyword:
        if re.fullmatch(r'\d+\.\d+', match_keyword):
            match_keyword += '.0'

    # If it's headless then don't append flavor str to the URL, otherwise add it.
    flavor_str = 'full-' if flavor == 'full' else ''

    if swtype == 'yocto':
        firmware_download_url = (
            f'{download_url_base}SDK{match_keyword}/devkit/{board}/{swtype}/'
            f'simaai-devkit-fw-{board}-{swtype}-{flavor_str}{match_keyword}.tar.gz'
        )
    elif update_type == 'bootimg':
        firmware_download_url = (
            f'{download_url_base}SDK{match_keyword}/devkit/{board}/{swtype}/'
            f'elxr-palette-{board}-{match_keyword}-arm64.img.gz'
        )
    else:
        # eLxr netboot uses the minimal TFTP archive. The palette disk image is
        # downloaded separately for eMMC flashing.
        firmware_download_url = (
            f'{download_url_base}SDK{match_keyword}/devkit/{board}/{swtype}/'
            f'modalix-tftp-boot-minimal.tar.gz'
        )

    return [firmware_download_url]


def list_available_firmware_versions(
    board: str,
    match_keyword: str = None,
    internal: bool = False,
    flavor: str = 'headless',
    swtype: str = 'yocto',
    update_type: str = 'standard',
):
    """
    Public interface to list available firmware versions.

    Parameters:
    - board: str – Name of the board (e.g. 'davinci')
    - match_keyword: str – Optional keyword to filter versions (case-insensitive)
    - internal: bool – Must be True to access internal Artifactory
    - flavor (str, optional): A string indicating firmware flavor - headless or full.
    - update_type: str – Operation being prepared (standard, bootimg, or netboot).

    Returns:
    - List[str] of firmware version folder names, or None if access is not allowed
    """
    if not internal:
        return _list_available_firmware_versions_external(
            board, match_keyword, flavor, swtype, update_type
        )

    return _list_available_firmware_versions_internal(board, match_keyword, flavor, swtype)
