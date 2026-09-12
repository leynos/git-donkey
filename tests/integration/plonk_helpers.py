"""Repository builders shared by the ``git plonk`` integration suites.

The BDD scenarios bound in ``test_git_plonk_bdd.py`` and the direct regression
tests in ``test_git_plonk_trunk_history.py`` compose the same temporary
repository: a ``git donkey`` worktree per issue branch, completion markers
committed on ``main``, and the dirt that decides whether a sweep may remove a
worktree. Keeping that vocabulary in one place means a change to how a scenario
is seeded cannot leave the two suites asserting against differently built
repositories.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo

from git_donkey import donkey

if typ.TYPE_CHECKING:
    from pathlib import Path

# The tracked seed file the dirt helpers edit, and the content each edit leaves
# behind. Both kinds of dirt are uncommitted work, so both must keep their
# worktree and its branch.
TRACKED_FILE = "README.md"
MODIFIED_CONTENT = "edited in the worktree"
STAGED_CONTENT = "staged in the worktree"


@dataclasses.dataclass(frozen=True, slots=True)
class PlonkScenario:
    """Repository state shared by BDD steps and direct regression tests."""

    local_path: Path
    completed_branch: str
    active_branch: str | None = None
    dirty_branch: str | None = None

    @property
    def worktree_root(self) -> Path:
        """The git-donkey worktree root for the scenario repository."""
        return self.local_path.parent / f"{self.local_path.name}.worktrees"

    def worktree_path(self, branch_name: str) -> Path:
        """Return the expected worktree path for ``branch_name``."""
        return self.worktree_root / branch_name


def commit_completion_marker(local_path: Path, marker: str) -> None:
    """Commit a completion marker on ``main`` without changing worktree content.

    Parameters
    ----------
    local_path : Path
        Working repository to commit and push the marker in.
    marker : str
        Completion marker the trunk history must carry, such as ``(#123)``.

    """
    repo = Repo(local_path)
    repo.git.checkout("main")
    marker_name = marker.removeprefix("(").removesuffix(")").replace("#", "issue-")
    marker_path = local_path / f"completion-{marker_name}.txt"
    marker_path.write_text(marker)
    repo.index.add([marker_path.as_posix()])
    repo.index.commit(f"Complete work {marker}")
    repo.remote("origin").push("main")


def commit_ignore_rule(local_path: Path, rule: str) -> None:
    """Commit ``rule`` to the repository's ``.gitignore`` on ``main``.

    The rule must be committed before a worktree is created, so the worktree
    inherits it and the ignored path is genuinely ignored in both checkouts.

    Parameters
    ----------
    local_path : Path
        Working repository to commit and push the rule in.
    rule : str
        Ignore rule to commit.

    """
    repo = Repo(local_path)
    repo.git.checkout("main")
    ignore_path = local_path / ".gitignore"
    ignore_path.write_text(f"{rule}\n")
    repo.index.add([ignore_path.as_posix()])
    repo.index.commit("Ignore generated build output")
    repo.remote("origin").push("main")


def create_git_donkey_worktree(local_path: Path, branch_name: str) -> None:
    """Create a git-donkey worktree in ``local_path`` for ``branch_name``.

    Parameters
    ----------
    local_path : Path
        Working repository to create the worktree in.
    branch_name : str
        Issue branch, and worktree directory name, to create.

    Raises
    ------
    AssertionError
        If ``git donkey`` fails to create the worktree.

    """
    repo = Repo(local_path)
    repo.git.checkout("main")
    exit_code = donkey.run_git_donkey(branch_name, no_pull=True)
    if exit_code != 0:
        msg = f"expected git donkey to create {branch_name}"
        raise AssertionError(msg)


def edit_tracked_file(
    scenario: PlonkScenario,
    branch_name: str,
    content: str,
) -> Path:
    """Write ``content`` over the tracked seed file in ``branch_name``'s worktree.

    Parameters
    ----------
    scenario : PlonkScenario
        Scenario whose worktree holds the file to edit.
    branch_name : str
        Branch whose worktree to edit.
    content : str
        Content to leave in the tracked file.

    Returns
    -------
    Path
        The edited file.

    """
    readme = scenario.worktree_path(branch_name) / TRACKED_FILE
    readme.write_text(content)
    return readme


def stage_tracked_change(
    scenario: PlonkScenario,
    branch_name: str,
    content: str,
) -> None:
    """Stage an edit to the tracked seed file in ``branch_name``'s worktree.

    Parameters
    ----------
    scenario : PlonkScenario
        Scenario whose worktree holds the file to stage.
    branch_name : str
        Branch whose worktree to stage the change in.
    content : str
        Content to leave in the staged file.

    """
    readme = edit_tracked_file(scenario, branch_name, content)
    Repo(readme.parent).index.add([readme.as_posix()])
