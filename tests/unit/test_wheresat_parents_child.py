"""The rungs that read what the child itself says about its own stack.

Two of the ladder's five rungs read the child's own record: the stack record its
branch carries, which a run that wrote one finds beside its clone, and the
shared record its pull request body carries, which a run that wrote one finds
only by asking GitHub. Both are the child author's own deliberate act, so a
claim either names is attested and needs no corroboration from the search — and
both are read before the search is walked, the local record first because it is
already in hand.

A rung the ladder cannot take up is a fault rather than a reason to walk on
(ADR-005, INV-5), and the two rungs differ in what that means. A broken record
in the clone is reported by the collection phase, which reads the same branch
for its own evidence, so the ladder reports it once, in the phase that owns it.
A claim the ladder itself cannot take up — a body this run cannot read, or one
carrying two disagreeing records — stops the walk where it was found.

Nothing here opens a repository or a socket: the doubles this suite drives the
ladder with live in ``tests.unit.wheresat_parents_helpers``, with the builders
that put a question to the ladder. Every rung is therefore driven without a
network, and the assertions can be about what was asked as much as about what
was answered.

The one refusal that names every reading a body supports is pinned as a
snapshot rather than probed for the parts a reader expects, because the whole
sentence is what the operator is told: which readings disagreed, and why this
run will not choose between them.
"""

from __future__ import annotations

import typing as typ

from git_donkey import (
    stack_store,
    wheresat_shared_record,
)
from tests.unit.wheresat_helpers import (
    CHILD_BELOW,
    CHILD_TIP,
)
from tests.unit.wheresat_parents_helpers import (
    BOUNDARY,
    CHILD_BRANCH,
    CHILD_IDENTITY,
    DECOY_IDENTITY,
    OTHER_BOUNDARY,
    PARENT_IDENTIFICATION,
    PARENT_IDENTITY,
    SILENT_BODY,
    Forge,
    Opener,
    Records,
    Run,
    ask,
    association_page,
    born_on,
    boundary_request,
    child_payload,
    graph_over,
    parent_payload,
    search_bounds,
    shared_body,
    stacked_on,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

    from tests.observability_helpers import RecordingRecorder


def test_the_childs_own_record_names_the_parent_before_the_search(
    recording_recorder: RecordingRecorder,
) -> None:
    """A record written after the parent was opened answers the question outright."""
    forge = Forge(
        payloads={PARENT_IDENTITY: parent_payload()},
        page=association_page({CHILD_TIP: (DECOY_IDENTITY,)}),
    )

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_TIP),
            records=Records(record=stacked_on(PARENT_IDENTITY)),
            opener=Opener(forge=forge),
        ),
    )

    assert identified.parent is not None, "the record should name a parent"
    assert identified.parent.identity == PARENT_IDENTITY, (
        "the pull request the record names is the parent"
    )
    assert identified.faults == (), "a record that names a parent is no fault"
    assert forge.read == [PARENT_IDENTITY], (
        "a record naming a pull request should leave the search unasked"
    )
    assert not forge.bodies_read, "no body should be read once the record answers"


def test_a_record_that_names_a_branch_leaves_the_search_to_answer(
    recording_recorder: RecordingRecorder,
) -> None:
    """A record written at birth names a branch, and a branch is not a pull request."""
    forge = Forge(
        payloads={
            CHILD_IDENTITY: child_payload(),
            PARENT_IDENTITY: parent_payload(),
        },
        stacks={CHILD_IDENTITY: None},
        bodies={CHILD_IDENTITY: SILENT_BODY},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (PARENT_IDENTITY,),
        }),
    )

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_BELOW, CHILD_TIP),
            records=Records(record=born_on("main")),
            opener=Opener(forge=forge),
        ),
    )

    assert identified.parent is not None, "the search should answer for the record"
    assert identified.parent.identity == PARENT_IDENTITY, (
        "nothing local says which pull request heads a branch, so the walk goes on"
    )
    assert identified.faults == (), "a record naming a branch is not a fault"


def test_a_record_that_cannot_be_read_does_not_fault_the_ladder(
    recording_recorder: RecordingRecorder,
) -> None:
    """The collection phase reports a broken record; the ladder must not also."""
    forge = Forge(
        payloads={
            CHILD_IDENTITY: child_payload(),
            PARENT_IDENTITY: parent_payload(),
        },
        stacks={CHILD_IDENTITY: None},
        bodies={CHILD_IDENTITY: SILENT_BODY},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (PARENT_IDENTITY,),
        }),
    )
    records = Records(refusal=stack_store.StackRecordError("the anchor disagrees"))

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_BELOW, CHILD_TIP),
            records=records,
            opener=Opener(forge=forge),
        ),
    )

    assert records.reads == [CHILD_BRANCH], "the ladder reads the record it was handed"
    assert identified.faults == (), (
        "one unusable record is reported once, by the rung that owns it"
    )
    assert identified.parent is not None, (
        "a record that could not be read should send the walk to the rungs below"
    )


