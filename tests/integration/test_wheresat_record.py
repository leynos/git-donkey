"""INV-7: ``--record`` refreshes the record a run's own evidence attests.

A refresh is the one write ``git wheresat`` was given beyond retaining a boundary
no durable ref reaches, so it is measured rather than described. The invariant's
five cases are one test each: the anchor ref absent, present with an expectation
that matches, present with one that does not, present with none given, and a run
whose result was unresolved. Every one of them builds its own checkout, because
a record write is not a change a shared fixture could survive.

Five further tests stand beside those. One is the invariant over the space of
pairs rather than over five points in it: it runs every pair of whether the
anchor ref is still there and what the run is told it holds, and holds the write
to proceeding exactly when the two agree, so the five cases are examples of it
rather than the whole of the claim. The other four keep the five from passing
vacuously. One proves a boundary no record attests is never written back, which
is the difference between refreshing a claim and inventing one. One proves that
a record the branch has moved past is read as evidence rather than restated,
which is what makes a birth record go stale once the parent has been integrated
and the branch restacked. One proves a refreshed record is still read as
attested evidence, so the write cannot poison the record it just made. One
proves ``--record`` is the only path that builds the command's writer, which is
what keeps the read-only promise (INV-1) a property of the code rather than of
the flags a caller happened to pass. The observations the run records are read
for the same reason: they say which path a run took, where the exit status
alone would not.

The record is read back through Git — the anchor ref with ``rev-parse``, the four
values with ``config --list`` — rather than through the value the run reported,
so the evidence is the repository's state and not the command's account of it.
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
import tempfile
import typing as typ
from pathlib import Path

import pytest

from git_donkey import (
    observability,
    stack_records,
    stack_store,
    wheresat,
    wheresat_refs,
)
from tests.integration.wheresat_helpers import (
    CHILD,
    PARENT,
    Where,
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
    import collections.abc as cabc

    from git import Repo

    from tests.observability_helpers import RecordingRecorder

pytestmark = pytest.mark.timeout(120)

_ESTABLISHED: typ.Final = 0
_REFUSED: typ.Final = 1
_UNUSABLE: typ.Final = 2

_ANCHOR: typ.Final = stack_records.base_ref_path(CHILD)
"""Ref the child's record is anchored by, and the ref a refresh replaces."""

_RECORD_OPERATION: typ.Final[observability.Operation] = "stack_record_write"
"""Operation a refreshed record is recorded under."""

_RECORDED_FROM: typ.Final = stack_records.RecordKey.RECORDED_FROM.value
_EVIDENCE: typ.Final = stack_records.RecordKey.EVIDENCE.value
_PARENT: typ.Final = stack_records.RecordKey.PARENT.value
_BASE: typ.Final = stack_records.RecordKey.BASE.value

_TRUNK: typ.Final = "main"
"""Branch the child's parent was grown a commit ahead of."""

_NOTHING_TO_RECORD: typ.Final = (
    "nothing was recorded: the run established no boundary to record"
)
"""Warning a ``--record`` run with no boundary to state prints."""

_UNATTESTED_TO_RECORD: typ.Final = (
    "nothing was recorded: the boundary rests on derived evidence, and only an "
    "attested claim is written back"
)
"""Warning a ``--record`` run whose boundary nobody declared prints."""


def _record(
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
    *,
    expected: str | None = None,
    where: Where = "worktree",
) -> WheresatRun:
    """Run ``--record`` with an optional expectation, and return what it reported."""
    return run_wheresat_in(
        scenario,
        wheresat.WheresatOptions(record=True, expected_old=expected),
        capsys,
        where=where,
    )


def _stored(scenario: WheresatScenario) -> stack_records.StackRecord:
    """Return the child's record as the store reads it back."""
    record = stack_store.GitStackRecordReader(scenario.repo).read(CHILD)
    assert isinstance(record, stack_records.StackRecord), (
        f"expected {CHILD!r} to hold a record, got {record!r}"
    )
    return record


