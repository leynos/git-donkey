"""Behaviour-driven tests for the worktrees a ``git plonk`` sweep reaches.

Where ``test_git_plonk_bdd.py`` covers what each cleanup mode does to a
worktree it has selected, this module binds the scenarios in
``features/git_plonk_scope.feature``, which pin *which* worktrees a sweep may
touch at all. Scope is decided by path, not by provenance: a worktree under the
``<repo>.worktrees`` root qualifies even when a human created it with plain
``git worktree add``, and a worktree outside that root is invisible to the
sweep even when its branch carries a completed marker. The remaining scenarios
pin the edges of soft cleanup: it reaches the worktree it was invoked from,
removes generated content Git is tracking, and unlinks a symlinked generated
directory rather than following it.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo
from pytest_bdd import given, scenarios, then, when

from git_donkey import plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    PlonkScenario,
    commit_completion_marker,
    create_git_donkey_worktree,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


@dataclasses.dataclass(frozen=True, slots=True)
class ScopeScenario(PlonkScenario):
    """Scenario state plus the paths scope assertions need to reach.

    The extra fields are optional because no single scenario populates them
    all: a scope scenario either registers a worktree outside the git-donkey
    root, or seeds generated content inside one, never both.
    """

    remote_path: Path | None = None
    outside_path: Path | None = None
    generated_path: Path | None = None
    symlink_destination: Path | None = None


def _worktree_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    branch_name: str,
) -> ScopeScenario:
    """Create a repository holding one git-donkey worktree for ``branch_name``."""
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, branch_name)
    return ScopeScenario(
        local_path=local_path,
        completed_branch=branch_name,
        remote_path=remote_path,
    )


def _add_plain_worktree(local_path: Path, branch_name: str, target_path: Path) -> None:
    """Register ``target_path`` as a worktree on a new ``branch_name``.

    The worktree is created with plain Git rather than ``git donkey`` so the
    scenario proves scope is decided by the worktree's path alone.
    """
    Repo(local_path).git.worktree(
        "add",
        target_path.as_posix(),
        "-b",
        branch_name,
        "main",
    )


@given(
    "a repository with a completed worktree outside the git donkey root",
    target_fixture="scenario",
)
def repository_with_worktree_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ScopeScenario:
    """Register a completed worktree outside the git-donkey worktree root."""
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    branch_name = "issue-123-outside"
    outside_path = tmp_path / "elsewhere"

    _add_plain_worktree(local_path, branch_name, outside_path)
    commit_completion_marker(local_path, "(#123)")

    return ScopeScenario(
        local_path=local_path,
        completed_branch=branch_name,
        remote_path=remote_path,
        outside_path=outside_path,
    )


@given(
    "a repository with a manually created completed worktree under the git donkey root",
    target_fixture="scenario",
)
def repository_with_manual_worktree_under_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ScopeScenario:
    """Register a completed worktree under the root without using git donkey."""
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    branch_name = "issue-124-manual"
    scenario = ScopeScenario(
        local_path=local_path,
        completed_branch=branch_name,
        remote_path=remote_path,
    )

    scenario.worktree_root.mkdir()
    _add_plain_worktree(local_path, branch_name, scenario.worktree_path(branch_name))
    commit_completion_marker(local_path, "(#124)")

    return scenario


@given(
    "a repository with a completed git donkey worktree pushed to the remote",
    target_fixture="scenario",
)
def repository_with_pushed_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ScopeScenario:
    """Create a completed worktree whose branch also exists on the remote."""
    branch_name = "issue-125-pushed"
    scenario = _worktree_scenario(tmp_path, monkeypatch, branch_name)

    worktree_repo = Repo(scenario.worktree_path(branch_name))
    worktree_repo.remote("origin").push(branch_name)
    commit_completion_marker(scenario.local_path, "(#125)")

    return scenario


@given(
    "a repository with a generated directory in the invoking git donkey worktree",
    target_fixture="scenario",
)
def repository_with_generated_directory_in_invoking_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ScopeScenario:
    """Seed a generated directory and run from inside that worktree."""
    scenario = _worktree_scenario(tmp_path, monkeypatch, "issue-126-invoking")
    worktree_path = scenario.worktree_path(scenario.completed_branch)
    generated_path = worktree_path / "target"

    generated_path.mkdir()
    (generated_path / "artifact.bin").write_bytes(b"artifact")
    monkeypatch.chdir(worktree_path)

    return dataclasses.replace(scenario, generated_path=generated_path)


@given(
    "a repository with tracked content under a generated directory name",
    target_fixture="scenario",
)
def repository_with_tracked_generated_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ScopeScenario:
    """Commit a file under a generated directory name inside a worktree."""
    scenario = _worktree_scenario(tmp_path, monkeypatch, "issue-127-tracked-dist")
    generated_path = scenario.worktree_path(scenario.completed_branch) / "dist"

    generated_path.mkdir()
    tracked_path = generated_path / "keep.txt"
    tracked_path.write_text("committed build output")
    worktree_repo = Repo(generated_path.parent)
    worktree_repo.index.add([tracked_path.as_posix()])
    worktree_repo.index.commit("Commit build output under a generated name")

    return dataclasses.replace(scenario, generated_path=generated_path)


@given(
    "a repository with a symlinked generated directory",
    target_fixture="scenario",
)
def repository_with_symlinked_generated_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ScopeScenario:
    """Point a generated directory name at a shared directory outside the worktree."""
    scenario = _worktree_scenario(tmp_path, monkeypatch, "issue-128-symlinked")
    destination = tmp_path / "shared-node-modules"

    destination.mkdir()
    (destination / "package.txt").write_text("shared dependency")
    generated_path = scenario.worktree_path(scenario.completed_branch) / "node_modules"
    generated_path.symlink_to(destination, target_is_directory=True)

    return dataclasses.replace(
        scenario,
        generated_path=generated_path,
        symlink_destination=destination,
    )


@when("I run git plonk in default mode", target_fixture="plonk_output")
def run_default_plonk(
    scenario: ScopeScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run the default cleanup mode and capture its report."""
    exit_code = plonk.run_git_plonk()
    assert exit_code == 0, "expected default git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in hard mode", target_fixture="plonk_output")
