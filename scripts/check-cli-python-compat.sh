#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VERSIONS="3.8 3.9 3.10 3.11 3.12 3.13 3.14"
VERSIONS="${SIMA_CLI_COMPAT_VERSIONS:-$DEFAULT_VERSIONS}"
STRICT=0
KEEP_VENVS=0
INSTALL_MISSING=0
WHEEL_PATH=""

usage() {
  printf 'Usage: %s [--strict] [--install-missing] [--keep-venvs] [--versions "3.8 3.9 ..."] [--wheel dist/sima_cli-*.whl]\n' "$0"
  printf '\n'
  printf 'Builds and installs a sima-cli wheel under each available Python interpreter.\n'
  printf 'When --wheel is provided, installs that prebuilt wheel instead of rebuilding.\n'
  printf 'Missing interpreters are skipped unless --strict is set.\n'
  printf 'With --install-missing, missing interpreters are installed through pyenv.\n'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict)
      STRICT=1
      shift
      ;;
    --keep-venvs)
      KEEP_VENVS=1
      shift
      ;;
    --install-missing)
      INSTALL_MISSING=1
      shift
      ;;
    --versions)
      if [[ $# -lt 2 ]]; then
        usage >&2
        exit 2
      fi
      VERSIONS="$2"
      shift 2
      ;;
    --wheel)
      if [[ $# -lt 2 ]]; then
        usage >&2
        exit 2
      fi
      WHEEL_PATH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "$WHEEL_PATH" && "$WHEEL_PATH" != /* ]]; then
  WHEEL_PATH="$(pwd -P)/$WHEEL_PATH"
fi

find_python() {
  local version="$1"
  local candidate
  for candidate in "python${version}" "python${version//./}"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

pyenv_latest_patch() {
  local version="$1"
  pyenv install --list \
    | sed 's/^[[:space:]]*//' \
    | grep -E "^${version//./\\.}\\.[0-9]+$" \
    | sort -V \
    | tail -n 1
}

ensure_pyenv_python() {
  local version="$1"
  local patch_version python_bin

  if ! command -v pyenv >/dev/null 2>&1; then
    printf 'FAIL Python %s missing and pyenv is not installed\n' "$version" >&2
    return 1
  fi

  patch_version="$(pyenv_latest_patch "$version")"
  if [[ -z "$patch_version" ]]; then
    printf 'FAIL pyenv has no installable Python matching %s\n' "$version" >&2
    return 1
  fi

  if ! pyenv versions --bare | grep -Fxq "$patch_version"; then
    printf 'Installing Python %s with pyenv...\n' "$patch_version" >&2
    pyenv install "$patch_version"
  fi

  python_bin="$(pyenv root)/versions/$patch_version/bin/python"
  if [[ ! -x "$python_bin" ]]; then
    printf 'FAIL pyenv Python not found after install: %s\n' "$python_bin" >&2
    return 1
  fi

  printf '%s\n' "$python_bin"
}

run_smoke() {
  local python_version="$1"
  local python_bin="$2"
  local tmpdir venv py actual_version home_dir dist_dir wheel installed_version cli_version expected_version

  tmpdir="$(mktemp -d)"
  if [[ "$KEEP_VENVS" -eq 0 ]]; then
    trap 'rm -rf "$tmpdir"' RETURN
  else
    printf 'Keeping Python %s compatibility venv at %s\n' "$python_version" "$tmpdir"
  fi

  actual_version="$("$python_bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "$actual_version" != "$python_version" ]]; then
    printf 'FAIL python%s resolved to Python %s at %s\n' "$python_version" "$actual_version" "$python_bin" >&2
    return 1
  fi

  venv="$tmpdir/venv"
  home_dir="$tmpdir/home"
  dist_dir="$tmpdir/dist"
  mkdir -p "$home_dir"

  "$python_bin" -m venv "$venv"
  py="$venv/bin/python"
  expected_version="$("$py" -c 'import runpy, sys; print(runpy.run_path(sys.argv[1])["__version__"])' "$ROOT_DIR/sima_cli/__version__.py")"
  PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 "$py" -m pip install --quiet --upgrade pip setuptools wheel
  if [[ -n "$WHEEL_PATH" ]]; then
    wheel="$WHEEL_PATH"
    if [[ ! -f "$wheel" ]]; then
      printf 'FAIL Python %s wheel not found: %s\n' "$python_version" "$wheel" >&2
      return 1
    fi
  else
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 "$py" -m pip wheel --quiet --no-deps --wheel-dir "$dist_dir" "$ROOT_DIR"

    wheel="$(find "$dist_dir" -maxdepth 1 -type f -name 'sima_cli-*.whl' -print -quit)"
    if [[ -z "$wheel" ]]; then
      printf 'FAIL Python %s did not build a sima-cli wheel in %s\n' "$python_version" "$dist_dir" >&2
      return 1
    fi
  fi

  if ! PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 "$py" -m pip install "$wheel"; then
    printf 'FAIL Python %s could not install %s\n' "$python_version" "$wheel" >&2
    return 1
  fi
  if ! "$py" -m pip check; then
    printf 'FAIL Python %s dependency check failed after installing %s\n' "$python_version" "$wheel" >&2
    return 1
  fi

  (cd "$tmpdir" && SIMA_CLI_CHECK_FOR_UPDATE=0 HOME="$home_dir" "$py" -m sima_cli -h >/dev/null)
  cli_version="$(cd "$tmpdir" && SIMA_CLI_CHECK_FOR_UPDATE=0 HOME="$home_dir" "$py" -m sima_cli version)"
  (cd "$tmpdir" && SIMA_CLI_CHECK_FOR_UPDATE=0 HOME="$home_dir" "$venv/bin/sima-cli" -h >/dev/null)
  installed_version="$("$py" -c 'from importlib.metadata import version; print(version("sima-cli"))')"

  if [[ "$installed_version" != "$expected_version" ]]; then
    printf 'FAIL Python %s installed sima-cli %s, expected %s\n' "$python_version" "$installed_version" "$expected_version" >&2
    return 1
  fi

  if ! grep -Fxq "SiMa CLI version: $expected_version" <<<"$cli_version"; then
    printf 'FAIL Python %s CLI version output mismatch: %s\n' "$python_version" "$cli_version" >&2
    return 1
  fi

  printf 'PASS Python %s (%s) sima-cli %s\n' "$python_version" "$python_bin" "$installed_version"
}

failures=0
checked=0
skipped=0

cd "$ROOT_DIR"

for version in $VERSIONS; do
  if ! python_bin="$(find_python "$version")"; then
    if [[ "$INSTALL_MISSING" -eq 1 ]]; then
      if ! python_bin="$(ensure_pyenv_python "$version")"; then
        failures=$((failures + 1))
        continue
      fi
    elif [[ "$STRICT" -eq 1 ]]; then
      printf 'FAIL Python %s interpreter not found\n' "$version" >&2
      failures=$((failures + 1))
      continue
    else
      printf 'SKIP Python %s interpreter not found\n' "$version"
      skipped=$((skipped + 1))
      continue
    fi
  fi

  checked=$((checked + 1))
  if ! run_smoke "$version" "$python_bin"; then
    failures=$((failures + 1))
  fi
done

printf 'Compatibility summary: %s checked, %s skipped, %s failed\n' "$checked" "$skipped" "$failures"

if [[ "$failures" -gt 0 ]]; then
  exit 1
fi
