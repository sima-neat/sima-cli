import re
import sys
import json
from pathlib import Path

class MetadataValidationError(Exception):
    pass

VALID_TYPES = {"board", "palette", "host"}
VALID_OS = {"linux", "windows", "mac", "ubuntu"}
VALID_DEVKIT_SW = {"yocto", "elxr"}

def _validate_sha256_field(value, field_name: str):
    if not isinstance(value, str):
        raise MetadataValidationError(f"'{field_name}' must be a string")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value.strip()):
        raise MetadataValidationError(f"'{field_name}' must be a valid 64-character SHA-256 hex string")

def validate_metadata(data: dict):
    # Top-level required fields
    required_fields = ["name", "version", "release", "platforms", "resources"]
    for field in required_fields:
        if field not in data:
            raise MetadataValidationError(f"Missing required field: '{field}'")

    # Validate platforms
    if not isinstance(data["platforms"], list):
        raise MetadataValidationError("'platforms' must be a list")

    for i, platform in enumerate(data["platforms"]):
        if "type" not in platform:
            raise MetadataValidationError(f"Missing 'type' in platform entry {i}")
        if platform["type"] not in VALID_TYPES:
            raise MetadataValidationError(
                f"Invalid platform type '{platform['type']}' in entry {i}. Must be one of {VALID_TYPES}"
            )

        if platform["type"] == "board":
            if "compatible_with" not in platform:
                raise MetadataValidationError(f"'compatible_with' is required for board in entry {i}")
            if not isinstance(platform["compatible_with"], list):
                raise MetadataValidationError(f"'compatible_with' must be a list in entry {i}")

            # ✅ Check optional devkit_sw
            if "devkit_sw" in platform:
                devkit_sw_value = platform["devkit_sw"].lower()
                if devkit_sw_value not in VALID_DEVKIT_SW:
                    raise MetadataValidationError(
                        f"Invalid 'devkit_sw' value '{platform['devkit_sw']}' in platform entry {i}. "
                        f"Must be one of {VALID_DEVKIT_SW}"
                    )

        if "os" in platform:
            if not isinstance(platform["os"], list):
                raise MetadataValidationError(f"'os' must be a list in entry {i}")
            for os_value in platform["os"]:
                if os_value.lower() not in VALID_OS:
                    raise MetadataValidationError(
                        f"Invalid OS '{os_value}' in platform entry {i}. Supported: {VALID_OS}"
                    )

    # Validate resources
    if not isinstance(data["resources"], list) or not data["resources"]:
        raise MetadataValidationError("'resources' must be a non-empty list")
    for i, resource in enumerate(data["resources"]):
        if not isinstance(resource, str) or not resource.strip():
            raise MetadataValidationError(f"'resources[{i}]' must be a non-empty string")

    # Validate resources-checksum (optional)
    if "resources-checksum" in data:
        checksum_map = data["resources-checksum"]
        if not isinstance(checksum_map, dict):
            raise MetadataValidationError("'resources-checksum' must be an object/dictionary")
        for key, value in checksum_map.items():
            if not isinstance(key, str) or not key.strip():
                raise MetadataValidationError("'resources-checksum' keys must be non-empty strings")
            _validate_sha256_field(value, f"resources-checksum.{key}")

    # Validate prerequisite (optional)
    if "prerequisite" in data:
        prereq = data["prerequisite"]
        if "wheel_url" not in prereq or "entry_point" not in prereq:
            raise MetadataValidationError("Both 'wheel_url' and 'entry_point' are required in 'prerequisite'")
        _validate_entry_point_format(prereq["entry_point"], field="prerequisite.entry_point")

    # Validate installation (optional)
    if "installation" in data:
        install = data["installation"]
        if "script" not in install:
            raise MetadataValidationError("Missing 'script' in 'installation'")
        if not isinstance(install["script"], str):
            raise MetadataValidationError("'installation.script' must be a string")

    # Validate size (optional)
    if "size" in data:
        size = data["size"]
        if not isinstance(size, dict):
            raise MetadataValidationError("'size' must be a dictionary with 'download' and 'install' fields")

        for key in ["download", "install"]:
            if key not in size:
                raise MetadataValidationError(f"Missing '{key}' in 'size'")
            if not isinstance(size[key], str):
                raise MetadataValidationError(f"'size.{key}' must be a string")

            size_str = size[key].strip().upper()
            if not any(size_str.endswith(unit) for unit in ["KB", "MB", "GB"]):
                raise MetadataValidationError(
                    f"'size.{key}' must end with one of: KB, MB, GB (e.g., '30GB')"
                )

            try:
                float(size_str[:-2].strip())
            except ValueError:
                raise MetadataValidationError(
                    f"'size.{key}' must start with a numeric value (e.g., '30GB')"
                )

    # Validate selectable-resources checksum (optional)
    if "selectable-resources" in data:
        selectable = data["selectable-resources"]
        if not isinstance(selectable, list):
            raise MetadataValidationError("'selectable-resources' must be a list")
        for i, item in enumerate(selectable):
            if not isinstance(item, dict):
                raise MetadataValidationError(f"'selectable-resources[{i}]' must be an object")
            if "checksum" in item:
                _validate_sha256_field(item["checksum"], f"selectable-resources[{i}].checksum")

    return True


def _validate_entry_point_format(entry_point: str, field: str):
    if not re.match(r"^[a-zA-Z0-9_.\-]+:[a-zA-Z0-9_]+$", entry_point):
        raise MetadataValidationError(
            f"Invalid format for {field}. Must be in the form 'module:function'"
        )


def validate_file(filepath):
    try:
        with open(filepath, "r") as f:
            metadata = json.load(f)
        validate_metadata(metadata)
        print(f"✅ {filepath} is valid.")
    except FileNotFoundError:
        print(f"❌ File not found: {filepath}")
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error in {filepath}: {e}")
    except MetadataValidationError as e:
        print(f"❌ Validation failed in {filepath}: {e}")

def main():
    if len(sys.argv) != 2:
        print("Usage: python validate_metadata.py <file-or-folder>")
        sys.exit(1)

    path = Path(sys.argv[1])

    if not path.exists():
        print(f"❌ Path does not exist: {path}")
        sys.exit(1)

    if path.is_file():
        validate_file(path)
    elif path.is_dir():
        json_files = list(path.rglob("*.json"))
        if not json_files:
            print(f"⚠️ No JSON files found in directory: {path}")
        for file in json_files:
            validate_file(file)
    else:
        print(f"❌ Unsupported path type: {path}")
        sys.exit(1)

if __name__ == "__main__":
    main()
