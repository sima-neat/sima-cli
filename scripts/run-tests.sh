#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"

usage() {
  printf 'Usage: %s [unit|e2e|compat|all] [suite args...]\n' "$0"
  printf '\n'
  printf 'Examples:\n'
  printf '  %s\n' "$0"
  printf '  %s unit -q\n' "$0"
  printf '  %s e2e -q\n' "$0"
  printf '  %s compat --strict\n' "$0"
  printf '  %s compat --install-missing --strict\n' "$0"
  printf '  %s all\n' "$0"
}

has_tests() {
  local path="$1"
  find "$path" -type f -name 'test_*.py' -print -quit | grep -q .
}

run_pytest() {
  local suite="$1"
  local path="$2"
  shift 2

  if ! has_tests "$path"; then
    printf 'No %s tests found under %s; skipping.\n' "$suite" "${path#$ROOT_DIR/}"
    return 0
  fi

  printf 'Running %s tests from %s\n' "$suite" "${path#$ROOT_DIR/}"
  "$PYTHON_BIN" -m pytest "$path" "$@"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

suite="${1:-unit}"
if [[ $# -gt 0 ]]; then
  shift
fi

cd "$ROOT_DIR"

case "$suite" in
  unit)
    run_pytest unit "$ROOT_DIR/tests/unit" "$@"
    ;;
  e2e)
    run_pytest e2e "$ROOT_DIR/tests/e2e" "$@"
    ;;
  compat)
    "$ROOT_DIR/scripts/check-cli-python-compat.sh" "$@"
    ;;
  all)
    run_pytest unit "$ROOT_DIR/tests/unit" "$@"
    run_pytest e2e "$ROOT_DIR/tests/e2e" "$@"
    "$ROOT_DIR/scripts/check-cli-python-compat.sh"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
