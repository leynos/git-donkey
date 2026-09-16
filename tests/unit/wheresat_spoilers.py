"""One spoiler per gate, and the fact edits they are built from.

``wheresat_helpers`` builds the corpus: the run's question, the candidates the
evidence sources produced, the graph's answers, and the parent pull request.
This module edits it. Each ``with_*`` function returns the permissive facts with
a single named answer changed, ``changed`` judges the permissive case against
facts so edited, and each spoiler below builds the case in which one gate — and
nothing else — answers against the candidate.

``spoiled`` builds a row by gate name, so INV-4's truth table is indexed by the
same ``GateName`` values the policy dispatches on: a gate the procedure gains is
a gate the suite reports as having no row, and a gate that is renamed cannot
leave the table silently pointing at nothing.

The cases a report or a policy test needs by name — the ones whose answer is
missing rather than negative, the ones about a listing being cut short, and the
one whose range is long enough that a report abbreviates it — are in
:mod:`tests.unit.wheresat_variants`, which builds them from the edits here.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey.wheresat_records import (
    Ancestry,
    CommitRange,
    GateName,
    GateOutcome,
    GraphFacts,
)
from tests.unit.wheresat_helpers import (
    CHILD_BELOW,
    CHILD_TIP,
    FOREIGN_REPOSITORY,
    LANDED,
    LANDED_PATCH,
    OLD_BASE,
    PARENT_HEAD,
    REPLAY_RANGE,
    TARGET,
    Case,
    parent_pull_request,
    parented,
    permissive,
    permissive_facts,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def with_ancestry(key: tuple[str, str], answer: Ancestry | None) -> GraphFacts:
    """Return the permissive facts with one ancestry answer set or unasked.

    Parameters
    ----------
    key : tuple[str, str]
        The two commits the question was put to, which is how the facts index
        an ancestry answer.
    answer : Ancestry | None
        The answer to record, or ``None`` to leave the pair out of the facts
        entirely: a pair the facts do not hold is a question the run never
        asked.

    Returns
    -------
    GraphFacts
        The permissive facts, with that pair answered or unrecorded.

    """
    facts = permissive_facts()
    answers = dict(facts.ancestry)
    if answer is None:
        answers.pop(key, None)
    else:
        answers[key] = answer
    return dataclasses.replace(facts, ancestry=answers)


def with_range_contents(key: str, contents: CommitRange | None) -> GraphFacts:
    """Return the permissive facts with one listed range set or unlisted.

    Parameters
    ----------
    key : str
        Range key the listing was made for.
    contents : CommitRange | None
        The commits the range was listed with, or ``None`` to leave the range
        out of the facts: an unlisted range is one the run never listed.

    Returns
    -------
    GraphFacts
        The permissive facts, with that range listed or unlisted.

    """
    facts = permissive_facts()
    listed = _one_range(facts.range_contents, key, contents)
    return dataclasses.replace(facts, range_contents=listed)


def with_range_minus_parent(key: str, contents: CommitRange | None) -> GraphFacts:
    """Return the permissive facts with one parent-free range set or unlisted.

    Parameters
    ----------
    key : str
        Range key the listing was made for.
    contents : CommitRange | None
        The commits left once the parent's own are subtracted, or ``None`` to
        leave the question unasked.

    Returns
    -------
    GraphFacts
        The permissive facts, with that listing recorded or unrecorded.

    """
    facts = permissive_facts()
    listed = _one_range(facts.range_minus_parent, key, contents)
    return dataclasses.replace(facts, range_minus_parent=listed)


def with_landed_twins(key: str, twins: tuple[str, ...] | None) -> GraphFacts:
    """Return the permissive facts with one range's landed-content match set.

    Parameters
    ----------
    key : str
        Range key the comparison was made for.
    twins : tuple[str, ...] | None
        The commits carrying the landed commit's content, or ``None`` when the
        comparison was never made.

    Returns
    -------
    GraphFacts
        The facts, with that range compared or left uncompared.

    """
    facts = permissive_facts()
    compared = dict(facts.landed_twins)
    if twins is None:
        compared.pop(key, None)
    else:
        compared[key] = twins
    return dataclasses.replace(facts, landed_twins=compared)


def with_patch(commit: str, identifier: str | None) -> GraphFacts:
    """Return the permissive facts with one cumulative patch identifier set.

    Parameters
    ----------
    commit : str
        Commit the identifier was computed for.
    identifier : str | None
        The identifier, or ``None`` for a commit whose cumulative patch Git
        could not identify.

    Returns
    -------
    GraphFacts
        The permissive facts, with that commit's identifier set.

    """
    facts = permissive_facts()
    identifiers = dict(facts.cumulative_patch)
    identifiers[commit] = identifier
    return dataclasses.replace(facts, cumulative_patch=identifiers)


def with_recorded_from(
    commit: str | None, answer: Ancestry | None = None
) -> GraphFacts:
    """Return the permissive facts with the record's birth tip set or absent.

    Parameters
    ----------
    commit : str | None
        The child tip the record was written from, or ``None`` when the record
        names no tip at all.
    answer : Ancestry | None, optional
        Whether that tip is still on the child's history, when the run asked.

    Returns
    -------
    GraphFacts
        The facts, with the record's own history question answered or unasked.

    """
    facts = permissive_facts()
    if commit is not None and answer is not None:
        facts = with_ancestry((commit, CHILD_TIP), answer)
    return dataclasses.replace(facts, record_recorded_from=commit)


def with_parent_head(commit: str | None) -> GraphFacts:
    """Return the permissive facts with the recovered parent head set or absent.

    Parameters
    ----------
    commit : str | None
        Commit the parent's head was recovered at. ``None`` is a recovery that
        found no head at all, which the ``parent-history-intact`` gate reads as
        a parent it cannot judge rather than as a question left open.

    Returns
    -------
    GraphFacts
        Those facts, told where the parent's head is.

    """
    return dataclasses.replace(permissive_facts(), parent_head=commit)


def with_landed(commit: str | None) -> GraphFacts:
    """Return the permissive facts with the integration commit set or absent.

    Parameters
    ----------
    commit : str | None
        The commit the child's work was found integrated in, or ``None`` where
        the run found none.

    Returns
    -------
    GraphFacts
        The permissive facts, with the integration commit set or absent.

    """
    return dataclasses.replace(permissive_facts(), landed=commit)


def with_child_history(contents: CommitRange) -> GraphFacts:
    """Return the permissive facts with the child's own history relisted.

    Parameters
    ----------
    contents : CommitRange
        The child's own commits, as the listing the run is to be given.

    Returns
    -------
    GraphFacts
        The permissive facts, with the child's history relisted.

    """
    return dataclasses.replace(permissive_facts(), child_history=contents)


def changed(facts: GraphFacts) -> Case:
    """Return the permissive case judged against ``facts`` instead.

    Parameters
    ----------
    facts : GraphFacts
        The facts the case is to be judged against, in place of the permissive
        ones every other answer in it already agrees with.

    Returns
    -------
    Case
        The permissive case, re-judged against those facts.

    """
    return dataclasses.replace(permissive(), facts=facts)


def _one_range(
    listed: cabc.Mapping[str, CommitRange],
    key: str,
    contents: CommitRange | None,
) -> dict[str, CommitRange]:
    """Return a range listing with one range answered or left unlisted.

    The two range edits differ in which listing they rewrite, and in nothing
    else, so what to do with the key is decided here once: a range the caller
    supplies is recorded under it, and a range the caller leaves out is a
    question the run never put, which is the listing without the key at all.

    Parameters
    ----------
    listed : cabc.Mapping[str, CommitRange]
        Listing the edit is made to, as the permissive facts hold it.
    key : str
        Range key the listing was made for.
    contents : CommitRange | None
        The commits to record under ``key``, or ``None`` to leave the range
        out of the listing.

    Returns
    -------
    dict[str, CommitRange]
        A listing of its own with that key recorded or unrecorded.

    """
    updated = dict(listed)
    if contents is None:
        updated.pop(key, None)
    else:
        updated[key] = contents
    return updated


def _spoil_parent_identity(*, failed: bool) -> Case:
    """Return the case in which gate 1 alone answers against the candidate."""
    fetched_from = FOREIGN_REPOSITORY if failed else None
    case = parented()
    return dataclasses.replace(
        case,
        parent=parent_pull_request(head_fetched_from=fetched_from),
    )


def _spoil_parent_merged(*, failed: bool) -> Case:
    """Return the case in which gate 2 alone answers against the candidate."""
    case = parented()
    if failed:
        return dataclasses.replace(case, parent=parent_pull_request(merged=False))
    return dataclasses.replace(case, parent=parent_pull_request(merged_at=None))


def _spoil_landed_reachable(*, failed: bool) -> Case:
    """Return the case in which gate 3 alone answers against the candidate.

    The case consults a parent pull request, because that is what makes gate 3
    applicable at all: a parentless run leaves the integration commit out of its
    conjunction rather than judging it.

    Returns
    -------
    Case
        The case in which gate 3 alone answers against the candidate, or alone
        goes unanswered.

    """
    answer = Ancestry.NOT_ANCESTOR if failed else None
    case = parented()
    return dataclasses.replace(case, facts=with_ancestry((LANDED, TARGET), answer))


def _spoil_boundary_ancestor(*, failed: bool) -> Case:
    """Return the case in which gate 4 alone answers against the candidate."""
    answer = Ancestry.NOT_ANCESTOR if failed else None
    return changed(with_ancestry((OLD_BASE, CHILD_TIP), answer))


def _spoil_replay_range(*, failed: bool) -> Case:
    """Return the case in which gate 5 alone answers against the candidate."""
    listed = CommitRange(()) if failed else None
    return changed(with_range_contents(REPLAY_RANGE, listed))


def _spoil_parent_history(*, failed: bool) -> Case:
    """Return the case in which gate 6 alone answers against the candidate."""
    answer = Ancestry.NOT_ANCESTOR if failed else None
    return changed(with_ancestry((OLD_BASE, PARENT_HEAD), answer))


def _spoil_excludes_landed(*, failed: bool) -> Case:
    """Return the case in which gate 7 alone answers against the candidate."""
    if failed:
        return changed(with_patch(OLD_BASE, LANDED_PATCH))
    return changed(with_range_minus_parent(REPLAY_RANGE, None))


def _spoil_record_superseded(*, failed: bool) -> Case:
    """Return the case in which gate 8 alone answers against the candidate."""
    if failed:
        return changed(
            with_recorded_from(CHILD_BELOW, Ancestry.NOT_ANCESTOR),
        )
    return changed(with_recorded_from(None))


class _Spoiler(typ.Protocol):
    """What a spoiler is: one gate's case, built from the outcome it takes.

    A protocol rather than a ``Callable`` alias so the table below is checked
    rather than merely declared: the keyword-only ``failed`` is the whole of
    what ``spoiled`` passes, and a spoiler that took it positionally or not at
    all would be registered as one without the table noticing.
    """

    def __call__(self, *, failed: bool) -> Case:
        """Return the case in which this gate is the one that answers."""


# One spoiler per gate, each taking whether the gate is to fail rather than go
# unanswered. Indexing them by name is what makes the truth table complete: a
# gate with no spoiler raises here rather than being skipped.
_SPOILERS: cabc.Mapping[GateName, _Spoiler] = {
    GateName.PARENT_IDENTITY_MATCHES: _spoil_parent_identity,
    GateName.PARENT_MERGED: _spoil_parent_merged,
    GateName.LANDED_REACHABLE_FROM_TARGET: _spoil_landed_reachable,
    GateName.BOUNDARY_IS_ANCESTOR_OF_CHILD: _spoil_boundary_ancestor,
    GateName.REPLAY_RANGE_NON_EMPTY: _spoil_replay_range,
    GateName.PARENT_HISTORY_INTACT: _spoil_parent_history,
    GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK: _spoil_excludes_landed,
    GateName.RECORD_NOT_SUPERSEDED: _spoil_record_superseded,
}


def spoiled(gate: GateName, outcome: GateOutcome) -> Case:
    """Return the case in which ``gate`` alone takes ``outcome``.

    Parameters
    ----------
    gate : GateName
        Gate the case is built around.
    outcome : GateOutcome
        ``FAILED`` for a gate that answers against the candidate, or
        ``INDETERMINATE`` for one left without an answer.

    Returns
    -------
    Case
        The permissive case with the one answer ``gate`` reads changed, so
        every other gate passes and the gate under test is the only one that
        did not.

    Raises
    ------
    AssertionError
        If ``outcome`` is ``PASSED``, which names no way to spoil a gate: a
        case asked for it would otherwise be built as the unanswered one and
        read as coverage of the wrong answer.

    """
    match outcome:
        case GateOutcome.FAILED:
            return _SPOILERS[gate](failed=True)
        case GateOutcome.INDETERMINATE:
            return _SPOILERS[gate](failed=False)
        case _:
            msg = f"a gate is spoiled as failed or unanswered, not {outcome}"
            raise AssertionError(msg)
