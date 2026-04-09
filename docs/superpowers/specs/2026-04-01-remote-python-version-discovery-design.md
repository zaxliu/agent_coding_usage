# Remote Python Version Discovery Design

## Summary

This design hardens remote Python discovery for SSH-based collection. The current implementation accepts the first command named `python3` or `python`, which can incorrectly select Python 2 or an older Python 3 that does not satisfy the project's runtime requirement.

The new behavior keeps the existing discovery order, but adds a second validation step: each discovered candidate must report a version that satisfies `project.requires-python` from `pyproject.toml`. Candidates that fail version validation are skipped, and discovery continues until a compatible interpreter is found.

## Goals

- Keep the current discovery order and broad compatibility with remote shells.
- Treat `pyproject.toml` as the single source of truth for the minimum supported Python version.
- Skip incompatible remote interpreters instead of stopping at the first `python` hit.
- Produce a clearer error when remote Python exists but is too old.
- Add focused regression tests for Python 2 and older Python 3 candidates.

## Non-Goals

- No change to the remote collection payload format.
- No change to the SSH transport, timeout policy, or fallback upload behavior.
- No support for the full `requires-python` specifier grammar beyond the lower-bound form used by this project.

## Current Behavior

`RemoteFileCollector._discover_python()` iterates a fixed list of commands:

1. `sh -lc` with `command -v python3` then `command -v python`
2. `bash -lc` with the same lookup
3. `zsh -lc` with the same lookup
4. a common-path scan such as `/usr/bin/python3` and `/usr/bin/python`

The first stdout token whose basename is `python3` or `python` is accepted immediately. The code does not check whether that interpreter is Python 3, nor whether it satisfies the package's declared minimum version.

## Design Overview

Remote discovery becomes a two-step filter:

1. Discover candidate interpreter command or path using the existing logic.
2. Validate the candidate version against the minimum version loaded from `pyproject.toml`.

Only candidates that pass both steps are accepted.

## Minimum Version Source

The minimum supported version is read from `pyproject.toml` under `project.requires-python`.

For this repository, the value is currently `>=3.9`, so remote discovery must reject:

- Python 2.x
- Python 3.0 through 3.8

The runtime should parse the lower bound once and compare remote `sys.version_info[:2]` against that tuple.

## Parsing Rules

The implementation only needs to support the format currently used by the repo: a lower-bound specifier such as `>=3.9`.

Accepted examples:

- `>=3.9`
- `>=3.10`

Rejected or unsupported forms should surface a local configuration error because they indicate the code cannot determine the runtime floor reliably.

## Discovery Algorithm

For each discovery command:

1. Run the existing SSH probe command.
2. Extract the candidate interpreter name or absolute path from stdout.
3. Run a second SSH command using that exact interpreter:
   `python_cmd -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"`
4. Parse the returned major/minor version.
5. If the candidate version is lower than the required minimum, log a progress message and continue to the next candidate.
6. Return the first compatible candidate.

If no candidate is both discoverable and compatible, discovery returns an error that explicitly says no remote Python satisfying `requires-python` was found.

## Error Handling

There are three distinct outcomes:

- No interpreter found: preserve the existing "missing python" behavior.
- Interpreter found but version probe fails: treat that candidate as unusable and continue.
- Interpreter found but all candidates are too old: return a clear compatibility error mentioning the required version.

This distinction helps users debug machines where `python` exists but points to Python 2.

## Logging

Progress logs should make version filtering visible, for example:

- `探测命中但版本不满足：python requires >=3.9, got 2.7`
- `探测命中但版本不满足：/usr/bin/python3 requires >=3.9, got 3.8`

These logs remain informational and should not alter stdout payload parsing.

## Testing

Add regression coverage for:

- `python` resolves first but reports Python 2, and discovery continues to a later compatible `python3`
- `python` or `python3` resolves first but reports Python 3.8, and discovery continues
- all discovered candidates are below the required version, and probe fails with a compatibility error
- local parsing of `pyproject.toml` lower-bound version remains in sync with the repository setting

## Risks And Mitigations

- Extra SSH round-trips: acceptable because discovery happens once per collector and only on probe/collect startup.
- TOML parsing on older local Python versions: acceptable because the project already declares `>=3.9`, and tests can exercise the parser directly.
- Future `requires-python` complexity: fail loudly if the specifier format changes beyond what discovery can safely interpret.
