"""Shared helpers for git-donkey integration tests."""

from __future__ import annotations

from pathlib import Path

from git import Repo


def _configure_repo(repo: Repo) -> None:
    """Configure user identity for commit creation."""
    config = repo.config_writer()
    config.set_value("user", "name", "Test User")
    config.set_value("user", "email", "test@example.com")
    config.release()


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
