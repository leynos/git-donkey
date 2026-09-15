"""Behaviour-driven tests for the record ``git wheresat --record`` writes.

The module binds scenarios from ``features/git_wheresat_record.feature`` to real
temporary checkouts. It states INV-7 as behaviour rather than as a matrix of
calls: a record whose anchor ref has been collected is anchored again, a record
that is still anchored is replaced only by a run that names what it holds, and a
run that established no boundary states nothing and says so.

The create-only half of the invariant is the one that decides the shape of the
rest. Because a refresh may not invent a record, the two write scenarios both
begin from a record ``git donkey`` wrote at the branch's birth; what the runs
change is whether that record is still reachable, never whether it exists. The
fourth scenario is the other half of the same rule: a run whose answer rests on
nothing anybody declared has no boundary to write back, so it writes none.

Every assertion is made against Git — the anchor ref with ``rev-parse``, the
record's values with ``config --list``, and the whole repository with the shared
fingerprint — rather than against what the run reported, because a command that
wrote something other than it said would otherwise pass.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import stack_records, wheresat
from tests.integration.wheresat_helpers import (
    PARENT,
    Fingerprint,
    WheresatRun,
    WheresatScenario,
    anchor,
    configuration,
    forget_anchor,
    reading,
    run_wheresat_in,
    stacked_child,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

_ESTABLISHED: typ.Final = 0

_RECORDED_FROM: typ.Final = stack_records.RecordKey.RECORDED_FROM.value
_EVIDENCE: typ.Final = stack_records.RecordKey.EVIDENCE.value
_PARENT: typ.Final = stack_records.RecordKey.PARENT.value

_NOTHING_TO_RECORD: typ.Final = (
    "nothing was recorded: the run established no boundary to record"
)
"""Warning a ``--record`` run with no boundary to state prints."""

_EXPECTATION_REQUIRED: typ.Final = "an expected old object ID is required"
"""Refusal a ``--record`` run that named no expectation prints."""


@dataclasses.dataclass(slots=True)
class RecordScenario:
    """The checkout a scenario reads, and what the scenario's runs made of it.

    Attributes
    ----------
    checkout : WheresatScenario
        The stacked checkout, as the scenario's ``Given`` left it.
    untouched : Fingerprint
        The whole repository as the ``Given`` left it, which every run that
        refused to write must leave exactly as it found it.
    runs : list[WheresatRun]
        What each ``When`` step reported, in the order they ran.

    """

    checkout: WheresatScenario
    untouched: Fingerprint
    runs: list[WheresatRun] = dataclasses.field(default_factory=list)


def _holding(checkout: WheresatScenario) -> RecordScenario:
    """Return a scenario holding ``checkout`` as its ``Given`` left it.

    Parameters
    ----------
    checkout : WheresatScenario
        The stacked checkout the scenario reads.

    Returns
    -------
    RecordScenario
        The scenario, with the fingerprint a refusing run must preserve.

    """
    return RecordScenario(checkout=checkout, untouched=reading(checkout))


def _reported(scenario: RecordScenario) -> WheresatRun:
    """Return what the scenario's most recent run reported."""
    assert scenario.runs, "no run has been made yet"
    return scenario.runs[-1]


def _values(scenario: RecordScenario) -> dict[str, str]:
    """Return the child's record as Git holds it."""
    return configuration(scenario.checkout)


def _expected_parent() -> str:
    """Return the parent a birth record names, as the store stores it."""
    return stack_records.render_parent(
        stack_records.StackParent(branch=PARENT, pull_request=None)
    )


@given("a child branch with an existing stack record", target_fixture="scenario")
def child_with_an_existing_record(tmp_path: Path) -> RecordScenario:
    """Create a stacked child whose record is anchored.

    ``git donkey`` wrote the record and the anchor together at the branch's
    birth, which is the state every refresh begins from: nothing here creates a
    record, and no run under test may create one either.

    Returns
    -------
    RecordScenario
        The scenario, with the fingerprint a refusing run must preserve.

    """
    return _holding(stacked_child(tmp_path))


