"""Behaviour-driven tests for the GitHub merged-pull-request fallback.

The scenarios in ``features/git_plonk_github.feature`` keep the repository
local while presenting a raw GitHub URL to git-plonk: ``remote.origin.url`` is
the GitHub URL a user would configure, and a Git ``insteadOf`` rewrite sends
the actual fetches to the temporary bare remote. The only process that leaves
the machine is the recording ``gh`` stub installed by the suite's autouse
fixture, so each scenario can decide whether the answer is a matching merged
pull request, a failure, or a response that must be rejected.

The merged scenario also asserts the lookup was attempted exactly once. Every
other plonk BDD module asserts the opposite by default: a local-remote scenario
must never reach the fallback at all.
"""

from __future__ import annotations

import typing as typ

from git import Repo
from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import plonk
from tests.integration.conftest import GitHubCliStub, _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    ReportMode,
    create_git_donkey_worktree,
    record_plonk_run,
    removal_entry,
    unverified_entry,
)
from tests.output_assertions import require_stream, stream

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

_BRANCH = "fix-markdown-gates"
_GITHUB_URL = "git@github.com:leynos/demo.git"
_PULL_REQUEST_NUMBER = 61


def _repository_with_github_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a worktree whose configured remote is GitHub but fetched locally.

    The raw configuration keeps the GitHub URL, which is what the fallback
    parses. The ``insteadOf`` rewrite keeps every actual Git operation on the
    temporary bare remote, so the scenario never touches the network.

    Returns
    -------
    PlonkScenario
        The repository and the descriptive branch under test.

    """
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, _BRANCH)
    repo = Repo(local_path)
    repo.git.config("remote.origin.url", _GITHUB_URL)
    repo.git.config(f"url.{remote_path.as_posix()}.insteadOf", _GITHUB_URL)
    return PlonkScenario(local_path=local_path, completed_branch=_BRANCH)


@given(
    "a repository with a descriptive git donkey worktree on a GitHub remote",
    target_fixture="scenario",
)
def repository_with_github_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create one descriptive worktree behind a rewritten GitHub remote."""
    return _repository_with_github_remote(tmp_path, monkeypatch)


def _require_stub(github_cli_stub: GitHubCliStub | None) -> GitHubCliStub:
    """Return the autouse ``gh`` stub, failing if it was not installed."""
    if github_cli_stub is None:
        msg = "expected the plonk GitHub CLI stub fixture"
        raise AssertionError(msg)
    return github_cli_stub


@given("the gh stub reports a merged pull request for that branch")
def stub_reports_a_merged_pull_request(
    github_cli_stub: GitHubCliStub | None,
) -> None:
    """Configure the stub to answer with one matching merged pull request."""
    _require_stub(github_cli_stub).stub_merged_pull_request(
        number=_PULL_REQUEST_NUMBER,
        head_branch=_BRANCH,
        base_branch="main",
        owner="leynos",
    )


@given("the gh stub fails")
def stub_fails(github_cli_stub: GitHubCliStub | None) -> None:
    """Configure the stub to exit non-zero without output."""
    _require_stub(github_cli_stub).stub_failure()


@given("the gh stub reports a pull request targeting another base")
def stub_reports_another_base(github_cli_stub: GitHubCliStub | None) -> None:
    """Configure a merged answer that must be rejected for its base branch."""
    _require_stub(github_cli_stub).stub_merged_pull_request(
        number=_PULL_REQUEST_NUMBER,
        head_branch=_BRANCH,
        base_branch="release",
        owner="leynos",
    )


@when("I run git plonk in default mode expecting a GitHub lookup")
def run_plonk_expecting_github(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run default cleanup, recording that the fallback was expected."""
    record_plonk_run(
        scenario,
        capsys,
        ReportMode("default", expect_github_lookup=True),
        plonk.run_git_plonk,
    )


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


@then("the GitHub lookup was attempted once")
def github_lookup_was_attempted_once(
    github_cli_stub: GitHubCliStub | None,
) -> None:
    """Assert exactly one bounded merged-pull-request query reached gh."""
    calls = _require_stub(github_cli_stub).calls()
    assert len(calls) == 1, "expected exactly one GitHub lookup"
    assert calls[0].startswith("pr list "), (
        "expected the lookup to be a read-only pull-request listing"
    )
    assert f"--head {_BRANCH}" in calls[0], (
        "expected the lookup to name the branch under test"
    )
    assert "--state merged" in calls[0], (
        "expected the lookup to ask only about merged pull requests"
    )


scenarios("features/git_plonk_github.feature")
