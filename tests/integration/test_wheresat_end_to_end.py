"""EP-M8's acceptance: a plonked stack's boundary still comes back.

This suite runs the three commands in the order the feature exists for.
``git donkey`` cuts a child from a parent branch and records the boundary at the
child's birth; ``git plonk`` then sweeps the merged parent — its worktree, its
branch, and a tombstone where the branch was — and ``git wheresat`` has to
answer for the child from what survived.

The boundary is the parent's tip at the moment the child was cut from it, so it
is a commit the trunk never held: after the sweep, the two artefacts that name
it are the child's own record and the parent's tombstone. That is what makes the
run a test of the whole contract rather than of one module — delete either
artefact and the boundary is still recoverable as a merge base or a fork point,
but nothing attests it, so the answer would be derived rather than attested and
the report would have to say so.

The parent's worktree is deliberately gone by the time the run happens. A branch
no worktree holds has no working tree to warn about, and a run that reported its
absence as a problem would be reporting the ordinary state of a swept stack as a
fault.
"""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import plonk, stack_records, wheresat, wheresat_records
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    commit_completion_marker,
    create_git_donkey_worktree,
    create_stacked_git_donkey_worktree,
)
from tests.integration.wheresat_helpers import (
    Status,
    WheresatRun,
    WheresatScenario,
    in_directory,
    report_tokens,
    run_wheresat,
    worktree_root,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(120)

PARENT: str = "issue-41-parent"
"""Completed parent branch the sweep removes and entombs."""

CHILD: str = "issue-42-child"
"""Branch stacked on the parent, which the run is asked about."""

_PARENT_MARKER: str = "(#41)"
"""Completion marker the trunk must carry for the sweep to remove the parent."""

_FILE_SUFFIX: str = ".txt"
"""Suffix of the file each branch commits, so the two branches differ."""


@pytest.fixture(scope="module")
def plonked(tmp_path_factory: pytest.TempPathFactory) -> WheresatScenario:
    """Build a stack, sweep its parent, and return what the child survived with.

    Returns
    -------
    WheresatScenario
        The checkout, the boundary the child's record attests, and the child's
        own tip — with ``child`` and ``parent`` naming the issue-style branches
        this scenario has to use for the sweep to recognize the parent as
        completed.

    """
    root = tmp_path_factory.mktemp("wheresat-end-to-end")
    local_path, remote_path = _setup_repo(root)
    with in_directory(local_path):
        create_git_donkey_worktree(local_path, PARENT)
        boundary = _commit_work(local_path, PARENT)
        create_stacked_git_donkey_worktree(local_path, CHILD, PARENT)
        tip = _commit_work(local_path, CHILD)
        commit_completion_marker(local_path, _PARENT_MARKER)
        exit_code = plonk.run_git_plonk(hard=True)

    assert exit_code == 0, "expected the hard sweep of the parent to succeed"
    return WheresatScenario(
        local_path=local_path,
        remote_path=remote_path,
        boundary=boundary,
        tip=tip,
        child=CHILD,
        parent=PARENT,
    )


def _commit_work(local_path: Path, branch: str) -> str:
    """Commit a file of its own in ``branch``'s worktree and return the new tip.

    Parameters
    ----------
    local_path : Path
        Working repository the branch's worktree belongs to.
    branch : str
        Branch whose worktree is committed to.

    Returns
    -------
    str
        The commit the branch was left at.

    """
    worktree = worktree_root(local_path) / branch
    repo = Repo(worktree)
    name = f"{branch}{_FILE_SUFFIX}"
    (worktree / name).write_text(f"work on {branch}\n")
    repo.git.add(name)
    repo.git.commit("-m", f"work on {branch}")
    return repo.head.commit.hexsha


def _run(scenario: WheresatScenario, capsys: pytest.CaptureFixture[str]) -> WheresatRun:
    """Ask about the child branch from inside its own worktree.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout the child's worktree belongs to.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the run's output is read from.

    Returns
    -------
    WheresatRun
        The status and both output streams, which are the run's own: the
        scenario was built by ``git donkey`` and ``git plonk``, so the capture
        is drained first and the streams hold only what this run reported.

    """
    capsys.readouterr()
    with in_directory(scenario.worktree_path()):
        return run_wheresat(wheresat.WheresatOptions(), capsys)


def _assert_the_evidence_names(
    plonked: WheresatScenario,
    run: WheresatRun,
    lines: set[tuple[str, ...]],
) -> None:
    """Assert the report names its answer and the artefact each row came from.

    Parameters
    ----------
    plonked : WheresatScenario
        Scenario whose boundary, tip, and refs the evidence has to name.
    run : WheresatRun
        The run a failure is reported against, for its streams.
    lines : set[tuple[str, ...]]
        The run's output, split into whitespace-separated token rows.

    """
    width = wheresat_records.COMMIT_ABBREVIATION
    boundary = plonked.boundary[:width]
    target = plonked.repo.heads["main"].commit.hexsha[:width]

    assert run.exit_code == Status.ESTABLISHED, run.stderr
    assert not run.stderr, "a successful run writes nothing to the error stream"
    assert "Included (1 commit)" in run.stdout, (
        "the child's one commit is reported as one commit"
    )
    assert ("boundary", boundary) in lines, (
        "the answer names the boundary, abbreviated as the report prints it"
    )
    assert ("target", target) in lines, (
        "and names the target's commit, abbreviated as a detail line is"
    )
    assert (
        "attested",
        "stack-record-birth",
        boundary,
        stack_records.base_ref_path(CHILD),
    ) in lines, f"expected the record in the evidence, got:\n{run.stdout}"
    assert (
        "derived",
        "fork-point",
        boundary,
        "fork",
        "point",
        "of",
        stack_records.tombstone_ref_path(PARENT),
    ) in lines, f"expected the tombstone in the evidence, got:\n{run.stdout}"
    assert (
        "derived",
        "merge-base",
        boundary,
        "merge",
        "base",
        "of",
        "the",
        "parent",
        "head",
        "and",
        "the",
        "child",
    ) in lines, f"expected the tombstoned parent head to be used, got:\n{run.stdout}"


def _assert_the_plan_is_pasteable(
    plonked: WheresatScenario,
    run: WheresatRun,
    lines: set[tuple[str, ...]],
) -> None:
    """Assert the printed replay can be pasted and undone as it stands.

    Parameters
    ----------
    plonked : WheresatScenario
        Scenario whose tip and boundary the plan has to replay.
    run : WheresatRun
        The run a failure is reported against, for its streams.
    lines : set[tuple[str, ...]]
        The run's output, split into whitespace-separated token rows.

    """
    width = wheresat_records.COMMIT_ABBREVIATION
    full_target = plonked.repo.heads["main"].commit.hexsha
    backup = wheresat_records.backup_ref(CHILD)

    assert ("git", "update-ref", backup, plonked.tip) in lines, (
        f"expected the plan to keep the child tip first, got:\n{run.stdout}"
    )
    assert (
        "git",
        "rebase",
        "--onto",
        full_target,
        plonked.boundary,
        CHILD,
    ) in lines, f"expected the child's replay in full, got:\n{run.stdout}"
    assert ("#", "undo:", "git", "reset", "--hard", backup) in lines, (
        f"expected the replay to be undoable from that ref, got:\n{run.stdout}"
    )
    assert f"the child tip must still be {plonked.tip[:width]}" in run.stdout, (
        f"expected the premise to be checkable, got:\n{run.stdout}"
    )
    assert (plonked.tip[:width],) in lines, (
        "and the child's tip is listed on the included side"
    )


def test_the_sweep_leaves_the_boundary_to_the_record_and_the_tombstone(
    plonked: WheresatScenario,
) -> None:
    """The swept parent is gone, and only the two artefacts that name it are left.

    Without this the acceptance below could pass against a repository where the
    parent branch survived or the sweep never ran, which would test nothing
    about a plonked stack.
    """
    repo = plonked.repo
    tombstone = stack_records.tombstone_ref_path(PARENT)
    anchor = stack_records.base_ref_path(CHILD)

    assert PARENT not in repo.heads, "expected the sweep to delete the parent"
    assert not plonked.worktree_path(PARENT).exists(), (
        "expected the sweep to remove the parent's worktree"
    )
    assert repo.git.rev_parse(tombstone) == plonked.boundary, (
        "the tombstone ref names the boundary the sweep recorded"
    )
    assert repo.git.rev_parse(anchor) == plonked.boundary, (
        "and the child's anchor names the same commit"
    )


def test_the_boundary_comes_back_from_the_record_and_the_tombstone(
    plonked: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The run answers with the boundary, and names both artefacts in its evidence.

    The record attests the boundary, and the tombstone is where the parent head
    comes from now that the parent branch is gone — so the answer is attested
    rather than derived, and the report has to say which artefact each line of
    evidence came from. The replay it prints is the child's work on the trunk, at
    the boundary the sweep did not destroy: the commands are full object IDs with
    a backup ref ahead of them, which is the form a user pastes into a shell.
    """
    run = _run(plonked, capsys)
    lines = report_tokens(run.stdout)

    _assert_the_evidence_names(plonked, run, lines)
    _assert_the_plan_is_pasteable(plonked, run, lines)


def test_the_run_reports_no_problem_with_the_plonked_parent(
    plonked: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A branch no worktree holds is ordinary, and is not warned about.

    The sweep took the parent's worktree away, so the parent is checked out
    nowhere. That is what a swept stack looks like, not a fault, and a run that
    reported it — as a warning, or as an unreadable worktree — would make the
    ordinary state after a sweep look like something to repair.
    """
    run = _run(plonked, capsys)

    assert run.exit_code == Status.ESTABLISHED, run.stderr
    assert "Warnings" not in run.stdout, (
        "a swept stack is an ordinary state, so no warning section is printed"
    )
    assert "could not be read" not in run.stdout, (
        "and no worktree is reported as unreadable"
    )
    assert f"the worktree holding {PARENT}" not in run.stdout, (
        "and the parent is not named among them"
    )
