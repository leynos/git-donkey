"""The parent ladder: which rung answers, and what an unanswered question is.

Two claims are pinned here, and they are the two the ladder exists to keep
apart. The first is the rung order: a parent the user named is consulted and no
weaker question is asked, the child's own pull request is walked past rather
than reported, and a stack GitHub records answers before the association search
continues. The second is what a question that went unanswered *is* — a fault,
which the run reports as an indeterminate result, and never a quieter walk to
the next rung (ADR-005, INV-5). The credential case is the one that matters
most, because the class it raises is a usage failure everywhere else and the
ladder is the only caller for which the run's result is the evidence's rather
than the environment's.

Two things are deliberately *not* asked of a forge: a checkout that names no
GitHub repository has no question to put, and a run told ``--offline`` may not
put one. Both leave the walk skipped, because a question the run never asked is
not a question the forge failed to answer. The offline decision comes before
every rung, ``--parent`` included: a named parent is read from the forge as any
other is, so a run that may not ask may not answer that one either.

The two rungs that read what the child itself carries — the stack record its
branch holds and the shared record its pull request body carries — are stated
in ``test_wheresat_parents_child.py``.

Nothing here opens a repository or a socket: the history, the record reader and
the forge are doubles, one to a module named for what it answers —
``wheresat_parents_history``, ``wheresat_parents_record`` and
``wheresat_parents_forge``, over the values in ``wheresat_parents_corpus`` —
with the builders that put a question to the ladder in
``tests.unit.wheresat_parents_helpers``. Every rung is therefore driven without
a network, and the assertions can be about what was asked as much as about what
was answered.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import (
    wheresat_github,
)
from git_donkey.wheresat_errors import (
    ShallowHistoryError,
    WheresatCredentialError,
    WheresatGitHubError,
)
from tests.unit.wheresat_helpers import (
    CHILD_BELOW,
    CHILD_TIP,
    PR_IDENTITY,
    parent_pull_request,
)
from tests.unit.wheresat_parents_corpus import (
    ASSOCIATION_REPOSITORY,
    CHILD_BRANCH,
    CHILD_IDENTITY,
    DECOY_IDENTITY,
    FOREIGN_IDENTITY,
    PARENT_IDENTIFICATION,
    PARENT_IDENTITY,
    SECOND_CHILD_IDENTITY,
    SILENT_BODY,
)
from tests.unit.wheresat_parents_forge import Forge, Opener
from tests.unit.wheresat_parents_helpers import (
    Run,
    ask,
    boundary_request,
    search_bounds,
)
from tests.unit.wheresat_parents_history import History, graph_over
from tests.unit.wheresat_parents_payloads import (
    association_page,
    child_payload,
    parent_payload,
)
from tests.unit.wheresat_parents_record import Records, stacked_on

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder


def test_a_named_parent_is_read_from_the_forge(
    recording_recorder: RecordingRecorder,
) -> None:
    """A parent the user named is the whole of the ladder's work."""
    forge = Forge(payloads={PR_IDENTITY: parent_pull_request()})
    opener = Opener(forge=forge)
    records = Records(record=stacked_on(PR_IDENTITY))

    identified = ask(
        boundary_request(parent=PR_IDENTITY),
        search_bounds(repository=None),
        run=Run(records=records, opener=opener),
    )

    assert identified.parent is not None, "the named parent should have been read"
    assert identified.parent.identity == PR_IDENTITY, (
        "the run should answer about the pull request it was told to consult"
    )
    assert identified.faults == (), "a parent the forge named is no fault"
    assert forge.read == [PR_IDENTITY], (
        "a named parent should be the only pull request read"
    )
    assert not records.reads, (
        "a stronger rung answered, so the child's record should not be read"
    )
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["found"], (
        "the identification should be recorded as found"
    )


