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
    """Write a minimal project whose build requirement is left unconstrained."""
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
    (root / "constraints.txt").write_text("", encoding="utf-8")


def test_an_unpinned_build_dependency_is_rejected(tmp_path: Path) -> None:
    """A build dependency absent from the hashes fails rather than resolving."""
    uv = shutil.which("uv")
    assert uv is not None, "the build-constraint test requires uv"
    _write_project(tmp_path)

    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [
            uv,
            "build",
            "--out-dir",
            "out",
            "--build-constraints",
            "constraints.txt",
            "--require-hashes",
            "--require-build-hashes",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode != 0, "an unpinned build dependency must fail"
    assert "no trusted hash was provided for: `setuptools`" in completed.stderr, (
        "the failure must name the unpinned build dependency"
    )
