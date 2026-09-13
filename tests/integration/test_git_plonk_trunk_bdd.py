"""Behaviour-driven tests for the trunk ``git plonk`` judges completion against.

The module binds ``features/git_plonk_trunk.feature`` to real temporary
repositories built by ``tests.integration.conftest._setup_repo`` and seeded
through ``tests.integration.plonk_helpers``. Where
``test_git_plonk_bdd.py`` covers the cleanup modes, these scenarios cover the
one question they all ask first: which history counts as trunk, and what
happens when the remote cannot answer it.

The scenarios exercise a non-``main`` advertised default, a remote advertising
no default at all, an unreachable remote, a dry run that must still fetch the
advertisement, and the guarantee that neither default nor hard mode reaches the
GitHub API.
"""

from __future__ import annotations

import shutil
import typing as typ

import pytest
from git import Repo
from pytest_bdd import given, scenarios, then, when

from git_donkey import cli, plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    advertise_remote_default,
    commit_completion_marker,
    commit_message_on_branch,
    create_git_donkey_worktree,
    push_marker_from_clone,
    stop_advertising_default_branch,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from vcr.cassette import Cassette

_COMPLETED_BRANCH = "issue-123-fix-closed-work"
_COMPLETION_MARKER = "(#123)"
# A default branch named something other than ``main``, so a scenario can tell
# the advertised trunk apart from the branch a reader assumes is trunk.
_ADVERTISED_TRUNK = "trunk"
# git-plonk exits with this code when it cannot resolve or reach trunk.
_TRUNK_FAILURE_EXIT_CODE = 1


def _repository_with_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PlonkScenario, Path]:
    """Build a repository whose ``main`` completes its one git-donkey worktree."""
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, _COMPLETED_BRANCH)
    commit_completion_marker(local_path, _COMPLETION_MARKER)
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=_COMPLETED_BRANCH,
    )
    return scenario, remote_path


def _repository_advertising_trunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Build a repository whose remote advertises ``trunk``, with one worktree."""
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    advertise_remote_default(local_path, remote_path, _ADVERTISED_TRUNK)
    create_git_donkey_worktree(local_path, _COMPLETED_BRANCH)
    return PlonkScenario(local_path=local_path, completed_branch=_COMPLETED_BRANCH)


def _assert_marker_absent(local_path: Path, ref: str) -> None:
    """Assert ``ref`` does not carry the completion marker yet.

    Each scenario that hides the marker from a ref checks that it really is
    hidden, so a seeding mistake fails as a broken precondition rather than
    passing as the behaviour under test.
    """
    messages = [str(commit.message) for commit in Repo(local_path).iter_commits(ref)]
    assert not any(_COMPLETION_MARKER in message for message in messages), (
        f"expected {ref} to lack the completion marker before the run"
    )


@given(
    "a repository whose advertised trunk carries the completion marker",
    target_fixture="scenario",
)
def repository_with_marker_on_advertised_trunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Put the completion marker on the advertised trunk, never on ``main``."""
    scenario = _repository_advertising_trunk(tmp_path, monkeypatch)
    commit_message_on_branch(
        scenario.local_path,
        _ADVERTISED_TRUNK,
        f"Merge work {_COMPLETION_MARKER}",
    )
    _assert_marker_absent(scenario.local_path, "main")
    return scenario


@given(
    "a repository whose advertised trunk lacks a marker local main carries",
    target_fixture="scenario",
)
def repository_with_marker_on_local_main_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Put the completion marker on ``main`` while the remote advertises trunk."""
    scenario = _repository_advertising_trunk(tmp_path, monkeypatch)
    commit_completion_marker(scenario.local_path, _COMPLETION_MARKER)
    return scenario


@given(
    "a repository whose remote advertises no default branch",
    target_fixture="scenario",
)
def repository_without_an_advertised_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Break the remote's advertisement after the worktree has been created."""
    scenario, remote_path = _repository_with_completed_worktree(tmp_path, monkeypatch)
    stop_advertising_default_branch(remote_path)
    return scenario


@given("a repository whose remote has been deleted", target_fixture="scenario")
def repository_with_an_unreachable_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Delete the bare remote after the worktree has been created."""
    scenario, remote_path = _repository_with_completed_worktree(tmp_path, monkeypatch)
    shutil.rmtree(remote_path)
    return scenario


@given(
    "a repository whose remote carries a marker the local clone has not fetched",
    target_fixture="scenario",
)
def repository_with_an_unfetched_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Land the completion marker on the remote through a second clone."""
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, _COMPLETED_BRANCH)
    push_marker_from_clone(
        remote_path,
        tmp_path / "second-clone",
        _COMPLETION_MARKER,
    )
    _assert_marker_absent(local_path, "refs/remotes/origin/main")
    return PlonkScenario(local_path=local_path, completed_branch=_COMPLETED_BRANCH)


