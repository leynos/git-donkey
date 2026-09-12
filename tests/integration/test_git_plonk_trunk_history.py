"""Regression tests for the history ``git plonk`` treats as trunk.

Where the BDD suite in ``test_git_plonk_bdd.py`` covers the cleanup modes
through their user-visible contract, these tests pin the one decision the
modes share: which history says a worktree is complete. Completion must follow
the advertised default branch — fetched into a remote-tracking ref — and never
the invoking worktree's own history or a stale local ``origin/HEAD`` alias,
even when the sweep runs from inside a linked topic worktree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from git_donkey import plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    commit_completion_marker,
    create_git_donkey_worktree,
)


def test_git_plonk_uses_main_history_when_run_from_topic_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default mode should ignore topic-only markers from a topic CWD."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-789-completed-from-main"
    topic_branch = "issue-790-active-topic"

    create_git_donkey_worktree(local_path, completed_branch)
    create_git_donkey_worktree(local_path, topic_branch)
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
        active_branch=topic_branch,
    )

    monkeypatch.chdir(scenario.worktree_path(topic_branch))
    topic_repo = Repo(Path.cwd())
    marker_path = Path.cwd() / "topic-only-marker.txt"
    marker_path.write_text("(#789)")
    topic_repo.index.add([marker_path.as_posix()])
    topic_repo.index.commit("Topic-only completion marker (#789)")
    exit_code = plonk.run_git_plonk()

    assert exit_code == 0, "expected default git plonk to succeed from topic worktree"
    assert scenario.worktree_path(completed_branch).exists(), (
        "expected trunk history to ignore topic-only completion marker"
    )
    assert scenario.worktree_path(topic_branch).exists(), (
        "expected topic worktree to remain"
    )
    assert completed_branch in Repo(local_path).heads, (
        "expected completed branch to remain without trunk marker"
    )


def test_git_plonk_removes_main_completed_worktree_from_topic_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default mode should use main markers when run from a topic CWD."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-791-completed-on-main"
    topic_branch = "issue-792-active-topic"

    create_git_donkey_worktree(local_path, completed_branch)
    create_git_donkey_worktree(local_path, topic_branch)
    commit_completion_marker(local_path, "(#791)")
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
        active_branch=topic_branch,
    )

    monkeypatch.chdir(scenario.worktree_path(topic_branch))
    exit_code = plonk.run_git_plonk()

    assert exit_code == 0, "expected default git plonk to succeed from topic worktree"
    assert not scenario.worktree_path(completed_branch).exists(), (
        "expected main-history completion marker to remove completed worktree"
    )
    assert scenario.worktree_path(topic_branch).exists(), (
        "expected active topic worktree to remain"
    )
    assert completed_branch in Repo(local_path).heads, (
        "expected default mode to keep completed branch"
    )


@pytest.mark.parametrize("hard", [False, True])
def test_git_plonk_keeps_invoking_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hard: bool,
) -> None:
    """Default and hard modes should not remove their invoking worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-793-invoking-completed-worktree"

    create_git_donkey_worktree(local_path, completed_branch)
    commit_completion_marker(local_path, "(#793)")
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
    )

    monkeypatch.chdir(scenario.worktree_path(completed_branch))
    exit_code = plonk.run_git_plonk(hard=hard)

    assert exit_code == 0, "expected git plonk to succeed from completed worktree"
    assert scenario.worktree_path(completed_branch).exists(), (
        "expected invoking completed worktree to remain"
    )
    assert completed_branch in Repo(local_path).heads, (
        "expected invoking completed branch to remain"
    )


def test_git_plonk_ignores_a_stale_remote_head_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completion should follow the advertised default, not a stale origin/HEAD.

    The remote keeps advertising ``main`` on its symbolic ``HEAD``; only the
    local ``refs/remotes/origin/HEAD`` alias is pointed at a branch that carries
    no completion marker.
    """
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-794-completed-on-advertised-default"
    repo = Repo(local_path)

    # A branch that exists on the remote, but is not its advertised default and
    # never receives the completion marker.
    repo.git.push("origin", "main:refs/heads/legacy")
    repo.remote("origin").fetch()

    create_git_donkey_worktree(local_path, completed_branch)
    commit_completion_marker(local_path, "(#794)")
    repo.git.symbolic_ref("refs/remotes/origin/HEAD", "refs/remotes/origin/legacy")
    scenario = PlonkScenario(local_path=local_path, completed_branch=completed_branch)

    exit_code = plonk.run_git_plonk()

    assert repo.git.symbolic_ref("refs/remotes/origin/HEAD") == (
        "refs/remotes/origin/legacy"
    ), "expected the fixture to leave a stale local alias for the run"
    assert exit_code == 0, "expected default git plonk to succeed"
    assert not scenario.worktree_path(completed_branch).exists(), (
        "expected the advertised default to supply the completion history"
    )
    assert completed_branch in Repo(local_path).heads, (
        "expected default mode to keep the completed branch"
    )
