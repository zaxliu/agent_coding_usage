from __future__ import annotations

import os
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_includes_pypi_metadata():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert project["authors"] == [{"name": "Lewis"}]
    assert project["keywords"] == ["llm", "usage", "cli", "feishu"]
    assert "Homepage" in project["urls"]
    assert "Repository" in project["urls"]
    assert "Programming Language :: Python :: 3" in project["classifiers"]
    assert not any(classifier.startswith("License ::") for classifier in project["classifiers"])
    assert project["license-files"] == ["LICENSE"]


def test_release_script_builds_only_python_distributions():
    script_path = REPO_ROOT / "scripts" / "build_pypi_release.sh"

    assert script_path.exists()
    assert os.access(script_path, os.X_OK)

    script_text = script_path.read_text(encoding="utf-8")

    assert "python -m build --sdist --wheel --outdir \"$OUTPUT_DIR\"" in script_text
    assert "python -m twine check \"$OUTPUT_DIR\"/*" in script_text
    assert "twine upload" not in script_text
    assert "dist/*" not in script_text


def test_distribution_name_is_llm_usage():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "llm-usage-horizon"