def _forget_the_record(scenario: WheresatScenario) -> None:
    """Delete both artefacts of the child's record, so it has none."""
    for key in stack_records.RecordKey:
        scenario.repo.git.config(
            "--local",
            "--unset-all",
            f"branch.{CHILD}.{key.value}",
            with_exceptions=False,
        )
    forget_anchor(scenario)


def _parent_value() -> str:
    """Return the parent the child's birth record names, as the store stores it."""
    return stack_records.render_parent(
        stack_records.StackParent(branch=PARENT, pull_request=None)
    )


_EXPECTATIONS: typ.Final = ("none", "boundary", "tip")
"""The commits a run can be told the record's anchor ref holds: none, or one."""

_AGREEMENT_PAIRS: typ.Final = tuple(itertools.product((False, True), _EXPECTATIONS))
"""Every pair of anchor state and expectation the invariant ranges over.

The space is finite — whether the anchor ref is still there, crossed with what
the run is told it holds — so it is spelled out and run once per pair rather
than sampled: six cases, each of which builds a real repository and runs a real
command line, which is the same reason INV-1's matrix is parameterized rather
than generated.
"""


def _names(scenario: WheresatScenario, expectation: str) -> str | None:
    """Return the commit ``expectation`` names, or ``None`` for no expectation.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout the expectation is named against, since both commits it
        can name are the scenario's.
    expectation : str
        One of ``_EXPECTATIONS``: no expectation, the boundary the record
        attests, or the child tip above it.

    Returns
    -------
    str | None
        The commit to pass as ``--expected-old``.

    """
    match expectation:
        case "boundary":
            return scenario.boundary
        case "tip":
            return scenario.tip
        case _:
            return None


def _restack_the_child(scenario: WheresatScenario) -> None:
    """Replay the child's work onto the trunk, as a restack after a parent moves does.

    The record names the commit the child was cut at. Replaying the branch
    elsewhere leaves that commit off the branch's history, so the record's claim
    is still written down but no longer describes where the branch came from —
    which is the state the command has to read it as evidence for.
    """
    child = scenario.worktree_repo()
    trunk = scenario.repo.heads[_TRUNK].commit.hexsha
    child.git.rebase("--onto", trunk, scenario.boundary, CHILD)


@dataclasses.dataclass(frozen=True, slots=True)
class _Trace:
    """The record write one run of the command recorded, and nothing else.

    Attributes
    ----------
    outcomes : tuple[observability.Outcome, ...]
        What became of the write, in the order the run reported it.
    error_kinds : tuple[observability.ErrorKind, ...]
        Which bounded failures the write met, in order.
    timed : bool
        Whether the run spent time inside the write, which it only does once the
        write has started.

    """

    outcomes: tuple[observability.Outcome, ...]
    error_kinds: tuple[observability.ErrorKind, ...]
    timed: bool


def _traced(
    recorder: RecordingRecorder,
    run: cabc.Callable[[], WheresatRun],
) -> tuple[WheresatRun, _Trace]:
    """Run ``run`` and return what it reported and the record write it recorded.

    Each checkout these tests build was itself stacked with ``git donkey``,
    which writes a birth record and records that write under the same operation,
    so a run's trace is sliced out of the recorder rather than read whole. What
    is left is what this run did, which is the whole of what the assertions are
    about.

    Parameters
    ----------
    recorder : RecordingRecorder
        The recorder installed for the test.
    run : cabc.Callable[[], WheresatRun]
        The command line to run, as a callable returning what it reported.

    Returns
    -------
    tuple[WheresatRun, _Trace]
        What the command line reported, and the write's outcomes and error
        kinds with whether it was timed.

    """
    first = len(recorder.observations)
    recorded_spans = len(recorder.spans)
    reported = run()
    written = [
        observation
        for observation in recorder.observations[first:]
        if observation.operation == _RECORD_OPERATION
    ]
    trace = _Trace(
        outcomes=tuple(observation.outcome for observation in written),
        error_kinds=tuple(
            observation.error_kind
            for observation in written
            if observation.error_kind is not None
        ),
        timed=any(
            span.operation == _RECORD_OPERATION
            for span in recorder.spans[recorded_spans:]
        ),
    )
    return reported, trace


