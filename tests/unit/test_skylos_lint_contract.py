"""Contract tests for the blocking Skylos dead-code lint gate."""

import shutil
import subprocess  # noqa: S404 - regression test executes make without a shell
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_make_lint_runs_skylos_dead_code_gate() -> None:
    """Keep the Skylos invocation deterministic and production-scoped."""
    make_executable = shutil.which("make")
    assert make_executable is not None

    result = subprocess.run(  # noqa: S603 - test executes make without a shell
        [make_executable, "--no-print-directory", "--dry-run", "lint"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    skylos_commands = [
        line
        for line in result.stdout.splitlines()
        if "skylos --config-file pyproject.toml" in line
    ]
    assert len(skylos_commands) == 1
    assert "git_donkey --category dead_code --gate" in skylos_commands[0]
    assert "tests" not in skylos_commands[0]
    assert "--no-upload --no-provenance --no-grep-verify" in result.stdout


def test_skylos_allow_list_starts_empty() -> None:
    """Require verified false positives to be documented before allow-listing."""
    project_config = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    skylos = project_config["tool"]["skylos"]
    assert skylos["whitelist"]["names"] == []
