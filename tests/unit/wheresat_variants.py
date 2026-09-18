"""The named variants of the permissive case.

``wheresat_helpers`` builds the corpus: the run's question, the candidates the
evidence sources produced, the graph's answers, and the parent pull request.
``wheresat_spoilers`` edits it, one gate at a time; the functions here are the
cases a report or a policy test names directly — the ones whose answer is
missing rather than negative, the ones about a listing being cut short, and the
one whose range is long enough that a report abbreviates it.

Every function here returns the permissive case — or the parent-consulting one
— with a single named answer changed, so a test that means "this answer, and
nothing else, is what said no" builds exactly that. The edits themselves are
not repeated here: each case is built from the one ``wheresat_spoilers``
provides for the answer it is about.
"""

from __future__ import annotations

import dataclasses

from git_donkey.wheresat_records import (
    COMMIT_ABBREVIATION,
    Ancestry,
    Candidate,
    CommitRange,
)
from tests.unit.wheresat_helpers import (
    CHILD_BELOW,
    CHILD_HISTORY,
    CHILD_WORK,
    OLD_BASE,
    REPLAY_RANGE,
    Case,
    parented,
)
from tests.unit.wheresat_spoilers import (
    changed,
    with_child_history,
    with_landed,
    with_landed_twins,
    with_parent_head,
    with_patch,
    with_range_contents,
    with_range_minus_parent,
    with_recorded_from,
)


def parented_without_head() -> Case:
    """Return the parent-consulting case with no parent head recovered.

    Returns
    -------
    Case
        The case whose gates about the parent apply — the run set out to consult
        one — and whose questions about its history have nothing to read.

    """
    return dataclasses.replace(parented(), facts=with_parent_head(None))


def parented_without_landed() -> Case:
    """Return the parent-consulting case with no integration commit resolved.

    Returns
    -------
    Case
        The case whose gates about the parent apply and whose landed-work
        questions cannot be answered, which is what a parent merged by a
        strategy the run cannot resolve looks like.

    """
    return dataclasses.replace(parented(), facts=with_landed(None))


def without_parent_head() -> Case:
    """Return the parentless case with no parent head recovered.

    Returns
    -------
    Case
        The case in which no gate about a parent's history applies, which is
        what a local run whose parent left no tombstone and no remote-tracking
        ref looks like.

    """
    return changed(with_parent_head(None))


def without_landed() -> Case:
    """Return the parentless case with no integration commit resolved.

    Returns
    -------
    Case
        The case in which the parent's history is still judged — its head was
        recovered — and nothing about an integration is.

    """
    return changed(with_landed(None))


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
    return changed(with_landed_twins(REPLAY_RANGE, (CHILD_BELOW,)))


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
    return changed(with_landed_twins(REPLAY_RANGE, None))


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
    return changed(with_recorded_from(CHILD_BELOW, Ancestry.NOT_ANCESTOR))


def blank_patch_identifier() -> Case:
    """Return the permissive case whose cumulative patch identifier is blank.

    Returns
    -------
    Case
        The case whose patch pipeline produced no identifier, which is what a
        configured external diff driver makes it do for every range.

    """
    return changed(with_patch(OLD_BASE, ""))


def truncated_replay_range() -> Case:
    """Return the permissive case whose replay range was cut short.

    Returns
    -------
    Case
        The case whose established result has to report that the history it
        partitioned does not span the whole range.

    """
    listed = CommitRange(CHILD_WORK, truncated=True)
    return changed(with_range_contents(REPLAY_RANGE, listed))


def truncated_history() -> Case:
    """Return the permissive case whose child history was cut short.

    Returns
    -------
    Case
        The case whose excluded commits are only the ones the run listed.

    """
    return changed(with_child_history(CommitRange(CHILD_HISTORY, truncated=True)))


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
    facts = with_range_minus_parent(REPLAY_RANGE, listed)
    return changed(
        dataclasses.replace(
            facts,
            range_contents={**facts.range_contents, REPLAY_RANGE: listed},
            child_history=listed,
        )
    )
