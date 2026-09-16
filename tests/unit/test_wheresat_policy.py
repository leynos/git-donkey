"""Tests for the ``git wheresat`` assessment and the policy it applies.

The gate table itself — one row per gate that answered, one per gate that went
unanswered — is pinned in ``test_wheresat_gates``. What is here is what a run
does with the gates it collected: the tiers and exit statuses the decision
record fixes, what may establish a boundary and what may not, which of two
candidates that could both serve is preferred over the other, and that the
evidence a run reports partitions the child's history.
"""

from __future__ import annotations

import pytest

from git_donkey.wheresat_policy import (
    apply_collection_faults,
    may_establish,
)
from git_donkey.wheresat_records import (
    EXIT_CODES,
    EXIT_USAGE,
    TIERS,
    DerivedCandidate,
    Established,
    Establishing,
    EvidenceKind,
    EvidenceTier,
    GateName,
    Indeterminate,
    Unresolved,
)
from tests.unit.wheresat_candidates import (
    ANCHOR_SOURCE,
    FORK_POINT_SOURCE,
    MERGE_BASE_SOURCE,
    PARENT_MERGE_BASE_SOURCE,
    PATCH_SOURCE,
    RECORD_SOURCE,
    SHARED_SOURCE,
    TREE_SOURCE,
    attested,
    derived,
    inferred,
)
from tests.unit.wheresat_helpers import (
    CHILD_BELOW,
    CHILD_HISTORY,
    CHILD_WORK,
    INHERITED,
    OLD_BASE,
    OTHER_BASE,
    REQUIRED_SOURCES,
    TARGET,
    TRUNK,
    assessment_of,
    failed_gates,
    permissive,
    undecided_gates,
)
from tests.unit.wheresat_variants import (
    blank_patch_identifier,
    parented_without_head,
    parented_without_landed,
    range_carrying_landed_content,
    range_never_compared_with_the_landed_content,
    record_superseded,
    short,
    truncated_history,
    truncated_replay_range,
    unconsulted_parent,
    with_candidates,
    without_landed,
    without_parent_head,
)

# The tier of every evidence kind, as the boundary decision record fixes it.
_EXPECTED_TIERS = {
    EvidenceKind.STACK_RECORD_BIRTH: EvidenceTier.ATTESTED,
    EvidenceKind.STACK_RECORD_REFRESHED: EvidenceTier.ATTESTED,
    EvidenceKind.SHARED_RECORD: EvidenceTier.ATTESTED,
    EvidenceKind.PULL_REQUEST_HEAD: EvidenceTier.ATTESTED,
    EvidenceKind.MERGE_BASE: EvidenceTier.DERIVED,
    EvidenceKind.FORK_POINT: EvidenceTier.DERIVED,
    EvidenceKind.TREE_IDENTITY: EvidenceTier.INFERRED,
    EvidenceKind.PATCH_IDENTITY: EvidenceTier.INFERRED,
}


def test_the_evidence_tiers_match_the_boundary_decision_record() -> None:
    """Which kinds of evidence may establish a boundary is decided in one table."""
    assert TIERS == _EXPECTED_TIERS, (
        "a kind whose tier changes changes what may establish a boundary"
    )
    assert set(TIERS) == set(EvidenceKind), (
        "every evidence kind has a tier, and no tier is stated for a kind that "
        "cannot occur"
    )


def test_the_exit_status_table_matches_the_decision_record() -> None:
    """Each verdict has its own status, and no verdict shares the usage status."""
    assert {Established: 0, Unresolved: 1, Indeterminate: 3} == EXIT_CODES, (
        "the verdict is what the exit status reports"
    )
    assert EXIT_USAGE not in set(EXIT_CODES.values()), (
        "a usage or credential failure is not one of the verdicts"
    )


def test_one_attested_candidate_may_establish() -> None:
    """A record or a pull request head names the boundary by a deliberate act."""
    assert may_establish((attested(OLD_BASE),)), (
        "one attested candidate is enough to serve as the boundary"
    )


def test_a_lone_derived_candidate_may_not_establish() -> None:
    """Computed evidence alone is a guess, however plausible it looks."""
    assert not may_establish((derived(OLD_BASE),)), (
        "INV-2b: the merge base or fork point alone never establishes"
    )


