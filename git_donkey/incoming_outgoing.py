"""Mercurial-style incoming and outgoing branch comparison workflows.

The ``git-incoming`` and ``git-outgoing`` commands answer the two questions a
developer asks before syncing with a shared branch: which commits would arrive
on a pull, and which would leave on a push. Both compare the local ``HEAD``
against a comparison ref and print the commits reachable from one side only.

The comparison ref is the explicit ``ref`` argument when supplied, otherwise
the current branch's configured upstream (``@{upstream}``). A ref that names a
configured remote is fetched by default before the comparison, whether it is
written as ``origin/main`` or in canonical ``refs/remotes/origin/main`` form.
That fetch updates the shared remote-tracking ref, so a later ``--no-fetch``
run compares against the newly fetched state. ``--no-fetch`` skips the fetch
entirely and never contacts the remote; local refs such as ``main`` are never
fetched regardless.

Ref selection and commit lookup are query steps: they return data without
printing, fetching, or otherwise changing state, so callers can reuse them and
tests can drive them directly. ``git_donkey.incoming_outgoing_policy`` owns the
pure comparison decisions, the ``_ComparisonAdapter`` protocol describes the
Git surface the queries need, and ``_GitPythonComparison`` implements that
protocol over GitPython and the shared helpers in ``git_donkey.helpers``.
Fetching, rendering, and exit-code mapping belong to the command boundary in
``_run_comparison`` and the ``_fetch_comparison_remote`` and
``_read_comparison`` helpers it calls, which also emit structured log records
for comparison start, the fetch attempt and its outcome, and comparison
completion. Those steps also report through
``git_donkey.observability``: the fetch is timed as ``comparison_fetch`` and
the comparison read as ``comparison``, each recording a bounded outcome
through the process-wide recorder (``success``, ``failure``, or
``not_requested`` for the fetch; ``found``, ``empty``, or ``failure`` for the
comparison).

The ``git_donkey.cli`` module exposes these workflows as Cyclopts apps and
delegates to ``run_git_incoming`` and ``run_git_outgoing``.

Notes
-----
Both runners return standard process exit codes:

- 0: matching commits were found and printed
- 1: the comparison succeeded but found no matching commits
- 2: the command could not run, such as when no upstream is configured and no
  explicit ref was supplied, a fetch failed, or the comparison failed

Examples
--------
>>> from git_donkey import incoming_outgoing
>>> incoming_outgoing.run_git_incoming("origin/main")
0
>>> incoming_outgoing.run_git_outgoing("origin/main", fetch=False)
1

"""

from __future__ import annotations

import dataclasses
import logging
import typing as typ

from git import GitCommandError, Repo

from git_donkey import helpers, incoming_outgoing_policy, observability

_LOGGER = logging.getLogger(__name__)

_GIT_INCOMING_PREFIX = "git-incoming"
_GIT_OUTGOING_PREFIX = "git-outgoing"


class _GitLog(typ.Protocol):
    """Small protocol for the ``git log`` command surface."""

    def log(self, *args: str) -> str:
        """Run ``git log`` with the provided arguments."""


class _ComparisonAdapter(_GitLog, typ.Protocol):
    """Git infrastructure required by the comparison queries."""

    def upstream_ref(self) -> str | None:
        """Return the current branch upstream ref, or ``None`` when unset."""

    def remote_names(self) -> typ.Iterable[str]:
        """Return the configured remote names."""

    def fetch_remote(self, remote: str) -> None:
        """Fetch updates from ``remote``, reporting failures itself."""


@dataclasses.dataclass(frozen=True, slots=True)
class _ComparisonRequest:
    """A single incoming or outgoing comparison request."""

    prefix: str
    direction: typ.Literal["incoming", "outgoing"]
    ref: str | None = None
    fetch: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class _GitPythonComparison:
    """GitPython-backed implementation of the comparison adapter."""

    repo: Repo
    prefix: str

    def upstream_ref(self) -> str | None:
        """Return the upstream ref, or ``None`` when none is configured."""
        try:
            return str(
                self.repo.git.rev_parse(
                    "--abbrev-ref",
                    "--symbolic-full-name",
                    "@{upstream}",
                )
            )
        except GitCommandError:
            return None

    def remote_names(self) -> typ.Iterable[str]:
        """Return the configured remote names."""
        return [str(remote.name) for remote in self.repo.remotes]

    def fetch_remote(self, remote: str) -> None:
        """Fetch ``remote`` through the shared helper."""
        helpers._fetch_remote(self.repo, remote, self.prefix)

    def log(self, *args: str) -> str:
        """Run ``git log`` through GitPython."""
        return typ.cast("_GitLog", self.repo.git).log(*args)


