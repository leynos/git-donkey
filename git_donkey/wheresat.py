"""Run ``git wheresat``: locate a branch's replay boundary and report it.

The command is five pieces in the procedure's order: resolve what the run was
asked to something immutable, ask the evidence rungs
(:mod:`git_donkey.wheresat_collect`), ask every graph question the gates will
read (:mod:`git_donkey.wheresat_facts`), weigh the answers
(:mod:`git_donkey.wheresat_policy`), and render what that made of them
(:mod:`git_donkey.wheresat_report`). Nothing here decides anything about a
boundary: this module owns resolution, the exit status, and the bounded
observations, and every judgement between them belongs to the piece that owns
it.

A run also warns, without failing, when the worktree holding the child branch
has uncommitted changes or is stopped in the middle of a rebase, merge,
cherry-pick, revert, or bisect. That warning is about the command the report
prints rather than about the boundary: the command is safe to run in either
state and the replay it prints is not, so the state changes the report and
neither the verdict nor the exit status. Reading it is a read like any other
(INV-1), and failing to read it is warned about rather than hidden, because a
run that warns about nothing is read as a run with nothing to warn about.

Until a boundary is known to be one that no durable ref reaches, everything
here that touches the repository is a read. The writer of
:mod:`git_donkey.wheresat_refs` is constructed for the one write that a reported
boundary may need — a ref of its own, so that ``git gc`` cannot take the answer
with the run (INV-8) — and never for a run that has nothing to retain, which is
what keeps the default path free of anything that could change the repository
(INV-1).

The exit status follows the assessment rather than the run's plumbing: ``0``
when a boundary was established, ``1`` when complete evidence refused one, and
``3`` when a question the procedure asked could not be answered. ``2`` is
reserved for a run that could not start at all — a branch or target that does
not resolve, a malformed ``--parent``, or a repository with no remote to name a
default branch — and every one of those paths still writes the JSON envelope
when ``--json`` asked for one, so a consumer never has to parse prose.

This milestone answers from local evidence alone. There is no forge port yet, so
a run that names a parent pull request resolves nothing for it: the gates about
a parent report that they went unanswered and the run exits ``3``. ``--no-fetch``,
``--offline``, ``--limit``, ``--heuristic-window``, ``--deep``, ``--record``,
and ``--expected-old`` are accepted and inert, because the local evidence path
neither fetches evidence nor writes a record, and the deeper comparisons they
control arrive with the forge evidence they compare against. ``--op-id`` is
checked as it is read, so a hostile id is refused before a run could write a
ref built from it, and is otherwise inert for the same reason.
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
    stack_records,
    stack_store,
    wheresat_collect,
    wheresat_facts,
    wheresat_policy,
    wheresat_records,
    wheresat_refs,
    wheresat_report,
)
from git_donkey._constants import GIT_WHERESAT_PREFIX
from git_donkey.wheresat_errors import WheresatGraphError
from git_donkey.wheresat_graph import GitWheresatGraph, WheresatGraph

_PARENT_IDENTIFICATION: typ.Final[observability.Operation] = "parent_identification"
"""Operation a run's parent request is recorded under."""

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
class WheresatOptions:
    """Every command-line input, before resolution to object IDs.

    ``--op-id`` is checked as it is read and changes no answer yet; the
    remaining flags past ``--explain`` are accepted and inert at this
    milestone, because the local evidence path fetches nothing, records
    nothing, and compares nothing deeply, and the inputs that control those
    paths arrive with the evidence they compare against.

    """

    branch: str | None = None
    onto: str | None = None
    parent: str | None = None
    remote: str | None = None
    limit: int = 20
    heuristic_window: int = 200
    no_fetch: bool = False
    offline: bool = False
    deep: bool = False
    explain: bool = False
    json: bool = False
    op_id: str | None = None
    record: bool = False
    expected_old: str | None = None


