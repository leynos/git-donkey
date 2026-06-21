"""Shared helpers and fixtures for git-donkey integration tests.

The integration suite exercises command-line workflows that use GitPython,
plumbum, and stubbed external binaries. This module centralizes repository
configuration helpers and reusable stub command setup so workflow tests can
focus on their scenario-specific assertions.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from git import Repo


@dataclasses.dataclass(frozen=True)
class StubCommands:
    """Filesystem locations for generated command stubs."""

    bin_dir: Path
    log_path: Path


def _configure_repo(repo: Repo) -> None:
    """Configure user identity for commit creation."""
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test User")
        config.set_value("user", "email", "test@example.com")


@pytest.fixture
def stub_commands(tmp_path: Path) -> StubCommands:
    """Create ``git`` and ``copier`` stubs that log invocations."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "calls.log"

    stub_template = (
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "log_path = Path(os.environ['STUB_LOG'])\n"
        "name = Path(sys.argv[0]).name\n"
        "with log_path.open('a') as handle:\n"
        "    handle.write(f\"{name} {' '.join(sys.argv[1:])}\\n\")\n"
        "if name == 'copier':\n"
        "    Path(sys.argv[-1]).mkdir(parents=True, exist_ok=True)\n"
        "sys.exit(0)\n"
    )

    for cmd in ("copier", "git"):
        stub_path = bin_dir / cmd
        stub_path.write_text(stub_template)
        stub_path.chmod(0o755)

    return StubCommands(bin_dir=bin_dir, log_path=log_path)


def _seed_repo(repo: Repo, filename: str, content: str) -> None:
    """Create and commit a file in the repository."""
    path = Path(repo.working_tree_dir or ".") / filename
    path.write_text(content)
    repo.index.add([str(path)])
    repo.index.commit("Seed commit")


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a local repo with a bare remote and a seeded main branch."""
    remote_path = tmp_path / "remote.git"
    local_path = tmp_path / "local"

    Repo.init(remote_path, bare=True)
    local_repo = Repo.init(local_path)
    _configure_repo(local_repo)
    local_repo.create_remote("origin", remote_path.as_posix())

    _seed_repo(local_repo, "README.md", "seed")
    local_repo.git.branch("-M", "main")
    local_repo.remote("origin").push("main")

    return local_path, remote_path
