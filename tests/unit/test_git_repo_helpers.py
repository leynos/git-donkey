"""Tests for the shared repository builders and the ancestry readers.

Each stack builder makes a claim about the repository it built — that the child
still reaches the head it was cut from, that the parent's head is no longer an
ancestor of it, that the two branches share only the trunk — and every later
milestone asserts against those claims. A builder that quietly produced the
wrong shape would make those assertions meaningless, so nothing here trusts the
builder's bookkeeping: the same questions the boundary recovery will ask are
put to the built repository, and Git's answers are the assertions.

The rewritten shape's answers are the ones worth reading closely, because they
say where the recovery can and cannot get its answer from. Every best common
ancestor is an ancestor of both commits it was computed from, so the commit the
parent and child share is always on the parent's history; the check that
refuses it is the replay range rather than an ancestry question, and the range
only refuses it while the parent's head is the head the child inherited. The
fork point still names that head out of the parent's reflog, which is why the
design treats it as evidence needing corroboration rather than as an answer.
"""

from __future__ import annotations

import typing as typ

import pytest

from tests.git_repo_helpers import (
    advanced_parent_stack,
    is_ancestor,
    merge_bases,
    rewritten_parent_stack,
    seed_repo,
    squash_merged_stack,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git import Repo

# Every shape gives the child two commits of its own, so a replay range that
# holds more than this holds parent work as well.
_CHILD_COMMITS = 2


def _revisions(repo: Repo, *arguments: str) -> tuple[str, ...]:
    """Return the commits ``git rev-list`` names for the given arguments."""
    return tuple(repo.git.rev_list(*arguments).split())


def _tree(repo: Repo, commit: str) -> str:
    """Return the tree object ``commit`` names, whatever its history is."""
    return repo.git.rev_parse(f"{commit}^{{tree}}")


def test_squash_merged_stack_keeps_the_boundary_in_the_childs_history(
    tmp_path: Path,
) -> None:
    """Ancestry alone recovers the boundary while the parent has not moved."""
    fixture = squash_merged_stack(tmp_path / "repo")
    repo = fixture.repo
    boundary = fixture.expected_old_base

    assert boundary is not None, "the squash shape is one a working answer resolves"
    assert boundary == fixture.parent_head, (
        "the child was cut from the parent's head, so the two are one commit"
    )
    assert fixture.inherited_head == fixture.parent_head, (
        "the parent never moved, so the head it had is the head it has"
    )
    assert is_ancestor(repo, boundary, fixture.child_tip), (
        "the child still reaches the head it was cut from"
    )
    assert merge_bases(repo, fixture.parent_head, fixture.child_tip) == (boundary,), (
        "the merge base recovers the boundary here"
    )
    assert not is_ancestor(repo, fixture.landed, fixture.child_tip), (
        "the squash commit shares no ancestry with the work it carries"
    )
    assert is_ancestor(repo, fixture.landed, fixture.target), (
        "the integration commit is on the trunk the child is replayed onto"
    )
    assert (
        len(_revisions(repo, f"{boundary}..{fixture.child_tip}")) == _CHILD_COMMITS
    ), "the replay range from the boundary is exactly the child's own work"
    assert _tree(repo, fixture.landed) == _tree(repo, boundary), (
        "and the squash carries the boundary's content, which the inferred tier "
        "reads as evidence it cannot tell apart from the boundary itself"
    )


def test_advanced_parent_stack_leaves_the_fork_point_as_the_boundary(
    tmp_path: Path,
) -> None:
    """The parent's head is not in the child, so the boundary forked earlier."""
    fixture = advanced_parent_stack(tmp_path / "repo")
    repo = fixture.repo
    boundary = fixture.expected_old_base

    assert boundary is not None, "the advanced shape is one a working answer resolves"
    assert boundary == fixture.inherited_head, (
        "the boundary is the commit the child was cut from, not the parent's head"
    )
    assert fixture.inherited_head != fixture.parent_head, (
        "the parent committed after the fork, which is the shape under test"
    )
    assert is_ancestor(repo, boundary, fixture.child_tip), (
        "the boundary is an ancestor of the child"
    )
    assert is_ancestor(repo, boundary, fixture.parent_head), (
        "and it is an ancestor of the parent it forked from"
    )
    assert not is_ancestor(repo, fixture.parent_head, fixture.child_tip), (
        "the parent's head is not in the child's history"
    )
    assert merge_bases(repo, fixture.parent_head, fixture.child_tip) == (boundary,), (
        "the merge base is the fork point rather than the parent's head"
    )
    assert not is_ancestor(repo, fixture.landed, fixture.child_tip), (
        "the squash commit shares no ancestry with the work it carries"
    )
    assert is_ancestor(repo, fixture.landed, fixture.target), (
        "the integration commit is on the trunk the child is replayed onto"
    )
    assert (
        len(_revisions(repo, f"{boundary}..{fixture.child_tip}")) == _CHILD_COMMITS
    ), "the replay range from the boundary is exactly the child's own work"


def test_rewritten_parent_stack_leaves_the_boundary_unattested(tmp_path: Path) -> None:
    """The child reaches the boundary, but nothing names it as the boundary."""
    fixture = rewritten_parent_stack(tmp_path / "repo")
    repo = fixture.repo

    assert fixture.expected_old_base is None, (
        "no surviving evidence names the boundary, so the fixture expects refusal"
    )
    assert fixture.inherited_head != fixture.parent_head, (
        "the rewrite replaced the commits the child inherited"
    )
    assert is_ancestor(repo, fixture.inherited_head, fixture.child_tip), (
        "the child still reaches the head it was cut from"
    )
    assert not is_ancestor(repo, fixture.inherited_head, fixture.parent_head), (
        "the parent's history no longer contains it"
    )
    bases = merge_bases(repo, fixture.parent_head, fixture.child_tip)
    assert len(bases) == 1, "the rewritten parent leaves one best common ancestor"
    assert bases[0] != fixture.inherited_head, "the merge base is not the boundary"
    assert is_ancestor(repo, bases[0], fixture.inherited_head), (
        "the two histories meet at the trunk commit both branches came from"
    )
    # The design's parent-history gate refutes a candidate boundary when it is
    # not an ancestor of the parent's head. No merge base can ever be refuted
    # that way, which is why the rewritten shape has to be refused by the
    # replay range instead.
    assert is_ancestor(repo, bases[0], fixture.parent_head), (
        "and a merge base is an ancestor of the parent's head by construction"
    )
    assert fixture.inherited_head in _revisions(
        repo, f"{bases[0]}..{fixture.child_tip}"
    ), (
        "the range from the trunk commit holds the parent's own inherited work, "
        "which is the check that refuses it while the inherited head is named"
    )
    assert repo.git.merge_base("--fork-point", fixture.parent, fixture.child) == (
        fixture.inherited_head
    ), (
        "the parent's reflog still names the head the child inherited, which is "
        "why the fork point is derived evidence needing corroboration"
    )
    assert _tree(repo, fixture.landed) == _tree(repo, fixture.inherited_head), (
        "the squash carries the content of the commit whose boundary was lost"
    )


def test_an_unanswerable_ancestry_question_is_raised_not_answered(
    tmp_path: Path,
) -> None:
    """AXIOM-1: a question Git could not answer must not read as "no".

    The production adapter reports a status other than 0 or 1 as indeterminate.
    A fixture has no such answer to give — a test that carried on would assert
    against a boundary it never established — so the readers fail loudly.
    """
    repo = seed_repo(tmp_path / "repo")
    missing = "0" * 40

    with pytest.raises(AssertionError, match="exited 128"):
        is_ancestor(repo, missing, "HEAD")

    with pytest.raises(AssertionError, match="exited 128"):
        merge_bases(repo, missing, "HEAD")
