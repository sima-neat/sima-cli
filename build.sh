#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${PYTHON:-}" && -x "venv/bin/python" ]]; then
  PYTHON="venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" scripts/generate_cli_markdown_docs.py
"$PYTHON" scripts/check_py_compatibility.py
"$PYTHON" -m build
