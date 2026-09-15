"""Contracts for the package metadata published to PyPI."""

import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
EXPECTED_CLASSIFIERS = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: ISC License (ISCL)",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Version Control :: Git",
]


def test_pypi_metadata_describes_and_links_the_project() -> None:
    """PyPI metadata should help users find and assess git-donkey."""
    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert project["description"] == (
        "Git subcommands for branch-based development with linked worktrees"
    ), "PyPI should display a useful one-line package summary"
    assert project["keywords"] == [
        "git",
        "worktree",
        "cli",
        "developer-tools",
    ], "PyPI should expose the package's main search terms"
    assert project["urls"] == {
        "Homepage": "https://df12.studio",
        "Repository": "https://github.com/leynos/git-donkey",
        "Issues": "https://github.com/leynos/git-donkey/issues",
    }, "PyPI should link to the project website and support resources"
    assert project["classifiers"] == EXPECTED_CLASSIFIERS, (
        "PyPI should classify the package for suitable users and environments"
    )


def test_readme_uses_absolute_repository_links_and_pypi_badge() -> None:
    """Published README links should work outside the GitHub repository."""
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "https://img.shields.io/pypi/v/git-donkey" in readme, (
        "README should display the published PyPI version"
    )
    assert "https://pypi.org/project/git-donkey/" in readme, (
        "PyPI badge should link to the package page"
    )
    assert "](skill/" not in readme, "Skill links should work on PyPI"
    assert "](docs/" not in readme, "Documentation links should work on PyPI"
    assert "](LICENSE)" not in readme, "Licence link should work on PyPI"
    assert "](AGENTS.md)" not in readme, "Contribution link should work on PyPI"
