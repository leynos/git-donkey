"""The variants of the permissive case, one row per gate and a few beside.

``wheresat_helpers`` builds the corpus: the run's question, the candidates the
evidence sources produced, the graph's answers, and the parent pull request.
This module edits it. Every function here returns the permissive case — or the
parent-consulting one — with a single named answer changed, so a test that means
"this answer, and nothing else, is what said no" builds exactly that.

``spoiled`` builds a row by gate name, so INV-4's truth table is indexed by the
same ``GateName`` values the policy dispatches on: a gate the procedure gains is
a gate the suite reports as having no row, and a gate that is renamed cannot
leave the table silently pointing at nothing.

The rest are the cases a report or a policy test needs by name: the ones whose
answer is missing rather than negative, the ones about a listing being cut
short, and the one whose range is long enough that a report abbreviates it.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey.wheresat_records import (
    COMMIT_ABBREVIATION,
    Ancestry,
    Candidate,
    CommitRange,
    GateName,
    GateOutcome,
    GraphFacts,
)
from tests.unit.wheresat_helpers import (
    CHILD_BELOW,
    CHILD_HISTORY,
    CHILD_TIP,
    CHILD_WORK,
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


def _with_ancestry(key: tuple[str, str], answer: Ancestry | None) -> GraphFacts:
    """Return the permissive facts with one ancestry answer set or unasked."""
    facts = permissive_facts()
    answers = dict(facts.ancestry)
    if answer is None:
        del answers[key]
    else:
        answers[key] = answer
    return dataclasses.replace(facts, ancestry=answers)


def _with_range_contents(key: str, contents: CommitRange | None) -> GraphFacts:
    """Return the permissive facts with one listed range set or unlisted."""
    facts = permissive_facts()
    listed = dict(facts.range_contents)
    if contents is None:
        del listed[key]
    else:
        listed[key] = contents
    return dataclasses.replace(facts, range_contents=listed)


def _with_range_minus_parent(key: str, contents: CommitRange | None) -> GraphFacts:
    """Return the permissive facts with one parent-free range set or unlisted."""
    facts = permissive_facts()
    listed = dict(facts.range_minus_parent)
    if contents is None:
        del listed[key]
    else:
        listed[key] = contents
    return dataclasses.replace(facts, range_minus_parent=listed)


def _with_landed_twins(key: str, twins: tuple[str, ...] | None) -> GraphFacts:
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
        del compared[key]
    else:
        compared[key] = twins
    return dataclasses.replace(facts, landed_twins=compared)


def _with_patch(commit: str, identifier: str | None) -> GraphFacts:
    """Return the permissive facts with one cumulative patch identifier set."""
    facts = permissive_facts()
    identifiers = dict(facts.cumulative_patch)
    identifiers[commit] = identifier
    return dataclasses.replace(facts, cumulative_patch=identifiers)


def _with_recorded_from(
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
        facts = _with_ancestry((commit, CHILD_TIP), answer)
    return dataclasses.replace(facts, record_recorded_from=commit)


def _with_parent_head(commit: str | None) -> GraphFacts:
    """Return the permissive facts with the recovered parent head set or absent."""
    return dataclasses.replace(permissive_facts(), parent_head=commit)


def _with_landed(commit: str | None) -> GraphFacts:
    """Return the permissive facts with the integration commit set or absent."""
    return dataclasses.replace(permissive_facts(), landed=commit)


def _with_child_history(contents: CommitRange) -> GraphFacts:
    """Return the permissive facts with the child's own history relisted."""
    return dataclasses.replace(permissive_facts(), child_history=contents)


def _changed(facts: GraphFacts) -> Case:
    """Return the permissive case judged against ``facts`` instead."""
    return dataclasses.replace(permissive(), facts=facts)


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
    return dataclasses.replace(case, facts=_with_ancestry((LANDED, TARGET), answer))


def _spoil_boundary_ancestor(*, failed: bool) -> Case:
    """Return the case in which gate 4 alone answers against the candidate."""
    answer = Ancestry.NOT_ANCESTOR if failed else None
    return _changed(_with_ancestry((OLD_BASE, CHILD_TIP), answer))


def _spoil_replay_range(*, failed: bool) -> Case:
    """Return the case in which gate 5 alone answers against the candidate."""
    listed = CommitRange(()) if failed else None
    return _changed(_with_range_contents(REPLAY_RANGE, listed))


def _spoil_parent_history(*, failed: bool) -> Case:
    """Return the case in which gate 6 alone answers against the candidate."""
    answer = Ancestry.NOT_ANCESTOR if failed else None
    return _changed(_with_ancestry((OLD_BASE, PARENT_HEAD), answer))


def _spoil_excludes_landed(*, failed: bool) -> Case:
    """Return the case in which gate 7 alone answers against the candidate."""
    if failed:
        return _changed(_with_patch(OLD_BASE, LANDED_PATCH))
    return _changed(_with_range_minus_parent(REPLAY_RANGE, None))


def _spoil_record_superseded(*, failed: bool) -> Case:
    """Return the case in which gate 8 alone answers against the candidate."""
    if failed:
        return _changed(
            _with_recorded_from(CHILD_BELOW, Ancestry.NOT_ANCESTOR),
        )
    return _changed(_with_recorded_from(None))


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
    if outcome is GateOutcome.FAILED:
        return _SPOILERS[gate](failed=True)
    if outcome is GateOutcome.INDETERMINATE:
        return _SPOILERS[gate](failed=False)
    msg = f"a gate is spoiled as failed or unanswered, not {outcome}"
    raise AssertionError(msg)


