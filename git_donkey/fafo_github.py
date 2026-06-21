"""GitHub authentication and repository creation for ``git-fafo``.

This module owns the GitHub-facing half of the workflow used by
``git_donkey.fafo``. It keeps token discovery, OAuth device-flow fallback,
duplicate-repository detection, and adoption confirmation together so the
orchestrator can treat repository preparation as a single dependency.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
import tempfile
import typing as typ
from pathlib import Path

import github3
import loctocat
from github3 import exceptions as github3_exceptions

from git_donkey import helpers

_GIT_FAFO_PREFIX = "git-fafo"
_GITHUB_TOKEN_SCOPES = ["user", "repo"]
_GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
_GITHUB_DEVICE_ACCESS_URL = "https://github.com/login/oauth/access_token"
_GIT_DONKEY_CLIENT_ID_ENV = "GIT_DONKEY_GITHUB_CLIENT_ID"
_DEFAULT_GITHUB_CLIENT_ID = "Ov23liD2cKOAh7xmpXKR"
_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class _RemoteRepository:
    """GitHub repository creation result."""

    owner: str
    already_exists: bool = False


def _credentials_path() -> Path:
    """Return the configured path for the cached GitHub token."""
    raw = os.environ.get("GIT_DONKEY_CREDENTIALS_FILE")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.config/git-donkey/github-token").expanduser()


def _read_token_from_file(path: Path) -> str | None:
    """Read the first non-empty token line from ``path`` if available."""
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
    """Persist a GitHub token and optional authorization id."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{token}\n"
    if auth_id is not None:
        payload += f"{auth_id}\n"
    temporary_handle, temporary_name = tempfile.mkstemp(dir=path.parent)
    os.close(temporary_handle)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    with open(
        temporary_path,
        "w",
        encoding="utf-8",
        opener=lambda p, f: os.open(p, f | os.O_EXCL, 0o600),
    ) as fh:
        fh.write(payload)
    temporary_path.replace(path)


def _ensure_interactive() -> None:
    """Fail when OAuth fallback needs a terminal but none is attached."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        return
    helpers._die(
        _GIT_FAFO_PREFIX,
        "missing GitHub token and no interactive terminal available; set "
        "GITHUB_TOKEN, GH_TOKEN, or GIT_DONKEY_CREDENTIALS_FILE",
        1,
    )


def _device_flow_token(credentials_path: Path) -> str:
    """Create and cache a GitHub token using OAuth device authorization."""
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
    _LOGGER.info(
        "Acquired GitHub token using device flow",
        extra={"token_source": "device_flow"},
    )
    return token


def _github_token() -> str:
    """Return a GitHub token from env, cache, or interactive device flow."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        _LOGGER.info(
            "Using GitHub token from environment",
            extra={"token_source": "environment"},
        )
        return token

    credentials_path = _credentials_path()
    stored_token = _read_token_from_file(credentials_path)
    if stored_token:
        _LOGGER.info(
            "Using cached GitHub token",
            extra={"token_source": "cache"},
        )
        return stored_token

    _LOGGER.info(
        "Starting GitHub device flow token acquisition",
        extra={"token_source": "device_flow"},
    )
    return _device_flow_token(credentials_path)


def _create_remote_repository(*, token: str, repo_name: str) -> _RemoteRepository:
    """Create the GitHub repository or report that it already exists."""
    _LOGGER.info(
        "Creating GitHub repository",
        extra={"operation": "github_create_repository", "repo_name": repo_name},
    )
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
            _LOGGER.info(
                "GitHub repository already exists",
                extra={
                    "operation": "github_create_repository",
                    "repo_name": repo_name,
                    "owner": str(user.login),
                    "result": "already_exists",
                },
            )
            return _RemoteRepository(owner=str(user.login), already_exists=True)
        helpers._die(
            _GIT_FAFO_PREFIX,
            f"GitHub repository creation failed: {exc}",
            1,
        )
    _LOGGER.info(
        "Created GitHub repository",
        extra={
            "operation": "github_create_repository",
            "repo_name": repo_name,
            "owner": str(user.login),
            "result": "created",
        },
    )
    return _RemoteRepository(owner=str(user.login))


def _confirm_adopt_existing_repository(
    *,
    owner: str,
    repo_name: str,
    yes: bool,
) -> None:
    """Require explicit confirmation before adopting an existing repository."""
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
    """Return whether a GitHub API error means the repository already exists."""
    if _error_message_contains(error, "already exists"):
        return True

    details = getattr(error, "errors", None) or []
    return any(_detail_mentions_existing(detail) for detail in details)
