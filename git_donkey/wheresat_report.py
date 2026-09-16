"""Render a ``git wheresat`` assessment as text or as a JSON envelope.

Nothing here reads anything. The report is a projection of the assessment and
the request, so a snapshot test can pin every forensic path — an established
boundary, a refusal, and an environment that could not answer — without a
repository, and so the two renderers cannot disagree about what a run found:
they read the same values, and neither goes back to Git.

The JSON envelope is spelled out key by key rather than produced by
``dataclasses.asdict``. Renaming a field of the assessment is then a private
refactor, and the wire format changes only when a key is deliberately added or
retyped, which the schema string in the envelope records. An object is emitted
on every exit code, including the usage errors raised before any assessment
exists, so a consumer never has to read prose to find out what happened.

"""

from __future__ import annotations

import json
import types
import typing as typ

from git_donkey.wheresat_records import (
    COMMIT_ABBREVIATION,
    EXIT_CODES,
    TIERS,
    Assessment,
    BoundaryRequest,
    Candidate,
    Established,
    EvidenceKind,
    Indeterminate,
    Unresolved,
    WorktreeState,
    backup_ref,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey import stack_records
    from git_donkey.observability import WheresatVerdictLabel

RENDER_COMMIT_LIMIT: typ.Final = 20
"""Commits listed per range before the report prints a truncation tail."""

JSON_SCHEMA: typ.Final = "git-wheresat/1"
"""Version string of the machine-readable envelope."""

VERDICT_WORDS: typ.Final[cabc.Mapping[type, WheresatVerdictLabel]] = (
    types.MappingProxyType({
        Established: "established",
        Unresolved: "unresolved",
        Indeterminate: "indeterminate",
    })
)
"""Verdict word per assessment, keyed as ``EXIT_CODES`` is.

``error`` is the fourth verdict and is not here, because it is not an
assessment: a run that never collected evidence reports it. The run itself
reads this table to label the observation it records, so the word a report
prints and the word a recorder stores cannot drift apart — which is why the
words are typed as the label a
:class:`~git_donkey.observability.Observation` carries, rather than as bare
strings a reader would have to check by eye.
"""

ERROR_VERDICT: typ.Final[WheresatVerdictLabel] = "error"
"""The verdict a run that never reached an assessment reports.

Typed as the label rather than written where it is used, so the wire value and
the vocabulary a recorder stores cannot drift apart: dropping ``error`` from
:data:`~git_donkey.observability.WheresatVerdictLabel` would fail the
typecheck here rather than ship a verdict no consumer knows.
"""

_HEADLINES: typ.Final[cabc.Mapping[type, str]] = types.MappingProxyType({
    Unresolved: "git wheresat: no boundary could be established for",
    Indeterminate: "git wheresat: could not tell where",
})
"""How a run that established nothing opens its report."""

_NOT_APPLICABLE: typ.Final = "not applicable"
"""How the gate table renders a gate whose subject the run never set out to use."""

type _Payload = dict[str, object]
"""One JSON object of the envelope."""


def worktree_warnings(branch: str, state: WorktreeState) -> tuple[str, ...]:
    """Return what a run warns about the worktree holding the child branch.

    A refusal to *run* the replay is not a refusal to *report* one: the command
    is safe to run with a stopped rebase or a dirty tree, and the command it
    prints is not, so the two states are reported as warnings and change
    neither the verdict nor the exit status.

    Parameters
    ----------
    branch : str
        Child branch the run read.
    state : WorktreeState
        What the worktree holding it is in the middle of.

    Returns
    -------
    tuple[str, ...]
        One warning per obstacle, in the order the worktree can be repaired:
        the operation first, then the changes that would also stop a replay.

    """
    warnings = []
    if state.operation is not None:
        warnings.append(
            f"a {state.operation.value} is already in progress in the worktree "
            f"holding {branch}, so no replay can run there until it is finished "
            "or aborted"
        )
    if state.dirty:
        warnings.append(
            f"the worktree holding {branch} has uncommitted changes, so no "
            "replay can run there until they are committed or stashed"
        )
    return tuple(warnings)


def unknown_worktree_warning(branch: str, reason: str) -> tuple[str, ...]:
    """Return the warning a run that could not read the worktree reports.

    The state of a worktree is not evidence about a boundary, so failing to
    read it cannot make a verdict wrong and does not make the run
    indeterminate. It is reported instead of being dropped, because a run that
    warns about nothing is read as a run with nothing to warn about.

    Parameters
    ----------
    branch : str
        Child branch the run read.
    reason : str
        What Git reported.

    Returns
    -------
    tuple[str, ...]
        The warning, naming the reason the question went unanswered.

    """
    message = (
        f"the worktree holding {branch} could not be read, so whether a replay "
        f"can run there is not known: {reason}"
    )
    return (message,)


def candidates_of(assessment: Assessment) -> tuple[Candidate, ...]:
    """Return the candidates the assessment carries, in its own order.

    Parameters
    ----------
    assessment : Assessment
        Assessment to read. An established result reports the evidence that
        carried it; a refusal or an indeterminate result reports every
        candidate that was collected, which is what its report needs to show
        beside the reasons it gives.

    Returns
    -------
    tuple[Candidate, ...]
        The candidates the report names.

    """
    match assessment:
        case Established(support=support):
            return tuple(support)
        case Unresolved(candidates=candidates) | Indeterminate(candidates=candidates):
            return candidates
    return ()


def _reasons(assessment: Assessment) -> tuple[str, ...]:
    """Return the reasons a run that established nothing reports."""
    match assessment:
        case Established():
            return ()
        case Unresolved(reasons=reasons) | Indeterminate(reasons=reasons):
            return reasons
    return ()


def _abbreviate(commit: str) -> str:
    """Return ``commit`` as the report renders it."""
    return commit[:COMMIT_ABBREVIATION]


def _listed(commits: cabc.Sequence[str]) -> tuple[str, ...]:
    """Return the commits to render.

    Parameters
    ----------
    commits : collections.abc.Sequence[str]
        Commits of one side of the boundary, oldest first.

    Returns
    -------
    tuple[str, ...]
        The commits within :data:`RENDER_COMMIT_LIMIT`. Whether the *range*
        was cut short is a separate fact, carried by the assessment rather than
        inferred from this count, because a listing that stopped at the limit
        and a range Git never finished reading would otherwise render the same
        way: a partial range that reads as a whole one is how a replay loses
        work.

    """
    return tuple(commits[:RENDER_COMMIT_LIMIT])


def _candidate_line(candidate: Candidate) -> str:
    """Return one candidate as a rendered line."""
    return (
        f"  {TIERS[candidate.kind].value:<9} {candidate.kind.value:<24}"
        f" {_abbreviate(candidate.commit)}  {candidate.source}"
    )


def _text_warnings(warnings: cabc.Sequence[str]) -> list[str]:
    """Render the warnings a run collected, or nothing when it collected none.

    The section sits directly under the headline rather than beside the replay
    command it is about, because it is owed whether or not the run established
    a boundary and so cannot be a footer of one section of the report.

    Returns
    -------
    list[str]
        The rendered section, or an empty list when there is nothing to warn
        about.

    """
    if not warnings:
        return []
    lines = ["", "Warnings"]
    return lines + [f"  - {warning}" for warning in warnings]


def _text_gates(assessment: Assessment) -> list[str]:
    """Render the gate table, in the order the gates are named."""
    lines = ["", "Gates"]
    for gate in assessment.gates:
        outcome = gate.outcome.value if gate.applicable else _NOT_APPLICABLE
        lines += [f"  {outcome:<15} {gate.name.value}", f"  {'':<15} {gate.detail}"]
    return lines


def _text_candidates(assessment: Assessment) -> list[str]:
    """Render the evidence a run reports beside its verdict."""
    lines = ["", "Evidence"]
    lines += [_candidate_line(candidate) for candidate in candidates_of(assessment)]
    return lines


def _text_ranges(assessment: Established) -> list[str]:
    """Render the two halves of the partition an established boundary makes.

    The two reasons a listing can be short are reported apart, because they are
    different claims about the range. A listing the report itself cut short
    withheld commits it holds; a range the run saw cut short may have more
    commits than it ever saw, so its count cannot be read as the range's size.
    Rendering both as "more not listed" would state the second as the first, and
    a partial range that reads as a whole one is how a replay loses work.

    Returns
    -------
    list[str]
        The rendered sections for the included and excluded commits.

    """
    lines: list[str] = []
    for label, commits, truncated in (
        ("Included", assessment.included, assessment.included_truncated),
        ("Excluded", assessment.excluded, assessment.excluded_truncated),
    ):
        shown = _listed(commits)
        count = len(commits)
        withheld = count - len(shown)
        lines += ["", f"{label} ({count} {'commit' if count == 1 else 'commits'})"]
        lines += [f"  {_abbreviate(commit)}" for commit in shown]
        if withheld:
            lines.append(f"  ... {withheld} more not listed")
        if truncated:
            lines.append(f"  ... the range was cut short: {count} commits seen")
    return lines


def _replay_plan(assessment: Established, request: BoundaryRequest) -> list[str]:
    """Render the commands that replay the child's work onto the target.

    The commands are printed to be pasted, so they are not abbreviated the way
    a detail line is: the target and the boundary are full object IDs, because
    an abbreviation is resolved against whatever the repository holds when it is
    read, and a replay is run later than the run that proposed it. The backup
    ref is stated in full for the same reason — it is what the user returns to
    if the replay is wrong — and the child tip is printed once more beside the
    commands, so the premise the answer was computed against can be checked
    before it is acted on rather than after.

    Returns
    -------
    list[str]
        The rendered section, whose first command keeps the child tip and whose
        last line is the check to make before running anything.

    """
    backup = backup_ref(request.branch)
    return [
        "",
        "Replay",
        "  # back up the child tip first, then replay onto the target",
        f"  git update-ref {backup} {request.child_tip}",
        (
            f"  git rebase --onto {request.target} "
            f"{assessment.old_base} {request.branch}"
        ),
        f"  # undo: git reset --hard {backup}",
        "",
        (
            "  Verify before running: the child tip must still be "
            f"{_abbreviate(request.child_tip)}"
        ),
    ]


def _text_established(
    assessment: Established,
    request: BoundaryRequest,
    *,
    explain: bool,
    warnings: cabc.Sequence[str] = (),
) -> list[str]:
    """Render an established boundary, its evidence, and the replay plan."""
    lines = [
        f"git wheresat: boundary for {request.branch}",
        f"  child tip   {_abbreviate(request.child_tip)}",
        f"  target      {_abbreviate(request.target)}",
        f"  boundary    {_abbreviate(assessment.old_base)}",
    ]
    lines += _text_warnings(warnings)
    lines += _text_candidates(assessment)
    lines += _text_ranges(assessment)
    if explain:
        lines += _text_gates(assessment)
    lines += ["", "Durability"]
    if assessment.durable_ref is None:
        lines.append("  a ref that outlives this run already reaches the boundary")
    else:
        lines.append(f"  retained by {assessment.durable_ref}")
    lines += _replay_plan(assessment, request)
    return lines


def _text_refused(
    assessment: Assessment,
    request: BoundaryRequest,
    *,
    warnings: cabc.Sequence[str] = (),
) -> list[str]:
    """Render a refusal or an indeterminate result with its reasons."""
    lines = [f"{_HEADLINES[type(assessment)]} {request.branch}"]
    lines += _text_warnings(warnings)
    lines += _text_candidates(assessment)
    lines += _text_gates(assessment)
    lines += ["", "Reasons"]
    lines += [f"  - {reason}" for reason in _reasons(assessment)]
    return lines


def render_text(
    assessment: Assessment,
    request: BoundaryRequest,
    *,
    explain: bool = False,
    warnings: cabc.Sequence[str] = (),
) -> str:
    """Render the human-readable report.

    Parameters
    ----------
    assessment : Assessment
        What the run made of the evidence.
    request : BoundaryRequest
        What the run set out to answer, which is what names the branch and the
        replay target the report prints.
    explain : bool, optional
        Render the gate table even when a boundary was established. It is
        always rendered when nothing was established, because the gates are
        then what the reasons are about; an established run prints it only when
        asked, so that the answer to "where did this branch come from" does not
        arrive buried under eight gate results.
    warnings : collections.abc.Sequence[str], optional
        What the run found to warn about, from
        :func:`worktree_warnings` or :func:`unknown_worktree_warning`. They are
        rendered under the headline on every verdict, including a refusal: the
        state they describe stops a replay, whether or not this run found the
        boundary one should be run from.

    Returns
    -------
    str
        The report, ending in a newline.

    """
    if isinstance(assessment, Established):
        lines = _text_established(
            assessment, request, explain=explain, warnings=warnings
        )
    else:
        lines = _text_refused(assessment, request, warnings=warnings)
    return "\n".join(lines) + "\n"


def _json_range(commits: cabc.Sequence[str], *, cut_short: bool) -> _Payload:
    """Return one side of the partition as the envelope represents it.

    The two reasons a listing is short are reported apart here as they are in
    the text report, so a consumer cannot read a count the run did not vouch
    for as the size of the range: ``withheld`` is what the listing left out of
    commits the run saw, and ``cutShort`` says the range itself was seen cut
    short, which makes ``count`` a floor rather than a total.

    Returns
    -------
    _Payload
        One side of the partition, as the envelope represents it.

    """
    shown = _listed(commits)
    return {
        "commits": list(shown),
        "count": len(commits),
        "withheld": len(commits) - len(shown),
        "cutShort": cut_short,
    }


def _json_identity(
    identity: stack_records.PullRequestIdentity | None,
) -> _Payload | None:
    """Return a pull request identity as the envelope represents it."""
    if identity is None:
        return None
    return {"repository": identity.repository, "number": identity.number}


def _json_candidates(assessment: Assessment) -> list[_Payload]:
    """Return the candidates a run that established nothing collected.

    An established result reports its support under its own key and lists
    nothing here: the candidates that failed are not carried by
    :class:`~git_donkey.wheresat_records.Established`, and repeating the
    support in a second list would invite a reader to mistake the two for
    different views of one set.

    Returns
    -------
    list[_Payload]
        One object per collected candidate, or an empty list when the boundary
        was established.

    """
    if isinstance(assessment, Established):
        return []
    return [
        {
            "commit": candidate.commit,
            "kind": candidate.kind.value,
            "tier": TIERS[candidate.kind].value,
            "source": candidate.source,
        }
        for candidate in assessment.candidates
    ]


def _parent_head(assessment: Assessment) -> str | None:
    """Return the parent head the run read, when the evidence names one.

    The head is not a field of the assessment. It is the commit the run
    fetched as the parent pull request's head, which is exactly the
    ``PULL_REQUEST_HEAD`` candidate among the evidence: a run that consulted
    no parent, or that read a boundary out of a stack record alone, reports
    nothing here.

    Returns
    -------
    str | None
        The commit the run fetched as the parent head, or ``None`` when no
        candidate names one.

    """
    for candidate in candidates_of(assessment):
        if candidate.kind is EvidenceKind.PULL_REQUEST_HEAD:
            return candidate.commit
    return None


def _empty_payload() -> _Payload:
    """Return every envelope key with the value a run that found nothing reports.

    The key set is written once, here, so the error envelope and the envelope
    of a completed run cannot drift apart: both start from it, and a key added
    to one is a key added to both.

    Returns
    -------
    _Payload
        Every key of the envelope, with the value a run that found nothing
        reports.

    """
    return {
        "error": None,
        "child": None,
        "target": None,
        "parent": None,
        "parentHead": None,
        # ``landed`` stays null until an assessment carries the parent's landed
        # commit, which none does yet; ``backupRef`` is filled by a run that
        # established a boundary, because a run that established none proposes
        # no rebase and so has no ref to name. Declaring both beside the keys a
        # run does fill keeps the key set in one place, so a consumer reads one
        # shape from a run that established nothing and from one that never
        # started.
        "landed": None,
        "oldBase": None,
        "durableRef": None,
        "included": _json_range((), cut_short=False),
        "excluded": _json_range((), cut_short=False),
        "support": [],
        "candidates": [],
        "gates": [],
        "reasons": [],
        "warnings": [],
        "rebaseCommand": None,
        "backupRef": None,
    }


def _json_payload(assessment: Assessment, request: BoundaryRequest) -> _Payload:
    """Return every envelope key but the schema, verdict, and exit code."""
    established = assessment if isinstance(assessment, Established) else None
    payload = _empty_payload()
    payload["child"] = {"branch": request.branch, "tip": request.child_tip}
    payload["target"] = request.target
    payload["parent"] = _json_identity(request.parent)
    payload["parentHead"] = _parent_head(assessment)
    payload["candidates"] = _json_candidates(assessment)
    payload["gates"] = [
        {
            "name": gate.name.value,
            "outcome": gate.outcome.value,
            "detail": gate.detail,
            "applicable": gate.applicable,
        }
        for gate in assessment.gates
    ]
    payload["reasons"] = list(_reasons(assessment))
    if established is not None:
        payload["oldBase"] = established.old_base
        payload["durableRef"] = established.durable_ref
        payload["included"] = _json_range(
            established.included, cut_short=established.included_truncated
        )
        payload["excluded"] = _json_range(
            established.excluded, cut_short=established.excluded_truncated
        )
        payload["support"] = [
            {
                "commit": candidate.commit,
                "kind": candidate.kind.value,
                "tier": TIERS[candidate.kind].value,
            }
            for candidate in established.support
        ]
        payload["rebaseCommand"] = (
            f"git rebase --onto {request.target} {established.old_base} "
            f"{request.branch}"
        )
        # The ref the text report tells the user to create first. It is the
        # same string the plan prints, read from one helper, so a consumer that
        # performs the backup itself cannot be sent to a different ref than a
        # reader of the report was.
        payload["backupRef"] = backup_ref(request.branch)
    return payload


def _envelope(envelope: _Payload) -> str:
    """Return ``envelope`` serialised, one key per line."""
    return json.dumps(envelope, indent=2) + "\n"


def render_json(
    assessment: Assessment,
    request: BoundaryRequest,
    *,
    warnings: cabc.Sequence[str] = (),
) -> str:
    """Render the versioned machine-readable envelope.

    Parameters
    ----------
    assessment : Assessment
        What the run made of the evidence.
    request : BoundaryRequest
        What the run set out to answer.
    warnings : collections.abc.Sequence[str], optional
        What the run found to warn about, from
        :func:`worktree_warnings` or :func:`unknown_worktree_warning`. They are
        the envelope's ``warnings`` key, which is empty when there is nothing
        to warn about rather than absent, so a consumer reads one shape.

    Returns
    -------
    str
        The envelope, ending in a newline.

    """
    payload = _json_payload(assessment, request)
    payload["warnings"] = list(warnings)
    return _envelope({
        "schema": JSON_SCHEMA,
        "verdict": VERDICT_WORDS[type(assessment)],
        "exitCode": EXIT_CODES[type(assessment)],
        **payload,
    })


def render_error_json(code: int, message: str) -> str:
    """Render the envelope for a usage or environment failure.

    The envelope is emitted for a run that never reached an assessment, so a
    consumer reading standard output gets the same object shape whether the
    command ran or refused to start.

    Parameters
    ----------
    code : int
        Exit status the command will report.
    message : str
        What went wrong, in prose.

    Returns
    -------
    str
        The envelope, ending in a newline.

    """
    payload = _empty_payload()
    payload["error"] = message
    return _envelope({
        "schema": JSON_SCHEMA,
        "verdict": ERROR_VERDICT,
        "exitCode": code,
        **payload,
    })
