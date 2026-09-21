"""Resolve what ``git wheresat`` was asked into immutable object IDs.

The options are read as the command line spells them, and the names they carry
are resolved here: the child branch, the replay target, and the parent pull
request. Resolution is where a run can fail before it has anything to report,
and a failure here is a usage error rather than an indeterminate result — a
branch that does not exist, a target that does not resolve, and a ``--parent``
that is not spelled ``OWNER/REPOSITORY#NUMBER`` are reported before there is any
evidence to assess, and the run that reports one exits with the status that says
it could not start. A question the repository cannot answer is never quietly
widened into a weaker one, which is why the target defaults to the branch the
principal remote's own ``HEAD`` names rather than to a local guess.

Nothing here reads evidence or writes anything, and no question is put twice:
:func:`resolve` asks each one once and hands the run the question every later
phase reads, so the answer the gates weigh is the one the run set out to ask
(:mod:`git_donkey.wheresat`). The options live here rather than beside the run
because they *are* what was asked, and because the module that resolves them is
the only one that reads them field by field.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import stack_records, wheresat_records, wheresat_refs
from git_donkey.wheresat_errors import WheresatGraphError, WheresatUsageError

if typ.TYPE_CHECKING:
    from git import Repo

    from git_donkey.wheresat_graph import WheresatGraph


@dataclasses.dataclass(frozen=True, slots=True)
class WheresatOptions:
    """Every command-line input, before resolution to object IDs.

    ``--op-id`` is checked as it is read, ``--json`` prints the versioned
    envelope, and ``--record`` with ``--expected-old`` write the one record this
    command owns. ``--offline`` keeps the run from consulting any forge,
    ``--no-fetch`` keeps it from fetching the parent's head, and ``--limit``
    bounds the association search. ``--deep`` asks the comparison of the child
    against the target's content, and ``--heuristic-window`` is how many of the
    target's newest commits that comparison is bounded to: the flag is the
    cost control and the window is what the report states it scanned.

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


DEFAULT_OPTIONS = WheresatOptions()
"""What every option defaults to, for the command line to spread.

Cyclopts reads the fields of :class:`WheresatOptions` off the signature of the
command-line wrapper, so the wrapper needs an instance to default to. It is
built here, beside the class, so a field added to one is visible in the other.
"""


def validate_op_id(options: WheresatOptions) -> None:
    """Refuse ``--op-id`` before anything is built from it.

    The id names a ref namespace, so a value that could escape it, nest inside
    another run's, or be read as another option is a usage error rather than
    something to discover while writing refs. Checking it here means the
    refusal happens before the run reads anything, and is reported the same way
    whether or not this milestone writes any ref at all.

    Parameters
    ----------
    options : WheresatOptions
        What the run was asked, of which ``--op-id`` is checked.

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


def validate_record_options(options: WheresatOptions) -> None:
    """Refuse an expectation that no record write would consult.

    ``--expected-old`` names the value the anchor ref must still hold for the
    record to be replaced, so it is meaningless without ``--record``. Accepting
    it silently would let a user believe a record that this run never touches
    was protected by it, which is the one thing the option is for.

    Parameters
    ----------
    options : WheresatOptions
        What the run was asked, of which the record options are checked.

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


def resolve(
    options: WheresatOptions,
    repository: Repo,
    graph: WheresatGraph,
) -> tuple[wheresat_records.BoundaryRequest, str | None]:
    """Resolve the branch, the target, and the tip into the run's question.

    Resolution is where a run can fail before it has anything to report: a
    branch that does not exist, a target that does not resolve, and a parent
    that is not spelled ``OWNER/REPO#N`` are all answered here rather than by an
    assessment, because no evidence exists at this point to assess.

    Parameters
    ----------
    options : WheresatOptions
        What the run was asked for, of which the parent, the depth, the window,
        and the offline flag are read here.
    repository : git.Repo
        Repository the branch and the target are resolved in.
    graph : WheresatGraph
        Graph both resolution questions are put to.

    Returns
    -------
    tuple[BoundaryRequest, str | None]
        The question every later phase reads, and the ref the target was read
        from when a ref named it.

    Raises
    ------
    WheresatUsageError
        If a name the run was asked for does not resolve, or if the default
        branch of the remote cannot be named locally.

    """
    branch = _branch(options, repository)
    target, target_ref = _target(options, repository, graph)
    # The tip is read from the branch's own ref rather than from the bare name,
    # because a bare name is resolved by Git's precedence rules: a tag, or a
    # file, that happens to carry the branch's name would answer for it. The
    # ``--branch`` option names a branch, so only the branch's ref is asked.
    return (
        wheresat_records.BoundaryRequest(
            branch=branch,
            child_tip=object_id(
                graph, f"refs/heads/{branch}", what=f"the branch {branch}"
            ),
            target=target,
            parent=_named_parent(options),
            deep=options.deep,
            heuristic_window=options.heuristic_window,
            offline=options.offline,
        ),
        target_ref,
    )


