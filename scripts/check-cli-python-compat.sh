#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VERSIONS="3.8 3.9 3.10 3.11 3.12 3.13 3.14"
VERSIONS="${SIMA_CLI_COMPAT_VERSIONS:-$DEFAULT_VERSIONS}"
STRICT=0
KEEP_VENVS=0
INSTALL_MISSING=0

usage() {
  printf 'Usage: %s [--strict] [--install-missing] [--keep-venvs] [--versions "3.8 3.9 ..."]\n' "$0"
  printf '\n'
  printf 'Runs smoke checks for sima-cli under each available Python interpreter.\n'
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
  local version="$1"
  local python_bin="$2"
  local tmpdir venv py actual_version home_dir

  tmpdir="$(mktemp -d)"
  if [[ "$KEEP_VENVS" -eq 0 ]]; then
    trap 'rm -rf "$tmpdir"' RETURN
  else
    printf 'Keeping Python %s compatibility venv at %s\n' "$version" "$tmpdir"
  fi

  actual_version="$("$python_bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "$actual_version" != "$version" ]]; then
    printf 'FAIL python%s resolved to Python %s at %s\n' "$version" "$actual_version" "$python_bin" >&2
    return 1
  fi

  venv="$tmpdir/venv"
  home_dir="$tmpdir/home"
  mkdir -p "$home_dir"

  "$python_bin" -m venv "$venv"
  py="$venv/bin/python"
  PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 "$py" -m pip install --quiet --upgrade pip setuptools wheel
  PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 "$py" -m pip install --quiet --editable "$ROOT_DIR"

  SIMA_CLI_CHECK_FOR_UPDATE=0 HOME="$home_dir" "$py" -m sima_cli -h >/dev/null
  SIMA_CLI_CHECK_FOR_UPDATE=0 HOME="$home_dir" "$py" -m sima_cli version | grep -q 'SiMa CLI version:'
  SIMA_CLI_CHECK_FOR_UPDATE=0 HOME="$home_dir" "$venv/bin/sima-cli" -h >/dev/null

  printf 'PASS Python %s (%s)\n' "$version" "$python_bin"
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
