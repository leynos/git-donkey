"""What the content question makes of the commits above a candidate boundary.

Gate 7 asks whether a replay range holds work the target has already taken, and
two of its three clauses read history: the suffix clause asks what the parent
head reaches, and the patch clause compares the range's cumulative patch with
the one the landing applied. The third reads content, and it is the one that
survives a parent that was rewritten before it merged, because a rewrite moves
a commit and keeps its content.

That clause compares trees, and sitting at a tree is not the same as applying
it. The shape ``git wheresat`` exists for is exactly where the two come apart:
a squash merge leaves the parent head's content on the trunk, so any commit
above the boundary that carries nothing of its own sits at the landed content
without applying any of it. A comparison that named such a commit would refuse
the boundary it was asked about — and the run would print a rebase it had just
told the reader not to run — so the commits that apply nothing are left out of
what the clause sees.

The repository each test builds is real, because the question is Git's own: a
commit applies the difference between its tree and its first parent's, and a
double would assert this module's belief about that rather than Git's answer.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_facts -q
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import stack_store, wheresat_collect, wheresat_facts, wheresat_graph
from git_donkey.wheresat_heads import ParentHead
from git_donkey.wheresat_records import (
    AttestedCandidate,
    BoundaryRequest,
    EvidenceKind,
    GraphFacts,
    range_key,
)
from tests import git_repo_helpers
from tests.unit.wheresat_helpers import DEFAULT_WINDOW, parent_pull_request

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git import Repo

_CHILD: typ.Final = "child"
"""Branch the run is asked about, which is where the test's commits are made."""

_TRUNK: typ.Final = "main"
"""Branch the squash merge lands on, and the child's default target."""

_PARENT: typ.Final = "parent"
"""Branch the child was cut from, which is the boundary's own branch."""

_PARENT_FILE: typ.Final = "parent.txt"
"""File the parent commits and the squash merge lands."""

_PARENT_WORK: typ.Final = "parent work"
"""Contents the parent commits, which is the content the landing carries."""

_CHILD_EDIT: typ.Final = "changed by the child"
"""Contents the child writes over the parent's, which applies a change."""


@dataclasses.dataclass(frozen=True, slots=True)
class SquashedParent:
    """A child cut from a parent that was squash-merged into the trunk.

    Attributes
    ----------
    repo : Repo
        Repository every commit of the shape lives in, with ``child`` checked
        out so a test's own commits land on the right branch.
    root : Path
        Directory that repository lives in, which its files are written under.
    boundary : str
        Commit the child was cut from: the parent's own head, whose tree the
        landing carries.
    landed : str
        Commit the squash merge left on the trunk, whose tree is the boundary's.
    target : str
        Commit the run replays onto, which is where the landing is.

    """

    repo: Repo
    root: Path
    boundary: str
    landed: str
    target: str


def _squashed_parent(root: Path) -> SquashedParent:
    """Build the squash-merge shape and leave the child checked out at its head.

    The parent commits a file the child's own history then reaches, and the
    trunk takes the same content as one squash commit, so the landed commit's
    tree is the boundary's. That equality is what the tests below turn on: it
    is what puts a commit that applies nothing at the landed content whenever
    the child commits one.

    Parameters
    ----------
    root : Path
        Directory the repository is created in.

    Returns
    -------
    SquashedParent
        The repository and the three commits that describe the shape.

    """
    repo_path = root / "repo"
    repo = git_repo_helpers.seed_repo(repo_path)
    repo.git.checkout("-b", _PARENT)
    boundary = git_repo_helpers.commit_file(
        repo, repo_path / _PARENT_FILE, _PARENT_WORK, "Parent work"
    )
    repo.git.checkout(_TRUNK)
    repo.git.merge("--squash", _PARENT)
    repo.git.commit("-m", "Squash-merge the parent")
    landed = repo.head.commit.hexsha
    repo.git.checkout("-b", _CHILD, boundary)
    return SquashedParent(
        repo=repo, root=repo_path, boundary=boundary, landed=landed, target=landed
    )


def _facts(shape: SquashedParent, *, candidate: str, child_tip: str) -> GraphFacts:
    """Return the facts a run about ``candidate`` would assemble.

    The run is given a parent pull request whose head is the boundary and whose
    integration is the landing, because that is the pair gate 7 needs before it
    asks the content question at all: the question is asked from the landed
    commit's side, and it is asked about nothing when there is no landing.

    Parameters
    ----------
    shape : SquashedParent
        The repository and the commits the run reasons about.
    candidate : str
        Commit the run proposes as the boundary.
    child_tip : str
        Commit the child branch is at, which the range is listed up to.

    Returns
    -------
    GraphFacts
        The graph answers the gates read, with the twins of every listed range.

    """
    parent = parent_pull_request(head_sha=shape.boundary, landed=shape.landed)
    head = ParentHead(shape.boundary, ref=None)
    return wheresat_facts.assemble_facts(
        wheresat_collect.CollectionContext(
            request=BoundaryRequest(
                branch=_CHILD,
                child_tip=child_tip,
                target=shape.target,
                parent=parent.identity,
                deep=False,
                heuristic_window=DEFAULT_WINDOW,
                offline=False,
            ),
            target_ref=None,
            parent=parent,
            graph=wheresat_graph.GitWheresatGraph(shape.repo),
            records=stack_store.GitStackRecordReader(shape.repo),
            parent_head=head,
        ),
        wheresat_collect.CollectedEvidence(
            candidates=(
                AttestedCandidate(
                    commit=candidate,
                    kind=EvidenceKind.STACK_RECORD_BIRTH,
                    source="stack record",
                ),
            ),
            parent_head=head,
            recorded_from=None,
        ),
    ).facts


def test_a_commit_that_applies_nothing_is_not_a_twin(tmp_path: Path) -> None:
    """A commit at the landed content that changes nothing carries no work.

    This is the boundary the squash shape produces: the child is cut from the
    parent's head, commits nothing above it, and its tree is therefore the
    landing's tree. Naming it would refuse the boundary on a comparison that
    says only that the two hold the same content, which a replay of the commit
    would not apply a second time — it would drop it.
    """
    shape = _squashed_parent(tmp_path)
    nothing = git_repo_helpers.advance(shape.repo, message="Nothing at all")

    facts = _facts(shape, candidate=shape.boundary, child_tip=nothing)

    assert facts.landed_twins[range_key(shape.boundary, nothing)] == (), (
        "a commit that applies nothing is not work the target has already taken"
    )


def test_a_range_names_the_commit_applying_the_landed_content(tmp_path: Path) -> None:
    """The range names the commit that applies it, and not the no-op above it.

    The child edits the file the landing carried and then puts it back, so the
    range holds one commit that reaches the landed content by applying a
    change, and one above it that applies nothing. Only the first is a second
    application of that content, so only the first is named.
    """
    shape = _squashed_parent(tmp_path)
    git_repo_helpers.commit_file(
        shape.repo, shape.root / _PARENT_FILE, _CHILD_EDIT, "Change the parent's file"
    )
    restored = git_repo_helpers.commit_file(
        shape.repo,
        shape.root / _PARENT_FILE,
        _PARENT_WORK,
        "Put the parent's file back",
    )
    nothing = git_repo_helpers.advance(shape.repo, message="Nothing at all")

    facts = _facts(shape, candidate=shape.boundary, child_tip=nothing)

    assert facts.landed_twins[range_key(shape.boundary, nothing)] == (restored,), (
        "the commit applying the landed content is named, and the no-op is not"
    )
