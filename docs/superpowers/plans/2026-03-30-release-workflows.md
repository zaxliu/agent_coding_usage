# Release Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add separate GitHub Actions workflows to publish the Python package to PyPI and the Node package to npm through both tag-based and manual releases.

**Architecture:** Keep Python and Node release automation in separate workflow files so each ecosystem can validate and publish independently. Reuse existing build and test commands, add lightweight version consistency checks, and document the required GitHub and registry-side trusted publishing setup.

**Tech Stack:** GitHub Actions, PyPI trusted publishing, npm trusted publishing, setuptools/build/twine, npm CLI

---

### Task 1: Map release surfaces and triggers

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/publish-pypi.yml`
- Create: `.github/workflows/publish-npm.yml`
- Modify: `README.md`
- Modify: `node/README.md`

- [ ] **Step 1: Define release trigger conventions**

Use `py-v*` tags for Python and `node-v*` tags for Node, and support `workflow_dispatch` for both workflows.

- [ ] **Step 2: Keep validation separate from the main CI workflow**

Do not overload `.github/workflows/ci.yml`; publish workflows should rerun their own test/build checks before release.

### Task 2: Implement PyPI publishing workflow

**Files:**
- Create: `.github/workflows/publish-pypi.yml`
- Modify: `README.md`
- Test: `.github/workflows/publish-pypi.yml`

- [ ] **Step 1: Add workflow with tag and manual triggers**

Create a workflow that runs on `push.tags: ['py-v*']` and `workflow_dispatch`.

- [ ] **Step 2: Add validation and version check**

Run `pip install -e '.[dev]' build twine`, run `pytest`, build with `./scripts/build_pypi_release.sh`, and if the event is a tag, verify the tag version matches `[project].version`.

- [ ] **Step 3: Publish with trusted publishing**

Grant `id-token: write` and use `pypa/gh-action-pypi-publish` against `dist/pypi/`.

### Task 3: Implement npm publishing workflow

**Files:**
- Create: `.github/workflows/publish-npm.yml`
- Modify: `node/README.md`
- Test: `.github/workflows/publish-npm.yml`

- [ ] **Step 1: Add workflow with tag and manual triggers**

Create a workflow that runs on `push.tags: ['node-v*']` and `workflow_dispatch`.

- [ ] **Step 2: Add validation and version check**

Run `npm ci`, `npm test`, and `npm pack --dry-run` inside `node/`. If the event is a tag, verify the tag version matches `node/package.json`'s `version`.

- [ ] **Step 3: Publish with provenance**

Grant `id-token: write`, `contents: read`, and use `npm publish --provenance --access public` in `node/`.

### Task 4: Document release setup and operator flow

**Files:**
- Modify: `README.md`
- Modify: `node/README.md`

- [ ] **Step 1: Document tag formats and manual dispatch usage**

Explain `py-vX.Y.Z` and `node-vX.Y.Z`, and state that manual runs publish the current checked-in version.

- [ ] **Step 2: Document trusted publishing prerequisites**

List the required GitHub environment/registry configuration for PyPI and npm trusted publishing.

- [ ] **Step 3: Document local preflight commands**

Keep the existing local PyPI build script and add the Node dry-run checks that match the workflow.
