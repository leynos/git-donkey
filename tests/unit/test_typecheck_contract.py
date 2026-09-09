"""Contract tests for the reproducible Ty type-checking gate."""

from __future__ import annotations

import subprocess  # noqa: S404 - regression test executes make without a shell
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_make_typecheck_pins_ty_and_resolves_script_modules(
    repository_root: Path,
    make_command: typ.Callable[..., tuple[str, ...]],
) -> None:
    """Keep local type checking aligned with the CI module-resolution contract."""
    result = subprocess.run(  # noqa: S603 - test executes make without a shell
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
    assert (
        "uv tool run ty@0.0.79 check --extra-search-path scripts" in result.stdout
    ), (
        "typecheck must resolve script modules through --extra-search-path: "
        f"{result.stdout}"
    )
