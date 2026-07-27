"""Main orchestration for the ``git-fafo`` workflow.

This module keeps the public ``run_git_fafo`` API and the local scaffolding
steps. GitHub authentication and repository creation live in
``git_donkey.fafo_github``; existing-remote classification and adoption checks
live in ``git_donkey.fafo_adoption``. The split keeps each module focused while
preserving the historical private helpers imported by the test suite.

Key capabilities:

- OAuth device flow and token-based authentication
- GitHub repository creation via github3.py
- Project scaffolding with copier templates
- Git initialisation, commits, and upstream push

Notes
-----
Returns standard process exit codes (0 on success, non-zero on failure).
Side effects include file and directory creation, GitHub API calls, and Git
commands that initialise, commit, and push a repository.

Examples
--------
>>> from git_donkey import fafo
>>> fafo.run_git_fafo("demo-repo", "python", token="...")
0

"""

from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # drives Copier as the intended process boundary
import sys
from pathlib import Path

from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from git_donkey import helpers
from git_donkey.fafo_adoption import (
    _assert_remote_adoptable as _assert_remote_adoptable_for_url,
)
from git_donkey.fafo_adoption import (
    _classify_remote,
    _die_existing_not_empty,
    _GitCommand,
    _heads_and_tags_from_ls_remote,
    _is_empty_initial_commit,
    _remote_heads_and_tags,
    _RemoteState,
)
from git_donkey.fafo_github import (
    _DEFAULT_GITHUB_CLIENT_ID,
    _GIT_DONKEY_CLIENT_ID_ENV,
    _confirm_adopt_existing_repository,
    _create_remote_repository,
    _credentials_path,
    _detail_mentions_existing,
    _device_flow_token,
    _ensure_interactive,
    _error_message_contains,
    _github_token,
    _is_repo_already_exists_error,
    _read_token_from_file,
    _RemoteRepository,
    _write_token_file,
    github3,
    loctocat,
)

_GIT_FAFO_PREFIX = "git-fafo"
_LOGGER = logging.getLogger(__name__)

__all__ = [
    "_DEFAULT_GITHUB_CLIENT_ID",
    "_GIT_DONKEY_CLIENT_ID_ENV",
    "_GitCommand",
    "_RemoteRepository",
    "_RemoteState",
    "_classify_remote",
    "_confirm_adopt_existing_repository",
    "_create_remote_repository",
    "_credentials_path",
    "_detail_mentions_existing",
    "_device_flow_token",
    "_die_existing_not_empty",
    "_ensure_interactive",
    "_error_message_contains",
    "_github_token",
    "_heads_and_tags_from_ls_remote",
    "_is_empty_initial_commit",
    "_is_repo_already_exists_error",
    "_read_token_from_file",
    "_remote_heads_and_tags",
    "_write_token_file",
    "github3",
    "loctocat",
    "run_git_fafo",
]


@dataclasses.dataclass(frozen=True, slots=True)
class _FafoRequest:
    """Validated git-fafo workflow request."""

    token: str
    repo_name: str
    language: str | None
    trust: bool
    yes: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _FafoOptions:
    """User-selected git-fafo workflow options."""

    trust: bool = False
    yes: bool = False


_DEFAULT_FAFO_OPTIONS = _FafoOptions()


def _copier_copy_command(
    *,
    copier_path: str,
    template: str,
    repo_name: str,
    trust: bool,
) -> list[str]:
    """Build the interactive Copier command for a repository scaffold."""
    cmd = [copier_path, "copy"]
    if trust:
        cmd.append("--trust")
    cmd.extend([template, repo_name])
    return cmd


def _run_copier_interactive(
    *,
    template: str,
    repo_name: str,
    trust: bool,
) -> None:
    """Run Copier interactively for a template-backed repository scaffold."""
    copier_path = shutil.which("copier") or "copier"
    if trust:
        _LOGGER.info(
            "Running Copier with trusted template tasks enabled",
            extra={"template": template, "repo_name": repo_name},
        )
    cmd = _copier_copy_command(
        copier_path=copier_path,
        template=template,
        repo_name=repo_name,
        trust=trust,
    )
    stdin = helpers._stream_or_none(sys.stdin)
    stdout = helpers._stream_or_none(sys.stdout)
    stderr = helpers._stream_or_none(sys.stderr)
    try:
        # nosemgrep
        # Args are slug-validated and shell=False.
        subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
            cmd,
            check=True,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.CalledProcessError as exc:
        raise ProcessExecutionError(cmd, exc.returncode, "", "") from exc


def _template_for_language(*, owner: str, language: str | None) -> str | None:
    """Return the owner-scoped template URL for ``language`` if supplied."""
    if language is None:
        _LOGGER.info("No language supplied; using empty scaffold")
        return None
    return f"git@github.com:{owner}/agent-template-{language}"


def _remote_repository_url(*, owner: str, repo_name: str) -> str:
    """Return the SSH URL for the GitHub repository."""
    return f"git@github.com:{owner}/{repo_name}"


def _scaffold_repo(*, template: str | None, repo_name: str, trust: bool) -> Path:
    """Create the local repository scaffold from Copier or an empty directory."""
    repo_path = Path(repo_name)
    if template is None:
        try:
            repo_path.mkdir()
        except FileExistsError:
            helpers._die_conflict(
                _GIT_FAFO_PREFIX,
                f"repository path '{repo_name}' already exists",
                1,
            )
        return repo_path

    _run_copier_interactive(template=template, repo_name=repo_name, trust=trust)
    return repo_path


