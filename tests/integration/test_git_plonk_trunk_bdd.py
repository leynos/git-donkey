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

from git import Repo
from pytest_bdd import given, scenarios, then, when

from git_donkey import cli, plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    advertise_remote_default,
    capture_plonk_run,
    commit_completion_marker,
    commit_message_on_branch,
    create_git_donkey_worktree,
    push_marker_from_clone,
    record_plonk_run,
    stop_advertising_default_branch,
)
from tests.output_assertions import require_stream, stream

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
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
def run_default_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the default cleanup mode and record its report."""
    record_plonk_run(scenario, capsys, "default", plonk.run_git_plonk)
    assert scenario.exit_code == 0, "expected default git plonk to succeed"


@when("I run git plonk in hard mode")
def run_hard_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the hard cleanup mode and record its report."""
    record_plonk_run(
        scenario,
        capsys,
        "hard",
        lambda: plonk.run_git_plonk(hard=True),
    )
    assert scenario.exit_code == 0, "expected hard git plonk to succeed"


@when("I run git plonk in default dry-run mode")
def run_default_dry_run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run default cleanup in dry-run mode through the CLI boundary."""
    record_plonk_run(
        scenario,
        capsys,
        "default dry-run",
        lambda: cli._plonk_app(["--dry-run"]),
    )
    assert scenario.exit_code == 0, "expected default dry-run git plonk to succeed"


@when("git plonk fails in default mode")
def run_failing_default_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run default cleanup against a trunk it cannot resolve."""
    scenario.mode = "default"
    capture_plonk_run(scenario, capsys, plonk.run_git_plonk)
    assert scenario.exit_code == _TRUNK_FAILURE_EXIT_CODE, (
        "expected unresolvable trunk to exit with a failure code"
    )


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
def git_plonk_reports_a_missing_advertised_default(scenario: PlonkScenario) -> None:
    """Assert stderr is exactly the missing-advertisement diagnostic."""
    remote_name = Repo(scenario.local_path).remotes[0].name
    require_stream(
        scenario.stderr,
        stream(
            f"git-plonk: remote '{remote_name}' does not advertise a default branch"
        ),
        context="git plonk stderr",
    )


@then("git plonk plans to remove the completed worktree")
def git_plonk_plans_to_remove_the_completed_worktree(scenario: PlonkScenario) -> None:
    """Assert the dry-run plan is exactly the one worktree it would remove."""
    require_stream(
        scenario.stdout,
        stream(
            f"git-plonk: mode={scenario.mode}",
            "Planned worktree removals:",
            f"- {scenario.worktree_path(scenario.completed_branch)}",
        ),
        context="git plonk stdout",
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