def test_an_absent_anchor_is_written_from_the_record_that_still_carries_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INV-7, ref absent: a record whose anchor is gone is anchored again.

    Configuration is authoritative for the values, so a record whose anchor was
    collected still names its boundary; writing the anchor is what makes that
    boundary reachable again. Nothing else may be written either: the boundary is
    a commit the parent branch itself reaches, so no retaining ref is owed for
    it, and the only ref the run adds is the anchor.
    """
    scenario = stacked_child(tmp_path)
    forget_anchor(scenario)
    before = reading(scenario)

    run = _record(scenario, capsys)

    assert run.exit_code == _ESTABLISHED, (
        f"expected the refresh to write the anchor, not exit {run.exit_code}: "
        f"{run.stderr.strip()}"
    )
    assert anchor(scenario) == scenario.boundary, (
        "the anchor is written again, naming the boundary the record attests"
    )
    after = reading(scenario)
    assert set(after.refs) - set(before.refs) == {f"{_ANCHOR} {scenario.boundary}"}, (
        "the refresh adds the anchor and no other ref"
    )
    assert set(before.refs) - set(after.refs) == set(), "and removes none"
    values = configuration(scenario)
    assert values[_BASE] == scenario.boundary, (
        "the boundary the record attests is not moved by writing it again"
    )
    assert values[_RECORDED_FROM] == scenario.tip, (
        "the record names the child tip this run was made at"
    )
    assert values[_EVIDENCE] == stack_records.EVIDENCE_REFRESHED, (
        "and the evidence kind that says a later run re-stated it"
    )
    assert values[_PARENT] == _parent_value(), (
        "a refresh preserves the parent a previous writer stated"
    )


def test_an_existing_anchor_is_not_replaced_without_an_expectation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INV-7, present with no expectation: the create-only half of the write.

    A record that is already anchored is replaced only by a run that names what
    the anchor holds. A run that names nothing is refused before anything is
    written, so an unqualified ``--record`` cannot quietly move a boundary that
    another run may already depend on.
    """
    scenario = stacked_child(tmp_path)
    before = reading(scenario)

    run = _record(scenario, capsys)

    assert run.exit_code == _UNUSABLE, (
        f"expected the write to be refused, not exit {run.exit_code}"
    )
    assert "an expected old object ID is required" in run.stderr, (
        f"the refusal names what is missing, got: {run.stderr.strip()}"
    )
    assert scenario.boundary in run.stderr, (
        "and names the commit the anchor holds, so the user can pass it"
    )
    assert not before.differences(reading(scenario)), (
        "a refused write changes nothing at all"
    )


