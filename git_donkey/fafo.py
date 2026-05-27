"""git-fafo workflow helpers.

This module provides the git-fafo workflow, handling authentication, GitHub
repository creation, template scaffolding, and initial Git setup for a new
project.

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
>>> fafo.run_git_fafo("demo-repo", "python")
0

"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import subprocess  # noqa: S404
import sys
import typing as typ
from pathlib import Path

import github3
import loctocat
from github3 import exceptions as github3_exceptions
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from git_donkey import helpers

_GIT_FAFO_PREFIX = "git-fafo"
_GITHUB_TOKEN_SCOPES = ["user", "repo"]
_GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
_GITHUB_DEVICE_ACCESS_URL = "https://github.com/login/oauth/access_token"
_GIT_DONKEY_CLIENT_ID_ENV = "GIT_DONKEY_GITHUB_CLIENT_ID"
_DEFAULT_GITHUB_CLIENT_ID = "Ov23liD2cKOAh7xmpXKR"


@dataclasses.dataclass(frozen=True)
class _RemoteRepository:
    """GitHub repository creation result."""

    owner: str
    already_exists: bool = False


@dataclasses.dataclass(frozen=True)
class _FafoRequest:
    """Validated git-fafo workflow request."""

    token: str
    repo_name: str
    language: str | None
    trust: bool
    yes: bool


class _GitCommand(typ.Protocol):
    """Minimal plumbum command protocol used by Git helpers."""

    def __getitem__(self, args: object) -> _GitCommand:
        """Return a command with additional arguments bound."""
        ...

    def __call__(self) -> str:
        """Run the command and return stdout."""
        ...


def _credentials_path() -> Path:
    raw = os.environ.get("GIT_DONKEY_CREDENTIALS_FILE")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.config/git-donkey/github-token").expanduser()


def _read_token_from_file(path: Path) -> str | None:
    if not path.exists():
        return None

    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None

    if not lines:
        return None

    token = lines[0].strip()
    return token or None


def _write_token_file(path: Path, token: str, auth_id: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{token}\n"
    if auth_id is not None:
        payload += f"{auth_id}\n"
    path.write_text(payload)
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _ensure_interactive() -> None:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return
    helpers._die(
        _GIT_FAFO_PREFIX,
        "missing GitHub token and no interactive terminal available; set "
        "GITHUB_TOKEN, GH_TOKEN, or GIT_DONKEY_CREDENTIALS_FILE",
        1,
    )


def _device_flow_token(credentials_path: Path) -> str:
    _ensure_interactive()
    client_id = os.environ.get(_GIT_DONKEY_CLIENT_ID_ENV) or _DEFAULT_GITHUB_CLIENT_ID

    authenticator = loctocat.Authenticator(
        client_id=client_id,
        auth_url=_GITHUB_DEVICE_CODE_URL,
        token_url=_GITHUB_DEVICE_ACCESS_URL,
        scopes=_GITHUB_TOKEN_SCOPES,
    )
    auth_info = authenticator.ping()
    helpers._eprint("Complete device authorisation to continue:")
    helpers._eprint(f"  URL: {auth_info.verification_uri}")
    helpers._eprint(f"  Code: {auth_info.user_code}")

    token = authenticator.poll()
    if not token:
        helpers._die(_GIT_FAFO_PREFIX, "failed to create GitHub device token", 1)

    _write_token_file(credentials_path, token, None)
    return token


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    credentials_path = _credentials_path()
    stored_token = _read_token_from_file(credentials_path)
    if stored_token:
        return stored_token

    return _device_flow_token(credentials_path)


def _create_remote_repository(*, token: str, repo_name: str) -> _RemoteRepository:
    github = github3.login(token=token)
    if github is None:
        helpers._die(_GIT_FAFO_PREFIX, "GitHub authentication failed", 1)

    user = github.me()
    if user is None or not getattr(user, "login", None):
        helpers._die(_GIT_FAFO_PREFIX, "could not determine GitHub username", 1)

    try:
        github.create_repository(repo_name, private=False)
    except github3_exceptions.UnprocessableEntity as exc:
        if _is_repo_already_exists_error(exc):
            return _RemoteRepository(owner=str(user.login), already_exists=True)
        helpers._die(
            _GIT_FAFO_PREFIX,
            f"GitHub repository creation failed: {exc}",
            1,
        )
    return _RemoteRepository(owner=str(user.login))


def _confirm_adopt_existing_repository(
    *,
    owner: str,
    repo_name: str,
    yes: bool,
) -> None:
    if yes:
        return

    if helpers._prompt_yes_no(
        "GitHub repository "
        f"'{owner}/{repo_name}' already exists. Adopt it if it has no commits "
        "or only an empty initial commit?",
        default=False,
    ):
        return

    helpers._die_conflict(
        _GIT_FAFO_PREFIX,
        f"GitHub repository '{owner}/{repo_name}' already exists. Pass --yes to "
        "adopt it when it has no commits or only an empty initial commit.",
        1,
    )


def _error_message_contains(
    error: github3_exceptions.GitHubError,
    needle: str,
) -> bool:
    """Return whether the GitHub error message contains the needle."""
    message = str(getattr(error, "message", "") or "").lower()
    return needle in message


def _detail_mentions_existing(detail: object) -> bool:
    """Return True when an error detail indicates a duplicate repository."""
    if isinstance(detail, dict):
        detail_map = typ.cast("dict[str, object]", detail)
        detail_message = str(detail_map.get("message") or "").lower()
        detail_code = str(detail_map.get("code") or "").lower()
        return "already exists" in detail_message or detail_code == "already_exists"
    return "already exists" in str(detail).lower()


def _is_repo_already_exists_error(
    error: github3_exceptions.GitHubError,
) -> bool:
    if _error_message_contains(error, "already exists"):
        return True

    details = getattr(error, "errors", None) or []
    return any(_detail_mentions_existing(detail) for detail in details)


def _copier_copy_command(
    *,
    copier_path: str,
    template: str,
    repo_name: str,
    trust: bool,
) -> list[str]:
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
    copier_path = shutil.which("copier") or "copier"
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
        subprocess.run(  # noqa: S603
            cmd,
            check=True,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.CalledProcessError as exc:
        raise ProcessExecutionError(cmd, exc.returncode, "", "") from exc


def _template_for_language(*, owner: str, language: str | None) -> str | None:
    if language is None:
        return None
    return f"git@github.com:{owner}/agent-template-{language}"


def _remote_repository_url(*, owner: str, repo_name: str) -> str:
    return f"git@github.com:{owner}/{repo_name}"


def _scaffold_repo(*, template: str | None, repo_name: str, trust: bool) -> Path:
    repo_path = Path(repo_name)
    if template is None:
        repo_path.mkdir()
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
    git = local["git"]
    with local.cwd(repo_path):
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
        git["push", "--set-upstream", "origin", branch]()


def _prepare_new_remote(git: _GitCommand) -> str:
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
    branch = _remote_default_branch(git)
    if branch is None:
        return _prepare_new_remote(git)

    git["fetch", "origin", branch]()
    remote_ref = f"origin/{branch}"
    if not _is_empty_initial_commit(git, remote_ref):
        helpers._die_conflict(
            _GIT_FAFO_PREFIX,
            f"GitHub repository '{owner}/{repo_name}' already exists and is not "
            "empty. Pick a new name or delete the existing repository before "
            "running git-fafo again.",
            1,
        )

    git["checkout", "-B", branch, remote_ref]()
    return branch


def _remote_default_branch(git: _GitCommand) -> str | None:
    output = git["ls-remote", "--symref", "origin", "HEAD"]()
    for line in output.splitlines():
        if not line.startswith("ref: "):
            continue
        ref = line.split()[1]
        return ref.removeprefix("refs/heads/")
    return None


def _is_empty_initial_commit(git: _GitCommand, ref: str) -> bool:
    if int(git["rev-list", "--count", ref]().strip()) != 1:
        return False

    try:
        git["diff-tree", "--quiet", "--exit-code", "--root", ref]()
    except ProcessExecutionError:
        return False
    return True


def _run_fafo_commands(request: _FafoRequest) -> None:
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

    try:
        with local.env(**env_overrides):
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
    trust: bool = False,
    yes: bool = False,
) -> int:
    """Run the git-fafo workflow.

    Parameters
    ----------
    repo_name : str
        Name for the new repository (alphanumeric, _, -, . only).
    language : str | None
        Optional programming language for the project (alphanumeric, _, -, .
        only). When omitted, create an empty project without Copier.
    trust : bool
        Pass Copier's ``--trust`` flag so templates with trusted tasks can run.
    yes : bool
        Adopt an existing empty GitHub repository without prompting.

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

    token = _github_token()
    _run_fafo_commands(
        _FafoRequest(
            token=token,
            repo_name=repo_name,
            language=language,
            trust=trust,
            yes=yes,
        )
    )
    return 0
