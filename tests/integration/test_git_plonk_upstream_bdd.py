"""Behaviour-driven tests for the opt-in gone-upstream completion tier.

The scenarios in ``features/git_plonk_upstream.feature`` run against real
temporary repositories: a clean git-donkey worktree whose branch carries no
offline marker, and whose configured upstream is deleted, still present, or
never created. The tier is deliberately opt-in because a deleted branch cannot
distinguish a merged change from an abandoned one, so default mode must leave
the same worktree unverified. Each scenario also pins the fixed reason text the
summary uses, including the distinction between a branch that still exists and
one that was never pushed.
"""

from __future__ import annotations

import typing as typ

from git import Repo
from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    create_git_donkey_worktree,
    record_plonk_run,
    removal_entry,
    unverified_entry,
)
from tests.output_assertions import require_stream, stream

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    import pytest

_BRANCH = "issue-77-upstream-state"

# Each invocation the feature names, mapped to the summary mode and the call.
_PLONK_INVOCATIONS: dict[str, tuple[str, cabc.Callable[[], int | None]]] = {
    "in default mode": ("default", plonk.run_git_plonk),
    "with the upstream-gone tier": (
        "default",
        lambda: plonk.run_git_plonk(options=plonk._PlonkRunOptions(upstream_gone=True)),
    ),
}


def _repository_with_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a repository with one clean, descriptive git-donkey worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, _BRANCH)
    return PlonkScenario(local_path=local_path, completed_branch=_BRANCH)


def _push_with_upstream(scenario: PlonkScenario) -> None:
    """Push the worktree's branch and record its upstream configuration."""
    worktree_repo = Repo(scenario.worktree_path(_BRANCH))
    worktree_repo.git.push("--set-upstream", "origin", _BRANCH)


@given(
    "a repository with a git donkey worktree whose upstream has been deleted",
    target_fixture="scenario",
)
def repository_with_deleted_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Push the branch, then delete it from the remote."""
    scenario = _repository_with_worktree(tmp_path, monkeypatch)
    _push_with_upstream(scenario)
    Repo(scenario.local_path).git.push("origin", "--delete", _BRANCH)
    return scenario


@given(
    "a repository with a git donkey worktree whose upstream still exists",
    target_fixture="scenario",
)
def repository_with_present_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Push the branch and leave it on the remote."""
    scenario = _repository_with_worktree(tmp_path, monkeypatch)
    _push_with_upstream(scenario)
    return scenario


@given(
    "a repository with a git donkey worktree that was never pushed",
    target_fixture="scenario",
)
def repository_with_never_pushed_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Leave the branch local, so it configures no upstream."""
    return _repository_with_worktree(tmp_path, monkeypatch)


@when(parsers.parse("I run git plonk {invocation}"))
def run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
    invocation: str,
) -> None:
    """Run the named invocation and record its report."""
    mode, call = _PLONK_INVOCATIONS[invocation]
    record_plonk_run(scenario, capsys, mode, call)


@then("the completed worktree is removed")
def completed_worktree_is_removed(scenario: PlonkScenario) -> None:
    """Assert the worktree no longer exists."""
    assert not scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected the completed worktree to be removed"
    )


@then("the completed worktree remains")
def completed_worktree_remains(scenario: PlonkScenario) -> None:
    """Assert the worktree still exists."""
    assert scenario.worktree_path(scenario.completed_branch).is_dir(), (
        "expected the unverified worktree to remain"
    )


@then("the completed branch remains")
def completed_branch_remains(scenario: PlonkScenario) -> None:
    """Assert default mode kept the local branch."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads, (
        "expected default mode to keep the local branch"
    )


@then(parsers.parse('git plonk reports the removal as "{label}"'))
def git_plonk_reports_the_removal_as(
    scenario: PlonkScenario,
    label: str,
) -> None:
    """Assert the removal carries the expected evidence label."""
    require_stream(
        scenario.stdout,
        stream(
            f"git-plonk: mode={scenario.mode}",
            "Removed worktrees:",
            removal_entry(
                scenario.worktree_path(scenario.completed_branch),
                label,
            ),
        ),
        context="git plonk stdout",
    )


@then(
    parsers.parse(
        'git plonk reports the completed worktree as unverified with "{reason}"'
    )
)
def git_plonk_reports_the_worktree_as_unverified(
    scenario: PlonkScenario,
    reason: str,
) -> None:
    """Assert the unverified report carries the expected fixed reason."""
    require_stream(
        scenario.stdout,
        stream(
            f"git-plonk: mode={scenario.mode}",
            "Unverified worktrees:",
            unverified_entry(
                scenario.worktree_path(scenario.completed_branch),
                reason,
            ),
        ),
        context="git plonk stdout",
    )


scenarios("features/git_plonk_upstream.feature")
