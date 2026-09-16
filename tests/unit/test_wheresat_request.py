"""The run's names, resolved before there is any evidence to assess.

``--onto`` is answered as the caller wrote it. A ref names the reflog the
fork-point question reads, and anything else that resolves to a commit names no
ref and so has no reflog at all. Both are answers, and both are given before a
single boundary is read, which is what makes a question Git cannot put a failure
to start rather than an indeterminate result. The pair of cases here is exactly
that difference: an unanswerable question ends the run as a usage failure that
names the target, and a target that names no ref is still a target.

``--branch`` is resolved here too, and a name the record's own refs cannot hold
is refused the same way — as a usage failure raised before the run reads
anything, rather than as a ``ValueError`` from the collection phase that would
reach the operator as a traceback once evidence was already being read.

The graph is a double because the failure is one Git only reaches when it cannot
be asked at all — the same fault the graph's own suite injects by corrupting the
object the quiet question has to parse. What is under test here is not that Git
fails but what the run does with the failure: the class an operator sees, the
name the refusal carries, and the cause it keeps.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_request -q
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from git_donkey import wheresat_request
from git_donkey.wheresat_errors import WheresatGraphError, WheresatUsageError
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git_donkey import wheresat_graph

_COMMIT: typ.Final = "e" * 40
"""A commit nobody has to hold: both cases here answer before Git is read."""

_REFUSAL: typ.Final = "cannot tell whether 'main' names a ref: bad object"
"""What the graph reports when it cannot be asked which ref a revision names."""


@dataclasses.dataclass(frozen=True, slots=True)
class _Graph:
    """A graph that resolves everything and names a ref only when told to fail.

    ``WheresatGraph`` is a protocol of fourteen reads, and this double answers
    the two the target is resolved through. The cast at each call site is what
    makes that a deliberate answer rather than a stub grown until it stopped
    complaining: every other read is a question this module's subject never
    puts.

    Parameters
    ----------
    refusal : str | None
        The fault ``ref_name`` raises, or ``None`` to answer that no ref is
        named.

    """

    refusal: str | None = None

    @staticmethod
    def resolve(rev: str) -> str:
        """Return the commit every revision here resolves to."""
        return _COMMIT

    def ref_name(self, rev: str) -> str | None:
        """Report the configured fault, or answer that ``rev`` names no ref."""
        if self.refusal is None:
            # Nothing is configured to refuse the question, so this graph
            # answers that the revision names no ref of its own.
            return
        raise WheresatGraphError(self.refusal)


def _target(tmp_path: Path, graph: _Graph) -> tuple[str, str | None]:
    """Return what resolving ``--onto main`` gives, over a real repository."""
    return wheresat_request._target(
        wheresat_request.WheresatOptions(onto="main"),
        git_repo_helpers.seed_repo(tmp_path / "local"),
        typ.cast("wheresat_graph.WheresatGraph", graph),
    )


def test_a_target_git_cannot_be_asked_about_is_a_usage_failure(
    tmp_path: Path,
) -> None:
    """A question that cannot be put is a failure to start, not a result."""
    graph = _Graph(refusal=_REFUSAL)

    with pytest.raises(WheresatUsageError) as refusal:
        _target(tmp_path, graph)

    assert "the target main" in str(refusal.value), (
        "the refusal names which of the run's names could not be asked about"
    )
    assert isinstance(refusal.value.__cause__, WheresatGraphError), (
        "the graph's own failure is kept as the cause"
    )


def test_a_target_that_names_no_ref_is_still_a_target(tmp_path: Path) -> None:
    """An object ID resolves to a commit with no reflog, and that is an answer."""
    target, ref = _target(tmp_path, _Graph())

    assert target == _COMMIT, "the target is the commit its name resolved to"
    assert ref is None, "a name that is not a ref has no reflog to read"


def test_a_branch_no_ref_may_carry_is_a_usage_failure(tmp_path: Path) -> None:
    """A branch the record's refs cannot hold is refused before anything is read.

    The branch reaches two places a bare name may not: the ref path its record
    is kept under, and the command line of the commands that write it. A value
    such as ``--branch -d`` would otherwise fail where the anchor ref is built,
    as an uncaught ``ValueError`` raised after the run had begun reading
    evidence. A hierarchical name is still a branch and is still accepted.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    nested = wheresat_request.WheresatOptions(branch="feature/child")
    refused = wheresat_request.WheresatOptions(branch="-d")

    with pytest.raises(WheresatUsageError) as refusal:
        wheresat_request._branch(refused, repo)

    assert "--branch cannot name a branch" in str(refusal.value), (
        "the refusal should name the option that carried the value"
    )
    assert "begins with '-'" in str(refusal.value), (
        "the refusal should carry Git's own reason for refusing the value"
    )
    assert wheresat_request._branch(nested, repo) == "feature/child", (
        "a hierarchical branch name is one the record's refs may mirror"
    )