def test_a_named_parent_carries_the_answer_its_own_repository_holds(
    recording_recorder: RecordingRecorder,
) -> None:
    """A pull request number names a pull request only within its repository.

    The child's own pull request and the parent named here share a number and
    differ in repository, so the forge holds two answers that a mapping keyed by
    number could not hold at once: a question about either would be answered
    with the other's payload. The named parent is the shortest rung that reaches
    a payload, so it is where the two are told apart, and the child's answer is
    seeded last so that a forge holding one answer for the shared number would
    be holding that one.
    """
    foreign_head = "parent-of-another-repository"
    forge = Forge(
        payloads={
            FOREIGN_IDENTITY: parent_pull_request(
                identity=FOREIGN_IDENTITY,
                head_ref=foreign_head,
                head_repository=FOREIGN_IDENTITY.repository,
            ),
            CHILD_IDENTITY: child_payload(),
        }
    )

    identified = ask(
        boundary_request(parent=FOREIGN_IDENTITY),
        search_bounds(),
        run=Run(opener=Opener(forge=forge)),
    )

    assert identified.parent is not None, "the named parent should have been read"
    assert identified.parent.head_ref == foreign_head, (
        "the payload read is the one held for the repository the number was "
        "asked under, not the child's payload for the same number"
    )
    assert forge.read == [FOREIGN_IDENTITY], (
        "the question is recorded as the pull request whole, so two repositories "
        "that share a number are two questions rather than one"
    )


def test_a_named_parent_is_skipped_when_no_opener_is_offered(
    recording_recorder: RecordingRecorder,
) -> None:
    """A caller with no forge to offer leaves every question unasked."""
    identified = ask(boundary_request(parent=PR_IDENTITY), search_bounds())

    assert identified.parent is None, "no forge means no parent"
    assert identified.faults == (), "asking nothing is not a question gone unanswered"
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["skipped"], (
        "the skip should be recorded rather than passed over in silence"
    )


def test_a_credential_that_cannot_be_opened_is_a_fault(
    recording_recorder: RecordingRecorder,
) -> None:
    """A missing credential is an unanswered question, not a refusal to start.

    The run's result is the evidence's, so the ladder reports the refusal as a
    fault rather than letting the class reach the usage handler: the exit
    status is Table 3's row for a credential that is not there, which is ``3``,
    and the reason the message carries is the one naming every source that was
    tried.
    """
    opener = Opener(refusal=WheresatCredentialError("no GitHub credential: set …"))

    identified = ask(
        boundary_request(parent=PR_IDENTITY), search_bounds(), run=Run(opener=opener)
    )

    assert identified.parent is None, "a credential that is not there names no parent"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert "the forge could not be opened" in identified.faults[0], (
        "the fault should say which step failed"
    )
    assert "no GitHub credential" in identified.faults[0], (
        "the fault should carry the refusal's own account of what was missing"
    )
    assert identified.error_kind == "credential_unavailable", (
        "the run should report the credential as the bounded class of the fault"
    )
    assert recording_recorder.error_kinds(PARENT_IDENTIFICATION) == [
        "credential_unavailable"
    ], "the observation should carry the same class the identification reports"


def test_a_forge_that_will_not_answer_is_a_fault(
    recording_recorder: RecordingRecorder,
) -> None:
    """A forge that fails to open stops the ladder rather than the run."""
    opener = Opener(refusal=WheresatGitHubError("GitHub did not answer"))

    identified = ask(
        boundary_request(parent=PR_IDENTITY), search_bounds(), run=Run(opener=opener)
    )

    assert identified.parent is None, "a fault is never read as a parent"
    assert identified.error_kind == "github_api_error", (
        "a transport failure is the GitHub class of fault"
    )
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["unavailable"], (
        "an unanswered question should be recorded as unavailable"
    )


