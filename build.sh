#!/usr/bin/env bash
set -euo pipefail

python scripts/check_py_compatibility.py
python -m build
