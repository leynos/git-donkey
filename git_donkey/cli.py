"""Command-line interfaces for the git-donkey tools.

Provides CLI entrypoints for the git-donkey workflow tools. This module is the
console-script boundary: it owns Cyclopts argument parsing and delegates all
workflow behaviour to modules such as ``donkey``, ``track``, ``fafo``,
``incoming_outgoing``, ``plonk``, ``wheresat``, and ``template_cmd``.

This module exposes console scripts (git-donkey, git-track, git-fafo,
git-plonk, git-wheresat, git-donkey-template, git-incoming, git-in,
git-outgoing, and git-out) registered in pyproject.toml. Each entrypoint maps
to a workflow runner.

Run with::

    git-donkey --help
    git-track --help
    git-fafo --help
    git-plonk --help
    git-wheresat --help
    git-donkey-template --help
    git-incoming --help
    git-outgoing --help
"""

from __future__ import annotations

import sys
import typing as typ

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError

from git_donkey import (
    donkey,
    fafo,
    incoming_outgoing,
    plonk,
    template_cmd,
    track,
    wheresat,
    wheresat_records,
    wheresat_report,
    wheresat_request,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_donkey_app = App(
    name="git donkey",
    help=(
        "Create a linked worktree at ../{repo}.worktrees/{branch}, branching from "
        "the first remote's default branch, a specified base, or '.' meaning the "
        "branch currently checked out in the CWD. Fetches remote refs without "
        "pulling by default. --pull-rebase or --pull-ff enables a confirmation "
        "prompt to update a behind local base. Reuses existing branches."
    ),
)


@_donkey_app.default
def _donkey_cli(
    branch_name: str,
    origin_branch: str | None = None,
    *,
    no_pull: bool = False,
    options: typ.Annotated[
        donkey._PullOptions,
        Parameter(name="*"),
    ] = donkey._DEFAULT_PULL_OPTIONS,
) -> None:
    """CLI wrapper for git-donkey."""
    raise SystemExit(
        donkey.run_git_donkey(
            branch_name,
            origin_branch,
            no_pull=no_pull,
            options=options,
        )
    )


_track_app = App(
    name="git track",
    help=(
        "Fetch from the first remote, then switch to an existing local branch "
        "and update it, or create a new tracking branch from remote/branch."
    ),
)


@_track_app.default
def _track_cli(branch: str) -> None:
    """CLI wrapper for git-track."""
    raise SystemExit(track.run_git_track(branch))


_fafo_app = App(
    name="git fafo",
    help=(
        "Scaffold and publish a new GitHub repository, optionally from "
        "agent-template-<language> using copier and git. Uses GITHUB_TOKEN/GH_TOKEN "
        "when set; otherwise runs device flow with default client ID "
        f"{fafo._DEFAULT_GITHUB_CLIENT_ID} (override via "
        "GIT_DONKEY_GITHUB_CLIENT_ID) and stores the token at "
        "~/.config/git-donkey/github-token (override via "
        "GIT_DONKEY_CREDENTIALS_FILE)."
    ),
)


@_fafo_app.default
def _fafo_cli(
    repo_name: str,
    language: str | None = None,
    *,
    trust: bool = False,
    yes: typ.Annotated[
        bool,
        Parameter(alias=["--yes", "-y"]),
    ] = False,
) -> None:
    """CLI wrapper for git-fafo."""
    token = fafo._github_token()
    raise SystemExit(
        fafo.run_git_fafo(
            repo_name,
            language,
            token=token,
            options=fafo._FafoOptions(trust=trust, yes=yes),
        )
    )


def git_donkey() -> None:
    """Console entrypoint for git-donkey."""
    _donkey_app()


def git_track() -> None:
    """Console entrypoint for git-track."""
    _track_app()


class _ComparisonRunner(typ.Protocol):
    """Callable surface shared by the incoming and outgoing runners."""

    def __call__(self, ref: str | None = None, *, fetch: bool = True) -> int:
        """Run one comparison and return its exit code."""


def _run_incoming_outgoing_cli(
    runner: _ComparisonRunner,
    ref: str | None,
    *,
    no_fetch: bool,
) -> None:
    """Run an incoming or outgoing comparison CLI wrapper."""
    raise SystemExit(runner(ref, fetch=not no_fetch))


_incoming_app = App(
    name="git incoming",
    help=(
        "Show commits present in the upstream or explicit ref and absent from "
        "HEAD; only remote-backed refs are fetched by default."
    ),
)


@_incoming_app.default
def _incoming_cli(
    ref: str | None = None,
    *,
    no_fetch: bool = False,
) -> None:
    """CLI wrapper for git-incoming."""
    _run_incoming_outgoing_cli(
        incoming_outgoing.run_git_incoming,
        ref,
        no_fetch=no_fetch,
    )


def git_incoming() -> None:
    """Console entrypoint for ``git-incoming``.

    Registered in ``pyproject.toml`` as the ``git-incoming`` console script.
    Parses arguments with Cyclopts through the shared ``git incoming`` app and
    delegates to ``incoming_outgoing.run_git_incoming``.

    Examples
    --------
    ::

        $ git-incoming origin/main
        abc1234 Fix parser edge case

    """
    _incoming_app()


def git_in() -> None:
    """Console entrypoint for ``git-in``, an alias of ``git-incoming``.

    Registered in ``pyproject.toml`` as the ``git-in`` console script. Shares
    the ``git incoming`` Cyclopts app with ``git_incoming`` and therefore
    behaves identically.

    Examples
    --------
    ::

        $ git-in --no-fetch

    """
    _incoming_app()


_outgoing_app = App(
    name="git outgoing",
    help=(
        "Show commits present in HEAD and absent from the upstream or explicit "
        "ref; only remote-backed refs are fetched by default."
    ),
)


@_outgoing_app.default
def _outgoing_cli(
    ref: str | None = None,
    *,
    no_fetch: bool = False,
) -> None:
    """CLI wrapper for git-outgoing."""
    _run_incoming_outgoing_cli(
        incoming_outgoing.run_git_outgoing,
        ref,
        no_fetch=no_fetch,
    )


def git_outgoing() -> None:
    """Console entrypoint for ``git-outgoing``.

    Registered in ``pyproject.toml`` as the ``git-outgoing`` console script.
    Parses arguments with Cyclopts through the shared ``git outgoing`` app and
    delegates to ``incoming_outgoing.run_git_outgoing``.

    Examples
    --------
    ::

        $ git-outgoing origin/main
        abc1234 Local commit

    """
    _outgoing_app()


def git_out() -> None:
    """Console entrypoint for ``git-out``, an alias of ``git-outgoing``.

    Registered in ``pyproject.toml`` as the ``git-out`` console script. Shares
    the ``git outgoing`` Cyclopts app with ``git_outgoing`` and therefore
    behaves identically.

    Examples
    --------
    ::

        $ git-out --no-fetch

    """
    _outgoing_app()


def git_fafo() -> None:
    """Console entrypoint for git-fafo."""
    _fafo_app()


_plonk_app = App(
    name="git plonk",
    help=(
        "Clean up git-donkey worktrees. Default mode removes completed "
        "worktrees, --soft removes generated directories, and --hard also "
        "deletes completed local branches. Completion is judged against the "
        "default branch the principal remote advertises, fetched before the "
        "sweep. A completed worktree with uncommitted or untracked files is "
        "skipped and reported rather than forced, and its branch is kept. "
        "--dry-run previews planned actions without removing generated paths, "
        "worktrees, or branches."
    ),
)


@_plonk_app.default
def _plonk_cli(
    *,
    soft: bool = False,
    hard: bool = False,
    dry_run: bool = False,
) -> None:
    """CLI wrapper for git-plonk."""
    raise SystemExit(plonk.run_git_plonk(soft=soft, hard=hard, dry_run=dry_run))


def git_plonk() -> None:
    """Console entrypoint for git-plonk."""
    _plonk_app()


_JSON_FLAG: typ.Final = "--json"
_NO_JSON_FLAG: typ.Final = "--no-json"
_JSON_ASSIGNMENT: typ.Final = "--json="
_OPTIONS_DELIMITER: typ.Final = "--"
"""Token after which every argument is positional rather than an option."""
_JSON_FALSE_VALUES: typ.Final = frozenset({"no", "n", "0", "false", "f"})
"""What Cyclopts reads as a false value for a boolean flag."""


def _asks_for_json(tokens: cabc.Sequence[str]) -> bool:
    """Return whether the raw arguments ask for the JSON envelope.

    The question is put to the arguments rather than to the parsed options,
    because it is asked exactly when parsing has failed: a run whose argument
    the parser refused still owes the caller who asked for JSON the document
    that every other exit status is written in. A later flag overrides an
    earlier one, as it does in Cyclopts, and the scan stops at ``--``, after
    which a token is positional rather than an option. A value Cyclopts would
    refuse to read as a boolean counts as asking for the envelope, because the
    caller plainly meant to ask for one.

    Parameters
    ----------
    tokens : cabc.Sequence[str]
        The arguments as the process received them, before parsing.

    Returns
    -------
    bool
        Whether the envelope was asked for.

    """
    asked = False
    for token in tokens:
        if token == _OPTIONS_DELIMITER:
            break
        if token == _JSON_FLAG:
            asked = True
        elif token == _NO_JSON_FLAG:
            asked = False
        elif token.startswith(_JSON_ASSIGNMENT):
            value = token.removeprefix(_JSON_ASSIGNMENT).strip().lower()
            asked = value not in _JSON_FALSE_VALUES
    return asked


_wheresat_app = App(
    name="git wheresat",
    help=(
        "Locate the boundary a branch was replayed over: the commit it should "
        "be rebased onto to drop work that has already landed. Reads the stack "
        "record git donkey wrote at the branch's birth, the parent pull "
        "request's head, the merge base, and the fork point, and weighs them "
        "under one precedence. Exits 0 when a boundary was established, 1 when "
        "the evidence refused one, 2 for a usage or environment error, and 3 "
        "when the repository could not answer. --json emits a versioned "
        "envelope on every exit code."
    ),
)


@_wheresat_app.default
def _wheresat_cli(
    *,
    options: typ.Annotated[
        wheresat.WheresatOptions,
        Parameter(name="*"),
    ] = wheresat_request.DEFAULT_OPTIONS,
) -> None:
    """CLI wrapper for git-wheresat."""
    raise SystemExit(wheresat.run_git_wheresat(options))


def git_wheresat() -> None:
    """Console entrypoint for git-wheresat.

    A failure to parse the arguments is intercepted rather than left to
    Cyclopts, which reports one as a panel on standard error and exits ``1``.
    That status is the one this command reserves for a boundary the evidence
    refused, and it is not a status a parser established: the report that names
    what refused exists for a run that reached the evidence, and a refused
    argument produces none. Every parse failure is therefore the usage status,
    as the status table says of anything that stopped the command from running.
    A run that asked for ``--json`` is given the error envelope, alone on
    standard output; a run that did not is given Cyclopts' own diagnostic on
    standard error, which is the panel every console script here reports a
    refused argument with.

    Examples
    --------
    ::

        $ git-wheresat --json --limit 30

    """
    _wheresat_main(tuple(sys.argv[1:]))


def _wheresat_main(tokens: cabc.Sequence[str]) -> None:
    """Run the command over ``tokens``, keeping the envelope on every status.

    The parser is asked to report its refusals rather than to act on them, in
    both modes, so that one place decides the status: a panel on standard error
    is what a caller that asked for no envelope reads, and the envelope is what
    the caller that asked for one reads, while the status is the usage status
    either way.

    Parameters
    ----------
    tokens : cabc.Sequence[str]
        The arguments to parse, as the process received them.

    Raises
    ------
    SystemExit
        With the run's own status, or with the usage status when an argument
        was refused.

    """
    asked = _asks_for_json(tokens)
    try:
        _wheresat_app(tokens=tokens, exit_on_error=False, print_error=not asked)
    except CycloptsError as exc:
        if asked:
            # The parser's own sentence names the argument it refused, and prose
            # is what the envelope's ``error`` key holds on every other path too.
            sys.stdout.write(
                wheresat_report.render_error_json(wheresat_records.EXIT_USAGE, str(exc))
            )
        raise SystemExit(wheresat_records.EXIT_USAGE) from exc


_template_app = App(
    name="git donkey-template",
    help=(
        "Display and create the template directory for the current repository. "
        "Template files placed in this directory are automatically copied to new "
        "worktrees created by git-donkey."
    ),
)


@_template_app.default
def _template_cli() -> None:
    """CLI wrapper for git-donkey-template."""
    raise SystemExit(template_cmd.run_git_donkey_template())


def git_donkey_template() -> None:
    """Console entrypoint for git-donkey-template."""
    _template_app()
