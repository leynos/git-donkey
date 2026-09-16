"""Candidate builders shared by the ``git wheresat`` boundary-assessment suites.

A candidate is one source's answer to the question a run asked, and the three
kinds are told apart by how much weight they carry: an attested candidate is a
deliberate statement and is the only kind that can establish a boundary on its
own, a derived one is computed from the repository and needs a second
independent source to agree on the same commit, and an inferred one compares
content rather than history and can never be support at all.

The commits these builders name and the cases built from them are in
:mod:`tests.unit.wheresat_helpers`; this module holds only what turns a commit
into a candidate, and the names the questions a candidate came from are
reported under.
"""

from __future__ import annotations

from git_donkey.wheresat_records import (
    AttestedCandidate,
    DerivedCandidate,
    EvidenceKind,
    InferredCandidate,
)

# Source names as the collector reports them: the label each question its rung
# asked is reported under. Corroboration is counted by *kind* rather than by
# these names, so the two merge-base questions are one source however they are
# labelled, and the two record kinds are two: a record read at birth and the
# same record read from the anchor ref that outlives it.
RECORD_SOURCE = "stack record"
ANCHOR_SOURCE = "stack-base anchor"
SHARED_SOURCE = "shared record"
MERGE_BASE_SOURCE = "merge base"
PARENT_MERGE_BASE_SOURCE = "merge base of the parent head"
FORK_POINT_SOURCE = "fork point"
TREE_SOURCE = "tree identity"
PATCH_SOURCE = "patch identity"


def _candidate[C: (AttestedCandidate, DerivedCandidate, InferredCandidate)](
    candidate_type: type[C],
    commit: str,
    kind: EvidenceKind,
    source: str,
) -> C:
    """Return a candidate of ``candidate_type`` naming ``commit``."""
    return candidate_type(commit=commit, kind=kind, source=source)


def attested(
    commit: str,
    kind: EvidenceKind = EvidenceKind.STACK_RECORD_BIRTH,
    *,
    source: str = RECORD_SOURCE,
) -> AttestedCandidate:
    """Return an attested candidate naming ``commit``.

    Attested evidence is what a record written at birth or a review approval
    carries: a deliberate statement, which is the only kind of support that
    can establish a boundary on its own.

    Parameters
    ----------
    commit : str
        Commit the candidate offers as the boundary.
    kind : EvidenceKind, optional
        A kind ``TIERS`` classes as attested; the birth record by default.
    source : str, optional
        Which producer stated it; the birth record by default.

    Returns
    -------
    AttestedCandidate
        A candidate that can establish a boundary on its own.

    """
    return _candidate(AttestedCandidate, commit, kind, source)


def derived(
    commit: str,
    kind: EvidenceKind = EvidenceKind.MERGE_BASE,
    *,
    source: str = MERGE_BASE_SOURCE,
) -> DerivedCandidate:
    """Return a derived candidate naming ``commit``.

    Derived evidence is computed from the repository rather than stated, so
    it carries a boundary only when two independent sources agree on it.

    Parameters
    ----------
    commit : str
        Commit the candidate computes as the boundary.
    kind : EvidenceKind, optional
        A kind ``TIERS`` classes as derived; the merge base by default.
    source : str, optional
        The computation that produced it; the merge base by default.

    Returns
    -------
    DerivedCandidate
        A candidate that supports a boundary only alongside another.

    """
    return _candidate(DerivedCandidate, commit, kind, source)


def inferred(
    commit: str,
    kind: EvidenceKind = EvidenceKind.TREE_IDENTITY,
    *,
    source: str = TREE_SOURCE,
) -> InferredCandidate:
    """Return an inferred candidate naming ``commit``.

    Inferred evidence compares content rather than history, which is why a
    ``--deep`` search can report it beside a boundary but never as support for
    one.

    Parameters
    ----------
    commit : str
        Commit whose content resembles the boundary.
    kind : EvidenceKind, optional
        A kind ``TIERS`` classes as inferred; the tree identity by default.
    source : str, optional
        The comparison that produced it; the tree identity by default.

    Returns
    -------
    InferredCandidate
        A candidate that can never serve as support, only as a lead.

    """
    return _candidate(InferredCandidate, commit, kind, source)
