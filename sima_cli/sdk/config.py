# config.py

# Unified container image configuration
IMAGE_CONFIG = {
    "mpk_cli_toolset": {
        "display": "MPK CLI tools",
        "privileged": True,
        "port_mapping_required": True,
        "baseline": True,
        "var-log-folders": ["simaai", "sima"]
    },
    "yocto": {
        "display": "Yocto Cross Compiler",
        "privileged": False,
        "port_mapping_required": False,
        "var-log-folders": ["sima"]
    },
    "elxr": {
        "display": "eLXr Cross Compiler",
        "privileged": False,
        "port_mapping_required": False,
        "var-log-folders": ["sima"]
    },
    "neat": {
        "display": "Neat SDK",
        "privileged": False,
        "port_mapping_required": False,
        "var-log-folders": ["sima", "supervisor"]
    },
    "modelsdk": {
        "display": "ModelSDK",
        "privileged": False,
        "port_mapping_required": False
    },
}

# Aliases from registry/repo image names to canonical SDK keys above.
IMAGE_ALIASES = {
    "elxr-sdk": "elxr",
}

# ---- Derived constants (auto-generated) ----
IMAGE_NAMES = list(IMAGE_CONFIG.keys())

CHOICE_MAP = {cfg["display"]: name for name, cfg in IMAGE_CONFIG.items()}

INDEX_TO_NAME = {i: display for i, display in enumerate(CHOICE_MAP.keys(), start=1)}

BASELINE_IMAGE = next(name for name, cfg in IMAGE_CONFIG.items() if cfg.get("baseline"))
BASELINE_DISPLAY = IMAGE_CONFIG[BASELINE_IMAGE]["display"]