def test_a_checkout_that_names_no_repository_asks_nothing(
    recording_recorder: RecordingRecorder,
) -> None:
    """No GitHub repository to ask about is no question to put."""
    opener = Opener(forge=Forge())

    identified = ask(
        boundary_request(), search_bounds(repository=None), run=Run(opener=opener)
    )

    assert identified.parent is None, "a checkout with no repository names no parent"
    assert identified.faults == (), "having nothing to ask is not a failure to answer"
    assert not opener.opened, (
        "a run with no question should not open the forge to ask it"
    )


def test_an_offline_run_asks_nothing_of_an_offered_forge(
    recording_recorder: RecordingRecorder,
) -> None:
    """``--offline`` is a property of the run, not a promise the ladder keeps."""
    opener = Opener(forge=Forge())

    identified = ask(
        boundary_request(offline=True), search_bounds(), run=Run(opener=opener)
    )

    assert identified.parent is None, "an offline run names no parent from the forge"
    assert identified.faults == (), "declining to ask is not a question unanswered"
    assert not opener.opened, "an offline run should not open the forge at all"
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["skipped"], (
        "the skip should be recorded as such"
    )


def test_an_offline_run_does_not_read_a_parent_it_was_told_about(
    recording_recorder: RecordingRecorder,
) -> None:
    """``--offline`` outranks ``--parent``, because a named parent is a read.

    The strongest rung is still a question put to the forge — the address came
    from the user, but the pull request it names is read as any other — so a
    run that may not ask must not answer this one either. The parent is
    skipped rather than read, and the walk never reaches the opener it was
    offered.
    """
    opener = Opener(forge=Forge())

    identified = ask(
        boundary_request(parent=PR_IDENTITY, offline=True),
        search_bounds(),
        run=Run(opener=opener),
    )

    assert identified.parent is None, "an offline run names no parent, named or not"
    assert identified.faults == (), "declining to ask is not a question unanswered"
    assert not opener.opened, "an offline run should not open the forge at all"
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["skipped"], (
        "the skip should be recorded as such"
    )


def test_a_history_longer_than_the_window_refuses_the_search(
    recording_recorder: RecordingRecorder,
) -> None:
    """A window that stopped short cannot say that nothing names a boundary.

    The refusal names the bound and the way out of it, because the answer the
    search would otherwise give is a negative one — "no pull request is
    associated with these commits" — that a partial history cannot support.
    """
    limit = 3
    # Twice the bound, and one commit per value, so a window taken from the tip
    # is a different four commits from one taken from the root: the refusal is
    # the same either way, and only the values the read kept tell the two apart.
    commits = tuple(f"{index:040d}" for index in range(limit * 2))
    opener = Opener(forge=Forge())
    history = graph_over(*commits)

    identified = ask(
        boundary_request(),
        search_bounds(limit=limit),
        run=Run(history=history, opener=opener),
    )

    assert identified.parent is None, "a truncated window names no parent"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert f"more commits than the {limit}" in identified.faults[0], (
        "the refusal should name the bound the history exceeded"
    )
    assert "--parent" in identified.faults[0], (
        "the refusal should name the way out of the search"
    )
    assert history.limits == [limit + 1], (
        "one commit beyond the bound is asked for, so a full window is recognizable"
    )
    assert history.windows == [commits[-(limit + 1) :]], (
        "the read keeps the newest commits, which are the ones a squash lands"
    )
    assert identified.error_kind == "search_incomplete", (
        "the bound the run set is what stopped the search, not the forge"
    )


def test_a_shallow_history_is_reported_as_a_shallow_history(
    recording_recorder: RecordingRecorder,
) -> None:
    """A graft that stopped the read is labelled for the operator."""
    refusal = ShallowHistoryError("cannot trust the history: the clone is shallow")
    opener = Opener(forge=Forge())

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(history=History(refusal=refusal), opener=opener),
    )

    assert identified.parent is None, "a history that could not be read names nothing"
    assert identified.error_kind == "shallow_history", (
        "the operator should be told to deepen the clone rather than to debug Git"
    )
    assert "could not be read" in identified.faults[0], (
        "the fault should say the history was the question that failed"
    )


