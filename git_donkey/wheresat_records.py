"""``git wheresat``'s value types.

This module is the vocabulary every other ``wheresat`` module exchanges: the
evidence kinds and their tiers, the boundary candidates, the graph facts a
candidate is judged against, the eight named gates, and the three assessments
the command can reach. Nothing here reads or writes anything — no Git, no
filesystem, no network, and no process — so the forensic paths are data rather
than behaviour: a boundary that came from a rewritten parent, a gate that could
not be answered, and a record a later integration superseded are all values a
test can build without a repository.

The only intra-package import is :mod:`git_donkey.stack_records`, for the
identity type the command shares with ``git donkey`` and ``git plonk``. The
dependency runs one way, from this command's types towards the shared contract,
never back.
"""

from __future__ import annotations

import dataclasses
import enum
import typing as typ

if typ.TYPE_CHECKING:
    from git_donkey import stack_records


class Ancestry(enum.StrEnum):
    """Answer to one ancestry question, including "could not tell"."""

    ANCESTOR = "ancestor"
    NOT_ANCESTOR = "not-ancestor"
    UNKNOWN = "unknown"


class EvidenceTier(enum.StrEnum):
    """How much weight a boundary candidate may carry."""

    ATTESTED = "attested"
    DERIVED = "derived"
    INFERRED = "inferred"


class EvidenceKind(enum.StrEnum):
    """Where a boundary candidate came from."""

    STACK_RECORD_BIRTH = "stack-record-birth"
    STACK_RECORD_REFRESHED = "stack-record-refreshed"
    SHARED_RECORD = "shared-record"
    PULL_REQUEST_HEAD = "pull-request-head"
    MERGE_BASE = "merge-base"
    FORK_POINT = "fork-point"
    TREE_IDENTITY = "tree-identity"
    PATCH_IDENTITY = "patch-identity"


TIERS: typ.Final[typ.Mapping[EvidenceKind, EvidenceTier]] = {
    EvidenceKind.STACK_RECORD_BIRTH: EvidenceTier.ATTESTED,
    EvidenceKind.STACK_RECORD_REFRESHED: EvidenceTier.ATTESTED,
    EvidenceKind.SHARED_RECORD: EvidenceTier.ATTESTED,
    EvidenceKind.PULL_REQUEST_HEAD: EvidenceTier.ATTESTED,
    EvidenceKind.MERGE_BASE: EvidenceTier.DERIVED,
    EvidenceKind.FORK_POINT: EvidenceTier.DERIVED,
    EvidenceKind.TREE_IDENTITY: EvidenceTier.INFERRED,
    EvidenceKind.PATCH_IDENTITY: EvidenceTier.INFERRED,
}
"""The single place the tier of each evidence kind is decided.

A candidate's class is not chosen by the collector but by this table, through
:func:`candidate_for`, so a source cannot declare inferred evidence and hand
the assessment an attested candidate.
"""

_RECORD_EVIDENCE_KINDS: typ.Final = frozenset({
    EvidenceKind.STACK_RECORD_BIRTH,
    EvidenceKind.STACK_RECORD_REFRESHED,
})


class GateOutcome(enum.StrEnum):
    """Result of one validation gate."""

    PASSED = "passed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class GateName(enum.StrEnum):
    """The gates, in the order every report lists them."""

    PARENT_IDENTITY_MATCHES = "parent-identity-matches"
    PARENT_MERGED = "parent-merged"
    LANDED_REACHABLE_FROM_TARGET = "landed-reachable-from-target"
    BOUNDARY_IS_ANCESTOR_OF_CHILD = "boundary-is-ancestor-of-child"
    REPLAY_RANGE_NON_EMPTY = "replay-range-non-empty"
    PARENT_HISTORY_INTACT = "parent-history-intact"
    REPLAY_RANGE_EXCLUDES_LANDED_WORK = "replay-range-excludes-landed-work"
    RECORD_NOT_SUPERSEDED = "record-not-superseded"


GATE_NAMES: typ.Final[tuple[GateName, ...]] = tuple(GateName)
"""Every gate, in the order the procedure lists them and reports them."""


@dataclasses.dataclass(frozen=True, slots=True)
class CommitRange:
    """Commits one question returned, and whether the answer was cut short.

    ``truncated`` is carried rather than inferred from a count, because a range
    cut short by ``--heuristic-window`` must never read as a complete one: the
    report says how much of the history it saw.
    """

    commits: tuple[str, ...]
    truncated: bool = False


COMMIT_ABBREVIATION: typ.Final = 7
"""How much of an object ID a reason or a report renders.

Shorter than a full object ID by design: the renderings are for a reader who is
about to paste one of them into Git, and every place that abbreviates a commit
abbreviates it the same way so two renderings of one commit are recognisable as
one commit.
"""


