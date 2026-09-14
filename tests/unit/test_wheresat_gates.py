"""The gate table ``git wheresat`` judges a candidate against.

The gates are a conjunction over named questions, so this suite is mostly a
table: one row per gate for a gate that answered against the candidate, one row
per gate for a gate that went unanswered, and one row in which nothing answered
against it at all. Every row is the same permissive case with a single answer
changed, which is what makes the table readable and what makes the claim it
supports checkable — the refusal can only be the gate under test.

A gate's own engine is a truth table in its turn: what an ancestry answer means
for a question that expects one polarity. That table is pinned here too, because
the gates about a parent's history are read through it, and an answer Git could
not give must never be read as a refusal (INV-5).

The assessment that reads these gates, and the policy it applies to the
candidates they judged, are pinned in ``test_wheresat_policy``.

"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey.wheresat_gates import ancestry_outcome
from git_donkey.wheresat_records import (
    EXIT_CODES,
    GATE_NAMES,
    Ancestry,
    Established,
    GateName,
    GateOutcome,
    Indeterminate,
    Unresolved,
)
from tests.unit.wheresat_helpers import (
    OLD_BASE,
    PARENT_GATES,
    assessment_of,
    failed_gates,
    permissive,
    spoiled,
    undecided_gates,
)

# What an ancestry answer means for a gate that expects one polarity. Every
# combination of the three answers and the two expectations is a row, and the
# unknown answer never becomes a refusal.
_ANCESTRY_TABLE = (
    (Ancestry.ANCESTOR, Ancestry.ANCESTOR, GateOutcome.PASSED),
    (Ancestry.ANCESTOR, Ancestry.NOT_ANCESTOR, GateOutcome.FAILED),
    (Ancestry.NOT_ANCESTOR, Ancestry.ANCESTOR, GateOutcome.FAILED),
    (Ancestry.NOT_ANCESTOR, Ancestry.NOT_ANCESTOR, GateOutcome.PASSED),
    (Ancestry.UNKNOWN, Ancestry.ANCESTOR, GateOutcome.INDETERMINATE),
    (Ancestry.UNKNOWN, Ancestry.NOT_ANCESTOR, GateOutcome.INDETERMINATE),
)


# The gates a run that named no parent pull request never applies.
_ABSENT_PARENT_GATES = frozenset({
    GateName.PARENT_IDENTITY_MATCHES,
    GateName.PARENT_MERGED,
})


# The gates such a run leaves out of its conjunction: the two about the parent
# pull request itself and the one about its integration, which is judged only
# when a parent pull request put it in play. The gates about a parent's
# *history* are a different matter, because a head outlives the branch it named:
# a run holding one — from a tombstone — is judged against it, and a run holding
# both a head and an integration is judged on the range the two describe.
_INAPPLICABLE_WITHOUT_PARENT = frozenset({
    GateName.PARENT_IDENTITY_MATCHES,
    GateName.PARENT_MERGED,
    GateName.LANDED_REACHABLE_FROM_TARGET,
})


# A row that withholds one answer withholds it from every gate that reads it, so
# a row's unanswered gates are not always one gate: the replay range's listed
# contents are what "the range is not empty" counts *and* what "the range holds
# no landed work" subtracts the parent's view from, so a run that could not list
# the range could not answer either question about it.
_ALSO_UNANSWERED = {
    GateName.REPLAY_RANGE_NON_EMPTY: frozenset({
        GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK
    }),
}


_INDETERMINATE_STATUS: typ.Final = 3
"""The exit status a run reports when a question it needed went unanswered."""


_EXPECTED_GATE_NAMES: typ.Final = (
    GateName.PARENT_IDENTITY_MATCHES,
    GateName.PARENT_MERGED,
    GateName.LANDED_REACHABLE_FROM_TARGET,
    GateName.BOUNDARY_IS_ANCESTOR_OF_CHILD,
    GateName.REPLAY_RANGE_NON_EMPTY,
    GateName.PARENT_HISTORY_INTACT,
    GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK,
    GateName.RECORD_NOT_SUPERSEDED,
)
"""The eight gates, in the order the procedure lists them.

