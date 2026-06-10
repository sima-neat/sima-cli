import re
from typing import Dict, List, Optional, Sequence


VALID_OS = {"linux", "windows", "mac", "ubuntu"}
VALID_PLATFORM_TYPES = {"board", "palette", "host"}

_COMPATIBLE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_VERSION_RE = r"v?\d+(?:\.\d+)*"
_VERSION_CLAUSE_RE = re.compile(r"^(>=|<=|==|=|>|<)?\s*({})$".format(_VERSION_RE))


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _version_tuple(version: str) -> tuple:
    normalized = version.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    return tuple(int(part) for part in normalized.split("."))


def _pad_versions(left: tuple, right: tuple) -> tuple:
    length = max(len(left), len(right))
    return left + (0,) * (length - len(left)), right + (0,) * (length - len(right))


def normalize_version_spec(spec: str) -> str:
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("version spec must be a non-empty string")

    clauses = []
    for raw_clause in spec.split(","):
        clause = raw_clause.strip()
        if not clause:
            raise ValueError("version spec contains an empty clause")
        match = _VERSION_CLAUSE_RE.fullmatch(clause)
        if not match:
            raise ValueError(
                "invalid version spec '{}'; expected versions like 2.1.1, ==2.1.1, "
                ">=2.1.0, or >=2.1.0,<=2.1.2".format(spec)
            )
        op, version = match.groups()
        clauses.append("{}{}".format(op or "", version))

    return ",".join(clauses)


def validate_version_spec(spec: str) -> None:
    normalize_version_spec(spec)


def version_matches(version: str, spec: str) -> bool:
    normalized_spec = normalize_version_spec(spec)
    current = _version_tuple(version)

    for clause in normalized_spec.split(","):
        match = _VERSION_CLAUSE_RE.fullmatch(clause)
        if not match:
            return False
        op, target_value = match.groups()
        op = op or "=="
        target = _version_tuple(target_value)
        left, right = _pad_versions(current, target)

        if op in ("=", "=="):
            clause_matches = left == right
        elif op == ">":
            clause_matches = left > right
        elif op == ">=":
            clause_matches = left >= right
        elif op == "<":
            clause_matches = left < right
        elif op == "<=":
            clause_matches = left <= right
        else:
            clause_matches = False

        if not clause_matches:
            return False

    return True


def parse_host_platform_specs(host_platforms: Optional[Sequence[str]]) -> List[Dict]:
    platforms = []
    for raw_spec in host_platforms or []:
        os_values = []
        seen = set()
        for value in _split_csv(raw_spec):
            os_value = value.lower()
            if os_value not in VALID_OS:
                raise ValueError(
                    "invalid host platform OS '{}'; supported values are {}".format(
                        value, ", ".join(sorted(VALID_OS))
                    )
                )
            if os_value not in seen:
                seen.add(os_value)
                os_values.append(os_value)
        if not os_values:
            raise ValueError("host platform spec must include at least one OS")
        platforms.append({"type": "host", "os": os_values})
    return platforms


def parse_board_platform_spec(spec: str) -> Dict:
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("board platform spec must be a non-empty string")

    raw_compat, separator, raw_version_spec = spec.strip().partition("@")
    compatible_with = []
    seen = set()
    for value in _split_csv(raw_compat):
        if not _COMPATIBLE_NAME_RE.fullmatch(value):
            raise ValueError(
                "invalid board compatibility target '{}'; use letters, numbers, dots, underscores, or hyphens".format(
                    value
                )
            )
        if value not in seen:
            seen.add(value)
            compatible_with.append(value)

    if not compatible_with:
        raise ValueError("board platform spec must include at least one compatibility target")

    platform = {"type": "board", "compatible_with": compatible_with}
    if separator:
        platform["version"] = normalize_version_spec(raw_version_spec)
    return platform


def parse_board_platform_specs(board_platforms: Optional[Sequence[str]]) -> List[Dict]:
    return [parse_board_platform_spec(spec) for spec in board_platforms or []]


def build_platform_specs(
    host_platforms: Optional[Sequence[str]] = None,
    board_platforms: Optional[Sequence[str]] = None,
    palette_platform: bool = False,
) -> List[Dict]:
    platforms = []
    platforms.extend(parse_host_platform_specs(host_platforms))
    platforms.extend(parse_board_platform_specs(board_platforms))
    if palette_platform:
        platforms.append({"type": "palette"})
    return platforms