@given(
    "a repository with a completed git donkey worktree and recorded GitHub API traffic",
    target_fixture="scenario",
)
def repository_with_recorded_github_traffic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    github_api_cassette: Cassette,
) -> PlonkScenario:
    """Build the completed repository with the GitHub API cassette replaying.

    The cassette is requested here, rather than only in the assertion, so it is
    active for the whole scenario: a request made while the worktree is created
    would fail just as loudly as one made during the sweep.

    Returns
    -------
    PlonkScenario
        The repository and the completed branch under test.

    """
    scenario, _remote_path = _repository_with_completed_worktree(tmp_path, monkeypatch)
    return scenario


@when("I run git plonk in default mode")
def run_default_plonk(scenario: PlonkScenario) -> None:
    """Run the default cleanup mode."""
    exit_code = plonk.run_git_plonk()
    assert exit_code == 0, "expected default git plonk to succeed"


@when("I run git plonk in hard mode")
def run_hard_plonk(scenario: PlonkScenario) -> None:
    """Run the hard cleanup mode."""
    exit_code = plonk.run_git_plonk(hard=True)
    assert exit_code == 0, "expected hard git plonk to succeed"


@when("I run git plonk in default dry-run mode", target_fixture="plonk_output")
def run_default_dry_run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run default cleanup in dry-run mode through the CLI boundary."""
    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--dry-run"])
    assert exc_info.value.code == 0, "expected default dry-run git plonk to succeed"
    return capsys.readouterr().out


@when("git plonk fails in default mode", target_fixture="plonk_stderr")
def run_failing_default_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run default cleanup against a trunk it cannot resolve and capture stderr."""
    with pytest.raises(SystemExit) as exc_info:
        plonk.run_git_plonk()
    assert exc_info.value.code == _TRUNK_FAILURE_EXIT_CODE, (
        "expected unresolvable trunk to exit with a failure code"
    )
    return capsys.readouterr().err


@then("the completed worktree is removed")
def completed_worktree_is_removed(scenario: PlonkScenario) -> None:
    """Assert the completed worktree path no longer exists."""
    assert not scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected completed worktree to be removed"
    )


@then("the completed worktree remains")
def completed_worktree_remains(scenario: PlonkScenario) -> None:
    """Assert the completed worktree path still exists."""
    assert scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected completed worktree to remain"
    )


@then("the completed branch remains")
def completed_branch_remains(scenario: PlonkScenario) -> None:
    """Assert the completed local branch survives the run."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads, (
        "expected completed branch to remain"
    )


@then("the completed branch is deleted")
def completed_branch_is_deleted(scenario: PlonkScenario) -> None:
    """Assert hard mode deleted the completed local branch."""
    assert scenario.completed_branch not in Repo(scenario.local_path).heads, (
        "expected hard mode to delete completed branch"
    )


@then("git plonk reports a missing advertised default branch")
def git_plonk_reports_a_missing_advertised_default(plonk_stderr: str) -> None:
    """Assert stderr names the missing advertisement rather than a generic error."""
    assert "does not advertise a default branch" in plonk_stderr, (
        "expected git plonk to report the missing advertised default branch"
    )


@then("git plonk plans to remove the completed worktree")
def git_plonk_plans_to_remove_the_completed_worktree(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert the dry-run plan lists the worktree completed only on the remote."""
    assert "Planned worktree removals:" in plonk_output, (
        "expected a planned worktree removal section"
    )
    worktree_path = scenario.worktree_path(scenario.completed_branch)
    assert f"- {worktree_path}" in plonk_output, (
        "expected the dry run to fetch trunk and plan the removal"
    )


@then("no GitHub API request was made")
def no_github_api_request_was_made(github_api_cassette: Cassette) -> None:
    """Assert the run never reached the GitHub API."""
    assert len(github_api_cassette.requests) == 0, (
        "expected git plonk to make no GitHub API request"
    )
    assert github_api_cassette.play_count == 0, (
        "expected git plonk to replay no recorded GitHub API interaction"
    )


scenarios("features/git_plonk_trunk.feature")
