"""Run ``git wheresat``: locate a branch's replay boundary and report it.

The command is five pieces in the procedure's order: resolve what the run was
asked to something immutable, ask the evidence rungs
(:mod:`git_donkey.wheresat_collect`), ask every graph question the gates will
read (:mod:`git_donkey.wheresat_facts`), weigh the answers
(:mod:`git_donkey.wheresat_policy`), and render what that made of them
(:mod:`git_donkey.wheresat_report`). Nothing here decides anything about a
boundary: the names a run was asked for are resolved by
:mod:`git_donkey.wheresat_request`, and what is left here is the run itself —
the ladder, the assessment, the exit status, and the bounded observations — with
every judgement between them belonging to the piece that owns it.

A run also warns, without failing, when the worktree holding the child branch
has uncommitted changes or is stopped in the middle of a rebase, merge,
cherry-pick, revert, or bisect. That warning is about the command the report
prints rather than about the boundary: the command is safe to run in either
state and the replay it prints is not, so the state changes the report and
neither the verdict nor the exit status. Reading it is a read like any other
(INV-1), and failing to read it is warned about rather than hidden, because a
run that warns about nothing is read as a run with nothing to warn about.

The parent is identified and its head fetched before anything is assessed: the
parent the user named, or the one the child's pull requests lead to
(:mod:`git_donkey.wheresat_parents`), and then that pull request's head, brought
down into the command's own cache ref (:mod:`git_donkey.wheresat_writes`). A
question the ladder could not put, and a head it could not fetch, are carried as
faults and the run answers that it could not tell: the gates about a parent are
the gates that could have refused the boundary, so an unanswered one may not be
read as a boundary that stands (ADR-005).

Until a boundary is known to be one that no durable ref reaches, everything
here that touches the repository is a read. The three writes a run may make —
the parent's head the fetch caches, the ref a reported boundary needs when
nothing else reaches it, so that ``git gc`` cannot take the answer with the run
(INV-8), and the record ``--record`` refreshes (INV-7) — belong to
:mod:`git_donkey.wheresat_writes`, which is the one module of the command that
can change a repository. The fetch is the one of the three a run makes without
being asked, and it is confined to the cache ref under the namespace INV-1
permits this command's refs to occupy; the other two happen only when the run
asks for them or when a boundary would otherwise be collectable.

That order matters to INV-8 as well as to INV-1: the record write happens
before the boundary's reachability is checked, so a run whose own record now
names the boundary reports it as already durable rather than retaining it a
second time under an evidence ref.

The exit status follows the assessment rather than the run's plumbing: ``0``
when a boundary was established, ``1`` when complete evidence refused one, and
``3`` when a question the procedure asked could not be answered. ``2`` is
reserved for a run that could not start, or could not carry out what it was
asked to write — a branch or target that does not resolve, a malformed
``--parent``, a repository with no remote to name a default branch, or a
``--record`` whose expectation the anchor does not meet — and every one of
those paths still writes the JSON envelope when ``--json`` asked for one, so a
consumer never has to parse prose.

``--offline`` and ``--no-fetch`` are honoured rather than accepted: a run told
to stay offline consults no forge at all and one told not to fetch reasons from
a head a previous run cached, or reports that it has none. ``--limit`` bounds
the association search, and ``--heuristic-window`` bounds the deep comparison
``--deep`` asks for: how many of the target's newest commits a child is
compared against. A run without ``--deep`` puts no comparison question at all,
and the window a deep run's scan was cut short by is a warning on the report
rather than a fault, because what the comparison would have found is inferred
evidence and ``--deep`` may not change the verdict. ``--op-id`` is checked as
it is read, so a hostile id is refused before a run could write a ref built
from it.
"""

from __future__ import annotations

import dataclasses
import sys
import typing as typ
from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from git_donkey import (
    helpers,
    observability,
    stack_store,
    wheresat_collect,
    wheresat_facts,
    wheresat_github,
    wheresat_heads,
    wheresat_parents,
    wheresat_policy,
    wheresat_records,
    wheresat_remotes,
    wheresat_report,
    wheresat_request,
    wheresat_writes,
)
from git_donkey._constants import GIT_WHERESAT_PREFIX
from git_donkey.wheresat_errors import WheresatGraphError, WheresatUsageError
from git_donkey.wheresat_graph import GitWheresatGraph, WheresatGraph
from git_donkey.wheresat_request import WheresatOptions

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = ["WheresatOptions", "run_git_wheresat"]
"""What this module's callers reach for: the run, and what it was asked.

``WheresatOptions`` is re-exported rather than defined here. The options are
what a run was asked, so they live beside the module that resolves them
(:mod:`git_donkey.wheresat_request`); naming them here keeps this module the one
a caller states a run through.
"""

