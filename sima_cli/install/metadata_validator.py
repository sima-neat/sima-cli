import re
import sys
import json
from pathlib import Path, PureWindowsPath

from sima_cli.install.compatibility import (
    VALID_OS,
    VALID_PLATFORM_TYPES,
    normalize_host_arch,
    normalize_exact_version,
    validate_version_spec,
)

class MetadataValidationError(Exception):
    pass

VALID_DEVKIT_SW = {"yocto", "elxr"}


def _validate_windows_path_spec(value, field_name: str):
    if not isinstance(value, dict):
        raise MetadataValidationError(f"'{field_name}' must be an object")
    environment = value.get("environment")
    relative = value.get("relative")
    if not isinstance(environment, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", environment):
        raise MetadataValidationError(
            f"'{field_name}.environment' must be a valid environment variable name"
        )
    if not isinstance(relative, str) or not relative.strip():
        raise MetadataValidationError(f"'{field_name}.relative' must be a non-empty string")
    path = PureWindowsPath(relative)
    if path.is_absolute() or path.drive or any(part in {"", ".", ".."} for part in path.parts):
        raise MetadataValidationError(
            f"'{field_name}.relative' must be a safe relative Windows path"
        )


def _validate_windows_prerequisites(value):
    if not isinstance(value, list) or not value:
        raise MetadataValidationError("'installation.prerequisites' must be a non-empty list")
    names = set()
    for index, prerequisite in enumerate(value):
        prefix = f"installation.prerequisites[{index}]"
        if not isinstance(prerequisite, dict):
            raise MetadataValidationError(f"'{prefix}' must be an object")
        name = prerequisite.get("name")
        kind = prerequisite.get("type")
        guidance = prerequisite.get("guidance")
        if not isinstance(name, str) or not name.strip():
            raise MetadataValidationError(f"'{prefix}.name' must be a non-empty string")
        if name in names:
            raise MetadataValidationError("'installation.prerequisites' contains a duplicate name")
        names.add(name)
        if not isinstance(guidance, str) or not guidance.strip():
            raise MetadataValidationError(f"'{prefix}.guidance' must be a non-empty string")
        if kind in {"executable", "windows-service"}:
            target = prerequisite.get("value")
            if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", target):
                raise MetadataValidationError(
                    f"'{prefix}.value' must be a safe executable or service name"
                )
        elif kind == "windows-product":
            target = prerequisite.get("value")
            version = prerequisite.get("version")
            if (not isinstance(target, str) or not target.strip()
                    or len(target) > 128
                    or not re.fullmatch(r"[A-Za-z0-9 .()_-]+", target)):
                raise MetadataValidationError(
                    f"'{prefix}.value' must be a safe Windows product name"
                )
            if (not isinstance(version, str)
                    or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}", version)):
                raise MetadataValidationError(
                    f"'{prefix}.version' must be an exact numeric product version"
                )
        elif kind == "command-version":
            candidates = prerequisite.get("candidates")
            arguments = prerequisite.get("arguments")
            version = prerequisite.get("version")
            pattern = prerequisite.get("version-pattern")
            if not isinstance(candidates, list) or not candidates:
                raise MetadataValidationError(f"'{prefix}.candidates' must be a non-empty list")
            for candidate_index, candidate in enumerate(candidates):
                _validate_windows_path_spec(
                    candidate, f"{prefix}.candidates[{candidate_index}]"
                )
            if (not isinstance(arguments, list)
                    or not arguments
                    or any(not isinstance(argument, str)
                           or not re.fullmatch(r"[A-Za-z0-9_.=:/-]+", argument)
                           for argument in arguments)):
                raise MetadataValidationError(
                    f"'{prefix}.arguments' must contain safe command arguments"
                )
            if (not isinstance(version, str)
                    or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version)):
                raise MetadataValidationError(
                    f"'{prefix}.version' must be an exact major.minor.patch version"
                )
            if not isinstance(pattern, str) or len(pattern) > 200:
                raise MetadataValidationError(
                    f"'{prefix}.version-pattern' must be a bounded pattern with one capture"
                )
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise MetadataValidationError(
                    f"'{prefix}.version-pattern' is invalid: {exc}"
                ) from exc
            if compiled.groups != 1:
                raise MetadataValidationError(
                    f"'{prefix}.version-pattern' must contain exactly one capture group"
                )
        elif kind == "file":
            candidates = prerequisite.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise MetadataValidationError(f"'{prefix}.candidates' must be a non-empty list")
            for candidate_index, candidate in enumerate(candidates):
                _validate_windows_path_spec(
                    candidate, f"{prefix}.candidates[{candidate_index}]"
                )
        else:
            raise MetadataValidationError(
                f"'{prefix}.type' must be one of: command-version, executable, file, "
                "windows-product, windows-service"
            )

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
        if not isinstance(platform, dict):
            raise MetadataValidationError(f"platform entry {i} must be an object")
        if "type" not in platform:
            raise MetadataValidationError(f"Missing 'type' in platform entry {i}")
        if platform["type"] not in VALID_PLATFORM_TYPES:
            raise MetadataValidationError(
                f"Invalid platform type '{platform['type']}' in entry {i}. Must be one of {VALID_PLATFORM_TYPES}"
            )

        if platform["type"] == "board":
            if "compatible_with" not in platform:
                raise MetadataValidationError(f"'compatible_with' is required for board in entry {i}")
            if not isinstance(platform["compatible_with"], list):
                raise MetadataValidationError(f"'compatible_with' must be a list in entry {i}")
            if not platform["compatible_with"]:
                raise MetadataValidationError(f"'compatible_with' must be a non-empty list in entry {i}")
            for compat_value in platform["compatible_with"]:
                if not isinstance(compat_value, str) or not compat_value.strip():
                    raise MetadataValidationError(
                        f"'compatible_with' values must be non-empty strings in entry {i}"
                    )

            if "version" in platform:
                if not isinstance(platform["version"], str):
                    raise MetadataValidationError(f"'version' must be a string for board in entry {i}")
                try:
                    validate_version_spec(platform["version"])
                except ValueError as exc:
                    raise MetadataValidationError(f"Invalid board version spec in entry {i}: {exc}")

            # ✅ Check optional devkit_sw
            if "devkit_sw" in platform:
                devkit_sw_value = platform["devkit_sw"].lower()
                if devkit_sw_value not in VALID_DEVKIT_SW:
                    raise MetadataValidationError(
                        f"Invalid 'devkit_sw' value '{platform['devkit_sw']}' in platform entry {i}. "
                        f"Must be one of {VALID_DEVKIT_SW}"
                    )

        if platform["type"] == "host":
            if "os" not in platform:
                raise MetadataValidationError(f"'os' is required for host in entry {i}")
            if not isinstance(platform["os"], list):
                raise MetadataValidationError(f"'os' must be a list in entry {i}")
            if not platform["os"]:
                raise MetadataValidationError(f"'os' must be a non-empty list in entry {i}")
            for os_value in platform["os"]:
                if not isinstance(os_value, str) or os_value.lower() not in VALID_OS:
                    raise MetadataValidationError(
                        f"Invalid OS '{os_value}' in platform entry {i}. Supported: {VALID_OS}"
                    )

            if "versions" in platform:
                versions = platform["versions"]
                if not isinstance(versions, dict):
                    raise MetadataValidationError(f"'versions' must be an object for host in entry {i}")
                for os_key, specs in versions.items():
                    if not isinstance(os_key, str) or os_key.lower() not in VALID_OS:
                        raise MetadataValidationError(
                            f"Invalid OS '{os_key}' in host versions for platform entry {i}. Supported: {VALID_OS}"
                        )
                    if os_key.lower() not in [str(value).lower() for value in platform["os"]]:
                        raise MetadataValidationError(
                            f"Host versions key '{os_key}' must also be listed in 'os' for platform entry {i}"
                        )
                    if not isinstance(specs, list) or not specs:
                        raise MetadataValidationError(
                            f"'versions.{os_key}' must be a non-empty list in platform entry {i}"
                        )
                    for spec in specs:
                        if not isinstance(spec, str):
                            raise MetadataValidationError(
                                f"'versions.{os_key}' values must be strings in platform entry {i}"
                            )
                        try:
                            validate_version_spec(spec)
                        except ValueError as exc:
                            raise MetadataValidationError(
                                f"Invalid host version spec for '{os_key}' in entry {i}: {exc}"
                            )

            if "arch" in platform:
                arches = platform["arch"]
                if not isinstance(arches, list) or not arches:
                    raise MetadataValidationError(f"'arch' must be a non-empty list for host in entry {i}")
                for arch in arches:
                    try:
                        normalize_host_arch(arch)
                    except ValueError as exc:
                        raise MetadataValidationError(f"Invalid host architecture in entry {i}: {exc}")

        if platform["type"] == "palette" and "version" in platform:
            if not isinstance(platform["version"], str):
                raise MetadataValidationError(f"'version' must be a string for palette in entry {i}")
            try:
                normalize_exact_version(platform["version"])
            except ValueError as exc:
                raise MetadataValidationError(f"Invalid palette version in entry {i}: {exc}")

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
        if not isinstance(install, dict):
            raise MetadataValidationError("'installation' must be an object")
        install_type = install.get("type", "script")
        if install_type == "script":
            if "script" not in install:
                raise MetadataValidationError("Missing 'script' in 'installation'")
            if not isinstance(install["script"], str):
                raise MetadataValidationError("'installation.script' must be a string")
        elif install_type == "windows-msi":
            checksum_map = data.get("resources-checksum")
            if not isinstance(checksum_map, dict):
                raise MetadataValidationError(
                    "Windows MSI metadata requires 'resources-checksum' for every resource"
                )
            missing_checksums = [
                resource for resource in data["resources"] if resource not in checksum_map
            ]
            extra_checksums = [
                resource for resource in checksum_map if resource not in data["resources"]
            ]
            if missing_checksums or extra_checksums:
                raise MetadataValidationError(
                    "Windows MSI 'resources-checksum' keys must exactly match 'resources'"
                )
            native_packages = install.get("native-packages")
            if native_packages is None:
                msi = install.get("msi")
                if not isinstance(msi, str) or not msi.strip().lower().endswith(".msi"):
                    raise MetadataValidationError("'installation.msi' must name an MSI resource")
                if msi not in data["resources"]:
                    raise MetadataValidationError("'installation.msi' must be listed in 'resources'")
            else:
                if "msi" in install:
                    raise MetadataValidationError(
                        "Use either 'installation.msi' or 'installation.native-packages', not both"
                    )
                if not isinstance(native_packages, list) or not native_packages:
                    raise MetadataValidationError(
                        "'installation.native-packages' must be a non-empty list"
                    )
                owners = set()
                for index, package in enumerate(native_packages):
                    prefix = f"'installation.native-packages[{index}]'"
                    if not isinstance(package, dict):
                        raise MetadataValidationError(f"{prefix} must be an object")
                    for field in ("owner", "msi", "version"):
                        value = package.get(field)
                        if not isinstance(value, str) or not value.strip():
                            raise MetadataValidationError(
                                f"{prefix}.{field} must be a non-empty string"
                            )
                    if package["owner"] in owners:
                        raise MetadataValidationError(
                            "'installation.native-packages' contains a duplicate owner"
                        )
                    owners.add(package["owner"])
                    if not package["msi"].lower().endswith(".msi"):
                        raise MetadataValidationError(f"{prefix}.msi must name an MSI resource")
                    if package["msi"] not in data["resources"]:
                        raise MetadataValidationError(
                            f"{prefix}.msi must be listed in 'resources'"
                        )
            python_package = install.get("python")
            if python_package is not None:
                if not isinstance(python_package, dict):
                    raise MetadataValidationError("'installation.python' must be an object")
                for field in ("wheel", "distribution", "version", "import"):
                    value = python_package.get(field)
                    if not isinstance(value, str) or not value.strip():
                        raise MetadataValidationError(
                            f"'installation.python.{field}' must be a non-empty string"
                        )
                if not python_package["wheel"].lower().endswith(".whl"):
                    raise MetadataValidationError(
                        "'installation.python.wheel' must name a wheel resource"
                    )
                if python_package["wheel"] not in data["resources"]:
                    raise MetadataValidationError(
                        "'installation.python.wheel' must be listed in 'resources'"
                    )
                requires_python = python_package.get("requires-python")
                if requires_python is not None and (
                    not isinstance(requires_python, str)
                    or not re.fullmatch(r"[0-9]+\.[0-9]+", requires_python)
                ):
                    raise MetadataValidationError(
                        "'installation.python.requires-python' must be a major.minor version"
                    )
                python_arch = python_package.get("arch")
                if python_arch is not None:
                    try:
                        normalize_host_arch(python_arch)
                    except ValueError as exc:
                        raise MetadataValidationError(
                            f"Invalid 'installation.python.arch': {exc}"
                        )
            prerequisites = install.get("prerequisites")
            if prerequisites is not None:
                _validate_windows_prerequisites(prerequisites)
            verification = install.get("verification")
            if verification is not None:
                if not isinstance(verification, dict):
                    raise MetadataValidationError("'installation.verification' must be an object")
                _validate_windows_path_spec(
                    verification.get("command"), "installation.verification.command"
                )
            skills = install.get("skills")
            if skills is not None:
                if not isinstance(skills, list) or not skills:
                    raise MetadataValidationError("'installation.skills' must be a non-empty list")
                for index, skill in enumerate(skills):
                    _validate_windows_path_spec(skill, f"installation.skills[{index}]")
        else:
            raise MetadataValidationError(f"Unsupported installation type: '{install_type}'")

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
