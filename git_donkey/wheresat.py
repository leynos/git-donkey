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
the association search. ``--heuristic-window`` and ``--deep`` are still
accepted and inert, because the comparisons they control arrive with the report
work that states the window they scanned. ``--op-id`` is checked as it is read,
so a hostile id is refused before a run could write a ref built from it.
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
    wheresat_github,
    wheresat_parents,
    wheresat_policy,
    wheresat_records,
    wheresat_refs,
    wheresat_remotes,
    wheresat_report,
    wheresat_writes,
)
from git_donkey._constants import GIT_WHERESAT_PREFIX
from git_donkey.wheresat_errors import WheresatGraphError, WheresatUsageError
from git_donkey.wheresat_graph import GitWheresatGraph, WheresatGraph

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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

    ``--op-id`` is checked as it is read, ``--json`` prints the versioned
    envelope, and ``--record`` with ``--expected-old`` write the one record this
    command owns. ``--offline`` keeps the run from consulting any forge,
    ``--no-fetch`` keeps it from fetching the parent's head, and ``--limit``
    bounds the association search. ``--heuristic-window`` and ``--deep`` are
    accepted and inert at this milestone, because the comparisons they control
    arrive with the report work that states the window it scanned.

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
        assessment = _assess(session)
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
    _write(options, assessment, session.context.request, _warnings(session) + recorded)
    return wheresat_records.EXIT_CODES[type(assessment)]


def _session(
    options: WheresatOptions,
    *,
    repo: Repo | None,
    graph: WheresatGraph | None,
    github: wheresat_github.WheresatGitHub | None,
) -> _Session:
    """Resolve what the run was asked to immutable object IDs.

    Resolution is where a run can fail before it has anything to report: a
    branch that does not exist, a target that does not resolve, and a parent
    that is not spelled ``OWNER/REPO#N`` are all answered here rather than by an
    assessment, because no evidence exists at this point to assess.

    What the forge could not answer is not one of those failures. The ladder
    reports a question it could not put, and the head a fetch could not bring
    down is reported the same way, so both leave the run with a fault and an
    assessment to make rather than with a refusal to report.

    Returns
    -------
    _Session
        The repository, the graph, the parent the run identified, and the
        context the collection phase reads.

    Raises
    ------
    WheresatUsageError
        If a name the run was asked for does not resolve, or if the default
        branch of the remote cannot be named locally.

    """
    _validate_op_id(options)
    _validate_record_options(options)
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
    identified = wheresat_parents.identify_parent(
        request,
        wheresat_parents.SearchBounds(
            limit=options.limit,
            repository=wheresat_remotes.principal_repository(repository),
        ),
        graph=questions,
        opener=_opener(github, offline=options.offline),
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
            records=stack_store.GitStackRecordReader(repository),
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
) -> wheresat_collect.ParentHead | None:
    """Return the parent head the gates read, or nothing when neither is in hand.

    The head is offered as a bare object ID with no ref beside it, because that
    is what it is: a commit the run fetched into its own cache ref rather than
    a local branch whose reflog could be asked where the child forked from. The
    fork-point rung reads the absent ref exactly that way, and so asks nothing
    about a ref the run never had.

    Returns
    -------
    wheresat_collect.ParentHead | None
        The head the parent's gates ask about, or ``None`` when the run has
        none.

    """
    if fetched.parent is None:
        return None
    return wheresat_collect.ParentHead(fetched.parent.head_sha, ref=None)


def _faults_of(fetched: wheresat_writes.ParentHeadFetch) -> tuple[str, ...]:
    """Return the fault a fetch reported, when it reported one."""
    return (fetched.fault,) if fetched.fault is not None else ()


def _validate_op_id(options: WheresatOptions) -> None:
    """Refuse ``--op-id`` before anything is built from it.

    The id names a ref namespace, so a value that could escape it, nest inside
    another run's, or be read as another option is a usage error rather than
    something to discover while writing refs. Checking it here means the
    refusal happens before the run reads anything, and is reported the same way
    whether or not this milestone writes any ref at all.

    Raises
    ------
    WheresatUsageError
        If ``--op-id`` was given and may not name a run's namespace.

    """
    if options.op_id is None:
        return
    try:
        wheresat_refs.validate_op_id(options.op_id)
    except ValueError as exc:
        raise WheresatUsageError(str(exc)) from exc