def test_no_record_is_read_when_the_run_may_not_ask_the_forge(
    recording_recorder: RecordingRecorder,
) -> None:
    """A run that may not act on a record's address has no reason to read it."""
    records = Records(record=stacked_on(PARENT_IDENTITY))
    opener = Opener(forge=Forge())

    identified = ask(
        boundary_request(offline=True),
        search_bounds(),
        run=Run(records=records, opener=opener),
    )

    assert identified.faults == (), "declining to ask is not a question unanswered"
    assert not records.reads, (
        "a record this run may not take up should not be read at all"
    )
    assert not opener.opened, "an offline run should not open the forge"


def test_the_childs_body_names_the_parent_when_no_stack_does(
    recording_recorder: RecordingRecorder,
) -> None:
    """A claim pasted into the child's body is a rung, and it rides to collection."""
    forge = Forge(
        payloads={
            CHILD_IDENTITY: child_payload(),
            PARENT_IDENTITY: parent_payload(),
        },
        stacks={CHILD_IDENTITY: None},
        bodies={CHILD_IDENTITY: shared_body(PARENT_IDENTITY.number)},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (DECOY_IDENTITY,),
        }),
    )

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_BELOW, CHILD_TIP),
            opener=Opener(forge=forge),
        ),
    )

    assert identified.parent is not None, "the body should name a parent"
    assert identified.parent.identity == PARENT_IDENTITY, (
        "the pull request the body names is the parent"
    )
    assert DECOY_IDENTITY not in forge.read, (
        "the association below the child should not be reached once the body answers"
    )
    assert identified.shared_record == wheresat_shared_record.SharedRecord(
        parent=PARENT_IDENTITY, boundary=BOUNDARY
    ), "the reading should ride back so the collection phase credits the claim"
    assert identified.error_kind is None, "a claim this run can take up is no fault"


def test_a_body_that_claims_nothing_leaves_the_walk_to_continue(
    recording_recorder: RecordingRecorder,
) -> None:
    """Prose that names no label is not a claim, and not a fault either."""
    forge = Forge(
        payloads={
            CHILD_IDENTITY: child_payload(),
            PARENT_IDENTITY: parent_payload(),
        },
        stacks={CHILD_IDENTITY: None},
        bodies={CHILD_IDENTITY: SILENT_BODY},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (PARENT_IDENTITY,),
        }),
    )

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_BELOW, CHILD_TIP),
            opener=Opener(forge=forge),
        ),
    )

    assert identified.parent is not None, "the search below should still answer"
    assert identified.parent.identity == PARENT_IDENTITY, (
        "a body claiming nothing should send the walk to the commits below"
    )
    assert identified.shared_record == wheresat_shared_record.SharedRecordAbsent(), (
        "a body read and claiming nothing is reported as having claimed nothing"
    )


def test_a_body_that_cannot_be_read_stops_the_ladder(
    recording_recorder: RecordingRecorder,
) -> None:
    """A claim this run cannot take up is a fault, not a reason to walk on."""
    half = shared_body(PARENT_IDENTITY.number).splitlines()[0]
    forge = Forge(
        payloads={CHILD_IDENTITY: child_payload()},
        stacks={CHILD_IDENTITY: None},
        bodies={CHILD_IDENTITY: half},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (DECOY_IDENTITY,),
        }),
    )

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_BELOW, CHILD_TIP),
            opener=Opener(forge=forge),
        ),
    )

    assert identified.parent is None, "a body that claimed a record names no parent"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert "could not be read" in identified.faults[0], (
        "the fault should say the body's record was what could not be read"
    )
    assert identified.error_kind == "stack_record_malformed", (
        "a record this version cannot read is the stack-record class of fault"
    )
    assert DECOY_IDENTITY not in forge.read, (
        "the walk should stop rather than fall back to a weaker rung"
    )
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["unavailable"], (
        "the unanswered question should be recorded as such"
    )


def test_a_body_that_supports_several_readings_names_every_reading(
    recording_recorder: RecordingRecorder,
    snapshot: SnapshotAssertion,
) -> None:
    """A disagreement is reported with every reading rather than resolved.

    The body names two parents and two boundaries, and a reading is a parent
    read with a boundary, so it supports four of them: each pairing is one the
    body gives and none of them is a reading this run may take. The two
    boundaries are distinct so that a sentence carrying one boundary through
    every reading fails here rather than passing on a body whose readings
    happened to agree about it.
    """
    body = "\n".join((
        shared_body(PARENT_IDENTITY.number),
        shared_body(DECOY_IDENTITY.number, boundary=OTHER_BOUNDARY),
    ))
    forge = Forge(
        payloads={CHILD_IDENTITY: child_payload()},
        stacks={CHILD_IDENTITY: None},
        bodies={CHILD_IDENTITY: body},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (DECOY_IDENTITY,),
        }),
    )

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_BELOW, CHILD_TIP),
            opener=Opener(forge=forge),
        ),
    )

    assert identified.parent is None, "this run will not choose among readings"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert identified.faults[0] == snapshot, (
        "the refusal names every reading the body supports, in the order the "
        "body gives them, so the sentence is recorded rather than probed for "
        "the parts a reader expects"
    )
    assert identified.error_kind == "stack_record_malformed", (
        "two readings of one record are the stack-record class of fault"
    )