def _branch(options: WheresatOptions, repo: Repo) -> str:
    """Return the child branch, defaulting to the branch checked out here.

    Parameters
    ----------
    options : WheresatOptions
        What the run was asked, of which ``--branch`` is read.
    repo : git.Repo
        Repository the checked-out branch is read from.

    Returns
    -------
    str
        The branch named by ``--branch``, or the checked-out branch.

    Raises
    ------
    WheresatUsageError
        If HEAD is detached and no ``--branch`` was given, because a boundary
        read for a detached HEAD would name a branch that does not exist; or if
        the branch named is one no ref may carry.

    """
    if options.branch is not None:
        return _usable_branch(options.branch)
    if repo.head.is_detached:
        msg = (
            "HEAD is detached in the current directory, so no child branch can be "
            "read from it; name one with --branch"
        )
        raise WheresatUsageError(msg)
    return repo.active_branch.name


def _usable_branch(branch: str) -> str:
    """Return ``branch`` when a record may be kept for it, else refuse.

    The branch names the refs a record is written to and read from, and it
    reaches the command line of the Git commands those refs are written with.
    A value that begins with a dash, that carries the ``:`` a fetch refspec
    separates its halves with, or that is otherwise no ref path component would
    fail inside the collection phase, where the anchor ref is built — an
    uncaught :class:`ValueError` raised after the run had begun reading
    evidence. Checking it here makes it a usage error instead: reported before
    the run reads anything, and with the status every other unresolvable name
    carries.

    Returns
    -------
    str
        ``branch``, unchanged.

    Raises
    ------
    WheresatUsageError
        If ``branch`` would be unsafe in a ref path.

    """
    try:
        return stack_records.validate_ref_component(branch)
    except ValueError as exc:
        msg = f"--branch cannot name a branch: {exc}"
        raise WheresatUsageError(msg) from exc


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

    Parameters
    ----------
    options : WheresatOptions
        What the run was asked, of which ``--onto`` and ``--remote`` are read.
    repo : git.Repo
        Repository the principal remote is read from when none was named.
    graph : WheresatGraph
        Graph the target and the remote's symbolic ref are resolved through.

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
        If the named revision does not resolve, if Git cannot be asked which ref
        it names, or if no local ref records the remote's default branch.

    """
    if options.onto is not None:
        what = f"the target {options.onto}"
        target = object_id(graph, options.onto, what=what)
        return target, _ref_name(graph, options.onto, what=what)
    remote = options.remote if options.remote is not None else _principal_remote(repo)
    ref = _default_branch_ref(remote, graph)
    target = object_id(graph, ref, what=f"the default branch of {remote!r}")
    return target, ref


def _principal_remote(repo: Repo) -> str:
    """Return the first configured remote, which is the principal one.

    Parameters
    ----------
    repo : git.Repo
        Repository whose configured remotes are read.

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

    Parameters
    ----------
    remote : str
        Name of the remote whose ``HEAD`` is read.
    graph : WheresatGraph
        Graph the symbolic ref is read through.

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


def _named_parent(options: WheresatOptions) -> stack_records.PullRequestIdentity | None:
    """Return the parent pull request the run named, if it named one.

    The request records what the run set out to consult, and not what a forge
    answered: a run that named a parent pull request applies the gates about one
    whether or not the pull request could be read, so a parent that cannot be
    resolved withholds an answer instead of quietly widening the run. Nothing is
    recorded here: the ladder records one observation for every run, including
    the run that named no parent, so recording the request as well would report
    one question twice.

    Parameters
    ----------
    options : WheresatOptions
        What the run was asked, of which ``--parent`` is read.

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


def object_id(graph: WheresatGraph, rev: str, *, what: str) -> str:
    """Return ``rev`` resolved to an object ID.

    Parameters
    ----------
    graph : WheresatGraph
        Graph the revision is resolved through.
    rev : str
        Revision, ref, or object ID to resolve.
    what : str
        What the revision was for, phrased to read after a subject: the refusal
        names it so the operator knows which of the run's names did not resolve.

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


def _ref_name(graph: WheresatGraph, rev: str, *, what: str) -> str | None:
    """Return the full ref path ``rev`` names, or ``None`` when it names none.

    Naming no ref is an answer rather than a fault — a revision that resolves to
    a commit without naming a ref simply has no reflog to read — so only a
    question Git cannot put is a failure here.

    Parameters
    ----------
    graph : WheresatGraph
        Graph the ref name is read through.
    rev : str
        Revision to look up, already resolved to a commit.
    what : str
        What the revision was for, phrased to read after a subject: the refusal
        names it so the operator knows which of the run's names Git could not
        be asked about.

    Returns
    -------
    str | None
        The full ref path ``rev`` names, or ``None`` when ``rev`` names none.

    Raises
    ------
    WheresatUsageError
        If Git cannot be asked. The ref name decides whether the fork-point
        question can be put at all, and it is read before there is any evidence
        to assess, so an unanswerable question is a configuration failure rather
        than an indeterminate result.

    """
    try:
        return graph.ref_name(rev)
    except WheresatGraphError as exc:
        msg = f"{what} could not be named: {exc}"
        raise WheresatUsageError(msg) from exc
