#!/usr/bin/env bash

set -euo pipefail

OUTPUT_DIR="${1:-dist/pypi}"

echo "Building Python distributions into ${OUTPUT_DIR}"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

python -m build --sdist --wheel --outdir "$OUTPUT_DIR"
python -m twine check "$OUTPUT_DIR"/*

echo "Build and metadata checks completed."
echo "Artifacts:"
ls -1 "$OUTPUT_DIR"
