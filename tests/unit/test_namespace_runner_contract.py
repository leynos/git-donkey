"""Guard the repository-owned runner and type-checking contracts."""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIRECT_JOBS = (
    ("ci.yml", "lint-test"),
    ("get-codescene-sha.yml", "refresh-sha"),
    ("release.yml", "pure-wheel"),
    ("release.yml", "release"),
)


@pytest.mark.parametrize(("workflow_name", "job_name"), _DIRECT_JOBS)
def test_repository_owned_linux_job_uses_namespace_runner(
    workflow_name: str, job_name: str
) -> None:
    """Keep each repository-owned Linux job on the shared profile."""
    lines = (
        (_REPO_ROOT / ".github" / "workflows" / workflow_name)
        .read_text(encoding="utf-8")
        .splitlines()
    )
    start = lines.index(f"  {job_name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ") and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    assert "    runs-on: namespace-profile-default" in lines[start:end]


def test_reusable_wheel_build_keeps_matrix_runner() -> None:
    """Keep native wheel platform selection in the reusable matrix."""
    workflow = (_REPO_ROOT / ".github" / "workflows" / "build-wheels.yml").read_text(
        encoding="utf-8"
    )
    assert "    runs-on: ${{ matrix.os }}" in workflow


def test_actionlint_registers_namespace_labels() -> None:
    """Register every Namespace label used by repository workflows."""
    config = (_REPO_ROOT / ".github" / "actionlint.yaml").read_text(encoding="utf-8")
    assert "    - namespace-profile-default\n" in config
    assert "    - namespace-profile-default-arm64\n" in config


def test_typecheck_recipe_runs_the_configured_environment() -> None:
    """Keep the script import path on the executable type-check recipe."""
    lines = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    start = lines.index("typecheck: build ty ## Run typechecking")
    recipe = lines[start : start + 3]
    assert "\tPYTHONPATH=scripts ty check" in recipe
