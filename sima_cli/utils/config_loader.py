import os
import yaml

def load_resource_config():
    """
    Load and separate public and internal resource configuration files.

    Returns:
        dict: Dictionary with keys 'public' and 'internal' for both configs.
    """
    config = {
        "public": {},
        "internal": {}
    }

    public_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "resources_public.yaml")
    )
    if os.path.exists(public_path):
        with open(public_path, "r") as f:
            config["public"] = yaml.safe_load(f) or {}

    internal_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "resources_internal.yaml")
    )
    if os.path.exists(internal_path):
        with open(internal_path, "r") as f:
            config["internal"] = yaml.safe_load(f) or {}
    else:
        print(f"Internal resource map not found... {internal_path}")

    return config

def internal_resource_exists():
    """
    Check if the internal resource YAML file exists.

    Returns:
        bool: True if the internal resource file exists, False otherwise.
    """
    internal_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "resources_internal.yaml")
    )
    return os.path.exists(internal_path)

def artifactory_url():
    """
    Retrieve the Artifactory base URL from the internal resource configuration.

    Returns:
        str or None: The Artifactory URL if found, otherwise None.

    Notes:
        - Expects 'load_resource_config()' to return a dictionary with an 'internal' section
          containing an 'artifactory' section with a 'url' field.
        - If any error occurs (e.g., missing fields, file issues), prints an error message
          and returns None.
    """
    try:
        cfg = load_resource_config()
        return cfg.get("internal", {}).get("artifactory", {}).get("url", {})
    except Exception as e:
        print('Unable to retrieve Artifactory URL')
        return None