@given(
    "a child branch with a stack record whose anchor ref is gone",
    target_fixture="scenario",
)
def child_whose_anchor_is_gone(tmp_path: Path) -> RecordScenario:
    """Create a stacked child and collect the ref its record is anchored by.

    The configuration is what a run reads the boundary from, so the record still
    attests one after the anchor is gone; writing the anchor is what makes that
    boundary reachable again, and it is the half of INV-7 this scenario is for.

    Returns
    -------
    RecordScenario
        The scenario, with the fingerprint a refusing run must preserve.

    """
    checkout = stacked_child(tmp_path)
    forget_anchor(checkout)
    return _holding(checkout)


@given(
    "a stacked checkout whose parent cannot be replayed onto itself",
    target_fixture="scenario",
)
def parent_cannot_be_replayed_onto_itself(tmp_path: Path) -> RecordScenario:
    """Create a stacked checkout whose parent is the branch a run will ask about.

    A branch asked to replay onto its own tip has a boundary that is not an
    ancestor of the range it would have to replay, so the run reports a refusal
    rather than a boundary — the case the fourth scenario records nothing for.

    Returns
    -------
    RecordScenario
        The scenario, with the fingerprint a refusing run must preserve.

    """
    return _holding(stacked_child(tmp_path))


@when("I run git wheresat with recording enabled")
def run_with_recording(
    scenario: RecordScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run ``git wheresat --record`` from the child's worktree, naming no expectation.

    The child's worktree is where the branch is checked out, so the run needs no
    ``--branch``: it asks about the branch the current directory holds, exactly as
    a user standing on the branch would.
    """
    scenario.runs.append(
        run_wheresat_in(
            scenario.checkout,
            wheresat.WheresatOptions(record=True),
            capsys,
        )
    )


@when("I run git wheresat with recording enabled and the expected old value")
def run_with_recording_and_the_expectation(
    scenario: RecordScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run ``git wheresat --record --expected-old`` naming the anchored boundary.

    The expectation is the boundary the record attests, which is what the anchor
    ref holds: naming it is the user saying which value the write may replace.
    """
    scenario.runs.append(
        run_wheresat_in(
            scenario.checkout,
            wheresat.WheresatOptions(
                record=True, expected_old=scenario.checkout.boundary
            ),
            capsys,
        )
    )


@when("I run git wheresat with recording enabled for parent")
def run_with_recording_for_the_parent(
    scenario: RecordScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run ``git wheresat --record`` from the checkout, asking about the parent.

    The parent has no worktree to stand in, so the branch is named and the run is
    made from the checkout, which is where the command reads its repository from
    when it was not started on the branch under question.
    """
    scenario.runs.append(
        run_wheresat_in(
            scenario.checkout,
            wheresat.WheresatOptions(branch=PARENT, onto=PARENT, record=True),
            capsys,
            where="checkout",
        )
    )


@then("git wheresat succeeds")
def wheresat_succeeds(scenario: RecordScenario) -> None:
    """Check the run reported its boundary with the established status."""
    run = _reported(scenario)

    assert run.exit_code == _ESTABLISHED, (
        f"expected the run to establish a boundary, not exit {run.exit_code}: "
        f"{run.stderr.strip()}"
    )


@then("the stack-base ref names the boundary the record attests")
def the_anchor_names_the_recorded_boundary(scenario: RecordScenario) -> None:
    """Check that the ref keeping the boundary alive names the recorded commit.

    The boundary is the scenario's own: the parent's tip when the child was cut
    from it, which the record attests and no run under test may move.
    """
    checkout = scenario.checkout
    named = anchor(checkout)

    assert named == checkout.boundary, (
        f"expected the anchor ref to name the recorded boundary "
        f"{checkout.boundary}, but it names {named}"
    )


@then("the record preserves the parent it was born with")
def the_record_preserves_its_parent(scenario: RecordScenario) -> None:
    """Check that a refresh restates the branch's stack without re-deciding it.

    What the run knows about the parent is what it read, so a refresh that wrote
    its own guess would silently re-parent the branch — which no evidence the run
    collected could license.
    """
    written = _values(scenario)[_PARENT]

    assert written == _expected_parent(), (
        f"expected the refresh to preserve the parent {_expected_parent()!r}, "
        f"but the record names {written!r}"
    )


@then("the record names the child tip the run was made at")
def the_record_names_the_child_tip(scenario: RecordScenario) -> None:
    """Check that the record states the commit the branch was at when it was written.

    This is what a later run reads as the child tip, so a refresh that left it at
    the previous run's value would state the record was written from a commit the
    branch has already moved past.
    """
    checkout = scenario.checkout
    written = _values(scenario)[_RECORDED_FROM]

    assert written == checkout.tip, (
        f"expected the record to be written from the child tip {checkout.tip}, "
        f"but it names {written}"
    )


@then("the record names the refresh as its evidence")
def the_record_names_the_refresh(scenario: RecordScenario) -> None:
    """Check that the record says a later run re-stated it, rather than its birth.

    The kind is what tells a reader the boundary was restated by someone running
    the command against a checkout, and it is read back as attested evidence, so
    naming the wrong kind would either overstate the record or lose its weight.
    """
    written = _values(scenario)[_EVIDENCE]

    assert written == stack_records.EVIDENCE_REFRESHED, (
        f"expected the refresh to record the evidence kind "
        f"{stack_records.EVIDENCE_REFRESHED!r}, but the record names {written!r}"
    )


@then("the existing record is unchanged")
def the_existing_record_is_unchanged(scenario: RecordScenario) -> None:
    """Check that a refused write changed nothing at all."""
    differences = scenario.untouched.differences(reading(scenario.checkout))

    assert differences == (), (
        f"expected the refused run to change nothing, but the repository moved: "
        f"{differences}"
    )


@then("the command reports that an expected old object ID is required")
def the_command_reports_the_missing_expectation(scenario: RecordScenario) -> None:
    """Check that the refusal names what the run was missing."""
    run = _reported(scenario)

    assert _EXPECTATION_REQUIRED in run.stderr, (
        f"expected the refusal to say an expected old object ID is required, "
        f"got:\n{run.stderr}"
    )


@then("the command reports that nothing was recorded")
def the_command_reports_nothing_was_recorded(scenario: RecordScenario) -> None:
    """Check that a run with no boundary to state says what it left unwritten.

    A user who passed ``--record`` and read no warning would believe the record
    moved, so the warning is part of what the run owes them rather than a nicety.
    """
    run = _reported(scenario)

    assert _NOTHING_TO_RECORD in run.stdout, (
        f"expected the run to say nothing was recorded, got:\n{run.stdout}"
    )


@then(parsers.parse("no stack-base ref is created for {branch}"))
def no_stack_base_ref_is_created(scenario: RecordScenario, branch: str) -> None:
    """Check that no record was invented for the branch the run asked about.

    The branch is named rather than assumed, because the checkout already holds
    the child's anchor: asserting on the child's would pass without saying
    anything about the run, which asked about the parent.
    """
    named = anchor(scenario.checkout, branch)

    assert named is None, (
        f"expected no stack-base ref to be created for {branch!r}, but it names {named}"
    )


@then(parsers.parse("the command exits with status {status:d}"))
def the_command_exits_with_status(scenario: RecordScenario, status: int) -> None:
    """Check that the run reported ``status``."""
    run = _reported(scenario)

    assert run.exit_code == status, (
        f"expected the run to exit with status {status}, not {run.exit_code}: "
        f"{run.stderr.strip()}"
    )


scenarios("features/git_wheresat_record.feature")
