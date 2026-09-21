"""The ladder's own vocabulary: what a walk reads, and how it answers.

One walk of the parent ladder is handed three reads and answers with one of two
things: the pull request it identified, or the reason its question went
unanswered. Both are values rather than reports, and both are built here, so
every rung reports its answer the same way — the rung that answered records the
outcome it reached, and the rung whose question could not be put records the
bounded class of the failure beside the reason in the operator's words.

The vocabulary sits apart from the ladder that is written in it
(:mod:`git_donkey.wheresat_parents`) because the two are different kinds of
thing: the ladder is policy — which question is put, in what order, and what an
unanswered one means — and this is the set of values that policy answers with,
together with the two observations an answer can be recorded as.

Nothing here opens a repository or a socket: the reads are handed in, and the
forge is reached through a callable this module is given rather than one it opens
itself, which is what lets a run told to stay offline be a walk with nothing to
ask through.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import observability, wheresat_shared_record

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey import stack_store
    from git_donkey.wheresat_github import WheresatGitHub
    from git_donkey.wheresat_graph import WheresatGraph
    from git_donkey.wheresat_records import ParentPullRequest

_PARENT_IDENTIFICATION: typ.Final[observability.Operation] = "parent_identification"
"""Operation every question about the parent is recorded under."""

_NO_SHARED_RECORD: typ.Final = wheresat_shared_record.SharedRecordAbsent()
"""The claim of a walk that read no body, or read one that claimed nothing."""


@dataclasses.dataclass(frozen=True, slots=True)
class ParentIdentification:
    """The parent pull request the ladder named, and what it could not ask.

    Attributes
    ----------
    parent : ParentPullRequest | None
        The parent pull request, when one was identified. It is ``None`` for a
        run that named no parent, for a run that found none, and for every run
        whose question went unanswered.
    faults : tuple[str, ...]
        What the run could not ask or could not be told, in the operator's
        words. A fault is not a refusal to answer but the reason an answer could
        not be given, and a run that reports one is indeterminate rather than
        refused (ADR-005).
    error_kind : observability.ErrorKind | None
        The bounded class of that failure, for the run's observation.
    shared_record : wheresat_shared_record.SharedRecordResult
        What the child's pull request body claimed, as it was read, when a rung
        read one. It rides here rather than being re-read by the collection
        phase for two reasons: reading it takes a forge, which collection has
        none of, and a claim read twice is a claim two phases could read
        differently. A run that read no body leaves it absent.

    """

    parent: ParentPullRequest | None = None
    faults: tuple[str, ...] = ()
    error_kind: observability.ErrorKind | None = None
    shared_record: wheresat_shared_record.SharedRecordResult = _NO_SHARED_RECORD


@dataclasses.dataclass(frozen=True, slots=True)
class LadderReads:
    """Everything one walk of the ladder reads through.

    The three are one object because every rung's input is this walk rather
    than a question of its own, and because a rung that had to be handed its
    reads one at a time would be a rung whose signature changes every time the
    ladder grows a question.

    Attributes
    ----------
    graph : WheresatGraph
        Read-only history questions, for the association search's window.
    records : stack_store.StackRecordReader
        The child's own stack record, which may name a parent pull request.
        The same reader the collection phase is handed, so both read one
        record.
    opener : collections.abc.Callable[[], WheresatGitHub] | None
        Callable that returns a forge port, or ``None`` when the caller has
        none to offer.

    """

    graph: WheresatGraph
    records: stack_store.StackRecordReader
    opener: cabc.Callable[[], WheresatGitHub] | None


@dataclasses.dataclass(frozen=True, slots=True)
class SearchBounds:
    """How much of the child's history the association search may examine.

    Attributes
    ----------
    limit : int
        Commits the search may examine, from ``--limit``.
    repository : str | None
        ``OWNER/REPOSITORY`` slug of the repository to ask about those commits,
        or ``None`` when the checkout names no GitHub repository at all. A run
        with no repository to ask has no question to put, which is a different
        thing from a question the forge could not answer.

    """

    limit: int
    repository: str | None


def answered(
    parent: ParentPullRequest | None,
    outcome: observability.Outcome,
    *,
    shared: wheresat_shared_record.SharedRecordResult = _NO_SHARED_RECORD,
) -> ParentIdentification:
    """Return an identification, recorded as ``outcome``.

    Parameters
    ----------
    parent : ParentPullRequest | None
        The parent pull request, when the ladder identified one.
    outcome : observability.Outcome
        What became of the question, as the bounded vocabulary spells it.
    shared : wheresat_shared_record.SharedRecordResult
        What the child's body claimed, when this walk read one. Only the rung
        that parsed a claim passes it, because only one rung may report a claim
        this run is going to credit.

    Returns
    -------
    ParentIdentification
        The identification, with no fault: the ladder answered.

    """
    observability.get_recorder().record(
        observability.Observation(
            operation=_PARENT_IDENTIFICATION,
            outcome=outcome,
        )
    )
    return ParentIdentification(parent=parent, shared_record=shared)


def faulted(
    reason: str,
    error_kind: observability.ErrorKind,
) -> ParentIdentification:
    """Return the identification of a question the ladder could not put.

    Parameters
    ----------
    reason : str
        Why the question went unanswered, in the operator's words.
    error_kind : observability.ErrorKind
        The bounded class of the failure.

    Returns
    -------
    ParentIdentification
        The identification, carrying the fault and no parent.

    """
    observability.get_recorder().record(
        observability.Observation(
            operation=_PARENT_IDENTIFICATION,
            outcome="unavailable",
            error_kind=error_kind,
        )
    )
    return ParentIdentification(faults=(reason,), error_kind=error_kind)
