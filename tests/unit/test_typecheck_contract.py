"""Contract tests for the reproducible Ty type-checking gate."""

import shutil
import subprocess  # noqa: S404 - regression test executes make without a shell
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_make_typecheck_pins_ty_and_resolves_script_modules() -> None:
    """Keep local type checking aligned with the CI module-resolution contract."""
    make_executable = shutil.which("make")
    assert make_executable is not None

    result = subprocess.run(  # noqa: S603 - test executes make without a shell
        [make_executable, "--no-print-directory", "--dry-run", "typecheck"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "uv tool run ty==0.0.73 --version" in result.stdout
    assert "uv tool run ty==0.0.73 check --extra-search-path scripts" in result.stdout