def test_two_independent_derived_sources_may_establish() -> None:
    """Two sources that disagree about how to be wrong corroborate each other."""
    support = (
        derived(OLD_BASE, EvidenceKind.MERGE_BASE),
        derived(
            OLD_BASE,
            EvidenceKind.FORK_POINT,
            source=FORK_POINT_SOURCE,
        ),
    )

    assert len({one.kind for one in support}) >= REQUIRED_SOURCES, (
        "the corpus is the case with independent sources"
    )
    assert may_establish(support), (
        "derived evidence establishes when independent sources agree"
    )


def test_two_answers_from_one_rung_may_not_establish() -> None:
    """One method asked twice is not corroboration, however the questions differ.

    The merge-base rung puts two questions — one about the target, one about the
    parent's head — and it is the second that a rebased child leaves answering
    at all. When the child's line was rewritten, its old parent tip becomes
    unreachable and both questions collapse onto the same trunk commit, so a run
    that counted the two answers as two sources would establish that commit and
    offer to replay the parent's already-landed work from it. What makes them
    one source is that they are one method, and it is counted by the kind rather
    than by the label each question is reported under.
    """
    support = (
        derived(OLD_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
        derived(
            OLD_BASE,
            EvidenceKind.MERGE_BASE,
            source=PARENT_MERGE_BASE_SOURCE,
        ),
    )

    assert len({one.source for one in support}) == REQUIRED_SOURCES, (
        "the corpus is the case whose two answers are worded differently"
    )
    assert len({one.kind for one in support}) == 1, (
        "and they are one method of observation between them"
    )
    assert not may_establish(support), (
        "two answers from one rung are one answer, however they are labelled"
    )


def test_the_same_derived_observation_twice_may_not_establish() -> None:
    """The same answer reported twice is not corroboration either."""
    support = (
        derived(OLD_BASE, EvidenceKind.MERGE_BASE),
        derived(OLD_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
    )

    assert len({one.source for one in support}) == 1, (
        "the corpus is the case with a single source string"
    )
    assert not may_establish(support), "one source read twice is one source"


def test_inferred_evidence_never_establishes() -> None:
    """Content that matches names two commits more often than it names one."""
    support = (
        inferred(OLD_BASE, EvidenceKind.TREE_IDENTITY, source=TREE_SOURCE),
        inferred(OLD_BASE, EvidenceKind.PATCH_IDENTITY, source=PATCH_SOURCE),
    )

    assert not may_establish(support), (
        "INV-2: inferred evidence never establishes a boundary, whatever "
        "corroborates it"
    )


def test_inferred_evidence_does_not_disqualify_an_attested_answer() -> None:
    """A weak statement beside a strong one is ignored rather than fatal."""
    support = (attested(OLD_BASE), inferred(OLD_BASE))

    assert may_establish(support), (
        "one attested candidate establishes; inferred evidence neither adds to "
        "nor subtracts from the answer"
    )


def test_a_named_parent_that_could_not_be_resolved_refuses_to_establish() -> None:
    """A parent the run set out to consult and could not is not absent.

    Applicability is decided from what the run was asked, so the gates about the
    parent stay in the conjunction and go unanswered; the boundary is then not
    established on the questions that were never put.
    """
    assessment = assessment_of(unconsulted_parent())

    assert isinstance(assessment, Indeterminate), (
        "a parent the run named but could not resolve must not establish"
    )
    assert undecided_gates(assessment) == (
        GateName.PARENT_IDENTITY_MATCHES,
        GateName.PARENT_MERGED,
    ), "the gates about the parent are the ones that could not be answered"
    assert not failed_gates(assessment), (
        "nothing answered against the candidate: the parent simply was not read"
    )


def test_a_record_a_later_integration_superseded_is_demoted_not_discarded() -> None:
    """Gate 8 refuses the record's claim, and corroboration can still serve.

    The record is the only deliberate statement in the repository, so refusing
    its claim must not throw it away: its commit becomes one derived candidate
    among the others, which is why a second source can still carry the boundary
    and why nothing attested remains in the support.
    """
    case = with_candidates(
        record_superseded(),
        attested(OLD_BASE, EvidenceKind.STACK_RECORD_BIRTH),
        derived(OLD_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
    )
    assessment = assessment_of(case)

    assert isinstance(assessment, Established), (
        f"the fork point corroborates the demoted record's commit: {assessment!r}"
    )
    assert assessment.old_base == OLD_BASE, (
        "the demoted record's commit is still the commit the evidence names"
    )
    assert all(isinstance(one, DerivedCandidate) for one in assessment.support), (
        "the superseded record's attested claim is demoted, so nothing attested "
        "carries the boundary"
    )
    assert {one.source for one in assessment.support} == {
        RECORD_SOURCE,
        FORK_POINT_SOURCE,
    }, "both the demoted candidate and its corroborator are reported"
    assert not failed_gates(assessment), (
        "the gates an established boundary reports are the ones that carried it"
    )


def test_a_demotion_that_leaves_only_derived_evidence_is_not_a_refusal() -> None:
    """A record gate 8 demoted is read as derived evidence, whichever it was.

    Gate 8 refuses a record's attested claim without refusing the commit the
    record named, so what decides the boundary is INV-2b's rule over the
    evidence the demotion left. Two sources naming the same commit is what that
    rule asks for, so the boundary still establishes — as derived evidence. The
    report then has no supporter whose every applicable gate passed to read its
    conjunction from, and gate 8 is reported as what it became: a gate of an
    attested claim, which the evidence carrying this boundary no longer makes.
    Reading it as failed would print a refusal beside a boundary that holds;
    reading it as passed would say the record's claim was accepted.
    """
    case = with_candidates(
        record_superseded(),
        attested(OLD_BASE, EvidenceKind.STACK_RECORD_BIRTH),
        attested(
            OLD_BASE,
            EvidenceKind.STACK_RECORD_REFRESHED,
            source=ANCHOR_SOURCE,
        ),
    )
    assessment = assessment_of(case)

    assert isinstance(assessment, Established), (
        f"two sources named the same boundary: {assessment!r}"
    )
    assert all(isinstance(one, DerivedCandidate) for one in assessment.support), (
        "no attested claim survived gate 8, so nothing attested carries it"
    )
    assert {one.source for one in assessment.support} == {
        RECORD_SOURCE,
        ANCHOR_SOURCE,
    }, "both readings are reported as sources that carry the boundary"
    assert not failed_gates(assessment), (
        "the established result reports no applicable refusal at all"
    )
    superseded = [
        gate for gate in assessment.gates if gate.name is GateName.RECORD_NOT_SUPERSEDED
    ]
    assert len(superseded) == 1, (
        "the gates an established result reports are one candidate's — the one "
        f"that carried the boundary — not every supporter's: {superseded}"
    )
    assert not superseded[0].applicable, (
        "gate 8 no longer applies to the evidence the demotion left, so the "
        f"reported conjunction has no refusal in it: {superseded[0]}"
    )


def test_a_blank_patch_identifier_is_not_an_agreement() -> None:
    """A patch pipeline that produced nothing compared nothing.

    ``git patch-id`` prints nothing for a range that produces no patch, and a
    configured external diff driver makes it print nothing for every range. The
    missing identifier has to read as a question the run could not answer: a
    false agreement would refuse a sound boundary, and a false difference would
    wave through the landed work this gate exists to catch.
    """
    assessment = assessment_of(blank_patch_identifier())

    assert isinstance(assessment, Indeterminate), (
        "two blank identifiers are not an agreement, and reading them as a "
        "difference is no better"
    )
    assert undecided_gates(assessment) == (
        GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,
    ), "the patch clause is the half of the gate that went unanswered"


def test_a_range_carrying_the_landed_content_is_refused() -> None:
    """The clause a rewritten parent leaves standing, and why it must.

    A parent rewritten before it was merged takes the parent head's reach away
    from the work the child inherited, so the clause that reads what the range
    still holds beside the head has nothing to say. The content does not move
    with an amend: the merge lands it, and the range that replays it would
    apply it a second time, which is what this clause refuses.
    """
    assessment = assessment_of(range_carrying_landed_content())

    assert isinstance(assessment, Unresolved), (
        "a range holding the content the parent already landed must be refused, "
        f"not reported as {assessment!r}"
    )
    assert failed_gates(assessment) == (GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,), (
        "the refusal comes from the landed-work gate, and no other: "
        f"{failed_gates(assessment)}"
    )
    reason = next(
        gate.detail
        for gate in assessment.gates
        if gate.name is GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK
    )
    assert short(CHILD_BELOW) in reason, (
        f"the reason names the commit carrying that content: {reason}"
    )


def test_a_range_never_compared_is_not_a_range_with_no_match() -> None:
    """A comparison that was not made is not a comparison that found nothing.

    An empty answer and a missing one are the difference between "the range
    holds none of the landed content" and "nothing is known about what it
    holds", and reading the second as the first is how a boundary is
    established on a question that was never answered.
    """
    assessment = assessment_of(range_never_compared_with_the_landed_content())

    assert isinstance(assessment, Indeterminate), (
        "the range's content was never compared, so the boundary cannot be "
        f"established, yet the run reported {assessment!r}"
    )
    assert undecided_gates(assessment) == (
        GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,
    ), "the clause that reads the comparison is the one left unanswered"


def test_a_parent_a_run_consulted_leaves_its_history_gates_unanswered() -> None:
    """A parent the run set out to consult is judged on what it could not read.

    The gates about a parent's history are applicable because the run asked
    about that parent, not because an answer arrived: a run that consulted a
    parent and recovered no head has left a question unanswered, and a boundary
    cannot be established out of the record alone while a question about the
    parent the record names is still open.
    """
    assessment = assessment_of(parented_without_head())

    assert isinstance(assessment, Indeterminate), (
        "the parent's history cannot be judged without its head"
    )
    assert set(undecided_gates(assessment)) == {
        GateName.PARENT_HISTORY_INTACT,
        GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,
    }, "the gates that read the parent head are the ones left unanswered"


def test_a_parent_a_run_consulted_leaves_its_landed_work_gates_unanswered() -> None:
    """An integration the run cannot resolve is a question, not a yes."""
    assessment = assessment_of(parented_without_landed())

    assert isinstance(assessment, Indeterminate), (
        "landed work cannot be excluded without naming the commit it landed in"
    )
    assert set(undecided_gates(assessment)) == {
        GateName.LANDED_REACHABLE_FROM_TARGET,
        GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,
    }, "the gates that read the integration commit are the ones left unanswered"


def test_a_run_that_named_no_parent_leaves_the_parent_gates_out() -> None:
    """A local run is judged on the questions it put, not on ones it did not.

    This is what makes the local-only answer possible: with no parent pull
    request, no parent head, and no integration in play, the gates about a
    parent are not applicable — still reported, so the count stays visible, but
    outside the conjunction — and the record's boundary can be established.
    """
    assessment = assessment_of(without_parent_head())

    assert isinstance(assessment, Established), (
        "a parentless run establishes from the evidence it has, rather than "
        "refusing over questions it never put"
    )
    assert {gate.name for gate in assessment.gates if not gate.applicable} == {
        GateName.PARENT_IDENTITY_MATCHES,
        GateName.PARENT_MERGED,
        GateName.LANDED_REACHABLE_FROM_TARGET,
        GateName.PARENT_HISTORY_INTACT,
        GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,
    }, "the gates about the parent that left nothing behind are the ones left out"


def test_a_parent_head_that_survives_still_judges_the_boundary() -> None:
    """A recovered head is checked against, even with no integration in play.

    A tombstone is enough to judge the boundary's place on the parent's
    history, and it is why gate 6 applies to a local run whose parent branch is
    gone: the record says where the child was cut from, and the tombstone says
    what the parent was, so the two can be read against each other without a
    forge.
    """
    assessment = assessment_of(without_landed())

    assert isinstance(assessment, Established), (
        "the parent's head is recovered, so the boundary is judged against it"
    )
    assert GateName.PARENT_HISTORY_INTACT not in {
        gate.name for gate in assessment.gates if not gate.applicable
    }, "the parent-history gate is applicable whenever a head is known"
    assert {gate.name for gate in assessment.gates if not gate.applicable} == {
        GateName.PARENT_IDENTITY_MATCHES,
        GateName.PARENT_MERGED,
        GateName.LANDED_REACHABLE_FROM_TARGET,
        GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,
    }, "and the gates about a forge and an integration are the ones left out"


def test_two_commits_that_could_both_serve_are_refused_rather_than_chosen() -> None:
    """An ambiguity is a refusal: the run does not choose the first candidate."""
    case = with_candidates(
        permissive(),
        attested(OLD_BASE, EvidenceKind.STACK_RECORD_BIRTH),
        attested(OTHER_BASE, EvidenceKind.SHARED_RECORD, source=SHARED_SOURCE),
    )
    assessment = assessment_of(case)

    assert isinstance(assessment, Unresolved), (
        "two commits that both clear every gate are an ambiguity, not an answer"
    )
    assert len(assessment.reasons) == 1, "one refusal states the ambiguity"
    reason = assessment.reasons[0]
    assert short(OLD_BASE) in reason, (
        f"the refusal must name the record's boundary, {short(OLD_BASE)}: {reason}"
    )
    assert short(OTHER_BASE) in reason, (
        f"the refusal must name the rival boundary, {short(OTHER_BASE)}, rather "
        f"than choosing between them: {reason}"
    )


def test_a_record_outranks_computed_evidence_naming_another_commit() -> None:
    """A deliberate statement answers over history that was computed for.

    This is the shape ``git wheresat`` exists for: the child was cut from a
    parent branch whose tip the record names, and the merge base with the trunk
    — corroborated by the fork point, so a genuine rival rather than a lone
    derived candidate — names the commit the parent itself started from. Both
    clear every gate the run can ask locally, and the record is the precedent
    ADR-005 ranks first, so the boundary is the record's commit rather than a
    refusal the user cannot act on.
    """
    case = with_candidates(
        permissive(),
        attested(OLD_BASE, EvidenceKind.STACK_RECORD_BIRTH),
        derived(OTHER_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
        derived(OTHER_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
    )
    assessment = assessment_of(case)

    assert isinstance(assessment, Established), (
        "the record answers where the computed evidence would only tie with it"
    )
    assert assessment.old_base == OLD_BASE, (
        "the boundary is the commit the deliberate statement named"
    )
    assert {one.commit for one in assessment.support} == {OLD_BASE}, (
        "the record's commit is the only commit that carried the boundary"
    )
    assert {one.source for one in assessment.support} == {RECORD_SOURCE}, (
        "and the record is the only source that named it"
    )


def test_two_computed_boundaries_are_still_an_ambiguity() -> None:
    """Precedence decides between ranks, and never between equals.

    With no deliberate statement in the corpus, two corroborated commits are as
    close to an answer as the evidence gets, and the run refuses: both commits
    are named by computations ``TIERS`` classes alike, so nothing ranks one of
    them above the other.
    """
    case = with_candidates(
        permissive(),
        derived(OLD_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
        derived(OLD_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
        derived(OTHER_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
        derived(OTHER_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
    )
    assessment = assessment_of(case)

    assert isinstance(assessment, Unresolved), (
        "two corroborated commits at one rank are an ambiguity, not an answer"
    )
    assert len(assessment.reasons) == 1, (
        "an ambiguity is one disagreement, not one line per candidate"
    )
    assert short(OLD_BASE) in assessment.reasons[0], (
        f"the refusal names one rival: {assessment.reasons[0]}"
    )
    assert short(OTHER_BASE) in assessment.reasons[0], (
        f"and the other: {assessment.reasons[0]}"
    )


def test_a_demoted_record_no_longer_outranks_the_evidence_that_superseded_it() -> None:
    """Gate 8's demotion is what keeps precedence from trusting a stale record.

    Both corpora hold one record naming one commit, one computed source naming
    the same commit, and a corroborated pair naming another. While the record's
    attested claim stands it is the strongest evidence in the run, so its commit
    answers; once gate 8 refuses that claim the record is derived evidence like
    the rest, both commits are corroborated equally, and the run refuses instead
    of reporting a boundary the superseding evidence disagrees with.
    """
    candidates = (
        attested(OLD_BASE, EvidenceKind.STACK_RECORD_BIRTH),
        derived(OLD_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
        derived(OTHER_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
        derived(OTHER_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
    )
    standing = with_candidates(permissive(), *candidates)
    superseded = with_candidates(record_superseded(), *candidates)
    established = assessment_of(standing)
    refused = assessment_of(superseded)

    assert isinstance(established, Established), (
        "the record's commit answers while the record's claim stands"
    )
    assert established.old_base == OLD_BASE, (
        "while the record stands, the boundary is the commit it names"
    )
    assert isinstance(refused, Unresolved), (
        "a demoted record is derived evidence, so it ranks with its rival"
    )
    assert short(OLD_BASE) in refused.reasons[0], (
        f"the refusal names the record's commit: {refused.reasons[0]}"
    )
    assert short(OTHER_BASE) in refused.reasons[0], (
        f"and its rival: {refused.reasons[0]}"
    )


def test_no_candidate_leaves_nothing_to_establish() -> None:
    """Nothing naming a boundary is an answer, not a fault."""
    assessment = assessment_of(with_candidates(permissive()))

    assert isinstance(assessment, Unresolved), (
        "a repository whose evidence names no boundary refuses, and says so"
    )
    assert len(assessment.reasons) == 1, (
        "the refusal gives one reason, not a line per candidate it weighed"
    )
    assert "boundary candidate" in assessment.reasons[0], (
        f"the refusal says no candidate was found: {assessment.reasons}"
    )


def test_two_commits_only_content_comparison_names_state_the_distinction() -> None:
    """A refusal says when the repository holds more than one candidate.

    Two child commits matched the target's content, so both cleared every gate
    the run could ask and neither can serve: content comparison is carried no
    further than a lead, however much of it agrees. A refusal that listed the
    two reasons alone would read as two ways of finding nothing; the run adds
    what distinguishes the case, which is that the evidence names more than one
    boundary and cannot choose between them.
    """
    candidates = (
        inferred(OLD_BASE, EvidenceKind.TREE_IDENTITY, source=TREE_SOURCE),
        inferred(OTHER_BASE, EvidenceKind.PATCH_IDENTITY, source=PATCH_SOURCE),
    )
    assessment = assessment_of(with_candidates(permissive(), *candidates))

    assert isinstance(assessment, Unresolved), (
        "two inferred candidates establish nothing, however many gates they pass"
    )
    assert len(assessment.reasons) == len(candidates) + 1, (
        "one reason per candidate, and one stating what the pair leaves unresolved"
    )
    distinction = assessment.reasons[-1]
    assert short(OLD_BASE) in distinction, (
        f"the distinction names the first candidate: {distinction}"
    )
    assert short(OTHER_BASE) in distinction, (
        f"and the second, rather than choosing between them: {distinction}"
    )
    assert "content comparison" in distinction, (
        f"and says which evidence could not decide: {distinction}"
    )


def test_one_commit_named_by_content_comparison_states_no_distinction() -> None:
    """A single lead is stated as one candidate's shortfall, not as a tie.

    A derived candidate cleared the same gates and also cannot serve, because
    one computed kind needs a second; the content comparison matched one commit
    and not a rival. Neither is a distinction between candidates, so the refusal
    is the per-candidate reasons and nothing else: the line is for a corpus that
    leaves more than one commit in the running.
    """
    candidates = (
        derived(OLD_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
        inferred(OTHER_BASE, EvidenceKind.TREE_IDENTITY, source=TREE_SOURCE),
    )
    assessment = assessment_of(with_candidates(permissive(), *candidates))

    assert isinstance(assessment, Unresolved), (
        "the derived candidate needs a second kind and the inferred one never serves"
    )
    assert len(assessment.reasons) == len(candidates), (
        f"neither candidate's shortfall is a distinction between candidates: "
        f"{assessment.reasons}"
    )


def test_an_established_result_partitions_the_childs_history() -> None:
    """INV-6: the two halves are the child's history, cut at the boundary."""
    assessment = assessment_of(permissive())

    assert isinstance(assessment, Established), (
        "an all-passing run establishes the boundary"
    )
    assert assessment.included == CHILD_WORK, (
        "the included commits are the child's work above the boundary"
    )
    assert assessment.excluded == (INHERITED, TRUNK), (
        "the excluded commits are the rest of the child's history"
    )
    assert set(assessment.included) | set(assessment.excluded) == set(CHILD_HISTORY), (
        "the halves partition the history the run read, with nothing lost"
    )
    assert not assessment.included_truncated, "the run listed the whole range"
    assert not assessment.excluded_truncated, "and the whole history"


def test_a_range_cut_short_says_so() -> None:
    """A partition the run could not see the end of is not reported as whole."""
    assessment = assessment_of(truncated_replay_range())

    assert isinstance(assessment, Established), (
        "a range cut short is still a range that names the child's work"
    )
    assert assessment.included == CHILD_WORK, (
        "the included side is the child's work above the boundary, cut short or not"
    )
    assert assessment.included_truncated, "the included side was cut short"


def test_a_history_cut_short_says_so() -> None:
    """The excluded side carries its own completeness, separately."""
    assessment = assessment_of(truncated_history())

    assert isinstance(assessment, Established), (
        "a history cut short still names a boundary and still establishes"
    )
    assert assessment.excluded_truncated, "the excluded side was cut short"


def test_a_collection_fault_turns_a_refusal_into_could_not_tell() -> None:
    """INV-5: a source that could not answer leaves the answer unknown.

    A refusal is a claim about the repository — that nothing in it names a
    boundary — and a run that could not read one of its sources has not earned
    that claim. The fault is reported first, so a reader sees what went wrong
    before the reasons that follow from it.
    """
    refused = assessment_of(with_candidates(permissive()))
    assert isinstance(refused, Unresolved), "the corpus starts from a refusal"

    fault = "the shared record could not be read"
    forced = apply_collection_faults(refused, (fault,))

    assert isinstance(forced, Indeterminate), (
        "an incomplete evidence set cannot refuse the boundary"
    )
    assert forced.reasons[0] == fault, "the fault is what the report leads with"
    assert forced.reasons[1:] == refused.reasons, (
        "the reasons the refusal reached are kept behind the fault"
    )
    assert forced.candidates == refused.candidates, "the evidence set is unchanged"
    assert forced.gates == refused.gates, "and so are the gates it was judged by"


def test_a_collection_fault_leaves_an_established_boundary_alone() -> None:
    """An established boundary survives: its gates were all answered."""
    established = assessment_of(permissive())
    assert isinstance(established, Established), "the corpus starts established"

    forced = apply_collection_faults(established, ("another source was unreadable",))

    assert forced is established, (
        "the candidate that established the boundary passed every gate it "
        "needed, and a fault in another source neither checked nor unchecked it"
    )


def test_a_run_that_found_no_fault_is_unchanged() -> None:
    """The absence of faults is not a fault of its own."""
    refused = assessment_of(with_candidates(permissive()))

    assert apply_collection_faults(refused, ()) is refused, (
        "an empty fault list changes nothing, not even the object identity"
    )


@pytest.mark.parametrize(
    "support",
    [
        (attested(OLD_BASE),),
        (
            derived(OLD_BASE, EvidenceKind.MERGE_BASE),
            derived(OLD_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
        ),
    ],
    ids=["attested", "two-derived-sources"],
)
def test_assess_reports_the_support_it_judged(
    support: tuple[Establishing, ...],
) -> None:
    """The support a result reports is the evidence that carried it."""
    case = with_candidates(permissive(), *support)

    assessment = assessment_of(case)

    assert isinstance(assessment, Established), (
        f"support {support!r} should establish the boundary it names"
    )
    assert set(assessment.support) == set(support), (
        "the result reports the candidates whose clearance carried it, in the "
        "canonical order that keeps the verdict independent of the order they "
        "were collected in"
    )
    assert assessment.support[0].commit == OLD_BASE, (
        "the canonical order starts at the commit the boundary is"
    )


def test_assess_reports_the_boundary_the_evidence_names() -> None:
    """The boundary is the commit the evidence names, not the child's tip."""
    assessment = assessment_of(permissive())

    assert isinstance(assessment, Established), (
        "the corpus establishes, so the boundary can be read"
    )
    assert assessment.old_base == OLD_BASE != TARGET, (
        "the boundary is the old base the evidence names, never the target the "
        "child is replayed onto"
    )
    assert assessment.durable_ref is None, (
        "the assessment does not know which ref the run will retain, so the "
        "command fills that in once the ref exists"
    )
