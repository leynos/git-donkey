"""The ``git wheresat`` report, pinned as text and as a machine envelope.

The report is the command's user-visible contract, so both renderers are pinned
exactly: snapshots for the three verdicts, the explained gate table, and the
truncation tails, and literal assertions for the properties a snapshot cannot
state — that the envelope names the same verdict and exit status the process
will report, that every envelope carries every key, and that the envelope
reports the same boundary the text report prints.

Nothing here opens a repository. The report is a projection of an assessment
and a request, and the assessments come from the same builders the policy suite
weighs, so a rendering defect is reported as a rendering defect rather than as
a disagreement about evidence.
"""

from __future__ import annotations

import dataclasses
import json
import typing as typ

import pytest

from git_donkey import wheresat_report as report
from git_donkey.wheresat_records import (
    COMMIT_ABBREVIATION,
    EXIT_CODES,
    EXIT_USAGE,
    Assessment,
    BoundaryRequest,
    Established,
    EvidenceKind,
    GateName,
    GateOutcome,
    GitOperation,
    Indeterminate,
    Unresolved,
    WorktreeState,
    backup_ref,
)
from tests.unit.wheresat_helpers import (
    FORK_POINT_SOURCE,
    MERGE_BASE_SOURCE,
    OLD_BASE,
    OTHER_BASE,
    RECORD_SOURCE,
    Case,
    assessment_of,
    attested,
    derived,
    parented,
    permissive,
)
from tests.unit.wheresat_variants import (
    long_replay_range,
    spoiled,
    truncated_history,
    truncated_replay_range,
    unconsulted_parent,
    with_candidates,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


type _Builder = typ.Callable[[], Case]
"""A named case, built on demand so each example gets its own."""

_OBSTACLES: typ.Final = 2
"""The obstacles the warning case plants: a cherry-pick, and uncommitted files."""

# One case per verdict a completed run can reach, including the verdict of a
# run that found nothing at all to weigh. The refusal is rendered twice, because
# a run can reach it two ways: a gate that answered against the only candidate,
# and two candidates the gates left at one tier, where refusing to choose
# between them is the answer.
_CASES: typ.Final[typ.Mapping[str, _Builder]] = {
    "established": permissive,
    "established-with-parent": parented,
    "unresolved": lambda: spoiled(GateName.PARENT_MERGED, GateOutcome.FAILED),
    "unresolved-ambiguous": lambda: with_candidates(
        permissive(),
        derived(OLD_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
        derived(OLD_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
        derived(OTHER_BASE, EvidenceKind.MERGE_BASE, source=MERGE_BASE_SOURCE),
        derived(OTHER_BASE, EvidenceKind.FORK_POINT, source=FORK_POINT_SOURCE),
    ),
    "indeterminate": unconsulted_parent,
    "nothing-found": lambda: with_candidates(permissive()),
}


def _rendered(name: str, *, explain: bool = False) -> str:
    """Return the text report the named case renders."""
    case = _CASES[name]()
    return report.render_text(assessment_of(case), case.request, explain=explain)


def _envelope(name: str) -> dict[str, object]:
    """Return the envelope the named case renders, parsed."""
    case = _CASES[name]()
    return json.loads(report.render_json(assessment_of(case), case.request))


def _gates(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return the gate table a parsed envelope carries.

    Returns
    -------
    list[dict[str, object]]
        One object per gate, which is the shape a consumer reads: the envelope
        is untyped JSON, so the shape is asserted here rather than assumed.

    """
    gates = payload["gates"]
    assert isinstance(gates, list), "every envelope lists its gate table"
    return gates


def _side(payload: dict[str, object], name: str) -> dict[str, object]:
    """Return one side of the partition a parsed envelope carries.

    Parameters
    ----------
    payload : dict[str, object]
        Parsed envelope.
    name : str
        ``included`` or ``excluded``.

    Returns
    -------
    dict[str, object]
        The side's object, whose shape is asserted here for the same reason
        :func:`_gates` asserts its own: the envelope is untyped JSON.

    """
    side = payload[name]
    assert isinstance(side, dict), "every envelope carries both sides of the partition"
    return side


def _established() -> tuple[Established, BoundaryRequest]:
    """Return the established assessment and the request it was made for."""
    case = permissive()
    assessment = assessment_of(case)
    assert isinstance(assessment, Established), "the case is built to establish"
    return assessment, case.request


def _refused() -> tuple[Unresolved, BoundaryRequest]:
    """Return a refusal and the request it was made for."""
    case = spoiled(GateName.PARENT_MERGED, GateOutcome.FAILED)
    assessment = assessment_of(case)
    assert isinstance(assessment, Unresolved), "the case is built to refuse"
    return assessment, case.request


@pytest.mark.parametrize("name", tuple(_CASES))
def test_text_report_matches_snapshot(name: str, snapshot: SnapshotAssertion) -> None:
    """Each verdict should render the text it rendered when it was reviewed."""
    assert _rendered(name) == snapshot, (
        "expected the rendered report to match the recorded snapshot"
    )


@pytest.mark.parametrize("name", ["established", "established-with-parent"])
def test_explained_report_matches_snapshot(
    name: str, snapshot: SnapshotAssertion
) -> None:
    """An established run asked to explain itself should print its gate table."""
    assert _rendered(name, explain=True) == snapshot, (
        "expected the explained report to match the recorded snapshot"
    )


def test_explaining_an_established_run_adds_only_the_gate_table() -> None:
    """The gate table is the whole of what ``--explain`` adds."""
    assessment, _ = _established()
    gates = "\n".join(report._text_gates(assessment)) + "\n"
    plain = _rendered("established")
    explained = _rendered("established", explain=True)

    assert gates in explained, "the gate table is rendered where it belongs"
    assert explained.replace(gates, "", 1) == plain, (
        "expected --explain to add the gate table and change nothing else"
    )


def test_a_refusal_prints_the_gate_table_without_being_asked() -> None:
    """Nothing was established, so the gates are what the reasons are about."""
    rendered = _rendered("unresolved")

    assert "Gates" in rendered, "a refusal prints the gates unasked"
    assert "Reasons" in rendered, "a refusal names why it refused"


def test_truncation_tails_match_snapshots(snapshot: SnapshotAssertion) -> None:
    """A range that was listed in part must report the part it left out."""
    outranking = with_candidates(
        permissive(),
        attested(OLD_BASE, source=RECORD_SOURCE),
        derived(OTHER_BASE),
    )
    long_range = long_replay_range(report.RENDER_COMMIT_LIMIT + 1)
    for case in (
        truncated_replay_range(),
        truncated_history(),
        long_range,
        outranking,
    ):
        rendered = report.render_text(assessment_of(case), case.request)
        assert rendered == snapshot, (
            "expected the truncated report to match the recorded snapshot"
        )


def test_the_two_reasons_a_listing_is_short_are_reported_apart() -> None:
    """A withheld commit and an unseen commit are different claims.

    The report cuts its own listings at :data:`RENDER_COMMIT_LIMIT`, and a range
    the run saw cut short may hold commits it never saw at all. Reporting the
    second as the first states a count the run cannot vouch for as though it
    could; reporting neither leaves a partial range reading as a whole one,
    which is how a replay loses work.
    """
    unseen = truncated_history()
    withheld = long_replay_range(report.RENDER_COMMIT_LIMIT + 1)
    cut_short = report.render_text(assessment_of(unseen), unseen.request)
    listed = report.render_text(assessment_of(withheld), withheld.request)

    assert "the range was cut short" in cut_short, (
        "a range the run did not see the end of must say so"
    )
    assert "more not listed" not in cut_short, (
        "a range the run never saw the end of withheld nothing it holds"
    )
    assert "more not listed" in listed, (
        "a listing the report shortened must name what it left out"
    )
    assert "cut short" not in listed, (
        "a complete range the report shortened was not cut short"
    )


def test_the_envelope_keeps_the_two_reasons_a_listing_is_short_apart() -> None:
    """A script must be able to tell a range seen in part from one seen whole.

    The text report states the two reasons in different words; a script reading
    the envelope has only the keys, so a single boolean for both would let a
    count the run never vouched for be read as the size of the range.
    """
    unseen = truncated_history()
    withheld = long_replay_range(report.RENDER_COMMIT_LIMIT + 1)
    cut_short = _side(
        json.loads(report.render_json(assessment_of(unseen), unseen.request)),
        "excluded",
    )
    listed = _side(
        json.loads(report.render_json(assessment_of(withheld), withheld.request)),
        "included",
    )

    assert cut_short["cutShort"] is True, "a range the run saw in part says so"
    assert cut_short["withheld"] == 0, "the report withheld nothing from that range"
    assert listed["cutShort"] is False, "a range the run saw whole was not cut short"
    assert listed["withheld"] == 1, "the listing names the commit it left out"
    assert listed["count"] == report.RENDER_COMMIT_LIMIT + 1, (
        "a complete range reports its size"
    )


def test_established_report_names_the_ref_that_retains_it() -> None:
    """The durability line reports what a reader can check the answer against."""
    assessment, request = _established()
    assert "a ref that outlives this run already reaches the boundary" in (
        report.render_text(assessment, request)
    ), "an unretained boundary says a durable ref already reaches it"

    retained = dataclasses.replace(
        assessment, durable_ref="refs/wheresat/boundary/child"
    )
    assert "retained by refs/wheresat/boundary/child" in (
        report.render_text(retained, request)
    ), "a retained boundary names the ref that keeps it"


def test_every_verdict_has_a_word_and_an_exit_status() -> None:
    """The report's vocabulary and the process's statuses cover each other."""
    assert set(report.VERDICT_WORDS) == set(EXIT_CODES), (
        "a verdict that can be reported must have a word and an exit status"
    )
    assert sorted(report.VERDICT_WORDS.values()) == [
        "established",
        "indeterminate",
        "unresolved",
    ], "the verdict words are the command's stable vocabulary"


def test_json_envelopes_match_snapshot(snapshot: SnapshotAssertion) -> None:
    """The envelope should stay byte-stable for a consumer that parses it."""
    rendered = {name: report.render_json(*_generated(name)) for name in sorted(_CASES)}
    assert rendered == snapshot, "expected the envelopes to match the snapshot"


def _generated(name: str) -> tuple[Assessment, BoundaryRequest]:
    """Return the named case's assessment and the request behind it."""
    case = _CASES[name]()
    return assessment_of(case), case.request


@pytest.mark.parametrize("name", tuple(_CASES))
def test_json_envelope_agrees_with_the_text_report(name: str) -> None:
    """Both renderers must report one verdict, one status, and one boundary."""
    assessment, request = _generated(name)
    payload = _envelope(name)

    assert payload["verdict"] == report.VERDICT_WORDS[type(assessment)], (
        "the envelope names the verdict the assessment reached"
    )
    assert payload["exitCode"] == EXIT_CODES[type(assessment)], (
        "and the status the process will exit with"
    )
    assert payload["child"] == {
        "branch": request.branch,
        "tip": request.child_tip,
    }, "and the child the request asked about"
    assert payload["target"] == request.target, (
        "and the target the child would be replayed onto"
    )
    assert payload["error"] is None, "a completed run reports no error"


def test_error_envelope_carries_every_key_a_completed_one_does() -> None:
    """A consumer must never have to branch on the shape before reading it."""
    payload = json.loads(
        report.render_error_json(EXIT_USAGE, "not inside a Git repository")
    )
    completed = _envelope("established")

    assert set(payload) == set(completed), (
        "the error envelope and a completed envelope carry the same keys"
    )
    assert payload["schema"] == report.JSON_SCHEMA, (
        "an error envelope declares the schema too"
    )
    assert payload["verdict"] == "error", (
        "with a verdict the assessment itself can never reach"
    )
    assert payload["exitCode"] == EXIT_USAGE, (
        "and the usage status, which is no verdict's status"
    )
    assert payload["error"] == "not inside a Git repository", (
        "and carries the reason the run could not answer"
    )


def test_the_established_envelope_names_the_partition_and_the_replay() -> None:
    """The envelope is a script's whole view, so it carries the answer too."""
    assessment, request = _established()
    payload = json.loads(report.render_json(assessment, request))

    assert payload["oldBase"] == assessment.old_base, (
        "the envelope names the same boundary the assessment does"
    )
    assert payload["included"]["commits"] == list(assessment.included), (
        "and lists the included side commit for commit"
    )
    assert payload["excluded"]["commits"] == list(assessment.excluded), (
        "and the excluded side just as completely"
    )
    assert payload["rebaseCommand"] == (
        f"git rebase --onto {request.target} {assessment.old_base} {request.branch}"
    ), "the envelope names the same replay the text report prints"
    assert payload["backupRef"] == backup_ref(request.branch), (
        "and the ref the report tells the user to keep the child tip under"
    )
    assert [entry["commit"] for entry in payload["support"]] == [
        candidate.commit for candidate in assessment.support
    ], "the support is reported in the order the assessment holds it"


def test_the_replay_plan_is_pasteable_and_reversible() -> None:
    """The commands are full object IDs, and a backup ref precedes them.

    A detail line may be abbreviated, because a reader resolves it against the
    repository in front of them; these commands are run later than the run that
    proposed them, so an abbreviation would be resolved against a history that
    may have moved. The ref is what the user returns to if the replay is wrong,
    and the child tip is stated beside the commands so the premise the answer
    was computed against can be checked before they are run.
    """
    assessment, request = _established()
    rendered = report.render_text(assessment, request)
    backup = backup_ref(request.branch)
    width = COMMIT_ABBREVIATION
    tip = request.child_tip

    assert f"git update-ref {backup} {tip}" in rendered, (
        "the plan keeps the child tip under a ref the user creates"
    )
    assert (
        f"git rebase --onto {request.target} {assessment.old_base} {request.branch}"
        in rendered
    ), "and replays onto the target with the boundary in full"
    assert f"git reset --hard {backup}" in rendered, (
        "and says how to undo the replay from that ref"
    )
    assert f"the child tip must still be {tip[:width]}" in rendered, (
        "and states the child tip the answer was computed against"
    )


def test_a_refusal_envelope_lists_the_candidates_it_collected() -> None:
    """A refusal a script reads must carry the evidence it was made from."""
    assessment, request = _refused()
    payload = json.loads(report.render_json(assessment, request))

    assert payload["support"] == [], "nothing was established, so nothing supported"
    assert [entry["commit"] for entry in payload["candidates"]] == [
        candidate.commit for candidate in assessment.candidates
    ], "and the candidates it weighed are all reported"
    assert payload["reasons"] == list(assessment.reasons), (
        "the refusal's reasons are carried verbatim"
    )
    assert [gate["name"] for gate in payload["gates"]] == [
        gate.name.value for gate in assessment.gates
    ], "the gate table is reported in the order the procedure lists the gates"


def test_the_envelope_reports_every_gate_even_when_not_applicable() -> None:
    """A consumer counting gates must see the same gate set on every path."""
    established = _envelope("established")
    refusal = _envelope("indeterminate")

    assert _gates(established) != [], "gates are listed"
    for payload in (established, refusal):
        for gate in _gates(payload):
            assert isinstance(gate["applicable"], bool), "applicability is stated"
            assert isinstance(gate["detail"], str), "every gate explains itself"


def test_an_indeterminate_envelope_is_not_an_unresolved_one() -> None:
    """The two "no boundary" verdicts must not be conflated by a consumer."""
    assessment, _ = _generated("indeterminate")
    assert isinstance(assessment, Indeterminate), (
        "the case built here is the indeterminate one, or nothing below proves it"
    )
    payload = _envelope("indeterminate")

    assert payload["verdict"] == "indeterminate", (
        "the envelope reports that the run could not tell"
    )
    assert payload["exitCode"] != EXIT_CODES[Unresolved], (
        "an environment that could not answer is not a refusal"
    )


def _warned_report() -> str:
    """Return an established report carrying every warning a worktree can raise."""
    assessment, request = _established()
    warnings = report.worktree_warnings(
        request.branch,
        WorktreeState(operation=GitOperation.REBASE, dirty=True),
    )
    return report.render_text(assessment, request, warnings=warnings)


def test_the_warnings_a_replay_cannot_run_under_match_snapshot(
    snapshot: SnapshotAssertion,
) -> None:
    """The prose that stops a reader running an unsafe replay must not drift."""
    assert _warned_report() == snapshot, (
        "expected the warned report to match the recorded snapshot"
    )


def test_the_envelope_carries_the_warnings_match_snapshot(
    snapshot: SnapshotAssertion,
) -> None:
    """A script reading the envelope should read the warnings there too."""
    assessment, request = _established()
    warnings = report.unknown_worktree_warning("child", "fatal: no such worktree")
    rendered = report.render_json(assessment, request, warnings=warnings)
    assert rendered == snapshot, (
        "expected the warned envelope to match the recorded snapshot"
    )


def test_a_warning_is_added_under_the_headline_and_changes_nothing_else() -> None:
    """The warning is owed on every verdict, so it cannot belong to one section."""
    assessment, request = _established()
    warnings = ("a rebase is already in progress in the worktree holding child",)
    plain = report.render_text(assessment, request)
    warned = report.render_text(assessment, request, warnings=warnings)
    section = "\n".join(report._text_warnings(warnings)) + "\n"

    assert "Warnings" in warned, "the section is rendered"
    assert warned.replace(section, "", 1) == plain, (
        "expected a warning to add its section and change nothing else"
    )


@pytest.mark.parametrize("name", tuple(_CASES))
def test_every_verdict_renders_a_warning_it_is_given(name: str) -> None:
    """A run that cannot replay must say so whatever it made of the boundary."""
    case = _CASES[name]()
    warnings = report.worktree_warnings(
        case.request.branch, WorktreeState(operation=None, dirty=True)
    )
    rendered = report.render_text(assessment_of(case), case.request, warnings=warnings)

    assert "Warnings" in rendered, "the warning is rendered on this verdict too"


def test_a_clean_idle_worktree_warns_about_nothing() -> None:
    """A run with nothing to warn about prints no section and emits no prose."""
    assert not report.worktree_warnings(
        "child", WorktreeState(operation=None, dirty=False)
    ), "a worktree at rest raises no warning"


def test_a_stopped_rebase_and_dirty_files_are_warned_about_separately() -> None:
    """Two obstacles are two lines, each naming what the reader must repair."""
    warnings = report.worktree_warnings(
        "child", WorktreeState(operation=GitOperation.CHERRY_PICK, dirty=True)
    )

    assert len(warnings) == _OBSTACLES, "each obstacle is reported in its own right"
    assert "cherry-pick" in warnings[0], "the operation is named"
    assert "uncommitted changes" in warnings[1], "the changes are named"
    assert all("child" in warning for warning in warnings), (
        "each warning names the branch it is about"
    )


def test_an_unreadable_worktree_is_warned_about_rather_than_dropped(
    snapshot: SnapshotAssertion,
) -> None:
    """Silence would read as a clean worktree, which is a claim the run cannot make."""
    (warning,) = report.unknown_worktree_warning("child", "fatal: bad object")

    assert warning == snapshot, (
        "the warning says the answer is missing, carries what Git reported, and "
        "names the branch it is about"
    )
