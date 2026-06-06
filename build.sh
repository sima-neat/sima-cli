#!/usr/bin/env bash
set -euo pipefail

python scripts/generate_cli_markdown_docs.py
python scripts/check_py_compatibility.py
python -m build
