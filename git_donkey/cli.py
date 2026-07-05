"""Command-line interfaces for the git-donkey tools.

Provides CLI entrypoints for the git-donkey workflow tools. This module is the
console-script boundary: it owns Cyclopts argument parsing and delegates all
workflow behaviour to modules such as ``donkey``, ``track``, ``fafo``,
``plonk``, and ``template_cmd``.

This module exposes console scripts (git-donkey, git-track, git-fafo,
git-plonk, git-donkey-template) registered in pyproject.toml. Each entrypoint
maps to a workflow runner: git_donkey() -> donkey.run_git_donkey, git_track()
-> track.run_git_track, git_fafo() -> fafo.run_git_fafo, git_plonk() ->
plonk.run_git_plonk, and git_donkey_template() ->
template_cmd.run_git_donkey_template.

Run with::

    git-donkey --help
    git-track --help
    git-fafo --help
    git-plonk --help
    git-donkey-template --help
"""

from __future__ import annotations

import typing as typ

from cyclopts import App, Parameter

from git_donkey import donkey, fafo, plonk, template_cmd, track

_donkey_app = App(
    name="git donkey",
    help=(
        "Create a linked worktree at ../{repo}.worktrees/{branch}, branching from "
        "main (default), a specified base branch, or '.' meaning the branch "
        "currently checked out in the CWD. Uses the first remote and reuses "
        "existing local/remote branches when present."
    ),
)


@_donkey_app.default
def _donkey_cli(
    branch_name: str,
    origin_branch: str | None = None,
    *,
    no_pull: bool = False,
) -> None:
    """CLI wrapper for git-donkey."""
    raise SystemExit(
        donkey.run_git_donkey(
            branch_name,
            origin_branch,
            no_pull=no_pull,
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


def git_fafo() -> None:
    """Console entrypoint for git-fafo."""
    _fafo_app()


_plonk_app = App(
    name="git plonk",
    help=(
        "Clean up git-donkey worktrees. Default mode removes completed "
        "worktrees, --soft removes generated directories, and --hard also "
        "deletes completed local branches. --dry-run previews planned "
        "actions without mutating the filesystem or Git state."
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
