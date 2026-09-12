"""Resolve the principal remote's advertised default branch.

``git donkey`` bases new branches on the default branch the principal remote
advertises, and ``git plonk`` judges whether a worktree's completion marker has
landed on that same branch. Both commands resolve it through this module so the
two can never disagree about which branch is the repository's trunk.

The remote's advertised symbolic ``HEAD`` is the authority. It is read with
``git ls-remote --symref`` and the branch it names is fetched explicitly into
its fully qualified remote-tracking ref. The local ``refs/remotes/<remote>/HEAD``
alias is never consulted, because a fetch can leave it naming a branch the
remote no longer advertises, and neither command falls back to local ``main``.

Usage
-----
Resolve the default branch ref before reading or building on trunk history::

    from git_donkey import remote_default

    remote = remote_default.principal_remote(repo, "git-plonk")
    branch = remote_default.discover_default_branch(repo, remote, "git-plonk")
    trunk_ref = remote_default.fetch_default_branch_ref(
        repo, remote, branch, "git-plonk"
    )
"""

from __future__ import annotations

from git import GitCommandError, Repo

from git_donkey import helpers, observability
from git_donkey.observability import Observation


def _record(observation: Observation) -> None:
    """Record one bounded workflow observation on the active recorder."""
    observability.get_recorder().record(observation)


def principal_remote(repo: Repo, prefix: str) -> str:
    """Return the name of the repository's principal remote.

    The principal remote is the first configured remote, preserving the
    selection rule both commands have always used.

    Parameters
    ----------
    repo : Repo
        Repository whose configured remotes are inspected.
    prefix : str
        Command name used for error messages.

    Returns
    -------
    str
        The name of the first configured remote.

    Raises
    ------
    SystemExit
        If the repository configures no remotes.

    """
    return helpers._first_remote_name(repo, prefix)


def advertised_default_branch(advertisement: str) -> str | None:
    r"""Extract the branch targeted by HEAD from ls-remote --symref output.

    Parameters
    ----------
    advertisement : str
        Raw output of ``git ls-remote --symref <remote> HEAD``.

    Returns
    -------
    str | None
        The advertised branch name, or ``None`` when the output names no
        branch under ``refs/heads/``.

    Examples
    --------
    >>> advertised_default_branch("ref: refs/heads/trunk\tHEAD\nabc\tHEAD")
    'trunk'
    >>> advertised_default_branch("abc\tHEAD") is None
    True

    """
    for line in advertisement.splitlines():
        match line.split():
            case ["ref:", ref, "HEAD"] if ref.startswith("refs/heads/"):
                return ref.removeprefix("refs/heads/")
    return None


def discover_default_branch(
    repo: Repo,
    remote: str,
    prefix: str,
    *,
    missing_advice: str | None = None,
) -> str:
    """Read the default branch name ``remote`` advertises.

    Parameters
    ----------
    repo : Repo
        Repository whose remote is queried.
    remote : str
        Name of the remote to query.
    prefix : str
        Command name used for error messages.
    missing_advice : str | None, optional
        Sentence appended when the remote advertises no default branch. It
        belongs to the calling command, because the remedy differs between
        them: `git donkey` can be given an explicit base, while `git plonk`
        cannot continue at all.

    Returns
    -------
    str
        The branch name the remote advertises for its ``HEAD``.

    Raises
    ------
    SystemExit
        If the remote cannot be queried, or advertises no default branch.

    """
    with observability.get_recorder().span("remote_default_discovery"):
        try:
            advertisement = repo.git.ls_remote("--symref", remote, "HEAD")
        except GitCommandError as exc:
            _record(
                Observation(
                    operation="remote_default_discovery",
                    outcome="failure",
                    error_kind="git_command_error",
                )
            )
            helpers._die(
                prefix,
                f"cannot discover the default branch on '{remote}': {exc}",
                1,
            )
        branch = advertised_default_branch(advertisement)
        if branch is None:
            _record(
                Observation(
                    operation="remote_default_discovery",
                    outcome="failure",
                    error_kind="missing_advertised_default",
                )
            )
            message = f"remote '{remote}' does not advertise a default branch"
            if missing_advice:
                message = f"{message}; {missing_advice}"
            helpers._die(prefix, message, 1)
        _record(Observation(operation="remote_default_discovery", outcome="success"))
    return branch


def fetch_default_branch_ref(
    repo: Repo,
    remote: str,
    branch: str,
    prefix: str,
) -> str:
    """Fetch ``branch`` from ``remote`` into its remote-tracking ref.

    The branch is fetched by explicit refspec rather than by name, so a narrow
    fetch configuration that omits the default branch still resolves it.

    Parameters
    ----------
    repo : Repo
        Repository to fetch into.
    remote : str
        Name of the remote to fetch from.
    branch : str
        Branch name the remote advertises as its default.
    prefix : str
        Command name used for error messages.

    Returns
    -------
    str
        The fully qualified remote-tracking ref of the fetched branch.

    Raises
    ------
    SystemExit
        If the branch cannot be fetched.

    """
    remote_ref = f"refs/remotes/{remote}/{branch}"
    # A narrow fetch configuration may omit the advertised default branch.
    # Fetch it explicitly rather than trusting a stale local remote/HEAD alias.
    with observability.get_recorder().span("default_branch_fetch"):
        try:
            repo.git.fetch(remote, f"+refs/heads/{branch}:{remote_ref}")
        except GitCommandError as exc:
            _record(
                Observation(
                    operation="default_branch_fetch",
                    outcome="failure",
                    error_kind="git_command_error",
                )
            )
            helpers._die(
                prefix,
                f"cannot fetch default branch '{remote}/{branch}': {exc}",
                1,
            )
        _record(Observation(operation="default_branch_fetch", outcome="success"))
    return remote_ref
