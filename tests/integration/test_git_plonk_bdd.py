"""Behaviour tests for the ``git plonk`` cleanup command.

These scenarios exercise the public `git_donkey.plonk.run_git_plonk` workflow
against real temporary Git repositories instead of stubs. They prove that the
plonk module honours its contract with `git donkey` worktrees: default mode
removes only completed worktrees, soft mode removes generated directories
without changing Git state, hard mode removes completed worktrees and local
branches, and cleanup still resolves completion markers from the main worktree
history when invoked from a linked topic worktree.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo
from pytest_bdd import given, scenarios, then, when

from git_donkey import donkey, plonk
from tests.integration.conftest import _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


@dataclasses.dataclass(frozen=True)
class PlonkScenario:
    """Repository state shared by BDD steps."""

    local_path: Path
    completed_branch: str
    active_branch: str | None = None

    @property
    def worktree_root(self) -> Path:
        """Return the git-donkey worktree root for the scenario repository."""
        return self.local_path.parent / f"{self.local_path.name}.worktrees"

    def worktree_path(self, branch_name: str) -> Path:
        """Return the expected worktree path for ``branch_name``."""
        return self.worktree_root / branch_name


def _commit_completion_marker(local_path: Path, marker: str) -> None:
    """Commit a completion marker on ``main`` without changing worktree content."""
    repo = Repo(local_path)
    repo.git.checkout("main")
    marker_name = marker.removeprefix("(").removesuffix(")").replace("#", "issue-")
    marker_path = local_path / f"completion-{marker_name}.txt"
    marker_path.write_text(marker)
    repo.index.add([marker_path.as_posix()])
    repo.index.commit(f"Complete work {marker}")


def _create_git_donkey_worktree(local_path: Path, branch_name: str) -> None:
    """Create a git-donkey worktree in ``local_path`` for ``branch_name``."""
    repo = Repo(local_path)
    repo.git.checkout("main")
    exit_code = donkey.run_git_donkey(branch_name, no_pull=True)
    assert exit_code == 0


@given(
    "a repository with completed and active git donkey worktrees",
    target_fixture="scenario",
)
def repository_with_completed_and_active_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create one completed issue worktree and one active issue worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-123-fix-closed-work"
    active_branch = "issue-456-active-work"

    _create_git_donkey_worktree(local_path, completed_branch)
    _create_git_donkey_worktree(local_path, active_branch)
    _commit_completion_marker(local_path, "(#123)")

    return PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
        active_branch=active_branch,
    )


@given(
    "a repository with generated directories inside git donkey worktrees",
    target_fixture="scenario",
)
def repository_with_generated_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create worktrees containing generated dependency and build directories."""
    scenario = repository_with_completed_and_active_worktrees(tmp_path, monkeypatch)
    assert scenario.active_branch is not None
    for branch_name in (scenario.completed_branch, scenario.active_branch):
        worktree_path = scenario.worktree_path(branch_name)
        for dirname in ("target", "node_modules"):
            generated_path = worktree_path / dirname
            generated_path.mkdir()
            (generated_path / "generated.txt").write_text("generated")
    return scenario


@given("a repository with a completed git donkey worktree", target_fixture="scenario")
def repository_with_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a single completed roadmap worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "road-1-2-3a-4-finished-task"

    _create_git_donkey_worktree(local_path, completed_branch)
    _commit_completion_marker(local_path, "(road.1.2.3a.4)")

    return PlonkScenario(local_path=local_path, completed_branch=completed_branch)


@when("I run git plonk in default mode")
def run_default_plonk(scenario: PlonkScenario) -> None:
    """Run the default cleanup mode."""
    exit_code = plonk.run_git_plonk()
    assert exit_code == 0


@when("I run git plonk in soft mode")
def run_soft_plonk(scenario: PlonkScenario) -> None:
    """Run the soft cleanup mode."""
    exit_code = plonk.run_git_plonk(soft=True)
    assert exit_code == 0


@when("I run git plonk in hard mode")
def run_hard_plonk(scenario: PlonkScenario) -> None:
    """Run the hard cleanup mode."""
    exit_code = plonk.run_git_plonk(hard=True)
    assert exit_code == 0


@then("the completed worktree is removed")
def completed_worktree_is_removed(scenario: PlonkScenario) -> None:
    """Assert the completed worktree path no longer exists."""
    assert not scenario.worktree_path(scenario.completed_branch).exists()


@then("the active worktree remains")
def active_worktree_remains(scenario: PlonkScenario) -> None:
    """Assert the active worktree path remains."""
    assert scenario.active_branch is not None
    assert scenario.worktree_path(scenario.active_branch).exists()


@then("the completed branch remains")
def completed_branch_remains(scenario: PlonkScenario) -> None:
    """Assert default mode leaves the completed local branch intact."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads


@then("the generated directories are removed")
def generated_directories_are_removed(scenario: PlonkScenario) -> None:
    """Assert generated directories are removed from every git-donkey worktree."""
    assert scenario.active_branch is not None
    for branch_name in (scenario.completed_branch, scenario.active_branch):
        worktree_path = scenario.worktree_path(branch_name)
        assert not (worktree_path / "target").exists()
        assert not (worktree_path / "node_modules").exists()


@then("the worktrees remain")
def worktrees_remain(scenario: PlonkScenario) -> None:
    """Assert soft mode leaves every linked worktree in place."""
    assert scenario.active_branch is not None
    for branch_name in (scenario.completed_branch, scenario.active_branch):
        assert scenario.worktree_path(branch_name).exists()


@then("the branches remain")
def branches_remain(scenario: PlonkScenario) -> None:
    """Assert soft mode leaves every local branch in place."""
    assert scenario.active_branch is not None
    heads = Repo(scenario.local_path).heads
    assert scenario.completed_branch in heads
    assert scenario.active_branch in heads


@then("the completed branch is deleted")
def completed_branch_is_deleted(scenario: PlonkScenario) -> None:
    """Assert hard mode deletes the completed local branch."""
    assert scenario.completed_branch not in Repo(scenario.local_path).heads


def test_git_plonk_uses_main_history_when_run_from_topic_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default mode should remove completed worktrees from a topic CWD."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-789-completed-from-main"
    topic_branch = "issue-790-active-topic"

    _create_git_donkey_worktree(local_path, completed_branch)
    _create_git_donkey_worktree(local_path, topic_branch)
    _commit_completion_marker(local_path, "(#789)")
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
        active_branch=topic_branch,
    )

    monkeypatch.chdir(scenario.worktree_path(topic_branch))
    exit_code = plonk.run_git_plonk()

    assert exit_code == 0
    assert not scenario.worktree_path(completed_branch).exists()
    assert scenario.worktree_path(topic_branch).exists()
    assert completed_branch in Repo(local_path).heads


scenarios("features/git_plonk.feature")