Written as a named expectation rather than into the assertion below: the pin is
a long list of enum members, and a name with a docstring states what is being
pinned where a snapshot would move it out of the file that reads as the
decision record.
"""


def test_the_gate_names_are_the_procedures_gates_in_order() -> None:
    """The eight gates, and the order every report lists them in, are fixed."""
    assert GATE_NAMES == _EXPECTED_GATE_NAMES, (
        "the gates and their order are the ones the decision record fixes"
    )
    assert len(_ABSENT_PARENT_GATES) == PARENT_GATES, (
        "the gates about a parent pull request are the two the case exercises"
    )


@pytest.mark.parametrize(
    ("observed", "expect", "outcome"),
    _ANCESTRY_TABLE,
    ids=[
        f"{observed.value}-expecting-{expect.value}"
        for observed, expect, _ in _ANCESTRY_TABLE
    ],
)
def test_ancestry_outcome_is_a_truth_table(
    observed: Ancestry,
    expect: Ancestry,
    outcome: GateOutcome,
) -> None:
    """An ancestry answer becomes the outcome its expectation demands."""
    assert ancestry_outcome(observed, expect=expect) is outcome, (
        f"{observed.value} answered against an expectation of {expect.value} "
        f"must be read as {outcome.value}"
    )


def test_the_ancestry_table_covers_every_answer_and_expectation() -> None:
    """The table above is every combination, not a sample of them."""
    assert len(_ANCESTRY_TABLE) == len(Ancestry) * 2, (
        "every answer is exercised against both expectations"
    )


def test_every_applicable_gate_passing_establishes_the_boundary() -> None:
    """INV-4's all-passed row: a conjunction with nothing against it holds."""
    case = permissive()
    assessment = assessment_of(case)

    assert isinstance(assessment, Established), (
        f"nothing answered against the boundary, yet it was not established: "
        f"{assessment!r}"
    )
    assert assessment.old_base == OLD_BASE, (
        "the established boundary is the commit the evidence named"
    )
    assert tuple(assessment.support) == case.candidates, (
        "the establishing candidate is reported as the boundary's support"
    )
    assert not failed_gates(assessment), "no applicable gate answered against it"
    assert not undecided_gates(assessment), "no applicable gate went unanswered"
    assert tuple(gate.name for gate in assessment.gates) == GATE_NAMES, (
        "every gate is reported, in the order the procedure lists them"
    )
    assert {
        gate.name for gate in assessment.gates if not gate.applicable
    } == _INAPPLICABLE_WITHOUT_PARENT, (
        "a run that named no parent pull request never consulted one, so the "
        "gates about the parent pull request and its integration do not apply "
        "and are reported as such"
    )


@pytest.mark.parametrize("gate", GATE_NAMES)
def test_one_gate_failing_alone_refuses_the_boundary(gate: GateName) -> None:
    """INV-4: the boundary holds only while every applicable gate passes.

    Each row spoils exactly one gate and leaves the rest of the permissive case
    alone, so the refusal has to be that gate rather than an accident of the
    example, and the reason has to name it.
    """
    case = spoiled(gate, GateOutcome.FAILED)
    assessment = assessment_of(case)

    assert isinstance(assessment, Unresolved), (
        f"{gate.value} answering against the candidate must refuse the "
        f"boundary, not report {assessment!r}"
    )
    assert failed_gates(assessment) == (gate,), (
        f"only {gate.value} was spoilt, but these gates answered against the "
        f"candidate: {failed_gates(assessment)}"
    )
    assert any(gate.value in reason for reason in assessment.reasons), (
        f"the refusal must name {gate.value}: {assessment.reasons}"
    )


@pytest.mark.parametrize("gate", GATE_NAMES)
def test_one_gate_unanswered_is_never_read_as_an_answer(gate: GateName) -> None:
    """INV-5: a gate without an answer refuses the answer, not the candidate.

    The distinction is the whole of the third verdict: a question Git could not
    answer is no more a yes than it is a no, so the run reports that it could
    not tell and exits with the indeterminate status rather than with the status
    that means "no boundary established".
    """
    case = spoiled(gate, GateOutcome.INDETERMINATE)
    assessment = assessment_of(case)

    assert isinstance(assessment, Indeterminate), (
        f"{gate.value} going unanswered must not be read as a refusal, yet the "
        f"run reported {assessment!r}"
    )
    assert not failed_gates(assessment), (
        f"no gate answered against the candidate, but these did: "
        f"{failed_gates(assessment)}"
    )
    unanswered = {gate} | _ALSO_UNANSWERED.get(gate, frozenset())
    assert set(undecided_gates(assessment)) == unanswered, (
        f"only {gate.value}, and the gates that read the same answer, were left "
        f"unanswered, but these went unanswered: {undecided_gates(assessment)}"
    )
    assert EXIT_CODES[Indeterminate] == _INDETERMINATE_STATUS, (
        "the run exits with what it could tell"
    )
    assert any(gate.value in reason for reason in assessment.reasons), (
        f"the report must say which question went unanswered: {assessment.reasons}"
    )
