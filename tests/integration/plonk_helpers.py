"""Repository builders shared by the ``git plonk`` integration suites.

The BDD scenarios bound in the ``test_git_plonk_*_bdd.py`` modules and the
direct regression tests in ``test_git_plonk_trunk_history.py`` compose the same
temporary repository: a ``git donkey`` worktree per issue branch, completion
markers committed on trunk, and the dirt that decides whether a sweep may remove
a worktree. Keeping that vocabulary in one place means a change to how a
scenario is seeded cannot leave the suites asserting against differently built
repositories.

The stack-record scenarios in ``test_git_plonk_stack_bdd.py`` need one thing
those builders do not provide: a branch stacked on another branch, which is what
``git donkey`` records a boundary for. They add the builders below, which grow a
parent ahead of the trunk so that a record has something to describe.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo

from git_donkey import donkey, stack_records, stack_store
from tests.git_repo_helpers import configure_repo
from tests.integration.conftest import _setup_repo
from tests.integration.donkey_helpers import seed_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

# The tracked seed file the dirt helpers edit, and the content each edit leaves
# behind. Both kinds of dirt are uncommitted work, so both must keep their
# worktree and its branch.
TRACKED_FILE = "README.md"
MODIFIED_CONTENT = "edited in the worktree"
STAGED_CONTENT = "staged in the worktree"

# The instant a stack-record scenario ages a tombstone to. Any date before the
# default retention window would do; this one is old enough that no window these
# suites configure can reach it, and explicit so the assertion cannot drift with
# the clock.
_TOMBSTONE_WRITTEN_ON = "2020-01-01T00:00:00 +0000"


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


@dataclasses.dataclass(frozen=True, slots=True)
class PlonkStackScenario(PlonkScenario):
    """Repository state shared by the stack-record cleanup scenarios.

    Attributes
    ----------
    parent : str | None
        Branch a stacked branch was cut from, when the scenario has one.
    tip : str | None
        Commit the completed branch named before the run. It is captured by the
        builder because the run is what deletes the branch naming it, and a
        tombstone can only be checked against a tip read while it still existed.
    orphan : str | None
        Branch whose record outlived it, when the scenario starts from a record
        rather than from a worktree.
    record : stack_records.RecordResult | None
        What the completed branch's record reconciled to before the run, so a
        scenario can assert that a run left it alone.

    """

    parent: str | None = None
    tip: str | None = None
    orphan: str | None = None
    record: stack_records.RecordResult | None = None

    @property
    def repo(self) -> Repo:
        """The primary working checkout of the scenario repository."""
        return Repo(self.local_path)

    def ref_commit(self, ref: str) -> str | None:
        """Return the commit ``ref`` names, or ``None`` when it does not exist."""
        value = self.repo.git.rev_parse(
            "--verify", "--quiet", ref, with_exceptions=False
        )
        return str(value).strip() or None

    def tombstone(self, branch_name: str) -> str | None:
        """Return the commit the tombstone for ``branch_name`` names, if any."""
        return self.ref_commit(stack_records.tombstone_ref_path(branch_name))

    def anchor(self, branch_name: str) -> str | None:
        """Return the commit the stack-base anchor for ``branch_name`` names."""
        return self.ref_commit(stack_records.base_ref_path(branch_name))


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


def _issue_marker(branch_name: str) -> str:
    """Return the completion marker trunk history carries for ``branch_name``."""
    number = branch_name.removeprefix("issue-").split("-", 1)[0]
    return f"(#{number})"


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


def create_stacked_git_donkey_worktree(
    local_path: Path, branch_name: str, base: str
) -> None:
    """Create a git-donkey worktree for ``branch_name`` stacked on ``base``.

    Naming a base other than the trunk is what makes the branch stacked: the
    boundary the worktree is cut at is recorded, because it is a commit the
    trunk does not already name (INV-11).

    Parameters
    ----------
    local_path : Path
        Working repository to create the worktree in.
    branch_name : str
        Issue branch, and worktree directory name, to create.
    base : str
        Branch to cut the new branch from.

    Raises
    ------
    AssertionError
        If ``git donkey`` fails to create the worktree.

    """
    repo = Repo(local_path)
    repo.git.checkout("main")
    exit_code = donkey.run_git_donkey(branch_name, base, no_pull=True)
    if exit_code != 0:
        msg = f"expected git donkey to create {branch_name} from {base}"
        raise AssertionError(msg)


def branch_ahead_of_trunk(local_path: Path, branch_name: str) -> str:
    """Create ``branch_name`` one commit ahead of ``main``; return its tip.

    The commit is what a scenario about a stacked branch turns on: a branch
    created at the trunk commit is not stacked, however it is named, so its
    tip differs from the trunk's only once it has moved.

    Parameters
    ----------
    local_path : Path
        Working repository to create the branch in.
    branch_name : str
        Branch to create off ``main`` and commit to.

    Returns
    -------
    str
        The commit the branch was left at.

    """
    repo = Repo(local_path)
    repo.git.checkout("main")
    repo.git.branch(branch_name, "main")
    repo.git.checkout(branch_name)
    seed_repo(repo, f"{branch_name}.txt", f"work on {branch_name}")
    tip = repo.head.commit.hexsha
    repo.git.checkout("main")
    return tip


def _captured(scenario: PlonkStackScenario, branch_name: str) -> PlonkStackScenario:
    """Return ``scenario`` holding ``branch_name``'s tip and record as it was.

    Parameters
    ----------
    scenario : PlonkStackScenario
        Scenario to fill in.
    branch_name : str
        Branch whose tip and record are read before the run.

    Returns
    -------
    PlonkStackScenario
        The scenario, with the tip and record the run must be judged against.

    """
    return dataclasses.replace(
        scenario,
        tip=scenario.repo.heads[branch_name].commit.hexsha,
        record=stack_store.GitStackRecordReader(scenario.repo).read(branch_name),
    )


def stacked_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    parent: str,
    branch: str,
) -> PlonkStackScenario:
    """Create a completed worktree for a branch stacked on ``parent``.

    The branch is committed to after its worktree is created, so the tip it
    holds when a sweep runs is a commit past the boundary its record names. Only
    that order makes the scenario about the tip: a branch left at its boundary
    would be preserved no matter which of the two a tombstone named.

    Parameters
    ----------
    tmp_path : Path
        Directory the working repository and its remote are created under.
    monkeypatch : pytest.MonkeyPatch
        Patcher used to enter the working repository for the test's duration.
    parent : str
        Branch the completed branch is cut from.
    branch : str
        Completed branch whose worktree the scenario builds.

    Returns
    -------
    PlonkStackScenario
        The seeded scenario, with the branch's tip and record captured.

    """
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    branch_ahead_of_trunk(local_path, parent)
    create_stacked_git_donkey_worktree(local_path, branch, parent)
    scenario = PlonkStackScenario(
        local_path=local_path,
        completed_branch=branch,
        parent=parent,
    )
    commit_in_worktree(scenario, branch, f"work on {branch}")
    commit_completion_marker(local_path, _issue_marker(branch))
    return _captured(scenario, branch)


def trunk_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch: str,
) -> PlonkStackScenario:
    """Create a completed worktree for a branch created from the trunk.

    ``branch`` is created from the trunk, so INV-11 records nothing for it: the
    case a sweep still has to preserve, because a surviving child needs the tip
    its parent reached. The commit made in the worktree is what gives that tip
    something to say, and a scenario stacks a child on it afterwards with
    ``create_stacked_git_donkey_worktree``, which does not move the branch.

    Parameters
    ----------
    tmp_path : Path
        Directory the working repository and its remote are created under.
    monkeypatch : pytest.MonkeyPatch
        Patcher used to enter the working repository for the test's duration.
    branch : str
        Completed trunk-based branch whose worktree the scenario builds.

    Returns
    -------
    PlonkStackScenario
        The seeded scenario, with the branch's tip and record captured.

    """
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    create_git_donkey_worktree(local_path, branch)
    scenario = PlonkStackScenario(local_path=local_path, completed_branch=branch)
    commit_in_worktree(scenario, branch, f"work on {branch}")
    commit_completion_marker(local_path, _issue_marker(branch))
    return _captured(scenario, branch)


def _recorded_branch(local_path: Path, branch: str, parent: str) -> str:
    """Create ``branch``, record it as ``parent``'s child, and return its tip.

    The record is written through the store the commands write through, so the
    fixture is the artefact a birth would have left and not a hand-built
    approximation of one. It is written after the branch has moved, because a
    record's ``recordedFrom`` is evidence about a branch that moved: a tip equal
    to the boundary records nothing the anchor does not.

    Parameters
    ----------
    local_path : Path
        Working repository to create and record the branch in.
    branch : str
        Branch to create and describe.
    parent : str
        Branch to name as the created branch's stack parent.

    Returns
    -------
    str
        The commit the created branch was left at.

    """
    repo = Repo(local_path)
    repo.git.checkout("main")
    repo.git.branch(parent, "main")
    base = repo.head.commit.hexsha
    tip = branch_ahead_of_trunk(local_path, branch)
    stack_store.GitStackRecordWriter(repo).create(
        stack_records.StackRecord(
            branch=branch,
            parent=stack_records.StackParent(branch=parent, pull_request=None),
            base=base,
            recorded_from=tip,
            evidence=stack_records.EVIDENCE_BIRTH,
        )
    )
    return tip


def orphan_by_ref_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch: str,
    parent: str,
) -> PlonkStackScenario:
    """Create a recorded branch, then delete the ref alone.

    ``git update-ref -d`` removes ``refs/heads/<branch>`` and nothing else, so
    the record outlives the branch it describes — the only route by which a
    deleted branch is still rescuable, and so the only route by which a sweep
    can produce a tombstone.

    Parameters
    ----------
    tmp_path : Path
        Directory the working repository and its remote are created under.
    monkeypatch : pytest.MonkeyPatch
        Patcher used to enter the working repository for the test's duration.
    branch : str
        Recorded branch to delete.
    parent : str
        Branch the deleted branch was recorded as a child of.

    Returns
    -------
    PlonkStackScenario
        The seeded scenario, naming ``branch`` as its orphan.

    """
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    tip = _recorded_branch(local_path, branch, parent)
    Repo(local_path).git.update_ref("-d", f"refs/heads/{branch}")
    return PlonkStackScenario(
        local_path=local_path,
        completed_branch=branch,
        parent=parent,
        tip=tip,
        orphan=branch,
    )


def orphan_by_branch_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch: str,
    parent: str,
) -> PlonkStackScenario:
    """Create a recorded branch, then delete it through Git alone.

    ``git branch -D`` destroys the whole ``branch.<name>`` configuration
    section, so the tip the record carried dies with the branch it described:
    the sweep can clear this orphan, but there is nothing left for it to
    preserve. A scenario asserting the two orphans are reported apart needs both
    routes, because the report they must not share is the report of a rescue.

    Parameters
    ----------
    tmp_path : Path
        Directory the working repository and its remote are created under.
    monkeypatch : pytest.MonkeyPatch
        Patcher used to enter the working repository for the test's duration.
    branch : str
        Recorded branch to delete.
    parent : str
        Branch the deleted branch was recorded as a child of.

    Returns
    -------
    PlonkStackScenario
        The seeded scenario, naming ``branch`` as its orphan.

    """
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    tip = _recorded_branch(local_path, branch, parent)
    Repo(local_path).git.branch("-D", branch)
    return PlonkStackScenario(
        local_path=local_path,
        completed_branch=branch,
        parent=parent,
        tip=tip,
        orphan=branch,
    )


def aged_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch: str,
) -> PlonkStackScenario:
    """Create a tombstone for a deleted branch, written before any window.

    A tombstone's age is read from the reflog entry of its ref, so ageing that
    entry is what makes an artefact written now look like one written years ago.
    The branch goes afterwards, because a tombstone is what a deleted branch
    leaves behind rather than something a live branch holds.

    Parameters
    ----------
    tmp_path : Path
        Directory the working repository and its remote are created under.
    monkeypatch : pytest.MonkeyPatch
        Patcher used to enter the working repository for the test's duration.
    branch : str
        Branch to delete, leaving its tombstone behind.

    Returns
    -------
    PlonkStackScenario
        The seeded scenario, with the tombstone's commit captured as its tip.

    """
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    tip = branch_ahead_of_trunk(local_path, branch)
    repo = Repo(local_path)
    repo.git.update_ref(
        "--create-reflog",
        stack_records.tombstone_ref_path(branch),
        tip,
        env={"GIT_COMMITTER_DATE": _TOMBSTONE_WRITTEN_ON},
    )
    repo.git.branch("-D", branch)
    # No ``orphan``: this scenario starts from a tombstone with no record
    # behind it, so there is no record for a sweep to rescue and nothing an
    # orphan-rescue assertion could hold it to.
    return PlonkStackScenario(
        local_path=local_path,
        completed_branch=branch,
        tip=tip,
    )


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
