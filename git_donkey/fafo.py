"""git-fafo workflow helpers."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess  # noqa: S404
import sys
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


def _credentials_path() -> Path:
    raw = os.environ.get("GIT_DONKEY_CREDENTIALS_FILE")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.config/git-donkey/github-token").expanduser()


def _read_token_from_file(path: Path) -> str | None:
    if not path.exists():
        return None

    try:
        token = path.read_text().splitlines()[0].strip()
    except OSError:
        return None

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


def _create_remote_repository(*, token: str, repo_name: str) -> str:
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
            helpers._die_conflict(
                _GIT_FAFO_PREFIX,
                f"GitHub repository '{user.login}/{repo_name}' already exists. "
                "Pick a new name or delete the existing repository before "
                "running git-fafo again.",
                1,
            )
        helpers._die(
            _GIT_FAFO_PREFIX,
            f"GitHub repository creation failed: {exc}",
            1,
        )
    return str(user.login)


def _is_repo_already_exists_error(
    error: github3_exceptions.GitHubError,
) -> bool:
    message = str(getattr(error, "message", "") or "").lower()
    if "already exists" in message:
        return True

    for detail in getattr(error, "errors", []):
        if isinstance(detail, dict):
            detail_message = str(detail.get("message", "")).lower()
            detail_code = str(detail.get("code", "")).lower()
            if "already exists" in detail_message or detail_code == "already_exists":
                return True
        elif "already exists" in str(detail).lower():
            return True

    return False


def _run_copier_interactive(*, template: str, repo_name: str) -> None:
    copier_path = shutil.which("copier") or "copier"
    cmd = [copier_path, "copy", template, repo_name]
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


def _scaffold_repo(*, template: str, repo_name: str) -> Path:
    _run_copier_interactive(template=template, repo_name=repo_name)
    return Path(repo_name)


def _initialise_and_push_git_repo(
    *,
    repo_path: Path,
    owner: str,
    repo_name: str,
) -> None:
    git = local["git"]
    with local.cwd(repo_path):
        git["init"]()
        git["remote", "add", "origin", f"git@github.com:{owner}/{repo_name}"]()
        git["branch", "-m", "main"]()
        git["add", "."]()
        if git["status", "--porcelain"]().strip():
            git["commit", "-m", "Add repo skeleton"]()
        else:
            git["commit", "-m", "Initial commit", "--allow-empty"]()
        git["push", "--set-upstream", "origin", "main"]()


def _run_fafo_commands(*, token: str, repo_name: str, language: str) -> None:
    owner = _create_remote_repository(token=token, repo_name=repo_name)
    template = f"git@github.com:{owner}/agent-template-{language}"

    env_overrides: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    stub_log = os.environ.get("STUB_LOG")
    if stub_log is not None:
        env_overrides["STUB_LOG"] = stub_log

    try:
        with local.env(**env_overrides):
            repo_path = _scaffold_repo(template=template, repo_name=repo_name)
            _initialise_and_push_git_repo(
                repo_path=repo_path,
                owner=owner,
                repo_name=repo_name,
            )
    except ProcessExecutionError as exc:
        helpers._die(_GIT_FAFO_PREFIX, f"command failed: {exc}", 1)


def run_git_fafo(repo_name: str, language: str) -> int:
    """Run the git-fafo workflow.

    Parameters
    ----------
    repo_name : str
        Name for the new repository (alphanumeric, _, -, . only).
    language : str
        Programming language for the project (alphanumeric, _, -, . only).

    Returns
    -------
    int
        The desired process exit code.

    """
    helpers._require_command("git", _GIT_FAFO_PREFIX)
    helpers._require_command("copier", _GIT_FAFO_PREFIX)

    repo_name = helpers.validate_slug(
        repo_name, label="repo name", prefix=_GIT_FAFO_PREFIX
    )
    language = helpers.validate_slug(
        language, label="language", prefix=_GIT_FAFO_PREFIX
    )

    if Path(repo_name).exists():
        helpers._eprint("You did that one already!")
        return 1

    token = _github_token()
    _run_fafo_commands(token=token, repo_name=repo_name, language=language)
    return 0