def _initialise_and_push_git_repo(
    *,
    repo_path: Path,
    owner: str,
    repo_name: str,
    adopt_existing: bool,
) -> None:
    """Initialize the local Git repository and push it to GitHub."""
    git = local["git"]
    with local.cwd(repo_path):
        _LOGGER.info(
            "Initializing local git repository",
            extra={"operation": "git_init", "repo_name": repo_name},
        )
        git["init"]()
        git[
            "remote",
            "add",
            "origin",
            _remote_repository_url(
                owner=owner,
                repo_name=repo_name,
            ),
        ]()
        branch = (
            _prepare_adopted_remote(git, owner=owner, repo_name=repo_name)
            if adopt_existing
            else _prepare_new_remote(git)
        )
        git["add", "."]()
        if git["status", "--porcelain"]().strip():
            git["commit", "-m", "Add repo skeleton"]()
        _LOGGER.info(
            "Pushing scaffolded repository",
            extra={
                "operation": "git_push",
                "repo_name": repo_name,
                "owner": owner,
                "branch": branch,
                "adopt_existing": adopt_existing,
            },
        )
        git["push", "--set-upstream", "origin", branch]()


def _prepare_new_remote(git: _GitCommand) -> str:
    """Create the default branch and empty initial commit for a new remote."""
    branch = "main"
    git["branch", "-m", branch]()
    git["commit", "-m", "Initial commit", "--allow-empty"]()
    return branch


def _prepare_adopted_remote(
    git: _GitCommand,
    *,
    owner: str,
    repo_name: str,
) -> str:
    """Prepare local Git state for an already-existing adoptable remote."""
    state = _classify_remote(git)
    if not state.adoptable:
        _die_existing_not_empty(owner=owner, repo_name=repo_name)
    if state.branch is None:
        # The remote exists but holds no refs at all; treat it like a fresh
        # remote and seed it with an empty initial commit.
        return _prepare_new_remote(git)

    # `_classify_remote` already fetched `origin/<branch>` to confirm the
    # commit is empty, so the objects are present for the checkout.
    git["checkout", "-B", state.branch, f"origin/{state.branch}"]()
    return state.branch


def _assert_remote_adoptable(*, owner: str, repo_name: str) -> None:
    """Reject a non-empty existing remote before any local scaffolding."""
    _assert_remote_adoptable_for_url(
        owner=owner,
        repo_name=repo_name,
        remote_url=_remote_repository_url(owner=owner, repo_name=repo_name),
    )


def _run_fafo_commands(request: _FafoRequest) -> None:
    """Run the side-effecting GitHub, scaffold, Git init, and push steps."""
    remote = _create_remote_repository(token=request.token, repo_name=request.repo_name)
    if remote.already_exists:
        _confirm_adopt_existing_repository(
            owner=remote.owner,
            repo_name=request.repo_name,
            yes=request.yes,
        )
    template = _template_for_language(owner=remote.owner, language=request.language)

    env_overrides: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    stub_log = os.environ.get("STUB_LOG")
    if stub_log is not None:
        env_overrides["STUB_LOG"] = stub_log
    # plumbum snapshots os.environ at import time, so values set later by
    # pytest's monkeypatch (and by CI runners with no global git config)
    # don't reach git subprocesses unless we forward them explicitly.
    for git_var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        git_val = os.environ.get(git_var)
        if git_val is not None:
            env_overrides[git_var] = git_val

    try:
        with local.env(**env_overrides):
            if remote.already_exists:
                # Validate the remote is adoptable before scaffolding so a
                # rejected adoption leaves no local directory behind.
                _assert_remote_adoptable(
                    owner=remote.owner,
                    repo_name=request.repo_name,
                )
            repo_path = _scaffold_repo(
                template=template,
                repo_name=request.repo_name,
                trust=request.trust,
            )
            _initialise_and_push_git_repo(
                repo_path=repo_path,
                owner=remote.owner,
                repo_name=request.repo_name,
                adopt_existing=remote.already_exists,
            )
    except ProcessExecutionError as exc:
        helpers._die(_GIT_FAFO_PREFIX, f"command failed: {exc}", 1)


def run_git_fafo(
    repo_name: str,
    language: str | None = None,
    *,
    token: str,
    options: _FafoOptions = _DEFAULT_FAFO_OPTIONS,
) -> int:
    """Run the git-fafo workflow.

    Parameters
    ----------
    repo_name : str
        Name for the new repository (alphanumeric, _, -, . only).
    language : str | None
        Optional programming language for the project (alphanumeric, _, -, .
        only). When omitted, create an empty project without Copier.
    token : str
        GitHub token supplied by the caller or CLI adapter.
    options : _FafoOptions
        User-selected scaffold trust and adoption confirmation options.

    Returns
    -------
    int
        The desired process exit code.

    """
    helpers._require_command("git", _GIT_FAFO_PREFIX)
    if language is not None:
        helpers._require_command("copier", _GIT_FAFO_PREFIX)

    repo_name = helpers.validate_slug(
        repo_name, label="repo name", prefix=_GIT_FAFO_PREFIX
    )
    if language is not None:
        language = helpers.validate_slug(
            language, label="language", prefix=_GIT_FAFO_PREFIX
        )

    if Path(repo_name).exists():
        helpers._eprint("You did that one already!")
        return 1

    _LOGGER.info("Starting git-fafo workflow", extra={"repo_name": repo_name})
    _run_fafo_commands(
        _FafoRequest(
            token=token,
            repo_name=repo_name,
            language=language,
            trust=options.trust,
            yes=options.yes,
        )
    )
    _LOGGER.info(
        "Completed git-fafo workflow",
        extra={"repo_name": repo_name, "status": "success"},
    )
    return 0