def range_key(base: str, tip: str) -> str:
    """Return the key a range's contents are stored under.

    Parameters
    ----------
    base : str
        Commit the range excludes.
    tip : str
        Commit the range includes up to.

    Returns
    -------
    str
        The range in ``git rev-list``'s own spelling, so the port that lists a
        range and the policy that reads it cannot disagree about which range a
        recorded answer belongs to.

    Examples
    --------
    >>> range_key("aaa", "bbb")
    'aaa..bbb'

    """
    return f"{base}..{tip}"


BACKUP_REF_PREFIX: typ.Final = "refs/wheresat-backup/"
"""Namespace the replay plan proposes keeping the child tip in.

Deliberately outside ``refs/wheresat/``, which is the evidence namespace a run
writes fetched objects into: a plan that kept the child tip inside it would be
naming a ref the next run could delete as its own, and the point of the backup
is that nothing but the user's own recovery touches it.
"""


def backup_ref(branch: str) -> str:
    """Return the ref the replay plan proposes for ``branch``'s child tip.

    Nothing here creates it. A run writes no ref outside the evidence namespace,
    so the ref belongs to the user, who is told to create it before the rebase
    the plan prints — which is what makes that rebase reversible.

    Parameters
    ----------
    branch : str
        Child branch the run is about.

    Returns
    -------
    str
        The fully qualified ref name, which the text report prints and the
        envelope carries. A branch name is a valid ref name by construction,
        because Git refuses to create one that is not, so concatenating it
        cannot put the ref outside the namespace above.

    Examples
    --------
    >>> backup_ref("issue-123-fix")
    'refs/wheresat-backup/issue-123-fix'

    """
    return f"{BACKUP_REF_PREFIX}{branch}"


@dataclasses.dataclass(frozen=True, slots=True)
class AttestedCandidate:
    """A boundary recorded by a deliberate act naming the exact commit."""

    commit: str
    kind: EvidenceKind
    source: str
    supporting: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class DerivedCandidate:
    """A boundary computed from surviving history; needs corroboration."""

    commit: str
    kind: EvidenceKind
    source: str
    supporting: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class InferredCandidate:
    """A boundary suggested by content comparison; never establishing."""

    commit: str
    kind: EvidenceKind
    source: str
    supporting: tuple[str, ...] = ()


type Candidate = AttestedCandidate | DerivedCandidate | InferredCandidate
type Establishing = AttestedCandidate | DerivedCandidate


def candidate_for(
    commit: str,
    kind: EvidenceKind,
    *,
    source: str,
    supporting: tuple[str, ...] = (),
) -> Candidate:
    """Return a candidate of the class the tier of ``kind`` demands.

    Parameters
    ----------
    commit : str
        Commit the evidence names as the boundary.
    kind : EvidenceKind
        Where the evidence came from.
    source : str
        Name of the source, which is what corroboration is counted by.
    supporting : tuple[str, ...], optional
        Further statements naming the same commit.

    Returns
    -------
    Candidate
        An attested, derived, or inferred candidate, chosen by ``TIERS``.

    """
    match TIERS[kind]:
        case EvidenceTier.ATTESTED:
            return AttestedCandidate(commit, kind, source, supporting)
        case EvidenceTier.DERIVED:
            return DerivedCandidate(commit, kind, source, supporting)
        case _:
            return InferredCandidate(commit, kind, source, supporting)


def demoted(candidate: AttestedCandidate) -> DerivedCandidate:
    """Return ``candidate`` as the candidate a failed gate demotes it to."""
    return DerivedCandidate(
        commit=candidate.commit,
        kind=candidate.kind,
        source=candidate.source,
        supporting=candidate.supporting,
    )


def is_record_kind(kind: EvidenceKind) -> bool:
    """Return whether ``kind`` is evidence a stack record supplied.

    Parameters
    ----------
    kind : EvidenceKind
        Evidence kind to classify.

    Returns
    -------
    bool
        Whether a stack record is what named this boundary, which is what
        decides whether the record's own history question applies to it.

    """
    return kind in _RECORD_EVIDENCE_KINDS


def record_evidence_kind(evidence: str) -> EvidenceKind | None:
    """Return the evidence kind a stack record's ``evidence`` value names.

    Parameters
    ----------
    evidence : str
        Value of the record's ``stackBaseEvidence`` key.

    Returns
    -------
    EvidenceKind | None
        The kind, or ``None`` when the value is one this version does not
        classify. Such a record is reported as a fault rather than read as some
        other tier's evidence: the record was written deliberately, so an
        unreadable value is a version mismatch and not a weak claim.

    """
    try:
        kind = EvidenceKind(evidence)
    except ValueError:
        return None
    return kind if kind in _RECORD_EVIDENCE_KINDS else None


