"""Integration tests for git-donkey worktree management.

These tests cover the worktree orchestration in ``git_donkey.donkey`` and
``git_donkey.donkey_worktrees`` using real Git repositories. Shared repository
setup comes from ``tests.integration.conftest``.
"""

from __future__ import annotations

import typing as typ

from git import Repo

from git_donkey import donkey
from tests.integration.conftest import _seed_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_git_donkey_creates_new_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should create a new linked worktree on a new branch."""
    local_path, _remote_path = _setup_repo(tmp_path)

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/worktree", no_pull=True)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/worktree"
    assert worktree_path.exists(), "expected worktree directory to be created"
    assert Repo(worktree_path).active_branch.name == "feature/worktree", (
        "expected worktree branch name to match"
    )


def test_git_donkey_allows_local_only_base_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should allow local-only base branches without --no-pull."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("-b", "feature/local-only")
    _seed_repo(repo, "local.txt", "local change")

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/from-local", ".", no_pull=False)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/from-local"
    assert worktree_path.exists(), "expected worktree directory to be created"
    assert Repo(worktree_path).active_branch.name == "feature/from-local", (
        "expected worktree branch name to match"
    )


def test_git_donkey_updates_base_branch_when_behind_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should update a behind base branch when the prompt is accepted."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("main")
    _seed_repo(repo, "upstream.txt", "upstream change")
    repo.remote("origin").push("main")
    repo.git.reset("--hard", "HEAD~1")

    monkeypatch.chdir(local_path)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    called: dict[str, object] = {}

    def _fake_update_base_branch_in_worktree(
        context: donkey._DonkeyContext,
        *,
        base_branch: str,
        prefix: str,
    ) -> None:
        called["context"] = context
        called["base_branch"] = base_branch
        called["prefix"] = prefix

    monkeypatch.setattr(
        donkey,
        "_update_base_branch_in_worktree",
        _fake_update_base_branch_in_worktree,
    )

    exit_code = donkey.run_git_donkey("feature/update", no_pull=False)

    assert exit_code == 0, "expected git-donkey to exit successfully"
    assert called["base_branch"] == "main", "expected main to be updated"
    assert called["prefix"] == donkey._GIT_DONKEY_PREFIX, (
        "expected git-donkey prefix in update call"
    )


def test_git_donkey_sets_upstream_for_existing_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should keep local branches and set upstream if remote exists."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("-b", "feature/existing")
    _seed_repo(repo, "feature.txt", "feature")
    repo.remote("origin").push("feature/existing")
    repo.git.checkout("main")

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/existing", no_pull=True)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/existing"
    assert worktree_path.exists(), "expected worktree directory to be created"
    worktree_repo = Repo(worktree_path)
    assert worktree_repo.active_branch.name == "feature/existing", (
        "expected existing branch to be checked out in worktree"
    )
    assert (
        str(worktree_repo.active_branch.tracking_branch()) == "origin/feature/existing"
    ), "expected worktree branch to track origin/feature/existing"
