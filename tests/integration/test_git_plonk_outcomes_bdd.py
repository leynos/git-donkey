"""Behaviour-driven tests for what a ``git plonk`` sweep reports and returns.

The scenarios in ``features/git_plonk_outcomes.feature`` pin the command's
reporting contract rather than its filesystem effects: a candidate whose
directory has vanished is named as skipped rather than treated as an error, a
dry run previews only the candidates it would really take and still names the
ones it would leave, a branch Git refuses to delete gets its own section and
fails the run, and a sweep that skips every candidate reports the skips instead
of claiming it found nothing.

Every failure here is provoked with real Git. The refused branch deletion drops
write permission from the main repository's loose-ref directory, which is why
that scenario is bound explicitly and skipped under a root effective UID.
"""

from __future__ import annotations

import os
import shutil
import typing as typ

import pytest
from git import Repo
from pytest_bdd import given, scenario, scenarios, then, when

from git_donkey import plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    commit_completion_marker,
    create_git_donkey_worktree,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

_UNTRACKED_CONTENT = "work in progress"


@pytest.fixture
def lock_refs_directory() -> cabc.Iterator[cabc.Callable[[Path], None]]:
    """Yield a helper that makes a loose-ref directory undeletable.

    Git deletes a branch by creating a ``.lock`` file beside the branch's loose
    ref, so withdrawing write permission from the directory holding the ref is
    enough to make a real ``git branch -D`` fail without substituting a double
    for Git. The mode is restored on teardown so ``tmp_path`` cleanup succeeds.

    Yields
    ------
    cabc.Callable[[Path], None]
        Callable that locks the loose-ref directory it is given.

    """
    locked_dirs: list[Path] = []

    def lock(refs_dir: Path) -> None:
        """Withdraw write permission from ``refs_dir``."""
        refs_dir.chmod(0o555)
        locked_dirs.append(refs_dir)

    yield lock

    for refs_dir in locked_dirs:
        refs_dir.chmod(0o755)


def _completed_worktree_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    branch_name: str,
    marker: str,
) -> PlonkScenario:
    """Create a repository with one completed git-donkey worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, branch_name)
    commit_completion_marker(local_path, marker)
    return PlonkScenario(local_path=local_path, completed_branch=branch_name)


def _leave_untracked_file(scenario: PlonkScenario, branch_name: str) -> None:
    """Leave an untracked file in ``branch_name``'s worktree."""
    untracked = scenario.worktree_path(branch_name) / "scratch.txt"
    untracked.write_text(_UNTRACKED_CONTENT)


def _two_completed_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create two completed git-donkey worktrees whose markers are both on trunk."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    first_branch = "issue-131-first-completed"
    second_branch = "issue-132-second-completed"

    create_git_donkey_worktree(local_path, first_branch)
    create_git_donkey_worktree(local_path, second_branch)
    commit_completion_marker(local_path, "(#131)")
    commit_completion_marker(local_path, "(#132)")

    return PlonkScenario(
        local_path=local_path,
        completed_branch=first_branch,
        dirty_branch=second_branch,
    )


def _assert_skipped_entry(
    worktree_path: Path,
    reason: str,
    plonk_output: str,
) -> None:
    """Assert ``plonk_output`` reports ``worktree_path`` as skipped for ``reason``."""
    assert "Skipped worktrees:" in plonk_output, (
        "expected a skipped worktree section in the report"
    )
    assert f"- {worktree_path} ({reason})" in plonk_output, (
        f"expected {worktree_path} to be reported as skipped for {reason}"
    )


@given(
    "a repository with a completed git donkey worktree whose directory is missing",
    target_fixture="scenario",
)
def repository_with_missing_worktree_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Delete a completed worktree's directory, leaving its registration behind."""
    scenario = _completed_worktree_scenario(
        tmp_path,
        monkeypatch,
        "issue-129-missing-directory",
        "(#129)",
    )
    shutil.rmtree(scenario.worktree_path(scenario.completed_branch))
    return scenario


@given(
    "a repository with one clean and one dirty completed git donkey worktree",
    target_fixture="scenario",
)
def repository_with_clean_and_dirty_completed_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create one removable completed worktree beside one holding untracked work."""
    scenario = _two_completed_worktrees(tmp_path, monkeypatch)
    assert scenario.dirty_branch is not None, "expected a dirty branch in scenario"
    _leave_untracked_file(scenario, scenario.dirty_branch)
    return scenario


@given(
    "a repository with two dirty completed git donkey worktrees",
    target_fixture="scenario",
)
def repository_with_two_dirty_completed_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create two completed worktrees that both hold untracked work."""
    scenario = _two_completed_worktrees(tmp_path, monkeypatch)
    assert scenario.dirty_branch is not None, "expected a dirty branch in scenario"
    _leave_untracked_file(scenario, scenario.completed_branch)
    _leave_untracked_file(scenario, scenario.dirty_branch)
    return scenario


@given(
    "a repository with a completed git donkey worktree whose branch cannot be deleted",
    target_fixture="scenario",
)
def repository_with_undeletable_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lock_refs_directory: cabc.Callable[[Path], None],
) -> PlonkScenario:
    """Create a completed worktree whose loose ref Git will not be able to unlink."""
    scenario = _completed_worktree_scenario(
        tmp_path,
        monkeypatch,
        "issue-130-undeletable-branch",
        "(#130)",
    )
    refs_heads_dir = scenario.local_path / ".git" / "refs" / "heads"
    assert (refs_heads_dir / scenario.completed_branch).is_file(), (
        "expected the completed branch to have a loose ref to lock"
    )
    lock_refs_directory(refs_heads_dir)
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


