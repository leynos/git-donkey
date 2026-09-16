"""Contract tests for the reproducible Ty type-checking gate."""

from __future__ import annotations

import subprocess  # ruff: ignore[suspicious-subprocess-import] - regression test executes make without a shell
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


def test_make_typecheck_pins_ty_and_omits_script_search_path(
    repository_root: Path,
    make_command: cabc.Callable[..., tuple[str, ...]],
) -> None:
    """Keep local type checking aligned with the CI module-resolution contract."""
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - test executes make without a shell
        make_command("--no-print-directory", "--dry-run", "typecheck"),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"make typecheck --dry-run must succeed: {result.stderr}"
    )
    assert "uv tool run ty@0.0.79 --version" in result.stdout, (
        f"typecheck must verify the pinned Ty release: {result.stdout}"
    )
    assert "uv tool run ty@0.0.79 check" in result.stdout, (
        f"typecheck must run the pinned Ty release: {result.stdout}"
    )
    assert "--extra-search-path" not in result.stdout, (
        f"typecheck must not reference the deleted scripts module path: {result.stdout}"
    )
