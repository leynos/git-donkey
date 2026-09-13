"""Behaviour-driven integration tests for git donkey base selection.

The module binds scenarios from ``features/git_donkey_bases.feature`` to real
temporary repositories built by ``tests.integration.donkey_helpers``. The steps
cover which commit a new worktree is started from — the principal remote's
advertised default, an explicit branch, or the calling checkout — along with
the fetch that always precedes it, the flags that opt in to updating a behind
base, and the prompt that guards such an update.

Branch reuse, target-path conflicts, and template overlays are covered by
``test_git_donkey_reuse_bdd.py``.
"""

from __future__ import annotations

import dataclasses
import io
import shutil
import sys
import typing as typ

import pytest
from git import Repo
from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import cli, donkey
from tests.integration.conftest import _seed_repo
from tests.integration.donkey_helpers import (
    DonkeyScenario,
    leave_base_behind_remote,
    new_scenario,
    run_donkey,
    run_donkey_without_pulling,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from vcr.cassette import Cassette

# Cyclopts and the workflow both report a rejected flag combination this way.
_USAGE_ERROR_EXIT_CODE = 2

# The two branches the pull-parity scenario creates, one per invocation.
_NO_PULL_BRANCH = "feature/explicit-no-pull"
_DEFAULT_BRANCH = "feature/default-no-pull"

_UNSTAGED_CONTENT = "unstaged edit"
_UNTRACKED_CONTENT = "work in progress"


@dataclasses.dataclass(frozen=True, slots=True)
class _CliOutcome:
    """Exit code and stderr captured from one git donkey CLI invocation."""

    code: int | str | None
    stderr: str


def _reject_prompt(_question: str) -> bool:
    """Fail the test if a run that must not prompt asks to update a base."""
    pytest.fail("this run must not prompt to update the base checkout")


@given("a repository whose remote has been deleted", target_fixture="scenario")
def repository_without_its_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Create a repository whose bare remote directory no longer exists."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/x")
    shutil.rmtree(scenario.remote_path)
    return scenario


@given("a repository whose local base is behind its remote", target_fixture="scenario")
def repository_behind_its_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Create a repository whose local main trails the remote by one commit."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/behind")
    leave_base_behind_remote(scenario)
    return scenario


@given("a prompt that fails the test if it is asked")
def prompt_that_fails_the_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the confirmation prompt with one that fails the test."""
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)


@given("a non-interactive stdin")
def non_interactive_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace stdin with a stream that is not a terminal."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())


@given("a directory that is not a Git repository")
def directory_that_is_not_a_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make an empty non-repository directory the working directory."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    monkeypatch.chdir(not_a_repo)


@given(
    "a repository with a committed change and uncommitted work in the calling checkout",
    target_fixture="scenario",
)
def repository_with_committed_and_uncommitted_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Commit one file, then leave an unstaged edit and an untracked file behind."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-caller")
    repo = scenario.repo
    _seed_repo(repo, "committed.txt", "committed work")
    scenario.local_tip = repo.head.commit.hexsha
    (scenario.local_path / "README.md").write_text(_UNSTAGED_CONTENT)
    (scenario.local_path / "scratch.txt").write_text(_UNTRACKED_CONTENT)
    return scenario


@given("a repository and a recorded GitHub API cassette", target_fixture="scenario")
def repository_with_github_api_cassette(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    github_api_cassette: Cassette,
) -> DonkeyScenario:
    """Create a repository while the recorded GitHub API cassette is replaying."""
    assert github_api_cassette.play_count == 0, (
        "expected the cassette to start unplayed"
    )
    return new_scenario(tmp_path, monkeypatch, "feature/no-api")


@given(
    "a repository whose first configured remote is named upstream",
    target_fixture="scenario",
)
def repository_with_renamed_principal_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Rename the seeded remote to upstream and add a later remote named origin."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/principal")
    repo = scenario.repo
    repo.git.remote("rename", "origin", "upstream")
    other_remote = Repo.init(tmp_path / "other.git", bare=True)
    repo.create_remote("origin", str(other_remote.git_dir))
    scenario.remote_tip = repo.commit("refs/remotes/upstream/main").hexsha
    return scenario


@when("I run git donkey with the current branch as the base")
def run_donkey_with_current_branch_base(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the workflow with an explicit dot base."""
    run_donkey(scenario, capsys, ".")


