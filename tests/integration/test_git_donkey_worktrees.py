"""Integration tests for git-donkey worktree management.

These tests cover the worktree orchestration in ``git_donkey.donkey`` and
``git_donkey.donkey_worktrees`` using real Git repositories. Shared repository
setup comes from ``tests.integration.conftest``, and the scenario and record
helpers the behavioural suites are built on come from
``tests.integration.donkey_helpers``.
"""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import donkey, donkey_worktrees
from tests.integration.conftest import _setup_repo
from tests.integration.donkey_helpers import (
    new_scenario,
    require_stack_record,
    run_donkey_without_pulling,
    seed_repo,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


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
    seed_repo(repo, "local.txt", "local change")

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/from-local", ".", no_pull=False)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/from-local"
    assert worktree_path.exists(), "expected worktree directory to be created"
    assert Repo(worktree_path).active_branch.name == "feature/from-local", (
        "expected worktree branch name to match"
    )
    assert Repo(worktree_path).head.commit == repo.head.commit, (
        "a local-only base supplies the new worktree's start point"
    )


@pytest.mark.parametrize(
    "options",
    [donkey._PullOptions(pull_rebase=True), donkey._PullOptions(pull_ff=True)],
)
@pytest.mark.parametrize("base", [None, "."])
def test_git_donkey_updates_base_branch_when_behind_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: donkey._PullOptions,
    base: str | None,
) -> None:
    """An explicit mode and accepted prompt update the actual base checkout."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    seed_repo(repo, "upstream.txt", "upstream change")
    repo.remote("origin").push("main")
    remote_tip = repo.head.commit.hexsha
    repo.git.reset("--hard", "HEAD~1")

    monkeypatch.chdir(local_path)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    exit_code = donkey.run_git_donkey("feature/update", base, options=options)

    assert exit_code == 0, (
        "an accepted prompt updates the base and creates the worktree"
    )
    assert repo.head.commit.hexsha == remote_tip, (
        "the base checkout is fast-forwarded to the remote tip"
    )
    worktree_path = local_path.parent / "local.worktrees" / "feature/update"
    assert Repo(worktree_path).head.commit.hexsha == remote_tip, (
        "the new worktree starts at the updated base"
    )


def test_a_base_that_moves_mid_run_cannot_move_the_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The base's commit is resolved once, and the branch is born from it.

    A base branch can move while a worktree is being created, because a push
    from somewhere else does exactly that. This case moves it inside the step
    that ensures the base is available, and therefore after the commit was
    resolved and before the branch exists. The worktree is created from the
    commit the base resolved to and the record keeps that same commit as its
    boundary, because both are one observation of one ref. A start point
    resolved by name instead would put the branch at the commit the base moved
    to, and record a boundary the branch never started from.
    """
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-base")
    repo = scenario.repo

    repo.git.checkout("-b", "feature/base")
    seed_repo(repo, "base.txt", "the base")
    resolved = repo.head.commit.hexsha
    seed_repo(repo, "moved.txt", "a commit the base gains while the branch is born")
    moved = repo.head.commit.hexsha
    repo.git.reset("--hard", resolved)
    repo.git.branch("feature/later", moved)
    repo.git.checkout("main")

    available = donkey_worktrees._ensure_base_branch_available

    def ensure_then_move(
        *,
        context: donkey_worktrees._WorktreeContext,
        base_branch: str,
    ) -> None:
        """Ensure the base is available, then move it out from under the run."""
        available(context=context, base_branch=base_branch)
        repo.git.update_ref("refs/heads/feature/base", moved)

    monkeypatch.setattr(
        donkey_worktrees,
        "_ensure_base_branch_available",
        ensure_then_move,
    )

    run_donkey_without_pulling(scenario, capsys, "feature/base")

    assert scenario.exit_code == 0, (
        f"the base exists, so the branch is created; git donkey said: {scenario.stderr}"
    )
    assert repo.commit("refs/heads/feature/base").hexsha == moved, (
        "the patched step ran and moved the base, so the branch had a newer "
        "commit to be born away from than the one it resolved"
    )
    assert scenario.worktree_head() == resolved, (
        "the branch starts at the commit the base resolved to, not at the commit "
        "it moved to while the worktree was being created"
    )
    record = require_stack_record(scenario, "feature/from-base")
    assert record.parent.branch == "feature/base", (
        "the record names the base the caller selected"
    )
    assert record.base == resolved, (
        "and keeps the commit that base resolved to as the boundary"
    )


def test_git_donkey_sets_upstream_for_existing_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should keep local branches and set upstream if remote exists."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("-b", "feature/existing")
    seed_repo(repo, "feature.txt", "feature")
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