_ASSESSMENT: typ.Final[observability.Operation] = "boundary_assessment"
"""Operation a completed assessment is recorded under."""

_VERDICT_OUTCOMES: typ.Final[typ.Mapping[type, observability.Outcome]] = {
    wheresat_records.Established: "success",
    wheresat_records.Unresolved: "declined",
    wheresat_records.Indeterminate: "unavailable",
}
"""Bounded outcome each verdict is recorded as.

The verdict is an outcome of the run and not a failure of it: a refusal is a
legitimate answer about the evidence, which is why it is recorded as
``declined`` rather than as a failure, and only a question the repository could
not answer is recorded as ``unavailable``.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class _Session:
    """What one run resolved before it asked its first question.

    ``parent_faults`` is what the run could not learn about its parent: a
    question the ladder could not put, or a head it could not fetch. They are
    beside the context rather than in it because no rung reads them — they are
    what the run reports in place of an answer.

    """

    options: WheresatOptions
    repo: Repo
    graph: WheresatGraph
    context: wheresat_collect.CollectionContext
    parent_faults: tuple[str, ...] = ()


def run_git_wheresat(
    options: WheresatOptions,
    *,
    repo: Repo | None = None,
    graph: WheresatGraph | None = None,
    github: wheresat_github.WheresatGitHub | None = None,
) -> int:
    """Locate the replay boundary and report it.

    Parameters
    ----------
    options : WheresatOptions
        What the command line asked for.
    repo : git.Repo | None, optional
        Repository to read. The current directory's repository is used when it
        is omitted, and a test uses this to point the run at a fixture.
    graph : WheresatGraph | None, optional
        Read-only history questions. A Git-backed graph over ``repo`` is used
        when it is omitted.
    github : git_donkey.wheresat_github.WheresatGitHub | None, optional
        Forge the run consults. ``None`` means nothing was injected, and the
        run opens the real one through
        :func:`git_donkey.wheresat_github.open_github`, so a caller that
        supplies nothing gets the command's own behaviour rather than a
        quietly weaker one. A run that was told ``--offline`` opens nothing
        whatever this names.

    Returns
    -------
    int
        ``0`` when a boundary was established, ``1`` when the evidence refused
        one, ``2`` for a usage or environment error, and ``3`` when a question
        the procedure asked could not be answered.

    """
    # Resolution, assessment, and the record write are one sequence because
    # each can refuse the run before there is a report to print, and every one
    # of those refusals is reported as the same usage failure.
    try:
        session = _session(options, repo=repo, graph=graph, github=github)
        assessment, caveats = _assess(session)
        writes = wheresat_writes.WheresatWrites(session.repo, session.context)
        recorded = writes.record(
            assessment,
            requested=options.record,
            expected=_expectation(session),
        )
    except WheresatUsageError as exc:
        return _failed(options, str(exc))
    assessment = writes.retain(assessment)
    _observe(assessment)
    warnings = _warnings(session) + caveats + recorded
    _write(options, assessment, session.context.request, warnings)
    return wheresat_records.EXIT_CODES[type(assessment)]


def _session(
    options: WheresatOptions,
    *,
    repo: Repo | None,
    graph: WheresatGraph | None,
    github: wheresat_github.WheresatGitHub | None,
) -> _Session:
    """Resolve the question, ask GitHub it, and hand back what one run reads.

    The question itself is resolved by
    :func:`~git_donkey.wheresat_request.resolve`, which is where a name that
    does not resolve is reported. What is left here is the ladder: the forge is
    asked, and what it could not answer is not a failure of the run — the ladder
    reports a question it could not put, and the head a fetch could not bring
    down is reported the same way, so both leave the run with a fault and an
    assessment to make rather than with a refusal to report.

    Returns
    -------
    _Session
        The repository, the graph, the parent the run identified, and the
        context the collection phase reads.

    """
    wheresat_request.validate_op_id(options)
    wheresat_request.validate_record_options(options)
    repository = repo if repo is not None else _open_repo()
    questions = graph if graph is not None else GitWheresatGraph(repository)
    request, target_ref = wheresat_request.resolve(options, repository, questions)
    records = stack_store.GitStackRecordReader(repository)
    identified = wheresat_parents.identify_parent(
        request,
        wheresat_parents.SearchBounds(
            limit=options.limit,
            repository=wheresat_remotes.principal_repository(repository),
        ),
        reads=wheresat_parents.LadderReads(
            graph=questions,
            records=records,
            opener=_opener(github, offline=options.offline),
        ),
    )
    fetched = _fetch_head(identified, repository, no_fetch=options.no_fetch)
    return _Session(
        options=options,
        repo=repository,
        graph=questions,
        context=wheresat_collect.CollectionContext(
            request=request,
            target_ref=target_ref,
            parent=fetched.parent,
            parent_head=_head(fetched),
            graph=questions,
            records=records,
            shared_record=identified.shared_record,
        ),
        parent_faults=identified.faults + _faults_of(fetched),
    )


def _opener(
    github: wheresat_github.WheresatGitHub | None,
    *,
    offline: bool,
) -> cabc.Callable[[], wheresat_github.WheresatGitHub] | None:
    """Return what opens the forge, or nothing when this run may not ask one.

    An offline run is offered no opener at all rather than one it declines to
    call, which is what makes ``--offline`` a property of the run instead of a
    promise the ladder's first rung happens to keep: the ladder reports that it
    asked no forge anything, and no later edit can turn the flag into a
    request.

    Parameters
    ----------
    github : git_donkey.wheresat_github.WheresatGitHub | None
        Port the caller supplied, if it supplied one.
    offline : bool
        Whether the run was told to touch nothing outside the repository.

    Returns
    -------
    collections.abc.Callable[[], WheresatGitHub] | None
        What the ladder calls when it is going to ask the forge something, or
        ``None`` when this run asks nothing.

    """
    if offline:
        return None
    if github is not None:
        return lambda: github
    return wheresat_github.open_github


def _fetch_head(
    identified: wheresat_parents.ParentIdentification,
    repo: Repo,
    *,
    no_fetch: bool,
) -> wheresat_writes.ParentHeadFetch:
    """Return the parent's head, fetched when the ladder identified a parent.

    A run that identified no parent fetches nothing and faults about nothing:
    there is no head to bring down, and the gates about a parent are not
    applicable anyway. The fetch is the write that turns an identified pull
    request into evidence a local gate can read.

    Parameters
    ----------
    identified : wheresat_parents.ParentIdentification
        What the ladder made of the run's parent question.
    repo : git.Repo
        Repository the head is fetched into.
    no_fetch : bool
        Whether the run was told not to fetch.

    Returns
    -------
    wheresat_writes.ParentHeadFetch
        The parent with its head in hand, or the reason the run has none.

    """
    if identified.parent is None:
        return wheresat_writes.ParentHeadFetch()
    return wheresat_writes.fetch_parent_head(repo, identified.parent, no_fetch=no_fetch)


def _head(
    fetched: wheresat_writes.ParentHeadFetch,
) -> wheresat_heads.ParentHead | None:
    """Return the parent head the gates read, or nothing when neither is in hand.

    The head is offered as a bare object ID with no ref beside it, because that
    is what it is: a commit the run fetched into its own cache ref rather than
    a local branch whose reflog could be asked where the child forked from. The
    fork-point rung reads the absent ref exactly that way, and so asks nothing
    about a ref the run never had.

    Returns
    -------
    wheresat_heads.ParentHead | None
        The head the parent's gates ask about, or ``None`` when the run has
        none.

    """
    if fetched.parent is None:
        return None
    return wheresat_heads.ParentHead(fetched.parent.head_sha, ref=None)


def _faults_of(fetched: wheresat_writes.ParentHeadFetch) -> tuple[str, ...]:
    """Return the fault a fetch reported, when it reported one."""
    return (fetched.fault,) if fetched.fault is not None else ()


def _open_repo() -> Repo:
    """Return the repository rooted at the current directory.

    Returns
    -------
    git.Repo
        Repository the run reads, found by searching upwards from the current
        directory.

    Raises
    ------
    WheresatUsageError
        If the current directory is not inside a Git repository.

    """
    try:
        return Repo(Path.cwd(), search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        msg = "not inside a Git repository"
        raise WheresatUsageError(msg) from exc


def _expectation(session: _Session) -> str | None:
    """Return ``--expected-old`` resolved, or ``None`` when none was given.

    The expectation is resolved here rather than by the write that compares it,
    because resolution is a question about the repository and belongs beside the
    other names the run resolves. A run that named no expectation resolves
    nothing, so the default path asks the repository no extra question.

    Returns
    -------
    str | None
        The commit ``--expected-old`` names, or ``None`` when the run named
        none.

    Raises
    ------
    WheresatUsageError
        If the expectation does not resolve.

    """
    expected = session.options.expected_old
    if expected is None:
        return None
    return wheresat_request.object_id(
        session.graph, expected, what=f"--expected-old {expected!r}"
    )


def _assess(
    session: _Session,
) -> tuple[wheresat_records.Assessment, tuple[str, ...]]:
    """Return what the evidence makes of the boundary, faults included.

    The parent the run identified is handed to the assessment with the
    candidates, because the gates about a parent read it and the boundary it
    establishes depends on them. Every fault is applied at once — the ladder's,
    the fetch's, the collection's, and the facts' — because they are one thing
    to the reader: a question the procedure asked that nothing answered.

    What the collection warns about is returned beside the assessment rather
    than applied to it. A fault forces an indeterminate result, and a caveat
    is not one: the deep comparison's window reaching only so far says what the
    run did not look at, and a run cannot be made less able to answer by being
    told less about a question no verdict rests on.

    Returns
    -------
    tuple[wheresat_records.Assessment, tuple[str, ...]]
        The boundary the evidence establishes, or why none was, with a fault
        forcing an indeterminate result rather than a refusal; and what the run
        warns about the reach of its own evidence.

    """
    evidence = wheresat_collect.collect_evidence(session.context)
    facts = wheresat_facts.assemble_facts(session.context, evidence)
    assessment = wheresat_policy.assess(
        session.context.request,
        evidence.candidates,
        facts.facts,
        session.context.parent,
    )
    return (
        wheresat_policy.apply_collection_faults(
            assessment,
            session.parent_faults + evidence.faults + facts.faults,
        ),
        evidence.warnings,
    )


def _observe(assessment: wheresat_records.Assessment) -> None:
    """Record the bounded observation for a completed assessment."""
    observability.get_recorder().record(
        observability.Observation(
            operation=_ASSESSMENT,
            outcome=_VERDICT_OUTCOMES[type(assessment)],
            verdict=wheresat_report.VERDICT_WORDS[type(assessment)],
        )
    )


def _warnings(session: _Session) -> tuple[str, ...]:
    """Return what the run warns about the worktree holding the child branch.

    The replay command the report prints is safe to read and unsafe to run
    over uncommitted changes or a stopped operation, so the answer is rendered
    as a warning on every verdict and changes neither the verdict nor the exit
    status. A state Git could not report is warned about rather than dropped:
    the state is not evidence about a boundary, so it cannot make the run
    indeterminate, but a run that warned about nothing would be read as a run
    with nothing to warn about.

    Returns
    -------
    tuple[str, ...]
        One warning per obstacle, or the warning naming the reason the
        worktree could not be read.

    """
    branch = session.context.request.branch
    try:
        state = session.graph.worktree_state(branch)
    except WheresatGraphError as exc:
        return wheresat_report.unknown_worktree_warning(branch, str(exc))
    return wheresat_report.worktree_warnings(branch, state)


def _write(
    options: WheresatOptions,
    assessment: wheresat_records.Assessment,
    request: wheresat_records.BoundaryRequest,
    warnings: typ.Sequence[str],
) -> None:
    """Write the assessment to standard output in the form the run asked for."""
    if options.json:
        rendered = wheresat_report.render_json(assessment, request, warnings=warnings)
    else:
        rendered = wheresat_report.render_text(
            assessment, request, explain=options.explain, warnings=warnings
        )
    sys.stdout.write(rendered)


def _failed(options: WheresatOptions, message: str) -> int:
    """Report a run that could not start, and return its exit status.

    Returns
    -------
    int
        Always the usage exit status. A run that fails here has no assessment,
        so it is the error envelope that is written when ``--json`` asked for
        one, and prose on standard error otherwise.

    """
    if options.json:
        sys.stdout.write(
            wheresat_report.render_error_json(wheresat_records.EXIT_USAGE, message)
        )
    else:
        helpers._eprint(f"{GIT_WHERESAT_PREFIX}: {message}")
    return wheresat_records.EXIT_USAGE
