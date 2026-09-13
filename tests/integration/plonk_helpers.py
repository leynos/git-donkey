"""Repository builders shared by the ``git plonk`` integration suites.

The BDD scenarios bound in the ``test_git_plonk_*_bdd.py`` modules and the
direct regression tests in ``test_git_plonk_trunk_history.py`` compose the same
temporary repository: a ``git donkey`` worktree per issue branch, completion
markers committed on trunk, and the dirt that decides whether a sweep may remove
a worktree. Keeping that vocabulary in one place means a change to how a
scenario is seeded cannot leave the suites asserting against differently built
repositories.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo

from git_donkey import donkey
from tests.git_repo_helpers import configure_repo

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


def commit_message_on_branch(
    local_path: Path,
    branch_name: str,
    message: str,
) -> None:
    """Commit ``message`` on ``branch_name`` and push it to ``origin``.

    The commit body is written verbatim, so a scenario can place a completion
    marker anywhere in it rather than only in the subject line.

    Parameters
    ----------
    local_path : Path
        Working repository to commit and push the message in.
    branch_name : str
        Existing local branch to commit on.
    message : str
        Full commit message, including any completion marker the trunk history
        must carry.

    """
    repo = Repo(local_path)
    repo.git.checkout(branch_name)
    # Name the file after the branch's commit count so repeated calls on one
    # branch never collide, and each commit changes the tree.
    sequence = sum(1 for _ in repo.iter_commits(branch_name))
    commit_path = local_path / f"trunk-commit-{sequence}.txt"
    commit_path.write_text(message)
    repo.index.add([commit_path.as_posix()])
    repo.index.commit(message)
    repo.remote("origin").push(branch_name)


def advertise_remote_default(
    local_path: Path,
    remote_path: Path,
    branch_name: str,
) -> None:
    """Advertise ``branch_name`` as the bare remote's default branch.

    The branch is created from ``main`` on both the remote and the local
    repository, so a scenario can commit trunk history on it while ``main``
    stays behind. The bare remote's symbolic ``HEAD`` is the advertisement both
    ``git donkey`` and ``git plonk`` resolve trunk through.

    Parameters
    ----------
    local_path : Path
        Working repository holding ``main`` and the ``origin`` remote.
    remote_path : Path
        Bare remote whose symbolic ``HEAD`` is repointed.
    branch_name : str
        Branch to create and advertise.

    """
    repo = Repo(local_path)
    repo.git.push("origin", f"main:refs/heads/{branch_name}")
    repo.git.branch(branch_name, "main")
    Repo(remote_path).git.symbolic_ref("HEAD", f"refs/heads/{branch_name}")


def stop_advertising_default_branch(remote_path: Path) -> None:
    """Point the bare remote's ``HEAD`` at a branch that does not exist.

    ``git ls-remote --symref`` then reports no ``ref:`` line at all, which is
    how a remote that advertises no default branch looks to the commands.

    Parameters
    ----------
    remote_path : Path
        Bare remote whose symbolic ``HEAD`` is repointed.

    """
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/missing")


def push_marker_from_clone(
    remote_path: Path,
    clone_path: Path,
    marker: str,
) -> None:
    """Push a completion marker to the bare remote from a separate clone.

    The marker reaches the remote without passing through the repository under
    test, so that repository only learns about it if the command fetches trunk
    itself.

    Parameters
    ----------
    remote_path : Path
        Bare remote to clone and push to.
    clone_path : Path
        Directory to create the second clone in.
    marker : str
        Completion marker the trunk history must carry, such as ``(#123)``.

    """
    clone = Repo.clone_from(remote_path.as_posix(), clone_path.as_posix())
    configure_repo(clone)
    branch_name = clone.active_branch.name
    marker_path = clone_path / "completion-from-clone.txt"
    marker_path.write_text(marker)
    clone.index.add([marker_path.as_posix()])
    clone.index.commit(f"Complete work {marker}")
    clone.remote("origin").push(branch_name)


def commit_in_worktree(
    scenario: PlonkScenario,
    branch_name: str,
    message: str,
) -> None:
    """Commit a new file inside ``branch_name``'s worktree, leaving it clean.

    The commit advances the branch beyond trunk without leaving uncommitted
    work behind, so the worktree is still one Git would discard unprompted.

    Parameters
    ----------
    scenario : PlonkScenario
        Scenario whose worktree receives the commit.
    branch_name : str
        Branch whose worktree to commit in.
    message : str
        Commit message to use, also written into the committed file.

    """
    worktree_path = scenario.worktree_path(branch_name)
    repo = Repo(worktree_path)
    commit_path = worktree_path / "worktree-work.txt"
    commit_path.write_text(message)
    repo.index.add([commit_path.as_posix()])
    repo.index.commit(message)
