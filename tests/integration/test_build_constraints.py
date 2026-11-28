"""Exercise hash-pinned build constraints against a real ``uv build``."""

from __future__ import annotations

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - runs the declared build tool.
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(180)


def _write_project(root: Path) -> None:
    """Write a minimal project whose build requirement is left uninstalled."""
    (root / "src" / "demo").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "src" / "demo" / "__init__.py").write_text("", encoding="utf-8")


def _run(uv: str, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one fixed ``uv`` command and capture its output."""
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [uv, *arguments],
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )


def test_a_missing_build_dependency_is_not_resolved(tmp_path: Path) -> None:
    """A backend absent from the pinned environment fails without isolation."""
    uv = shutil.which("uv")
    assert uv is not None, "the build-constraint test requires uv"
    _write_project(tmp_path)
    venv = tmp_path / "venv"
    created = _run(uv, "venv", "--python", "3.13", str(venv), cwd=tmp_path)
    assert created.returncode == 0, created.stderr

    completed = _run(
        uv,
        "build",
        "--out-dir",
        "out",
        "--no-build-isolation",
        "--python",
        str(venv / "bin" / "python"),
        cwd=tmp_path,
    )

    assert completed.returncode != 0, "an uninstalled backend must fail the build"
    assert "No module named 'setuptools'" in completed.stderr, (
        "the failure must name the missing build dependency"
    )
