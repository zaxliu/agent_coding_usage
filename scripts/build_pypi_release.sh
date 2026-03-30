#!/usr/bin/env bash

set -euo pipefail

OUTPUT_DIR="${1:-dist/pypi}"
PYTHON_BIN="${PYTHON_BIN:-}"

if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    PYTHON_BIN="python"
  fi
fi

echo "Building Python distributions into ${OUTPUT_DIR}"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

"$PYTHON_BIN" -m build --sdist --wheel --outdir "$OUTPUT_DIR"
"$PYTHON_BIN" -m twine check "$OUTPUT_DIR"/*

echo "Build and metadata checks completed."
echo "Artifacts:"
ls -1 "$OUTPUT_DIR"