def test_the_childs_own_pull_request_is_walked_past(
    recording_recorder: RecordingRecorder,
) -> None:
    """The first association that is not the child's own is the parent.

    The walk is ordered: the newest commit is asked about first, because it is
    the commit most likely to be the child's own, and the pull request reached
    through the commit below it is the parent.
    """
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
    history = graph_over(CHILD_BELOW, CHILD_TIP)

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(history=history, opener=Opener(forge=forge)),
    )

    assert identified.parent is not None, "the commit below the child should name one"
    assert identified.parent.identity == PARENT_IDENTITY, (
        "the parent is the association that is not the child's own pull request"
    )
    assert forge.read == [CHILD_IDENTITY, PARENT_IDENTITY], (
        "the child's own pull request is read before the parent's"
    )
    assert forge.bodies_read == [CHILD_IDENTITY], (
        "the child's body is read once, before the walk continues past the child"
    )
    assert history.revisions == [CHILD_TIP], (
        "the walk should read the history from the child's own tip"
    )
    assert identified.faults == (), "a parent the search found is no fault"


def test_a_native_stack_names_the_parent_before_the_walk_continues(
    recording_recorder: RecordingRecorder,
) -> None:
    """A stack GitHub records is a statement, and outranks an inference."""
    forge = Forge(
        payloads={
            CHILD_IDENTITY: child_payload(stacked=True),
            PARENT_IDENTITY: parent_payload(),
        },
        stacks={CHILD_IDENTITY: PARENT_IDENTITY},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (DECOY_IDENTITY,),
        }),
    )
    history = graph_over(CHILD_BELOW, CHILD_TIP)

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(history=history, opener=Opener(forge=forge)),
    )

    assert identified.parent is not None, "the stack should name the parent"
    assert identified.parent.identity == PARENT_IDENTITY, (
        "the pull request below the child in the stack is the parent"
    )
    assert DECOY_IDENTITY not in forge.read, (
        "the association below the child should not be reached once the stack answers"
    )
    assert not forge.bodies_read, "a stronger rung answered, so no body should be read"


def test_a_second_child_head_is_not_asked_for_a_stack(
    recording_recorder: RecordingRecorder,
) -> None:
    """A branch that heads two pull requests asks its stack question once.

    The first association whose head is the child branch is the child, and the
    stack question belongs to the child rather than to the association it was
    reached through, so a second pull request the branch heads is read and
    walked past without asking GitHub about its stack.
    """
    forge = Forge(
        payloads={
            CHILD_IDENTITY: child_payload(),
            SECOND_CHILD_IDENTITY: parent_pull_request(
                identity=SECOND_CHILD_IDENTITY, head_ref=CHILD_BRANCH
            ),
        },
        stacks={CHILD_IDENTITY: None},
        bodies={CHILD_IDENTITY: SILENT_BODY},
        page=association_page({
            CHILD_TIP: (CHILD_IDENTITY,),
            CHILD_BELOW: (SECOND_CHILD_IDENTITY,),
        }),
    )
    history = graph_over(CHILD_BELOW, CHILD_TIP)

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(history=history, opener=Opener(forge=forge)),
    )

    assert identified.parent is None, (
        "neither child pull request should be reported as the parent"
    )
    assert identified.faults == (), "reading two child pull requests is no fault"
    assert forge.read == [CHILD_IDENTITY, SECOND_CHILD_IDENTITY], (
        "both pull requests the branch heads should be read, newest association first"
    )
    assert forge.bodies_read == [CHILD_IDENTITY], (
        "only the child whose stack was asked about should have its body read"
    )
    assert history.revisions == [CHILD_TIP], (
        "reading two child pull requests is still one history question"
    )
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["empty"], (
        "the walk should run out of associations without a stack answer"
    )


