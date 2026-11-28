"""Contract tests for the reproducible Ty type-checking gate."""

from __future__ import annotations

import shlex
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
    commands = [
        shlex.split(line)
        for line in result.stdout.splitlines()
        if "ty@0.0.79 check" in line
    ]
    assert len(commands) == 1, (
        f"typecheck must run the pinned Ty check exactly once: {result.stdout}"
    )
    arguments = commands[0]
    assert arguments.count("--python") == 1, (
        f"typecheck must name the project environment exactly once: {arguments}"
    )
    position = arguments.index("--python")
    assert position + 1 < len(arguments), (
        f"typecheck must give --python a value: {arguments}"
    )
    assert arguments[position + 1] == ".venv", (
        f"typecheck must use exactly the project venv, not {arguments[position + 1]!r}"
    )
    assert "--extra-search-path" not in result.stdout, (
        f"typecheck must not reference a separate scripts module path: {result.stdout}"
    )