def _resolve_comparison_ref(
    adapter: _ComparisonAdapter,
    ref: str | None,
) -> str | None:
    """Return an explicit comparison ref or the current branch upstream."""
    if ref is not None:
        return ref
    return adapter.upstream_ref()


def _commits_unique_to(
    git: _GitLog,
    *,
    include_ref: str,
    exclude_ref: str,
) -> str:
    """Return commits reachable from ``include_ref`` but not ``exclude_ref``."""
    return git.log(
        "--oneline",
        "--decorate",
        include_ref,
        "--not",
        exclude_ref,
    )


def _fetch_comparison_remote(
    request: _ComparisonRequest,
    adapter: _ComparisonAdapter,
    remote_name: str | None,
) -> bool:
    """Fetch the comparison remote, returning ``False`` when the fetch fails."""
    if not request.fetch or remote_name is None:
        observability.get_recorder().record(
            observability.Observation(
                operation="comparison_fetch",
                outcome="not_requested",
            )
        )
        return True

    direction = request.direction
    _LOGGER.info(
        "Fetching comparison remote",
        extra={
            "operation": "fetch",
            "direction": direction,
            "remote": remote_name,
        },
    )
    try:
        with observability.get_recorder().span("comparison_fetch"):
            adapter.fetch_remote(remote_name)
    except SystemExit:
        # The shared helper exits 1; this workflow reserves 1 for a
        # successful comparison that found no commits, so a failed fetch
        # must exit 2 like any other failure to run.
        _LOGGER.warning(
            "Comparison fetch failed",
            extra={
                "operation": "fetch",
                "direction": direction,
                "remote": remote_name,
                "result": "failure",
            },
        )
        observability.get_recorder().record(
            observability.Observation(
                operation="comparison_fetch",
                outcome="failure",
                error_kind="git_command_error",
            )
        )
        return False

    _LOGGER.info(
        "Completed comparison fetch",
        extra={
            "operation": "fetch",
            "direction": direction,
            "remote": remote_name,
            "result": "success",
        },
    )
    observability.get_recorder().record(
        observability.Observation(
            operation="comparison_fetch",
            outcome="success",
        )
    )
    return True


def _read_comparison(
    request: _ComparisonRequest,
    git: _GitLog,
    *,
    include_ref: str,
    exclude_ref: str,
) -> int:
    """Read the commits unique to ``include_ref`` and report the outcome."""
    direction = request.direction
    try:
        with observability.get_recorder().span("comparison"):
            output = _commits_unique_to(
                git,
                include_ref=include_ref,
                exclude_ref=exclude_ref,
            )
    except GitCommandError as exc:
        _LOGGER.exception(
            "Comparison failed",
            extra={
                "operation": "compare",
                "direction": direction,
                "result": "failure",
            },
        )
        observability.get_recorder().record(
            observability.Observation(
                operation="comparison",
                outcome="failure",
                error_kind="git_command_error",
            )
        )
        helpers._eprint(f"{request.prefix}: comparison failed: {exc}")
        return 2

    if output:
        print(output)
    commit_count = len(output.splitlines())
    _LOGGER.info(
        "Completed %s comparison",
        direction,
        extra={
            "operation": "compare",
            "direction": direction,
            "commit_count": commit_count,
            "result": "found" if commit_count else "empty",
        },
    )
    observability.get_recorder().record(
        observability.Observation(
            operation="comparison",
            outcome="found" if commit_count else "empty",
        )
    )
    return 0 if commit_count else 1


