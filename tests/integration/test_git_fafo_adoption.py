"""Integration tests for git-fafo existing repository adoption."""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import fafo
from tests.integration._fafo_adoption_stubs import (
    _create_bare_remote,
    _patch_existing_github,
    _seed_empty_initial_commit,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_git_fafo_adopts_existing_zero_commit_repo_with_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-fafo should adopt an existing repository with no commits."""
    remote_path = _create_bare_remote(tmp_path)
    _patch_existing_github(monkeypatch, remote_path)
    monkeypatch.chdir(tmp_path)

    exit_code = fafo.run_git_fafo("demo-repo", yes=True)

    assert exit_code == 0, "expected git-fafo to adopt the empty remote"
    assert (tmp_path / "demo-repo").is_dir(), "expected local repo directory"
    remote_repo = Repo(remote_path)
    assert remote_repo.git.rev_list("--count", "main").strip() == "1"


def test_git_fafo_adopts_existing_empty_initial_commit_with_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-fafo should adopt a repository with only an empty initial commit."""
    remote_path = _create_bare_remote(tmp_path)
    _seed_empty_initial_commit(remote_path, tmp_path)
    _patch_existing_github(monkeypatch, remote_path)
    monkeypatch.chdir(tmp_path)

    exit_code = fafo.run_git_fafo("demo-repo", yes=True)

    assert exit_code == 0, "expected git-fafo to adopt the empty initial commit"
    remote_repo = Repo(remote_path)
    assert remote_repo.git.rev_list("--count", "main").strip() == "1"


def test_git_fafo_rejects_existing_repo_without_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-fafo should not adopt an existing repository without confirmation."""
    remote_path = _create_bare_remote(tmp_path)
    _patch_existing_github(monkeypatch, remote_path)
    monkeypatch.setattr(fafo.helpers, "_prompt_yes_no", lambda *_args, **_kwargs: False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        fafo.run_git_fafo("demo-repo")

    assert excinfo.value.code == 1
    assert not (tmp_path / "demo-repo").exists(), "expected no local scaffold"
