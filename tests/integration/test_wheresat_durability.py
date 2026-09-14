"""INV-8: a boundary the run reports is a boundary the repository keeps.

Evidence refs are not scratch. The head a run fetched is what it read the
boundary from, and a ``git gc --prune=now`` run later in the same afternoon
would take that commit with it, turning a correct answer into
``fatal: invalid upstream``. So a boundary reachable only through a per-run
evidence ref is retained under :data:`refs/wheresat/boundary/<branch>`, and
one a branch already reaches is left alone.

The two cases are built here rather than simulated. In the first the boundary
is an ordinary commit on a branch; in the second it is a pull request head
fetched from a remote, which nothing else in the repository names — the fetch
writes no ``FETCH_HEAD`` and no remote-tracking ref, so the evidence ref is the
only thing keeping the commit alive until the run retains it.

Each case then does what an hour's worth of other people's work would do to the
repository: the per-run evidence namespace is released, every ref under it is
confirmed gone, and ``git gc --prune=now`` runs. The boundary must still
resolve, and in the second case it must resolve to the ref the run would name
in its report.

The last test is the negative control. It builds the second case and stops
before retaining, so the commit is genuinely lost: that is what distinguishes a
run that retains when it must from one that retains always, and from a test that
would pass because garbage collection had quietly spared the object anyway. The
collector that decides which of the two cases it is looking at composes the
query in :mod:`git_donkey.wheresat_graph` with the writer in
:mod:`git_donkey.wheresat_refs`; this module pins both halves of that decision
so the composition has nothing left to get wrong.

Marked with a longer timeout than the suite default because it runs full
garbage collections.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from git import Repo

from git_donkey.wheresat_graph import GitWheresatGraph
from git_donkey.wheresat_refs import (
    GitWheresatRefWriter,
    per_run_ref,
)
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(120)

_ANSWERED_YES: typ.Final = 0
_PER_RUN_NAMESPACE: typ.Final = "refs/wheresat/op"
_BOUNDARY_NAMESPACE: typ.Final = "refs/wheresat/boundary"
_REF_NAME_FORMAT: typ.Final = "--format=%(refname)"


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceCase:
    """A repository whose boundary is kept alive, or not, by a ref.

    ``branch`` is the child the boundary is being established for, and so the
    name the retained ref is keyed by. ``reachable_from_a_branch`` is the
    answer the case is built to make true, and is asserted against the port's
    own answer rather than assumed.
    """

    label: str
    repo: Repo
    branch: str
    boundary: str
    op_id: str
    reachable_from_a_branch: bool


def _refs_under(repo: Repo, namespace: str) -> tuple[str, ...]:
    """Return every ref at or below ``namespace``."""
    return tuple(
        line
        for line in repo.git.for_each_ref(_REF_NAME_FORMAT, namespace).splitlines()
        if line
    )


def _resolves(repo: Repo, rev: str) -> bool:
    """Return whether ``rev`` still resolves to a commit."""
    status, _, _ = repo.git.rev_parse(
        "--verify",
        "--quiet",
        "--end-of-options",
        f"{rev}^{{commit}}",
        with_extended_output=True,
        with_exceptions=False,
    )
    return status == _ANSWERED_YES


def _branch_boundary(root: Path) -> EvidenceCase:
    """Build a boundary that an ordinary branch already reaches.

    The child was cut from the boundary and a branch still names it, so no
    reference has to be written for the commit to outlive the run.

    Returns
    -------
    EvidenceCase
        The case whose boundary an ordinary branch already reaches.

    """
    repo = git_repo_helpers.seed_repo(root)
    boundary = git_repo_helpers.advance(repo, message="The boundary")
    repo.git.branch("parent", boundary)
    repo.git.checkout("-b", "child")
    git_repo_helpers.advance(repo, message="Child work")
    return EvidenceCase(
        label="branch-boundary",
        repo=repo,
        branch="child",
        boundary=boundary,
        op_id="durability-branch",
        reachable_from_a_branch=True,
    )


def _fetched_boundary(root: Path) -> EvidenceCase:
    """Build a boundary only the fetched evidence ref reaches.

    The remote's pull request head is a commit the working repository has no
    other use for, and the fetch that brings it down writes no ``FETCH_HEAD``
    and no remote-tracking ref, so the evidence ref is the whole of what keeps
    it from being pruned.

    Returns
    -------
    EvidenceCase
        The case whose boundary only the fetched evidence ref reaches.

    """
    origin = git_repo_helpers.seed_repo(root / "origin")
    boundary = git_repo_helpers.advance(origin, message="The parent pull request head")
    origin.git.update_ref("refs/pull/7/head", boundary)

    repo = Repo.init(root / "work")
    git_repo_helpers.configure_repo(repo)
    repo.git.remote("add", "origin", (root / "origin").as_posix())
    op_id = "durability-fetched"
    GitWheresatRefWriter(repo).fetch_evidence(
        "origin", "refs/pull/7/head", per_run_ref(op_id, "parent-head")
    )
    return EvidenceCase(
        label="fetched-boundary",
        repo=repo,
        branch="child",
        boundary=boundary,
        op_id=op_id,
        reachable_from_a_branch=False,
    )


_BUILDERS: typ.Final[typ.Mapping[str, typ.Callable[[Path], EvidenceCase]]] = {
    "branch-boundary": _branch_boundary,
    "fetched-boundary": _fetched_boundary,
}


@pytest.mark.parametrize("name", tuple(_BUILDERS))
def test_a_reported_boundary_survives_garbage_collection(
    tmp_path: Path, name: str
) -> None:
    """INV-8: whatever the run reported, the repository still has it."""
    case = _BUILDERS[name](tmp_path / name)
    graph = GitWheresatGraph(case.repo)
    already_reachable = graph.is_reachable_from_durable_ref(case.boundary)
    assert already_reachable == case.reachable_from_a_branch, (
        "the case is built to answer this way, and the port must agree"
    )

    writer = GitWheresatRefWriter(case.repo)
    retained = None
    if not already_reachable:
        retained = case.repo.git.rev_parse(
            writer.retain_boundary(case.branch, case.boundary)
        )
    writer.release(case.op_id)
    assert not _refs_under(case.repo, _PER_RUN_NAMESPACE), (
        "the run's own evidence namespace is gone"
    )
    case.repo.git.gc("--prune=now")

    assert _resolves(case.repo, case.boundary), (
        "the boundary is reachable from a ref that survives the process"
    )
    if retained is None:
        assert not _refs_under(case.repo, _BOUNDARY_NAMESPACE), (
            "nothing was retained, because a branch was keeping it already"
        )
    else:
        assert retained == case.boundary, "the retained ref names the boundary"
        assert _refs_under(case.repo, _BOUNDARY_NAMESPACE) == (
            f"{_BOUNDARY_NAMESPACE}/{case.branch}",
        ), "and it is a ref the report can name"


def test_a_boundary_nothing_retains_is_lost(tmp_path: Path) -> None:
    """The negative control: retention is load-bearing, not decoration.

    The commit resolves while the evidence ref is there and is gone once it is
    released and the repository is collected, which is what makes the previous
    test's survival a fact about the retained ref rather than about the
    collector's mercy.
    """
    case = _fetched_boundary(tmp_path / "unretained")
    assert _resolves(case.repo, case.boundary), "the evidence ref is holding the commit"
    GitWheresatRefWriter(case.repo).release(case.op_id)
    case.repo.git.gc("--prune=now")
    assert not _resolves(case.repo, case.boundary), (
        "without a retained ref, the collected commit is the one the answer "
        "used to name"
    )
