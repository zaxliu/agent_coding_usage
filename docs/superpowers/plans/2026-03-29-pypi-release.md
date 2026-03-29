# PyPI Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the project locally ready for PyPI publication with complete metadata, a safe build script, and documented manual upload commands.

**Architecture:** Keep the current setuptools packaging flow and add a narrow release helper that only builds and validates Python artifacts in a dedicated output directory. Lock the behavior with focused packaging tests so future changes do not regress the safety boundary.

**Tech Stack:** Python, setuptools, pytest, bash, twine

---

### Task 1: Lock packaging requirements with tests

**Files:**
- Create: `tests/test_packaging.py`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pyproject_includes_pypi_metadata():
    ...


def test_release_script_builds_only_python_distributions():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_packaging.py -q`
Expected: FAIL because metadata keys and release script do not exist yet

- [ ] **Step 3: Write minimal implementation**

```text
Add the missing `project.readme`, `project.license`, `project.authors`,
`project.keywords`, `project.classifiers`, and `project.urls` fields.
Add `scripts/build_pypi_release.sh` that builds into `dist/pypi` and runs
`python -m twine check` without any upload step.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_packaging.py -q`
Expected: PASS

### Task 2: Document the manual release workflow

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the failing test**

```text
No dedicated automated README assertion for this task.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_packaging.py -q`
Expected: PASS from Task 1; this task is documentation-only

- [ ] **Step 3: Write minimal implementation**

```text
Add a `发布到 PyPI` section that covers:
- installing `build` and `twine`
- bumping the package version
- running `./scripts/build_pypi_release.sh`
- uploading with manual `twine upload` commands
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_packaging.py -q`
Expected: PASS

### Task 3: Verify the release path end-to-end

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Create: `LICENSE`
- Create: `scripts/build_pypi_release.sh`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Install release tooling**

Run: `python -m pip install -U build twine`
Expected: both packages available in the active environment

- [ ] **Step 2: Run the packaging test suite**

Run: `pytest tests/test_packaging.py -q`
Expected: PASS

- [ ] **Step 3: Run the release build script**

Run: `./scripts/build_pypi_release.sh`
Expected: build succeeds and `twine check` passes for `dist/pypi/*`
