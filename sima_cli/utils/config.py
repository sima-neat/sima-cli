import os
import json

# Path to the local CLI config file storing tokens and preferences
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".sima-cli", "config.json")

def load_config():
    """
    Load the configuration file from disk.

    Returns:
        dict: Parsed JSON config, or empty dict if file doesn't exist.
    """
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(data):
    """
    Save the given dictionary to the config file in JSON format.

    Args:
        data (dict): The config data to write to disk.
    """
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)

def get_auth_token(internal=False):
    """
    Retrieve the saved identity token for the given environment.

    Args:
        internal (bool): Whether to return the internal or external token.

    Returns:
        str or None: The saved token, or None if not set.
    """
    key = "internal" if internal else "external"
    return load_config().get(key, {}).get("auth_token")


def get_auth_username(internal=False):
    """
    Retrieve the saved auth username for the given environment.

    Args:
        internal (bool): Whether to return the internal or external username.

    Returns:
        str or None: The saved username, or None if not set.
    """
    key = "internal" if internal else "external"
    return load_config().get(key, {}).get("auth_username")

def set_auth_token(token, internal=False):
    """
    Save the given identity token under the internal or external section.

    Args:
        token (str): The access token to store.
        internal (bool): Whether to store it in the 'internal' or 'external' section.
    """
    config = load_config()
    key = "internal" if internal else "external"

    if key not in config:
        config[key] = {}

    config[key]["auth_token"] = token
    save_config(config)


def set_auth_username(username, internal=False):
    """
    Save the auth username under the internal or external section.

    Args:
        username (str): The auth username to store.
        internal (bool): Whether to store it in the 'internal' or 'external' section.
    """
    config = load_config()
    key = "internal" if internal else "external"

    if key not in config:
        config[key] = {}

    config[key]["auth_username"] = username
    save_config(config)