@dataclasses.dataclass(frozen=True, slots=True)
class GateResult:
    """One named gate, its outcome, its applicability, and why.

    ``applicable`` is false for a gate whose subject the run never set out to
    use. Such a gate carries ``GateOutcome.INDETERMINATE`` because it answered
    nothing, and the flag is what tells the report to render it as not
    applicable rather than as a question that went unanswered. Applicability is
    decided from the run's inputs, so a fault cannot shrink the gate set.
    """

    name: GateName
    outcome: GateOutcome
    detail: str
    applicable: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class ParentPullRequest:
    """The parent pull request's merge state and refs.

    ``head_fetched_from`` names the repository the head ref was actually
    fetched from, which gate 1 compares against ``head_repository``: a head
    fetched from anywhere else is not the commit the pull request reports,
    however similar the two names are.
    """

    identity: stack_records.PullRequestIdentity
    merged: bool
    merged_at: str | None
    head_sha: str
    head_ref: str
    head_repository: str
    head_fetched_from: str | None
    base_ref: str
    base_repository: str
    landed: str | None
    stacked: bool


@dataclasses.dataclass(frozen=True, slots=True)
class BoundaryRequest:
    """Everything the user asked for, resolved to immutable object IDs.

    ``parent`` is set when the user named a pull request rather than when one
    was discovered, because it records what the run set out to consult: that is
    what decides whether the gates about a parent are applicable, so a run that
    named a parent it could not resolve refuses instead of establishing a
    boundary no gate examined.

    ``heuristic_window`` is how many of the target's newest commits a
    ``--deep`` run compares the child against. It is the run's own bound rather
    than a rung's, because it is what the user asked for and what the scan's
    cost is linear in, and it has no default: a construction site that forgot it
    would be a run that silently scanned someone else's idea of a window.
    """

    branch: str
    child_tip: str
    target: str
    parent: stack_records.PullRequestIdentity | None
    deep: bool
    heuristic_window: int
    offline: bool


class GitOperation(enum.StrEnum):
    """A Git operation a worktree can be stopped in the middle of."""

    REBASE = "rebase"
    MERGE = "merge"
    CHERRY_PICK = "cherry-pick"
    REVERT = "revert"
    BISECT = "bisect"


@dataclasses.dataclass(frozen=True, slots=True)
class WorktreeState:
    """What the worktree holding the child branch is in the middle of.

    A branch no worktree holds has no working tree, and so reports nothing: the
    state is what a warning about the replay command is read from, and a branch
    that is checked out nowhere cannot have one. ``dirty`` counts changes to
    tracked files only, because those are what a replay refuses to run over;
    untracked files are reported as neither clean nor dirty in this record and
    are deliberately not part of the state.
    """

    operation: GitOperation | None
    dirty: bool


@dataclasses.dataclass(frozen=True, slots=True)
class GraphFacts:
    """Every graph answer the assessment needs, collected eagerly.

    This record holds data, never callables and never an adapter handle, so the
    assessment cannot reach the repository and a property test can build an
    arbitrary graph without one. ``child_history`` is what makes the partition
    checkable: the included commits are the child's history below the boundary,
    and the excluded ones are what the boundary's own history contributes to
    it.

    ``ancestry`` is keyed by the ordered pair that was asked about. A pair the
    run never asked about is absent, and absent reads as ``Ancestry.UNKNOWN``:
    a question never put to Git is not a negative answer.
    """

    parent_head: str | None
    landed: str | None
    ancestry: typ.Mapping[tuple[str, str], Ancestry]
    range_contents: typ.Mapping[str, CommitRange]
    range_minus_parent: typ.Mapping[str, CommitRange]
    child_history: CommitRange
    cumulative_patch: typ.Mapping[str, str | None]
    landed_patch: str | None
    record_recorded_from: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class Established:
    """A boundary that cleared every applicable gate."""

    old_base: str
    support: tuple[Establishing, ...]
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    included_truncated: bool
    excluded_truncated: bool
    gates: tuple[GateResult, ...]
    durable_ref: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class Unresolved:
    """No candidate could establish a boundary, and the evidence is complete."""

    candidates: tuple[Candidate, ...]
    gates: tuple[GateResult, ...]
    reasons: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class Indeterminate:
    """The repository or the forge could not answer."""

    candidates: tuple[Candidate, ...]
    gates: tuple[GateResult, ...]
    reasons: tuple[str, ...]


type Assessment = Established | Unresolved | Indeterminate

EXIT_CODES: typ.Final[typ.Mapping[type, int]] = {
    Established: 0,
    Unresolved: 1,
    Indeterminate: 3,
}
"""Exit status per assessment.

``2`` is reserved for a usage, configuration, or credential failure, which is
decided at the command-line boundary rather than by an assessment.
"""

EXIT_USAGE: typ.Final = 2
"""Exit status for a usage, configuration, or credential error."""