def test_the_refusal_reaches_the_machine_readable_envelope(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refused write is the run's error, in the envelope a consumer reads.

    ``--json`` is the interface a later milestone's automation reads, so a
    refusal has to be the error envelope rather than prose on standard error: a
    consumer that got an established-shaped object with a null boundary could not
    tell a refusal from an empty answer.
    """
    scenario = stacked_child(tmp_path)
    before = reading(scenario)

    run = run_wheresat_in(
        scenario,
        wheresat.WheresatOptions(record=True, json=True),
        capsys,
    )

    payload = run.envelope
    assert payload["verdict"] == "error", "a refused write is reported as an error"
    assert payload["exitCode"] == _UNUSABLE, "with the usage status"
    assert "an expected old object ID is required" in str(payload["error"]), (
        f"and the reason it was refused, got {payload['error']!r}"
    )
    assert not before.differences(reading(scenario)), "and nothing was written"


def test_a_matching_expectation_refreshes_the_record_in_place(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INV-7, present with a matching expectation: the record is refreshed.

    The boundary does not move. The record rung is attested evidence and
    outranks anything this run could compute, so what a refresh changes is when
    the record was written from and what it names as its evidence — not where the
    branch's own work begins.
    """
    scenario = stacked_child(tmp_path)

    run = _record(scenario, capsys, expected=scenario.boundary)

    assert run.exit_code == _ESTABLISHED, (
        f"expected the matching expectation to allow the write, not exit "
        f"{run.exit_code}: {run.stderr.strip()}"
    )
    assert anchor(scenario) == scenario.boundary, "the anchor is not moved"
    record = _stored(scenario)
    assert record.base == scenario.boundary, "and the record still names it"
    assert record.recorded_from == scenario.tip, (
        "while naming the child tip the refresh was made at"
    )
    assert record.evidence == stack_records.EVIDENCE_REFRESHED, (
        "and the evidence kind a later run reads as attested"
    )
    assert record.parent == stack_records.StackParent(
        branch=PARENT, pull_request=None
    ), "and the parent the branch was born with"


def test_a_stale_expectation_is_refused_and_changes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INV-7, present with a mismatched expectation: the write is refused.

    ``--expected-old`` is a compare-and-swap, so a run that names a commit the
    anchor does not hold is refused rather than allowed to replace a record that
    has moved on. The refusal names both commits, so the operator can see which
    of the two they were wrong about.
    """
    scenario = stacked_child(tmp_path)
    before = reading(scenario)

    run = _record(scenario, capsys, expected=scenario.tip)

    assert run.exit_code == _UNUSABLE, (
        f"expected the stale expectation to be refused, not exit {run.exit_code}"
    )
    assert "--expected-old does not match" in run.stderr, (
        f"the refusal says which comparison failed, got: {run.stderr.strip()}"
    )
    assert scenario.boundary in run.stderr, "and names the commit the anchor holds"
    assert scenario.tip in run.stderr, "as well as the one that was expected"
    assert not before.differences(reading(scenario)), (
        "a refused write changes nothing at all"
    )


def test_an_unresolved_run_records_nothing_and_says_so(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INV-7, an unresolved result: ``--record`` writes nothing and warns.

    The run below is refused because the boundary it would have to state names
    the branch's own tip, so there is no boundary to record. A run that wrote
    nothing and warned about nothing would leave a user who passed ``--record``
    believing the record moved.
    """
    scenario = stacked_child(tmp_path)
    before = reading(scenario)

    run = run_wheresat_in(
        scenario,
        wheresat.WheresatOptions(branch=PARENT, onto=PARENT, record=True),
        capsys,
        where="checkout",
    )

    assert run.exit_code == _REFUSED, (
        f"expected the refusal vector to be refused, not exit {run.exit_code}"
    )
    assert _NOTHING_TO_RECORD in run.stdout, (
        f"the run says what it left unwritten, got:\n{run.stdout}"
    )
    assert not before.differences(reading(scenario)), (
        "an unresolved run writes nothing, warning or not"
    )


def test_a_boundary_no_record_attests_is_not_written_back(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """Only a claim is written back: a computed boundary stays a computation.

    With both artefacts of the child's record gone, whatever the run establishes
    rests on where the branch forked from the trunk — real evidence about where
    the branch came from, but nobody's declaration. Promoting it into a record
    would make the next run read a computation as a claim, which is INV-7's
    point.
    """
    scenario = stacked_child(tmp_path)
    _forget_the_record(scenario)
    before = reading(scenario)

    run, trace = _traced(
        recording_recorder, functools.partial(_record, scenario, capsys)
    )

    assert run.exit_code == _ESTABLISHED, (
        f"expected the surviving history to establish the boundary, not exit "
        f"{run.exit_code}: {run.stdout.strip()}"
    )
    assert _UNATTESTED_TO_RECORD in run.stdout, (
        f"and the run to say only a claim is written back, got:\n{run.stdout}"
    )
    assert trace == _Trace(outcomes=("rejected",), error_kinds=(), timed=False), (
        "the write is observed as rejected rather than skipped, and having "
        f"nothing to write is not a failure of writing: {trace}"
    )
    assert anchor(scenario) is None, "and no record is invented for the branch"
    assert not before.differences(reading(scenario)), "and nothing was written"


@pytest.mark.parametrize(("present", "expectation"), _AGREEMENT_PAIRS)
def test_only_a_pair_that_agrees_replaces_the_record(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    present: bool,
    expectation: str,
) -> None:
    """INV-7 over the space of pairs: only a pair that agrees writes anything.

    The five cases above are examples of this property rather than the whole of
    it. The anchor ref is either there or collected, and the run is told the ref
    holds nothing, the boundary, or a commit the boundary is not; the write is
    the compare-and-swap of the two, so it proceeds exactly when they agree —
    including the create-only case, where both say the ref does not exist — and
    a pair that disagrees must leave the repository exactly as the run found it.
    A disagreeing pair that wrote anything is the failure this invariant exists
    to prevent, and it is the one a five-case matrix can only sample.

    The space is six pairs, and the matrix is exactly those six: the test is
    parametrized over every pair rather than run once over a generated sample,
    so a pair that stopped being covered would be a test that disappeared rather
    than a draw that was never made.
    """
    scenario = stacked_child(Path(tempfile.mkdtemp(dir=tmp_path, prefix="case-")))
    if not present:
        forget_anchor(scenario)
    before = reading(scenario)
    held = anchor(scenario)
    named = _names(scenario, expectation)
    agrees = held == named

    run = _record(scenario, capsys, expected=named)

    if not agrees:
        assert run.exit_code == _UNUSABLE, (
            f"expected the pair {held!r}/{named!r} to be refused as a usage "
            f"error, not exit {run.exit_code}: {run.stdout.strip()}"
        )
        assert not before.differences(reading(scenario)), (
            f"a pair that disagrees changes nothing, and {held!r}/{named!r} "
            "changed the repository"
        )
        return
    assert run.exit_code == _ESTABLISHED, (
        f"expected the pair {held!r}/{named!r} to refresh the record, not exit "
        f"{run.exit_code}: {run.stderr.strip()}"
    )
    assert anchor(scenario) == scenario.boundary, (
        f"and the ref to name the boundary the record attests, {scenario.boundary}"
    )
    assert _stored(scenario).evidence == stack_records.EVIDENCE_REFRESHED, (
        "and the record to be the run's refresh rather than the birth record "
        "the checkout already had, which would pass without a write"
    )


def test_a_record_the_branch_has_moved_past_is_not_written_back(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A record the branch has moved past is evidence, not a claim to restate.

    This is what makes a birth record go stale. Once the parent has been
    integrated and the branch restacked onto it, the tip the record was written
    from is no longer on the branch, so the record stops saying where the branch
    came from and becomes one more thing the run knows. The run still answers
    with the boundary the surviving history agrees on and still writes nothing
    back, because only an attested claim may become a record.
    """
    scenario = stacked_child(tmp_path)
    _restack_the_child(scenario)
    before = reading(scenario)
    superseded = _stored(scenario)

    assert superseded.recorded_from == scenario.boundary, (
        "the record still names the tip it was written from, which the restack "
        "left behind: it is the record's age, not its absence, that the run reads"
    )
    assert superseded.evidence == stack_records.EVIDENCE_BIRTH, (
        "and it is still the birth record, so nothing here refreshed it first"
    )

    run, trace = _traced(
        recording_recorder, functools.partial(_record, scenario, capsys)
    )

    assert run.exit_code == _ESTABLISHED, (
        f"expected the restacked branch's history to establish a boundary, not "
        f"exit {run.exit_code}: {run.stdout.strip()}"
    )
    assert _UNATTESTED_TO_RECORD in run.stdout, (
        f"and the superseded record to be read as derived evidence, got:\n{run.stdout}"
    )
    assert trace == _Trace(outcomes=("rejected",), error_kinds=(), timed=False), (
        "the write is observed as rejected rather than skipped, and having "
        f"nothing to write is not a failure of writing: {trace}"
    )
    assert anchor(scenario) == scenario.boundary, (
        "the anchor still names the boundary the record attests, unmoved"
    )
    assert not before.differences(reading(scenario)), "and nothing was written"


def test_a_refreshed_record_is_still_attested_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A refresh must not poison the record it wrote.

    The evidence kind a refresh stamps is read back as attested evidence by the
    next run, so a refreshed record is stronger evidence than no record rather
    than a value the reader cannot classify — which would make every later run
    indeterminate instead of established.
    """
    scenario = stacked_child(tmp_path)
    written = _record(scenario, capsys, expected=scenario.boundary)
    assert written.exit_code == _ESTABLISHED, (
        f"expected the refresh to succeed, not exit {written.exit_code}"
    )

    reread = run_wheresat_in(scenario, wheresat.WheresatOptions(), capsys)

    assert reread.exit_code == _ESTABLISHED, (
        f"expected the refreshed record to establish the boundary, not exit "
        f"{reread.exit_code}: {reread.stderr.strip()}"
    )
    observations = recording_recorder.observations
    assert any(
        observation.operation == "evidence_collection"
        and observation.evidence_tier == "attested"
        for observation in observations
    ), f"expected the refreshed record to be attested evidence, got {observations}"
    assert any(
        observation.operation == "boundary_assessment"
        and observation.verdict == "established"
        for observation in observations
    ), f"expected the refreshed record to establish the boundary, got {observations}"


def test_a_refresh_is_observed_as_one_timed_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A write that happened is reported once, started then successful.

    A trace that could not count the writes would leave a reader unable to tell a
    run that recorded from one that only read, which is the one difference
    ``--record`` makes.
    """
    scenario = stacked_child(tmp_path)

    run, trace = _traced(
        recording_recorder,
        functools.partial(_record, scenario, capsys, expected=scenario.boundary),
    )

    assert run.exit_code == _ESTABLISHED, (
        f"expected the refresh to succeed, not exit {run.exit_code}"
    )
    written = _Trace(outcomes=("started", "success"), error_kinds=(), timed=True)
    assert trace == written, f"expected exactly one completed write, got {trace}"


def test_a_refused_write_is_observed_as_a_conflict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A refused write is reported as the bounded failure it is.

    The error kind is what tells an operator that the record moved under the run
    rather than that the store was unusable, and it is the kind a consumer
    retries from. Nothing was written, so the write is not timed either.
    """
    scenario = stacked_child(tmp_path)

    run, trace = _traced(
        recording_recorder, functools.partial(_record, scenario, capsys)
    )

    assert run.exit_code == _UNUSABLE, (
        f"expected the write to be refused, not exit {run.exit_code}"
    )
    assert trace == _Trace(
        outcomes=("rejected",),
        error_kinds=("stack_record_conflict",),
        timed=False,
    ), f"expected the refusal to be observed as a conflict, got {trace}"


def test_only_a_record_run_builds_the_commands_writer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--record`` is the only path that builds the command's writer.

    The read-only promise (INV-1) is a property of the code only while a run that
    was asked for no write has no writer to reach for. Construction is counted
    rather than inferred: the same counting patch sees the writer built exactly
    once by the run that was asked to record, so the zero before it is a
    measurement rather than a patch that never fires.
    """
    scenario = stacked_child(tmp_path)
    built: list[Repo] = []
    original = wheresat_refs.GitWheresatRefWriter

    def counting(repo: Repo) -> wheresat_refs.GitWheresatRefWriter:
        built.append(repo)
        return original(repo)

    monkeypatch.setattr(wheresat_refs, "GitWheresatRefWriter", counting)

    run_wheresat_in(scenario, wheresat.WheresatOptions(), capsys)
    assert not built, "a run that was asked for no write builds no writer"

    _record(scenario, capsys, expected=scenario.boundary)
    assert len(built) == 1, (
        "and the run that was asked to record builds exactly one writer"
    )