def test_a_search_that_found_nothing_is_empty_and_not_a_fault(
    recording_recorder: RecordingRecorder,
) -> None:
    """A search that saw the whole history and found none has answered."""
    forge = Forge(page=association_page({CHILD_TIP: (), CHILD_BELOW: ()}))

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(
            history=graph_over(CHILD_BELOW, CHILD_TIP),
            opener=Opener(forge=forge),
        ),
    )

    assert identified.parent is None, "nothing was associated with the child"
    assert identified.faults == (), "an answer of nothing is not a fault"
    assert recording_recorder.outcomes(PARENT_IDENTIFICATION) == ["empty"], (
        "the empty answer should be recorded as its own outcome"
    )


def test_a_search_that_stopped_short_refuses(
    recording_recorder: RecordingRecorder,
) -> None:
    """A page the adapter truncated is a refusal, not a short answer."""
    forge = Forge(page=association_page({CHILD_TIP: ()}, truncated=True))

    identified = ask(
        boundary_request(),
        search_bounds(),
        run=Run(history=graph_over(CHILD_TIP), opener=Opener(forge=forge)),
    )

    assert identified.parent is None, "a search that stopped short names no parent"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert "stopped after 1" in identified.faults[0], (
        "the refusal should name how much of the history the search saw"
    )
    assert "--parent" in identified.faults[0], (
        "the refusal should name the way out of the search"
    )
    assert identified.error_kind == "search_incomplete", (
        "the adapter's budget is what stopped the search, and it is not the forge"
    )


def test_the_window_is_capped_by_the_adapter_ceiling(
    recording_recorder: RecordingRecorder,
) -> None:
    """``--limit`` may ask for fewer commits, and never for more."""
    history = graph_over(CHILD_TIP)

    ask(
        boundary_request(),
        search_bounds(limit=wheresat_github.ASSOCIATION_SEARCH_LIMIT * 10),
        run=Run(
            history=history,
            opener=Opener(forge=Forge(page=association_page({CHILD_TIP: ()}))),
        ),
    )

    assert history.limits == [wheresat_github.ASSOCIATION_SEARCH_LIMIT + 1], (
        "the search should be bounded by the adapter's ceiling"
    )
    assert history.revisions == [CHILD_TIP], (
        "the bounded history should still be read from the child's tip"
    )


def test_a_limit_below_one_is_read_as_one(
    recording_recorder: RecordingRecorder,
) -> None:
    """A search allowed no commits has nothing to report but that it saw none."""
    history = graph_over(CHILD_TIP)
    forge = Forge(page=association_page({CHILD_TIP: ()}))

    ask(
        boundary_request(),
        search_bounds(limit=0),
        run=Run(
            history=history,
            opener=Opener(forge=forge),
        ),
    )

    assert history.limits == [2], (
        "a limit below one should ask about one commit, and one beyond it"
    )
    assert forge.searches == [(ASSOCIATION_REPOSITORY, (CHILD_TIP,))], (
        "the search should be put to the run's repository with the one commit "
        "the bound allows, and no other"
    )


@pytest.mark.parametrize("limit", [1, 2, 20])
def test_a_history_within_the_window_is_searched(
    limit: int, recording_recorder: RecordingRecorder
) -> None:
    """A window that saw the whole history is an answer about that history."""
    commits = tuple(f"{index:040d}" for index in range(limit))
    forge = Forge(page=association_page(dict.fromkeys(commits, ())))

    identified = ask(
        boundary_request(),
        search_bounds(limit=limit),
        run=Run(history=graph_over(*commits), opener=Opener(forge=forge)),
    )

    assert identified.faults == (), "a history inside the bound is not a refusal"
    assert forge.searches == [(ASSOCIATION_REPOSITORY, tuple(reversed(commits)))], (
        "the search should be put to the run's repository for the window the "
        "walk read, newest commit first"
    )
