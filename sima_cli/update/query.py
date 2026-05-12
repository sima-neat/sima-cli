import re
import requests
from sima_cli.utils.config_loader import load_resource_config, artifactory_url
from sima_cli.utils.config import get_auth_token

ARTIFACTORY_BASE_URL = artifactory_url() + '/artifactory'

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
        fw_path = f"elxr/{board}"
        aql_query = f"""
                    items.find({{
                        "repo": "soc-images",
                        "path": {{
                            "$match": "{fw_path}/*/artifacts/palette"
                        }},
                        "$or": [
                            {{"name": "modalix-tftp-boot-palette.tar.gz"}},
                            {{"name": "modalix-tftp-boot.tar.gz"}}
                        ],
                        "type": "file"
                    }}).include("repo", "path", "name")
                    """.strip()
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
        # Extract version from path like: elxr/{board}/<version>/artifacts/palette
        top_level_folders = sorted({
            item['path'].split('/')[2] for item in results
        })

    if match_keyword:
        match_keyword = match_keyword.lower()
        top_level_folders = [
            f for f in top_level_folders if match_keyword in f.lower()
        ]

    return top_level_folders


def _list_available_firmware_versions_external(board: str, match_keyword: str = None, flavor: str = 'headless', swtype: str = 'yocto'):
    """
    Construct and return a list containing a single firmware download URL for a given board.
    
    If match_keyword is provided and matches a 'major.minor' version pattern (e.g., '1.6'),
    it is normalized to 'major.minor.patch' format (e.g., '1.6.0') to ensure consistent URL construction.

    Args:
        board (str): The name of the hardware board.
        match_keyword (str, optional): A version string to match (e.g., '1.6' or '1.6.0').
        flavor (str, optional): A string indicating firmware flavor - headless or full.
        swtype (str, optional): A string indicating firmware type - yocto or elxr.

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
    else:
        # For eLxr we just download the tftp minimal for netboot
        firmware_download_url = (
            f'{download_url_base}SDK{match_keyword}/devkit/{board}/{swtype}/'
            f'modalix-tftp-boot-minimal.tar.gz'
        )

    return [firmware_download_url]


def list_available_firmware_versions(board: str, match_keyword: str = None, internal: bool = False, flavor: str = 'headless', swtype: str = 'yocto'):
    """
    Public interface to list available firmware versions.

    Parameters:
    - board: str – Name of the board (e.g. 'davinci')
    - match_keyword: str – Optional keyword to filter versions (case-insensitive)
    - internal: bool – Must be True to access internal Artifactory
    - flavor (str, optional): A string indicating firmware flavor - headless or full.

    Returns:
    - List[str] of firmware version folder names, or None if access is not allowed
    """
    if not internal:
        return _list_available_firmware_versions_external(board, match_keyword, flavor, swtype)

    return _list_available_firmware_versions_internal(board, match_keyword, flavor, swtype)
