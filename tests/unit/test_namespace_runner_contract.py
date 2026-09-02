"""Guard the repository-owned runner and type-checking contracts."""

import shutil
import subprocess  # noqa: S404 - the test verifies the external spelling tool
import tempfile
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
    assert "    runs-on: namespace-profile-default" in lines[start:end], (
        f"{workflow_name}:{job_name} must use the shared Namespace runner"
    )


def test_lint_test_job_limits_token_permissions() -> None:
    """Keep the CI job's token read-only."""
    lines = (
        (_REPO_ROOT / ".github" / "workflows" / "ci.yml")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    start = lines.index("  lint-test:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ") and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    job = lines[start:end]
    permissions_start = job.index("    permissions:")
    permissions_end = next(
        index
        for index in range(permissions_start + 1, len(job))
        if job[index].startswith("    ") and not job[index].startswith("      ")
    )
    permissions = job[permissions_start + 1 : permissions_end]
    assert permissions == ["      contents: read"], (
        "lint-test must grant only read access to repository contents"
    )


def test_reusable_wheel_build_keeps_matrix_runner() -> None:
    """Keep native wheel platform selection in the reusable matrix."""
    workflow = (_REPO_ROOT / ".github" / "workflows" / "build-wheels.yml").read_text(
        encoding="utf-8"
    )
    assert "    runs-on: ${{ matrix.os }}" in workflow, (
        "the reusable wheel build must retain its caller-selected runner matrix"
    )


def test_actionlint_registers_namespace_labels() -> None:
    """Register every Namespace label used by repository workflows."""
    config = (_REPO_ROOT / ".github" / "actionlint.yaml").read_text(encoding="utf-8")
    assert "    - namespace-profile-default\n" in config, (
        "actionlint must recognise the default Namespace runner label"
    )
    assert "    - namespace-profile-default-arm64\n" in config, (
        "actionlint must recognise the arm64 Namespace runner label"
    )


def test_typecheck_recipe_runs_the_configured_environment() -> None:
    """Keep the script import path on the executable type-check recipe."""
    lines = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    start = lines.index("typecheck: build ty ## Run typechecking")
    recipe = lines[start : start + 3]
    assert "\tPYTHONPATH=scripts ty check" in recipe, (
        "typecheck must execute ty with the scripts import path"
    )


def test_spelling_policy_checks_inline_code() -> None:
    """Keep inline code subject to the spelling policy."""
    config = _REPO_ROOT / "typos.toml"
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    typos_version = next(
        line.split("?=", 1)[1].strip()
        for line in makefile.splitlines()
        if line.startswith("TYPOS_VERSION ?=")
    )
    uv = shutil.which("uv")
    assert uv is not None, "the spelling contract test requires uv on PATH"
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "inline-code.md"
        probe.write_text("Inline `acheive` should be reported.\n", encoding="utf-8")
        result = subprocess.run(  # noqa: S603 - arguments are fixed and isolated
            [
                uv,
                "tool",
                "run",
                f"typos@{typos_version}",
                "--config",
                str(config),
                "--force-exclude",
                str(probe),
            ],
            capture_output=True,
            check=False,
            text=True,
        )

    output = result.stdout + result.stderr
    assert result.returncode == 2, (
        "the configured spelling checker must reject a misspelling in inline code"
    )
    assert "acheive" in output, (
        "the spelling checker must identify the inline-code misspelling"
    )