@when("I run git plonk in default dry-run mode", target_fixture="plonk_output")
def run_default_dry_run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run default cleanup in dry-run mode and capture its report."""
    exit_code = plonk.run_git_plonk(dry_run=True)
    assert exit_code == 0, "expected default dry-run git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in hard dry-run mode", target_fixture="plonk_output")
def run_hard_dry_run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run hard cleanup in dry-run mode and capture its report."""
    exit_code = plonk.run_git_plonk(hard=True, dry_run=True)
    assert exit_code == 0, "expected hard dry-run git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in hard mode expecting failure", target_fixture="plonk_output")
def run_failing_hard_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run hard cleanup where Git will refuse the branch deletion."""
    exit_code = plonk.run_git_plonk(hard=True)
    assert exit_code == 1, "expected a refused branch deletion to fail the run"
    return capsys.readouterr().out


@then("git plonk reports the completed worktree as missing")
def git_plonk_reports_completed_worktree_missing(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert a vanished worktree directory is reported as its own skip reason."""
    _assert_skipped_entry(
        scenario.worktree_path(scenario.completed_branch),
        "worktree directory is missing",
        plonk_output,
    )


@then("git plonk reports the dirty worktree as skipped")
def git_plonk_reports_dirty_worktree_skipped(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert a preview still names the candidate it would leave behind."""
    assert scenario.dirty_branch is not None, "expected a dirty branch in scenario"
    _assert_skipped_entry(
        scenario.worktree_path(scenario.dirty_branch),
        "uncommitted changes",
        plonk_output,
    )


@then("git plonk reports both worktrees as skipped")
def git_plonk_reports_both_worktrees_skipped(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert a sweep that skipped every candidate names all of them."""
    assert scenario.dirty_branch is not None, "expected a dirty branch in scenario"
    for branch_name in (scenario.completed_branch, scenario.dirty_branch):
        _assert_skipped_entry(
            scenario.worktree_path(branch_name),
            "uncommitted changes",
            plonk_output,
        )


@then("git plonk does not report an empty sweep")
def git_plonk_does_not_report_an_empty_sweep(plonk_output: str) -> None:
    """Assert skipping every candidate is not reported as finding nothing."""
    assert "No matching git donkey worktrees found." not in plonk_output, (
        "expected skipped candidates to be reported instead of an empty sweep"
    )


@then("git plonk previews removing the clean worktree")
def git_plonk_previews_removing_clean_worktree(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert the dry run plans to remove only the clean completed worktree."""
    assert "Planned worktree removals:" in plonk_output, (
        "expected a planned worktree removal section"
    )
    assert f"- {scenario.worktree_path(scenario.completed_branch)}" in plonk_output, (
        "expected the clean worktree to be listed as a planned removal"
    )


@then("git plonk previews deleting only the clean branch")
def git_plonk_previews_deleting_only_clean_branch(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert the hard dry run plans to delete the clean branch alone."""
    assert scenario.dirty_branch is not None, "expected a dirty branch in scenario"
    assert "Planned branch deletions:" in plonk_output, (
        "expected a planned branch deletion section"
    )
    assert f"- {scenario.completed_branch}\n" in f"{plonk_output}\n", (
        "expected the clean branch to be listed as a planned deletion"
    )
    assert f"- {scenario.dirty_branch}\n" not in f"{plonk_output}\n", (
        "expected the skipped worktree's branch to stay out of the deletion plan"
    )


@then("git plonk reports the failed branch deletion")
def git_plonk_reports_failed_branch_deletion(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert a refused branch deletion gets its own section and no other."""
    assert "Failed branch deletions:" in plonk_output, (
        "expected a failed branch deletion section"
    )
    assert f"- {scenario.completed_branch} (branch deletion failed)" in plonk_output, (
        "expected the refused branch to be named as a failed deletion"
    )
    assert "Removed branches:" not in plonk_output, (
        "expected a refused branch deletion not to be reported as removed"
    )
    assert "Skipped worktrees:" not in plonk_output, (
        "expected a refused branch deletion not to be reported as a skip"
    )


@then("the completed worktree is removed")
def completed_worktree_is_removed(scenario: PlonkScenario) -> None:
    """Assert the completed worktree path no longer exists."""
    assert not scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected the completed worktree to be removed"
    )


@then("the completed branch remains")
def completed_branch_remains(scenario: PlonkScenario) -> None:
    """Assert the completed local branch survived the sweep."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads, (
        "expected the completed branch to remain"
    )


@then("both completed worktrees remain")
def both_completed_worktrees_remain(scenario: PlonkScenario) -> None:
    """Assert a dry run removed neither completed worktree."""
    assert scenario.dirty_branch is not None, "expected a dirty branch in scenario"
    for branch_name in (scenario.completed_branch, scenario.dirty_branch):
        assert scenario.worktree_path(branch_name).is_dir(), (
            f"expected the dry run to keep the worktree for {branch_name}"
        )


@then("both completed branches remain")
def both_completed_branches_remain(scenario: PlonkScenario) -> None:
    """Assert a dry run deleted neither completed branch."""
    assert scenario.dirty_branch is not None, "expected a dirty branch in scenario"
    heads = Repo(scenario.local_path).heads
    for branch_name in (scenario.completed_branch, scenario.dirty_branch):
        assert branch_name in heads, (
            f"expected the dry run to keep the branch {branch_name}"
        )


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="a root effective UID ignores the read-only loose-ref directory",
)
@scenario(
    "features/git_plonk_outcomes.feature",
    "A refused branch deletion is reported and fails the run",
)
def test_refused_branch_deletion_is_reported() -> None:
    """Bind the refused-deletion scenario so it can be skipped when running as root."""


scenarios("features/git_plonk_outcomes.feature")