def run_hard_plonk(
    scenario: ScopeScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run the hard cleanup mode and capture its report."""
    exit_code = plonk.run_git_plonk(hard=True)
    assert exit_code == 0, "expected hard git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in soft mode")
def run_soft_plonk(scenario: ScopeScenario) -> None:
    """Run the soft cleanup mode."""
    exit_code = plonk.run_git_plonk(soft=True)
    assert exit_code == 0, "expected soft git plonk to succeed"


@then("the outside worktree remains")
def outside_worktree_remains(scenario: ScopeScenario) -> None:
    """Assert a worktree outside the git-donkey root survives the sweep."""
    assert scenario.outside_path is not None, "expected an outside path in scenario"
    assert scenario.outside_path.is_dir(), (
        "expected a worktree outside the git donkey root to remain"
    )


@then("the outside branch remains")
def outside_branch_remains(scenario: ScopeScenario) -> None:
    """Assert the branch of an out-of-scope worktree is never deleted."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads, (
        "expected the branch of an out-of-scope worktree to remain"
    )


@then("git plonk reports no matching worktrees")
def git_plonk_reports_no_matching_worktrees(plonk_output: str) -> None:
    """Assert the sweep found nothing in scope and said so."""
    assert "No matching git donkey worktrees found." in plonk_output, (
        "expected git plonk to report an empty sweep"
    )


@then("the completed worktree is removed")
def completed_worktree_is_removed(scenario: ScopeScenario) -> None:
    """Assert the completed worktree path no longer exists."""
    assert not scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected completed worktree to be removed"
    )


@then("the completed branch is deleted")
def completed_branch_is_deleted(scenario: ScopeScenario) -> None:
    """Assert hard mode deletes the completed local branch."""
    assert scenario.completed_branch not in Repo(scenario.local_path).heads, (
        "expected hard mode to delete the completed local branch"
    )


@then("the remote branch remains")
def remote_branch_remains(scenario: ScopeScenario) -> None:
    """Assert hard mode leaves the branch on the remote alone."""
    assert scenario.remote_path is not None, "expected a remote path in scenario"
    assert scenario.completed_branch in Repo(scenario.remote_path).heads, (
        "expected hard mode to leave the remote branch in place"
    )


@then("the remote-tracking branch remains")
def remote_tracking_branch_remains(scenario: ScopeScenario) -> None:
    """Assert hard mode leaves the local remote-tracking ref in place."""
    tracking_names = [
        ref.name for ref in Repo(scenario.local_path).remote("origin").refs
    ]
    assert f"origin/{scenario.completed_branch}" in tracking_names, (
        "expected hard mode to leave the remote-tracking branch in place"
    )


@then("the generated directory is removed")
def generated_directory_is_removed(scenario: ScopeScenario) -> None:
    """Assert soft mode removed the seeded generated directory."""
    assert scenario.generated_path is not None, "expected a generated path in scenario"
    assert not scenario.generated_path.exists(), (
        "expected soft mode to remove the generated directory"
    )
    assert not scenario.generated_path.is_symlink(), (
        "expected soft mode to leave no dangling generated symlink"
    )


@then("the symlink destination remains")
def symlink_destination_remains(scenario: ScopeScenario) -> None:
    """Assert soft mode unlinked the symlink without following it."""
    assert scenario.symlink_destination is not None, (
        "expected a symlink destination in scenario"
    )
    assert scenario.symlink_destination.is_dir(), (
        "expected the symlink destination to survive soft cleanup"
    )
    assert (scenario.symlink_destination / "package.txt").is_file(), (
        "expected soft cleanup to leave the shared dependency untouched"
    )


@then("the worktree remains")
def worktree_remains(scenario: ScopeScenario) -> None:
    """Assert soft mode leaves the worktree itself in place."""
    assert scenario.worktree_path(scenario.completed_branch).is_dir(), (
        "expected soft mode to keep the worktree"
    )


@then("the branch remains")
def branch_remains(scenario: ScopeScenario) -> None:
    """Assert soft mode leaves the local branch in place."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads, (
        "expected soft mode to keep the branch"
    )


@then("the worktree has uncommitted deletions")
def worktree_has_uncommitted_deletions(scenario: ScopeScenario) -> None:
    """Assert removing tracked generated content left the worktree dirty."""
    worktree_repo = Repo(scenario.worktree_path(scenario.completed_branch))
    assert worktree_repo.is_dirty(), (
        "expected the removal of tracked generated content to leave the worktree dirty"
    )


scenarios("features/git_plonk_scope.feature")