def parented_without_head() -> Case:
    """Return the parent-consulting case with no parent head recovered.

    Returns
    -------
    Case
        The case whose gates about the parent apply — the run set out to consult
        one — and whose questions about its history have nothing to read.

    """
    return dataclasses.replace(parented(), facts=_with_parent_head(None))


def parented_without_landed() -> Case:
    """Return the parent-consulting case with no integration commit resolved.

    Returns
    -------
    Case
        The case whose gates about the parent apply and whose landed-work
        questions cannot be answered, which is what a parent merged by a
        strategy the run cannot resolve looks like.

    """
    return dataclasses.replace(parented(), facts=_with_landed(None))


def without_parent_head() -> Case:
    """Return the parentless case with no parent head recovered.

    Returns
    -------
    Case
        The case in which no gate about a parent's history applies, which is
        what a local run whose parent left no tombstone and no remote-tracking
        ref looks like.

    """
    return _changed(_with_parent_head(None))


def without_landed() -> Case:
    """Return the parentless case with no integration commit resolved.

    Returns
    -------
    Case
        The case in which the parent's history is still judged — its head was
        recovered — and nothing about an integration is.

    """
    return _changed(_with_landed(None))


def range_carrying_landed_content() -> Case:
    """Return the case whose replay range holds the content the parent landed.

    The case is what a parent that was rewritten before it was merged leaves
    behind: the parent head no longer reaches the work the child inherited, so
    the clause that reads the head's reach has nothing to say, and the range's
    content says it anyway.

    Returns
    -------
    Case
        The permissive case, with the replay range holding one commit whose
        tree is the landed commit's.

    """
    return _changed(_with_landed_twins(REPLAY_RANGE, (CHILD_BELOW,)))


def range_never_compared_with_the_landed_content() -> Case:
    """Return the case whose replay range was never compared with the landed tree.

    A comparison can be abandoned — a tree Git will not read, a fault the run
    reports — and the range is then left without an answer. The distinction
    matters: an empty answer is the range being read and holding no such
    commit, and a missing one is the question never being answered, which no
    refusal may stand on.

    Returns
    -------
    Case
        The permissive case, with the comparison never made.

    """
    return _changed(_with_landed_twins(REPLAY_RANGE, None))


def short(commit: str) -> str:
    """Return ``commit`` as a reason or a report abbreviates it.

    Parameters
    ----------
    commit : str
        Full object ID.

    Returns
    -------
    str
        The abbreviation a report's reader is expected to paste into Git.

    """
    return commit[:COMMIT_ABBREVIATION]


def unconsulted_parent() -> Case:
    """Return the case whose run named a parent pull request it could not read.

    Returns
    -------
    Case
        The case whose gates about the parent apply — the run set out to
        consult one — and go unanswered, since nothing resolved it.

    """
    return dataclasses.replace(parented(), parent=None)


def with_candidates(case: Case, *candidates: Candidate) -> Case:
    """Return ``case`` judged against ``candidates`` instead of its own.

    Parameters
    ----------
    case : Case
        The run's question and every answer it reads.
    *candidates : Candidate
        Candidates the evidence sources produced, in any order.

    Returns
    -------
    Case
        The case, with the candidates the assessment is to choose between.

    """
    return dataclasses.replace(case, candidates=tuple(candidates))


def record_superseded() -> Case:
    """Return the permissive case whose record a later integration superseded.

    Returns
    -------
    Case
        The case whose record was written from a commit the child has since
        discarded, which is the one gate 8 exists to refuse.

    """
    return _changed(_with_recorded_from(CHILD_BELOW, Ancestry.NOT_ANCESTOR))


def blank_patch_identifier() -> Case:
    """Return the permissive case whose cumulative patch identifier is blank.

    Returns
    -------
    Case
        The case whose patch pipeline produced no identifier, which is what a
        configured external diff driver makes it do for every range.

    """
    return _changed(_with_patch(OLD_BASE, ""))


def truncated_replay_range() -> Case:
    """Return the permissive case whose replay range was cut short.

    Returns
    -------
    Case
        The case whose established result has to report that the history it
        partitioned does not span the whole range.

    """
    listed = CommitRange(CHILD_WORK, truncated=True)
    return _changed(_with_range_contents(REPLAY_RANGE, listed))


def truncated_history() -> Case:
    """Return the permissive case whose child history was cut short.

    Returns
    -------
    Case
        The case whose excluded commits are only the ones the run listed.

    """
    return _changed(_with_child_history(CommitRange(CHILD_HISTORY, truncated=True)))


def long_replay_range(count: int) -> Case:
    """Return the permissive case whose ranges hold ``count`` commits.

    Parameters
    ----------
    count : int
        How many commits the run listed above the boundary.

    Returns
    -------
    Case
        The case whose partition is longer than a report lists in full, so the
        listing has to say how many commits it withheld. The ranges are complete
        — nothing here was cut short — which is what tells this case apart from
        the two above: the commits are missing from the *rendering*, not from the
        run's answer.

    """
    # The leading digits are the number of the commit, which is also all of it a
    # report prints, so the listing shows one distinguishable commit per line.
    # The fixed commits above are one digit repeated, so a generated commit
    # cannot abbreviate to one of them and read as though the gap a truncation
    # tail reports were a commit the report already named.
    listed = CommitRange(
        tuple(f"{index:07d}{'a' * 33}" for index in range(10, 10 + count)),
    )
    facts = _with_range_minus_parent(REPLAY_RANGE, listed)
    return _changed(
        dataclasses.replace(
            facts,
            range_contents={**facts.range_contents, REPLAY_RANGE: listed},
            child_history=listed,
        )
    )