@when("I run git donkey with the remote default base")
def run_donkey_with_remote_default_base(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the workflow with an implicit base."""
    run_donkey(scenario, capsys)


@when("I run git donkey with --pull-ff and the current branch as the base")
def run_donkey_with_pull_ff(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the workflow with fast-forward-only base updating opted in."""
    run_donkey(scenario, capsys, ".", options=donkey._PullOptions(pull_ff=True))


@when("I run git donkey with --no-pull and again with no pull option")
def run_with_and_without_no_pull(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Create one worktree with --no-pull and one with no pull option at all."""
    scenario.branch = _NO_PULL_BRANCH
    run_donkey_without_pulling(scenario, capsys)
    assert scenario.exit_code == 0, "expected --no-pull worktree creation to succeed"

    scenario.branch = _DEFAULT_BRANCH
    run_donkey(scenario, capsys)
    assert scenario.exit_code == 0, "expected default worktree creation to succeed"


@when(
    parsers.parse("I run the git donkey CLI with {flags}"),
    target_fixture="cli_outcome",
)
def run_donkey_cli_with_flags(
    flags: str,
    capsys: pytest.CaptureFixture[str],
) -> _CliOutcome:
    """Run the CLI boundary with the supplied pull-mode flags."""
    with pytest.raises(SystemExit) as excinfo:
        cli._donkey_app(["feature/x", *flags.split()])
    return _CliOutcome(code=excinfo.value.code, stderr=capsys.readouterr().err)


@then("git donkey succeeds")
def git_donkey_succeeds(scenario: DonkeyScenario) -> None:
    """Assert the workflow reported success."""
    assert scenario.exit_code == 0, "expected git donkey to succeed"


@then(parsers.parse('git donkey fails with code {code:d} and reports "{message}"'))
def git_donkey_fails_reporting(
    scenario: DonkeyScenario,
    code: int,
    message: str,
) -> None:
    """Assert the workflow exited with ``code`` and explained itself on stderr."""
    assert scenario.exit_code == code, f"expected git donkey to exit with {code}"
    assert message in scenario.stderr, f"expected stderr to report {message!r}"


@then(parsers.parse('git donkey reports "{message}"'))
def git_donkey_reports(scenario: DonkeyScenario, message: str) -> None:
    """Assert the workflow wrote ``message`` to stderr."""
    assert message in scenario.stderr, f"expected stderr to report {message!r}"


@then("no branch or worktree is created")
def no_branch_or_worktree_is_created(scenario: DonkeyScenario) -> None:
    """Assert the failed run left neither a branch nor a worktree behind."""
    assert scenario.branch not in scenario.repo.heads, (
        "expected no branch to be created by a failed run"
    )
    assert not scenario.worktree_path().exists(), (
        "expected no worktree to be created by a failed run"
    )


@then("both worktrees start at the remote tip")
def both_worktrees_start_at_the_remote_tip(scenario: DonkeyScenario) -> None:
    """Assert the explicit and implicit no-pull runs chose the same start point."""
    for branch_name in (_NO_PULL_BRANCH, _DEFAULT_BRANCH):
        assert scenario.worktree_head(branch_name) == scenario.remote_tip, (
            f"expected {branch_name} to start at the remote tip"
        )


@then("the local base is left at its behind tip")
def local_base_is_left_behind(scenario: DonkeyScenario) -> None:
    """Assert the skipped prompt left the local base where it was."""
    assert scenario.repo.head.commit.hexsha == scenario.local_tip, (
        "expected the skipped update to leave the local base untouched"
    )


@then("the new worktree starts at the behind tip")
def new_worktree_starts_at_the_behind_tip(scenario: DonkeyScenario) -> None:
    """Assert the new worktree was started from the un-updated base."""
    assert scenario.worktree_head() == scenario.local_tip, (
        "expected the new worktree to start at the un-updated base"
    )


@then("the new worktree starts at the calling checkout commit")
def new_worktree_starts_at_the_calling_commit(scenario: DonkeyScenario) -> None:
    """Assert the dot base supplied the calling checkout's commit."""
    assert scenario.worktree_head() == scenario.local_tip, (
        "expected the calling checkout's commit to supply the start point"
    )


@then("the new worktree starts at the principal remote default tip")
def new_worktree_starts_at_the_principal_remote_tip(scenario: DonkeyScenario) -> None:
    """Assert the first configured remote's default branch supplied the base."""
    assert scenario.worktree_head() == scenario.remote_tip, (
        "expected the first configured remote's default branch to supply the base"
    )


@then("the new worktree holds the committed content")
def new_worktree_holds_the_committed_content(scenario: DonkeyScenario) -> None:
    """Assert the worktree holds the committed files, not the caller's edits."""
    worktree_path = scenario.worktree_path()
    committed_readme = scenario.repo.git.show(f"{scenario.local_tip}:README.md")
    assert (worktree_path / "README.md").read_text() == committed_readme, (
        "expected the worktree to hold the committed README content"
    )
    assert (worktree_path / "committed.txt").read_text() == "committed work", (
        "expected the caller's committed file to reach the worktree"
    )


@then("the untracked file is absent from the new worktree")
def untracked_file_is_absent_from_the_new_worktree(scenario: DonkeyScenario) -> None:
    """Assert the caller's untracked file did not reach the worktree."""
    assert not (scenario.worktree_path() / "scratch.txt").exists(), (
        "expected the untracked file to stay in the calling checkout"
    )


@then("the calling checkout keeps its uncommitted work")
def calling_checkout_keeps_its_uncommitted_work(scenario: DonkeyScenario) -> None:
    """Assert the unstaged edit and untracked file survived the run."""
    assert (scenario.local_path / "README.md").read_text() == _UNSTAGED_CONTENT, (
        "expected the unstaged edit to survive"
    )
    assert (scenario.local_path / "scratch.txt").read_text() == _UNTRACKED_CONTENT, (
        "expected the untracked file to survive"
    )


@then("no GitHub API request is made")
def no_github_api_request_is_made(github_api_cassette: Cassette) -> None:
    """Assert the workflow consulted Git alone, never the GitHub API."""
    assert len(github_api_cassette.requests) == 0, (
        "expected no GitHub API request to be recorded"
    )
    assert github_api_cassette.play_count == 0, (
        "expected no recorded GitHub API interaction to be replayed"
    )


@then("the CLI reports a usage error about mutually exclusive options")
def cli_reports_a_mutually_exclusive_usage_error(cli_outcome: _CliOutcome) -> None:
    """Assert conflicting pull modes fail as a usage error before any Git access."""
    assert cli_outcome.code == _USAGE_ERROR_EXIT_CODE, (
        "expected conflicting pull modes to exit with a usage error"
    )
    assert "mutually exclusive" in cli_outcome.stderr, (
        "expected the error to name the mutually exclusive options"
    )


scenarios("features/git_donkey_bases.feature")
