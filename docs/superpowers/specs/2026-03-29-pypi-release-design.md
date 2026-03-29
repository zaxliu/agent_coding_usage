# PyPI Release Design

## Goal

Make this repository safe and straightforward to publish to PyPI without mixing Python package artifacts with the existing business bundle zip files already stored under `dist/`.

## Scope

- Add missing PyPI metadata to `pyproject.toml`
- Add a local build-and-check script for PyPI distributions
- Document the release workflow in `README.md`
- Keep upload as a separate manual step

## Approach

Use the existing `setuptools` build backend and keep packaging simple. The release script builds into `dist/pypi/` instead of `dist/` so the upload command can safely target only Python distributions.

## Safety Constraints

- No automatic upload in the script
- No `dist/*` glob in release instructions
- Keep the current package name and entry point unchanged

## Verification

- Add tests for packaging metadata and release script boundaries
- Run targeted packaging tests
- Run the release script after installing `build` and `twine`