_DEFAULT_WHERESAT_OPTIONS = WheresatOptions()
"""What every option defaults to, for the command line to spread.

Cyclopts reads the fields of :class:`WheresatOptions` off the signature of the
command-line wrapper, so the wrapper needs an instance to default to. It is
built here, beside the class, so a field added to one is visible in the other.
"""


class _UsageError(RuntimeError):
    """The run could not start: what it was asked for does not resolve."""


@dataclasses.dataclass(frozen=True, slots=True)
class _Session:
    """What one run resolved before it asked its first question."""

    options: WheresatOptions
    repo: Repo
    graph: WheresatGraph
    context: wheresat_collect.CollectionContext


def run_git_wheresat(
    options: WheresatOptions,
    *,
    repo: Repo | None = None,
    graph: WheresatGraph | None = None,
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

    Returns
    -------
    int
        ``0`` when a boundary was established, ``1`` when the evidence refused
        one, ``2`` for a usage or environment error, and ``3`` when the
        repository could not answer a question the procedure asked.

    """
    try:
        session = _session(options, repo=repo, graph=graph)
    except _UsageError as exc:
        return _failed(options, str(exc))
    assessment = _retained(session, _assess(session))
    _observe(assessment)
    _write(options, assessment, session.context.request, _warnings(session))
    return wheresat_records.EXIT_CODES[type(assessment)]


def _session(
    options: WheresatOptions,
    *,
    repo: Repo | None,
    graph: WheresatGraph | None,
) -> _Session:
    """Resolve what the run was asked to immutable object IDs.

    Resolution is where a run can fail before it has anything to report: a
    branch that does not exist, a target that does not resolve, and a parent
    that is not spelled ``OWNER/REPO#N`` are all answered here rather than by an
    assessment, because no evidence exists at this point to assess.

    Returns
    -------
    _Session
        The repository, the graph, and the context the collection phase reads.

    Raises
    ------
    _UsageError
        If a name the run was asked for does not resolve, or if the default
        branch of the remote cannot be named locally.

    """
    _validate_op_id(options)
    repository = repo if repo is not None else _open_repo()
    questions = graph if graph is not None else GitWheresatGraph(repository)
    branch = _branch(options, repository)
    target, target_ref = _target(options, repository, questions)
    request = wheresat_records.BoundaryRequest(
        branch=branch,
        child_tip=_resolved(questions, branch, what=f"the branch {branch}"),
        target=target,
        parent=_parent(options),
        deep=options.deep,
        offline=options.offline,
    )
    return _Session(
        options=options,
        repo=repository,
        graph=questions,
        context=wheresat_collect.CollectionContext(
            request=request,
            target_ref=target_ref,
            parent=None,
            graph=questions,
            records=stack_store.GitStackRecordReader(repository),
        ),
    )


def _validate_op_id(options: WheresatOptions) -> None:
    """Refuse ``--op-id`` before anything is built from it.

    The id names a ref namespace, so a value that could escape it, nest inside
    another run's, or be read as another option is a usage error rather than
    something to discover while writing refs. Checking it here means the
    refusal happens before the run reads anything, and is reported the same way
    whether or not this milestone writes any ref at all.

    Raises
    ------
    _UsageError
        If ``--op-id`` was given and may not name a run's namespace.

    """
    if options.op_id is None:
        return
    try:
        wheresat_refs.validate_op_id(options.op_id)
    except ValueError as exc:
        raise _UsageError(str(exc)) from exc


def _open_repo() -> Repo:
    """Return the repository rooted at the current directory.

    Returns
    -------
    git.Repo
        Repository the run reads, found by searching upwards from the current
        directory.

    Raises
    ------
    _UsageError
        If the current directory is not inside a Git repository.

    """
    try:
        return Repo(Path.cwd(), search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        msg = "not inside a Git repository"
        raise _UsageError(msg) from exc


def _branch(options: WheresatOptions, repo: Repo) -> str:
    """Return the child branch, defaulting to the branch checked out here.

    Returns
    -------
    str
        The branch named by ``--branch``, or the checked-out branch.

    Raises
    ------
    _UsageError
        If HEAD is detached and no ``--branch`` was given, because a boundary
        read for a detached HEAD would name a branch that does not exist.

    """
    if options.branch is not None:
        return options.branch
    if repo.head.is_detached:
        msg = (
            "HEAD is detached in the current directory, so no child branch can be "
            "read from it; name one with --branch"
        )
        raise _UsageError(msg)
    return repo.active_branch.name


def _target(
    options: WheresatOptions,
    repo: Repo,
    graph: WheresatGraph,
) -> tuple[str, str | None]:
    """Return the replay target and the ref it was read from, if any.

    ``--onto`` is resolved as given. Without it the target is the branch the
    principal remote's own ``HEAD`` symbolic ref names, which is the default
    branch a clone last recorded and needs no network to read: a run that cannot
    name one is told to pass ``--onto`` rather than left to guess between a
    local ``main`` and the remote's idea of it.

    Returns
    -------
    tuple[str, str | None]
        The target as an immutable object ID, and the full ref path it was
        resolved from when it was resolved from one. A fork-point question can
        only be asked about a ref, so a target that named none has no reflog to
        read.

    Raises
    ------
    _UsageError
        If the named revision does not resolve, or if no local ref records the
        remote's default branch.

    """
    if options.onto is not None:
        target = _resolved(graph, options.onto, what=f"the target {options.onto}")
        return target, graph.ref_name(options.onto)
    remote = options.remote if options.remote is not None else _principal_remote(repo)
    ref = _default_branch_ref(remote, graph)
    target = _resolved(graph, ref, what=f"the default branch of {remote!r}")
    return target, ref


def _principal_remote(repo: Repo) -> str:
    """Return the first configured remote, which is the principal one.

    Returns
    -------
    str
        Name of the first remote the repository configures.

    Raises
    ------
    _UsageError
        If the repository configures no remote, because the default branch the
        target would default to cannot then be named at all.

    """
    remotes = [remote.name for remote in repo.remotes]
    if not remotes:
        msg = (
            "this repository has no remote to name a default branch; pass --onto "
            "to name the replay target"
        )
        raise _UsageError(msg)
    return remotes[0]


def _default_branch_ref(remote: str, graph: WheresatGraph) -> str:
    """Return the ref the default branch of ``remote`` is tracked at.

    Returns
    -------
    str
        ``refs/remotes/<remote>/<branch>``, as the remote's own symbolic
        ``HEAD`` names it.

    Raises
    ------
    _UsageError
        If the remote-tracking ``HEAD`` is not a symbolic ref, which is the case
        for a remote that was never fetched from.

    """
    alias = f"refs/remotes/{remote}/HEAD"
    ref = graph.symbolic_ref(alias)
    if ref is None:
        msg = (
            f"{alias} is not a symbolic ref, so the default branch of {remote!r} "
            "cannot be named locally; pass --onto to name the replay target"
        )
        raise _UsageError(msg)
    return ref


def _parent(options: WheresatOptions) -> stack_records.PullRequestIdentity | None:
    """Return the parent pull request the run named, if it named one.

    The request records what the run set out to consult, and not what a forge
    answered: a run that named a parent pull request applies the gates about one
    whether or not the pull request could be read, so a parent that cannot be
    resolved withholds an answer instead of quietly widening the run.

    Returns
    -------
    stack_records.PullRequestIdentity | None
        The pull request ``--parent`` names, or ``None`` when no parent was
        named.

    Raises
    ------
    _UsageError
        If ``--parent`` is not spelled ``OWNER/REPOSITORY#NUMBER``.

    """
    if options.parent is None:
        observability.get_recorder().record(
            observability.Observation(
                operation=_PARENT_IDENTIFICATION, outcome="not_requested"
            )
        )
        return None
    identity = stack_records.parse_pull_request_identity(options.parent)
    if identity is None:
        msg = f"--parent must be OWNER/REPOSITORY#NUMBER, not {options.parent!r}"
        raise _UsageError(msg)
    observability.get_recorder().record(
        observability.Observation(
            operation=_PARENT_IDENTIFICATION, outcome="unavailable"
        )
    )
    return identity


def _resolved(graph: WheresatGraph, rev: str, *, what: str) -> str:
    """Return ``rev`` resolved to an object ID.

    Returns
    -------
    str
        The commit ``rev`` names.

    Raises
    ------
    _UsageError
        If the revision does not resolve, or if Git cannot be asked. The
        question is put before there is any evidence to assess, so an
        unanswerable one is a configuration failure rather than an
        indeterminate result.

    """
    try:
        return graph.resolve(rev)
    except WheresatGraphError as exc:
        msg = f"{what} could not be resolved: {exc}"
        raise _UsageError(msg) from exc


def _assess(session: _Session) -> wheresat_records.Assessment:
    """Return what the evidence makes of the boundary, faults included.

    Returns
    -------
    wheresat_records.Assessment
        The boundary the evidence establishes, or why none was, with a
        collection fault forcing an indeterminate result rather than a refusal.

    """
    evidence = wheresat_collect.collect_evidence(session.context)
    facts = wheresat_facts.assemble_facts(session.context, evidence)
    assessment = wheresat_policy.assess(
        session.context.request,
        evidence.candidates,
        facts.facts,
        None,
    )
    return wheresat_policy.apply_collection_faults(
        assessment, evidence.faults + facts.faults
    )


def _retained(
    session: _Session, assessment: wheresat_records.Assessment
) -> wheresat_records.Assessment:
    """Return the assessment with its boundary retained, if it needs retaining.

    A boundary that only this run's own refs reach would be collected by the
    next ``git gc --prune=now``, so it is written under a ref of its own before
    it is reported (INV-8). A boundary some other ref already reaches is left
    exactly as it is: the ref count of the repository is part of what a run
    without ``--record`` must not change.

    Returns
    -------
    wheresat_records.Assessment
        The assessment, with the retaining ref named when one was written, or an
        indeterminate result when the boundary could not be kept.

    """
    if not isinstance(assessment, wheresat_records.Established):
        return assessment
    try:
        reaches = session.graph.is_reachable_from_durable_ref(assessment.old_base)
    except WheresatGraphError as exc:
        return _indeterminate(
            assessment, f"cannot tell whether the boundary is retained: {exc}"
        )
    if reaches:
        return assessment
    return _retain(session, assessment)


def _retain(
    session: _Session, assessment: wheresat_records.Established
) -> wheresat_records.Assessment:
    """Return the assessment with a durable ref written for its boundary.

    Returns
    -------
    wheresat_records.Assessment
        The assessment naming the ref that now retains the boundary, or an
        indeterminate result when the ref could not be written: a boundary the
        run cannot keep must not be reported as an answer that outlives it.

    """
    branch = session.context.request.branch
    writer = wheresat_refs.GitWheresatRefWriter(session.repo)
    try:
        ref = writer.retain_boundary(branch, assessment.old_base)
    except (wheresat_refs.WheresatRefError, ValueError) as exc:
        return _indeterminate(assessment, f"the boundary could not be retained: {exc}")
    return dataclasses.replace(assessment, durable_ref=ref)


def _indeterminate(
    assessment: wheresat_records.Established, reason: str
) -> wheresat_records.Indeterminate:
    """Return the indeterminate result a run that cannot keep its answer reports.

    The boundary's own evidence is reported as the candidates the run
    collected, because that evidence is what the run was about to answer with
    and a reader of the refusal needs it to see which boundary went unreported.

    Returns
    -------
    wheresat_records.Indeterminate
        The verdict, with the reason it could not be established.

    """
    return wheresat_records.Indeterminate(
        candidates=tuple(assessment.support),
        gates=assessment.gates,
        reasons=(reason,),
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
