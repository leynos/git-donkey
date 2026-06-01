"""Stub classes and helpers for git-fafo adoption integration tests."""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo
from github3 import exceptions as github3_exceptions

from git_donkey import fafo
from tests.integration.conftest import _configure_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


@dataclasses.dataclass
class _ExistingRepoResponse:
    """Stub API response for an existing GitHub repository."""

    status_code: int = 422

    @staticmethod
    def json() -> dict[str, object]:
        return {
            "message": "Repository creation failed.",
            "errors": [{"message": "name already exists on this account"}],
        }


@dataclasses.dataclass
class _ExistingRepoUser:
    """GitHub user stub for an existing repository."""

    login: str


@dataclasses.dataclass
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


def _seed_empty_initial_commit(remote_path: Path, tmp_path: Path) -> None:
    seed_path = tmp_path / "seed-empty"
    repo = Repo.init(seed_path)
    _configure_repo(repo)
    repo.git.commit("--allow-empty", "-m", "Initial commit")
    repo.git.branch("-M", "main")
    repo.create_remote("origin", remote_path.as_posix())
    repo.remote("origin").push("main")
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/main")


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
