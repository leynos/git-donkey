"""Behaviour-driven tests for the completion markers ``git plonk`` acts on.

The module binds ``features/git_plonk_markers.feature`` to real temporary
repositories built by ``tests.integration.conftest._setup_repo`` and seeded
through ``tests.integration.plonk_helpers``. Where ``test_git_plonk_bdd.py``
covers the cleanup modes and ``test_git_plonk_trunk_bdd.py`` covers which
history is trunk, these scenarios cover the match itself: where in a commit
message a marker may sit, which references do and do not complete a branch,
which branch names carry a marker at all, and what a marker completes when
more than one worktree claims it.

They also pin the boundary between committed and uncommitted work: a completed
worktree whose branch has advanced by a clean commit is still removed, and hard
mode still deletes that branch even though the commit never reached trunk.
"""

from __future__ import annotations

import typing as typ

from git import Repo
from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    commit_completion_marker,
    commit_in_worktree,
    commit_message_on_branch,
    create_git_donkey_worktree,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

_ISSUE_BRANCH = "issue-123-fix-closed-work"
_COMPLETION_MARKER = "(#123)"
# A branch name matching neither the issue nor the roadmap convention, so no
# completion marker can be derived from it.
_UNRECOGNISED_BRANCH = "feature/plain"
# Two worktrees finishing the same issue, so one marker has to complete both.
_SHARED_MARKER_BRANCHES = ("issue-123-first", "issue-123-second")
# The commit a completed worktree makes after its marker landed on trunk.
_LATER_COMMIT_MESSAGE = "Follow-up work made in the worktree"


def _repository_with_issue_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Build a repository with one issue worktree and no trunk history yet."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, _ISSUE_BRANCH)
    return PlonkScenario(local_path=local_path, completed_branch=_ISSUE_BRANCH)


@given(
    parsers.parse('a repository whose trunk carries the commit message "{message}"'),
    target_fixture="scenario",
)
def repository_with_a_trunk_commit_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> PlonkScenario:
    r"""Commit ``message`` on trunk beside one ``issue-123`` worktree.

    A Gherkin step is a single line, so ``\\n`` in the feature file stands for
    a real newline here. That lets one step place a marker in a commit subject
    or in its body, which is where a merge commit usually carries it.

    Returns
    -------
    PlonkScenario
        The repository and the issue branch under test.

    """
    scenario = _repository_with_issue_worktree(tmp_path, monkeypatch)
    commit_message_on_branch(scenario.local_path, "main", message.replace("\\n", "\n"))
    return scenario


@given(
    "a repository with an unrecognised git donkey worktree",
    target_fixture="scenario",
)
def repository_with_an_unrecognised_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a worktree whose branch name implies no completion marker.

    Trunk still advances, so the scenario shows the branch is left alone for
    want of a marker rather than for want of history to match against.

    Returns
    -------
    PlonkScenario
        The repository and the unrecognised branch under test.

    """
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, _UNRECOGNISED_BRANCH)
    commit_message_on_branch(local_path, "main", "Unrelated trunk work (#999)")
    return PlonkScenario(
        local_path=local_path,
        completed_branch=_UNRECOGNISED_BRANCH,
    )


@given(
    "a repository with two git donkey worktrees sharing a completion marker",
    target_fixture="scenario",
)
def repository_with_worktrees_sharing_a_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create two worktrees for one issue and complete it once on trunk."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    for branch_name in _SHARED_MARKER_BRANCHES:
        create_git_donkey_worktree(local_path, branch_name)
    commit_completion_marker(local_path, _COMPLETION_MARKER)
    return PlonkScenario(
        local_path=local_path,
        completed_branch=_SHARED_MARKER_BRANCHES[0],
    )


@given(
    "a repository with a completed git donkey worktree holding a later commit",
    target_fixture="scenario",
)
def repository_with_a_later_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Advance the completed branch by one clean commit made in its worktree."""
    scenario = _repository_with_issue_worktree(tmp_path, monkeypatch)
    commit_completion_marker(scenario.local_path, _COMPLETION_MARKER)
    commit_in_worktree(scenario, scenario.completed_branch, _LATER_COMMIT_MESSAGE)
    return scenario


@when("I run git plonk in default mode", target_fixture="plonk_output")
def run_default_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run the default cleanup mode and capture its report."""
    exit_code = plonk.run_git_plonk()
    assert exit_code == 0, "expected default git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in hard mode", target_fixture="plonk_output")
def run_hard_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run the hard cleanup mode and capture its report."""
    exit_code = plonk.run_git_plonk(hard=True)
    assert exit_code == 0, "expected hard git plonk to succeed"
    return capsys.readouterr().out


@then("the completed worktree is removed")
def completed_worktree_is_removed(scenario: PlonkScenario) -> None:
    """Assert the worktree under test no longer exists."""
    assert not scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected completed worktree to be removed"
    )


@then("the completed worktree remains")
def completed_worktree_remains(scenario: PlonkScenario) -> None:
    """Assert the worktree under test still exists."""
    assert scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected worktree to remain"
    )


@then("the completed branch remains")
def completed_branch_remains(scenario: PlonkScenario) -> None:
    """Assert the local branch under test survives the run."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads, (
        "expected branch to remain"
    )


@then("the completed branch is deleted")
def completed_branch_is_deleted(scenario: PlonkScenario) -> None:
    """Assert hard mode deleted the local branch under test."""
    assert scenario.completed_branch not in Repo(scenario.local_path).heads, (
        "expected hard mode to delete the branch"
    )


@then("the completed branch keeps its later commit")
def completed_branch_keeps_its_later_commit(scenario: PlonkScenario) -> None:
    """Assert the commit made in the removed worktree survives on its branch."""
    tip = Repo(scenario.local_path).heads[scenario.completed_branch].commit
    assert _LATER_COMMIT_MESSAGE in str(tip.message), (
        "expected the branch to still point at the commit made in the worktree"
    )


@then("both worktrees sharing the marker are removed")
def both_worktrees_sharing_the_marker_are_removed(scenario: PlonkScenario) -> None:
    """Assert one marker removed every worktree claiming it."""
    for branch_name in _SHARED_MARKER_BRANCHES:
        assert not scenario.worktree_path(branch_name).exists(), (
            f"expected the shared marker to remove worktree {branch_name}"
        )


@then("git plonk reports both worktrees as removed")
def git_plonk_reports_both_worktrees_as_removed(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert the report names both removed worktrees, not merely the first."""
    assert "Removed worktrees:" in plonk_output, (
        "expected a removed worktree section in the report"
    )
    for branch_name in _SHARED_MARKER_BRANCHES:
        worktree_path = scenario.worktree_path(branch_name)
        assert f"- {worktree_path}" in plonk_output, (
            f"expected {branch_name} to be reported as removed"
        )


scenarios("features/git_plonk_markers.feature")
