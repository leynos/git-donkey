"""What an incomplete evidence set does to the verdict a run reports.

Collection reports what it could not read, and
:func:`git_donkey.wheresat_policy.apply_collection_faults` is the one place
those reports are read into a verdict. It is asked about two kinds of
incompleteness here, which are one question with two answers: a source that
could not reply leaves the candidates that were read where they were, while a
set cut at the candidate bound is missing candidates rather than answers. The
refusal a fault cannot leave standing, and the establishment it can, are what
the cases below pin. The gate table itself, and the rest of what a run does
with the gates it collected, is in ``test_wheresat_policy``.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_collection_faults -q
"""

from __future__ import annotations

from git_donkey.wheresat_policy import apply_collection_faults
from git_donkey.wheresat_records import Established, Indeterminate, Unresolved
from tests.unit.wheresat_helpers import assessment_of, permissive
from tests.unit.wheresat_variants import with_candidates


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


def test_a_set_cut_at_the_candidate_bound_cannot_establish() -> None:
    """A truncated candidate set is incomplete evidence like any other.

    Established is the one verdict a fault does not overturn, because the
    candidate that carried it passed every gate it needed and a fault in
    another source neither checked nor unchecked it. Truncation is the case
    that reasoning does not cover: the candidates a cut removed were never
    weighed, so the one that established the boundary is the survivor of a set
    nobody finished reading.
    """
    established = assessment_of(permissive())
    assert isinstance(established, Established), "the corpus starts established"

    truncated = "the candidate set was cut at the 32-candidate bound"
    forced = apply_collection_faults(established, (truncated,), capped=True)

    assert isinstance(forced, Indeterminate), (
        "a boundary chosen from the candidates that survived a cut is not one "
        "the run can vouch for"
    )
    assert forced.reasons[0] == truncated, "the cut is what the report leads with"
    assert forced.candidates == established.support, (
        "the support that carried the answer is reported unchanged"
    )
    assert forced.gates == established.gates, "and so are the gates it was judged by"


def test_the_bound_is_its_own_signal_rather_than_a_fault() -> None:
    """A cut reported with no fault still refuses to establish a boundary.

    The collect side reports the cut as a reason beside the flag, so the two
    arrive together in a real run. The policy is asked to decide on the flag
    alone, because a reader that had to find a fault to distrust the set would
    believe the next cut that happened to be reported without one.
    """
    established = assessment_of(permissive())
    assert isinstance(established, Established), "the corpus starts established"

    cut = apply_collection_faults(established, (), capped=True)

    assert isinstance(cut, Indeterminate), (
        "reaching the bound is what makes the evidence incomplete"
    )


def test_a_run_that_found_no_fault_is_unchanged() -> None:
    """The absence of faults is not a fault of its own."""
    refused = assessment_of(with_candidates(permissive()))

    assert apply_collection_faults(refused, ()) is refused, (
        "an empty fault list changes nothing, not even the object identity"
    )