def _run_comparison(
    request: _ComparisonRequest,
    adapter: _ComparisonAdapter | None = None,
) -> int:
    """Run one incoming or outgoing branch comparison."""
    prefix = request.prefix
    direction = request.direction
    if adapter is None:
        adapter = _GitPythonComparison(helpers._find_repo(prefix), prefix)

    comparison_ref = _resolve_comparison_ref(adapter, request.ref)
    if comparison_ref is None:
        helpers._eprint(
            f"{prefix}: no upstream branch configured; set one with "
            "`git branch --set-upstream-to <remote>/<branch>` or pass a ref"
        )
        return 2

    remote_name = incoming_outgoing_policy.remote_name_for_ref(
        adapter.remote_names(),
        comparison_ref,
    )
    include_ref, exclude_ref = incoming_outgoing_policy.comparison_range(
        direction=direction,
        ref=comparison_ref,
    )
    _LOGGER.info(
        "Starting %s comparison",
        direction,
        extra={
            "operation": "compare",
            "direction": direction,
            "fetch_enabled": request.fetch,
            "ref": comparison_ref,
        },
    )

    if not _fetch_comparison_remote(request, adapter, remote_name):
        return 2

    return _read_comparison(
        request,
        adapter,
        include_ref=include_ref,
        exclude_ref=exclude_ref,
    )


def run_git_incoming(ref: str | None = None, *, fetch: bool = True) -> int:
    """Report commits that would be pulled from the comparison ref.

    Compares ``HEAD`` against ``ref`` and prints the commits reachable from
    the comparison ref but not from ``HEAD``, one line per commit.

    Parameters
    ----------
    ref : str | None
        Explicit comparison ref. When ``None``, the current branch's
        configured upstream (``@{upstream}``) is used. A ref naming a
        configured remote, whether as ``origin/main`` or
        ``refs/remotes/origin/main``, is fetched when ``fetch`` is true.
    fetch : bool
        When true (the default), fetch the remote owning a remote-backed
        comparison ref before comparing, updating the shared remote-tracking
        ref. When false, compare against the currently known tracking ref
        without contacting the remote. Local refs are never fetched.

    Returns
    -------
    int
        ``0`` if commits were found and printed, ``1`` if the comparison
        succeeded but found no commits, or ``2`` if the command could not
        run (no upstream and no explicit ref, a failed fetch, or a failed
        comparison).

    Examples
    --------
    >>> from git_donkey import incoming_outgoing
    >>> incoming_outgoing.run_git_incoming("origin/main")
    0
    >>> incoming_outgoing.run_git_incoming("main", fetch=False)
    1

    """
    return _run_comparison(
        _ComparisonRequest(
            prefix=_GIT_INCOMING_PREFIX,
            direction="incoming",
            ref=ref,
            fetch=fetch,
        )
    )


def run_git_outgoing(ref: str | None = None, *, fetch: bool = True) -> int:
    """Report commits that would be pushed to the comparison ref.

    Compares ``HEAD`` against ``ref`` and prints the commits reachable from
    ``HEAD`` but not from the comparison ref, one line per commit.

    Parameters
    ----------
    ref : str | None
        Explicit comparison ref. When ``None``, the current branch's
        configured upstream (``@{upstream}``) is used. A ref naming a
        configured remote, whether as ``origin/main`` or
        ``refs/remotes/origin/main``, is fetched when ``fetch`` is true.
    fetch : bool
        When true (the default), fetch the remote owning a remote-backed
        comparison ref before comparing, updating the shared remote-tracking
        ref. When false, compare against the currently known tracking ref
        without contacting the remote. Local refs are never fetched.

    Returns
    -------
    int
        ``0`` if commits were found and printed, ``1`` if the comparison
        succeeded but found no commits, or ``2`` if the command could not
        run (no upstream and no explicit ref, a failed fetch, or a failed
        comparison).

    Examples
    --------
    >>> from git_donkey import incoming_outgoing
    >>> incoming_outgoing.run_git_outgoing("origin/main")
    0
    >>> incoming_outgoing.run_git_outgoing("main", fetch=False)
    1

    """
    return _run_comparison(
        _ComparisonRequest(
            prefix=_GIT_OUTGOING_PREFIX,
            direction="outgoing",
            ref=ref,
            fetch=fetch,
        )
    )