def _validate_record_options(options: WheresatOptions) -> None:
    """Refuse an expectation that no record write would consult.

    ``--expected-old`` names the value the anchor ref must still hold for the
    record to be replaced, so it is meaningless without ``--record``. Accepting
    it silently would let a user believe a record that this run never touches
    was protected by it, which is the one thing the option is for.

    Raises
    ------
    WheresatUsageError
        If ``--expected-old`` was given without ``--record``.

    """
    if options.expected_old is not None and not options.record:
        msg = (
            "--expected-old has no meaning without --record: it names the value "
            "the stack record must still hold for --record to replace it"
        )
        raise WheresatUsageError(msg)


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


def _branch(options: WheresatOptions, repo: Repo) -> str:
    """Return the child branch, defaulting to the branch checked out here.

    Returns
    -------
    str
        The branch named by ``--branch``, or the checked-out branch.

    Raises
    ------
    WheresatUsageError
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
        raise WheresatUsageError(msg)
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
    WheresatUsageError
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
    WheresatUsageError
        If the repository configures no remote, because the default branch the
        target would default to cannot then be named at all.

    """
    remotes = [remote.name for remote in repo.remotes]
    if not remotes:
        msg = (
            "this repository has no remote to name a default branch; pass --onto "
            "to name the replay target"
        )
        raise WheresatUsageError(msg)
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
    WheresatUsageError
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
        raise WheresatUsageError(msg)
    return ref


def _parent(options: WheresatOptions) -> stack_records.PullRequestIdentity | None:
    """Return the parent pull request the run named, if it named one.

    The request records what the run set out to consult, and not what a forge
    answered: a run that named a parent pull request applies the gates about one
    whether or not the pull request could be read, so a parent that cannot be
    resolved withholds an answer instead of quietly widening the run. Nothing is
    recorded here: the ladder records one observation for every run, including
    the run that named no parent, so recording the request as well would report
    one question twice.

    Returns
    -------
    stack_records.PullRequestIdentity | None
        The pull request ``--parent`` names, or ``None`` when no parent was
        named.

    Raises
    ------
    WheresatUsageError
        If ``--parent`` is not spelled ``OWNER/REPOSITORY#NUMBER``.

    """
    if options.parent is None:
        return None
    identity = stack_records.parse_pull_request_identity(options.parent)
    if identity is None:
        msg = f"--parent must be OWNER/REPOSITORY#NUMBER, not {options.parent!r}"
        raise WheresatUsageError(msg)
    return identity


def _resolved(graph: WheresatGraph, rev: str, *, what: str) -> str:
    """Return ``rev`` resolved to an object ID.

    Returns
    -------
    str
        The commit ``rev`` names.

    Raises
    ------
    WheresatUsageError
        If the revision does not resolve, or if Git cannot be asked. The
        question is put before there is any evidence to assess, so an
        unanswerable one is a configuration failure rather than an
        indeterminate result.

    """
    try:
        return graph.resolve(rev)
    except WheresatGraphError as exc:
        msg = f"{what} could not be resolved: {exc}"
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
    return _resolved(session.graph, expected, what=f"--expected-old {expected!r}")


def _assess(session: _Session) -> wheresat_records.Assessment:
    """Return what the evidence makes of the boundary, faults included.

    The parent the run identified is handed to the assessment with the
    candidates, because the gates about a parent read it and the boundary it
    establishes depends on them. Every fault is applied at once — the ladder's,
    the fetch's, the collection's, and the facts' — because they are one thing
    to the reader: a question the procedure asked that nothing answered.

    Returns
    -------
    wheresat_records.Assessment
        The boundary the evidence establishes, or why none was, with a fault
        forcing an indeterminate result rather than a refusal.

    """
    evidence = wheresat_collect.collect_evidence(session.context)
    facts = wheresat_facts.assemble_facts(session.context, evidence)
    assessment = wheresat_policy.assess(
        session.context.request,
        evidence.candidates,
        facts.facts,
        session.context.parent,
    )
    return wheresat_policy.apply_collection_faults(
        assessment,
        session.parent_faults + evidence.faults + facts.faults,
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
