"""Stub classes and helpers for git-fafo adoption integration tests.

The adoption workflow needs a GitHub API duplicate-repository response and
bare Git remotes with different histories. This module provides those reusable
fixtures for ``test_git_fafo_adoption`` while sharing repository configuration
with ``tests.integration.conftest``.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo
from github3 import exceptions as github3_exceptions

from git_donkey import fafo
from tests.integration.conftest import _configure_repo

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    import pytest


@dataclasses.dataclass(slots=True)
class _ExistingRepoResponse:
    """Stub API response for an existing GitHub repository."""

    status_code: int = 422

    @staticmethod
    def json() -> dict[str, object]:
        return {
            "message": "Repository creation failed.",
            "errors": [{"message": "name already exists on this account"}],
        }


@dataclasses.dataclass(slots=True)
class _ExistingRepoUser:
    """GitHub user stub for an existing repository."""

    login: str


@dataclasses.dataclass(slots=True)
class _ExistingRepoGitHub:
    """GitHub stub that reports an existing repository."""

    login: str

    def me(self) -> _ExistingRepoUser:
        return _ExistingRepoUser(self.login)

    @staticmethod
    def create_repository(name: str, *, private: bool = False) -> typ.NoReturn:
        raise github3_exceptions.UnprocessableEntity(_ExistingRepoResponse())


def _create_bare_remote(tmp_path: Path) -> Path:
    remote_path = tmp_path / "existing.git"
    Repo.init(remote_path, bare=True)
    return remote_path


@dataclasses.dataclass(slots=True)
class _SeedConfig:
    """Configuration for :func:`_seed_with_empty_initial`."""

    seed_name: str
    extra_refs: cabc.Sequence[str] = dataclasses.field(default_factory=list)
    setup_fn: cabc.Callable[[Repo], None] | None = None


def _seed_with_empty_initial(
    remote_path: Path,
    tmp_path: Path,
    config: _SeedConfig,
) -> None:
    """Initialise a repo, make an empty initial commit on main, push to remote.

    Args:
        remote_path: Path to the bare remote repository.
        tmp_path: Pytest temporary directory in which to create the seed clone.
        config: Seed parameters (name, extra refs, optional setup callback).

    """
    seed_path = tmp_path / config.seed_name
    repo = Repo.init(seed_path)
    _configure_repo(repo)
    repo.git.commit("--allow-empty", "-m", "Initial commit")
    repo.git.branch("-M", "main")
    if config.setup_fn is not None:
        config.setup_fn(repo)
    repo.create_remote("origin", remote_path.as_posix())
    repo.remote("origin").push("main")
    for ref in config.extra_refs:
        repo.remote("origin").push(ref)
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/main")


def _seed_empty_initial_commit(remote_path: Path, tmp_path: Path) -> None:
    _seed_with_empty_initial(remote_path, tmp_path, _SeedConfig("seed-empty"))


def _seed_nonempty_default(remote_path: Path, tmp_path: Path) -> None:
    """Seed the remote's default branch with a real (non-empty) commit."""
    seed_path = tmp_path / "seed-nonempty"
    repo = Repo.init(seed_path)
    _configure_repo(repo)
    (seed_path / "file.txt").write_text("real work\n")
    repo.git.add("file.txt")
    repo.git.commit("-m", "Add real work")
    repo.git.branch("-M", "main")
    repo.create_remote("origin", remote_path.as_posix())
    repo.remote("origin").push("main")
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/main")


def _seed_empty_initial_with_extra_branch(remote_path: Path, tmp_path: Path) -> None:
    """Seed an empty initial commit on main plus a branch with real commits."""
    seed_path = tmp_path / "seed-extra-branch"
    repo = Repo.init(seed_path)
    _configure_repo(repo)
    repo.git.commit("--allow-empty", "-m", "Initial commit")
    repo.git.branch("-M", "main")
    repo.git.checkout("-b", "feature")
    (seed_path / "file.txt").write_text("real work\n")
    repo.git.add("file.txt")
    repo.git.commit("-m", "Add real work")
    repo.create_remote("origin", remote_path.as_posix())
    repo.remote("origin").push("main")
    repo.remote("origin").push("feature")
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/main")


def _seed_empty_initial_with_tag(remote_path: Path, tmp_path: Path) -> None:
    """Seed an empty initial commit on main plus a tag."""

    def _add_tag(repo: Repo) -> None:
        repo.git.tag("v1")

    _seed_with_empty_initial(
        remote_path, tmp_path, _SeedConfig("seed-tag", ["v1"], _add_tag)
    )


def _patch_existing_github(
    monkeypatch: pytest.MonkeyPatch,
    remote_path: Path,
) -> None:
    def _fake_login(*, token: str | None = None) -> object:
        return _ExistingRepoGitHub("example")

    def _fake_remote_repository_url(*, owner: str, repo_name: str) -> str:
        return remote_path.as_posix()

    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test User")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test User")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    monkeypatch.setattr(fafo.github3, "login", _fake_login)
    monkeypatch.setattr(fafo, "_remote_repository_url", _fake_remote_repository_url)
