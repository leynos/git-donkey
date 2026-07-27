"""Unit tests for repository creation in git-fafo.

Covers GitHub repository creation flows and error handling.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_fafo_github -q
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from github3 import exceptions as github3_exceptions

from git_donkey import fafo

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from conftest import StubGitHub, StubUser


@dataclasses.dataclass
class _StubResponse:
    """Stub API response payload for repository creation errors."""

    status_code: int = 422

    @staticmethod
    def json() -> dict[str, object]:
        return {
            "message": "Repository creation failed.",
            "errors": [
                {
                    "resource": "Repository",
                    "field": "name",
                    "code": "custom",
                    "message": "name already exists on this account",
                }
            ],
        }


def _patch_github_login(
    monkeypatch: pytest.MonkeyPatch,
    handler: cabc.Callable[[str], object],
) -> None:
    """Patch github3.login with a handler that receives the token."""

    def _fake_login(*, token: str) -> object:
        return handler(token)

    monkeypatch.setattr(fafo.github3, "login", _fake_login)


def _create_repo_with_login(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str,
    repo_name: str,
    login_handler: cabc.Callable[[str], object],
) -> fafo._RemoteRepository:
    """Create a repository using a patched github3.login handler."""
    _patch_github_login(monkeypatch, login_handler)
    return fafo._create_remote_repository(token=token, repo_name=repo_name)


def _assert_create_repo_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    login_handler: cabc.Callable[[str], object],
    expected_message: str,
    normalize: bool = False,
) -> None:
    """Assert that repository creation fails with the expected message."""
    with pytest.raises(SystemExit) as excinfo:
        _create_repo_with_login(
            monkeypatch,
            token="example-value",  # ruff:ignore[hardcoded-password-func-arg]  FIXME: test constant, not a real secret
            repo_name="demo-repo",
            login_handler=login_handler,
        )

    assert excinfo.value.code == 1, "Expected SystemExit(1) on repo creation errors."
    err = capsys.readouterr().err
    haystack = err.lower() if normalize else err
    assert expected_message in haystack, "Expected repository creation error message."


def test_create_remote_repository_uses_github3_login(
    monkeypatch: pytest.MonkeyPatch,
    github_stubs: tuple[type[StubUser], type[StubGitHub]],
) -> None:
    """Creating the repo should call github3 login and create_repository."""
    created: dict[str, str | bool] = {}

    stub_github = github_stubs[1]

    def _login_handler(token: str) -> object:
        created["token"] = token
        return stub_github("octocat", created)

    auth_value = "token-123"
    remote = _create_repo_with_login(
        monkeypatch,
        token=auth_value,
        repo_name="demo",
        login_handler=_login_handler,
    )

    assert remote.owner == "octocat", "Expected repository owner to match stub login."
    assert not remote.already_exists, "Expected new repository metadata."
    assert created["token"] == auth_value, "Expected login token to be passed."
    assert created["name"] == "demo", (
        "Expected repository name to be passed to create_repository."
    )
    assert created["private"] is False, "Expected repository to be public by default."


def test_create_remote_repository_auth_failure_exits_with_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Auth failures should exit with a clear error message."""

    def _login_handler(token: str) -> None:
        return None

    _assert_create_repo_failure(
        monkeypatch,
        capsys,
        login_handler=_login_handler,
        expected_message="GitHub authentication failed",
    )


def test_create_remote_repository_missing_user_login_exits_with_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing user info should exit with a clear error message."""

    class _StubGitHub:
        @staticmethod
        def me() -> None:
            return None

    def _login_handler(token: str) -> _StubGitHub:
        return _StubGitHub()

    _assert_create_repo_failure(
        monkeypatch,
        capsys,
        login_handler=_login_handler,
        expected_message="could not determine GitHub username",
    )


def test_create_remote_repository_returns_existing_repo(
    monkeypatch: pytest.MonkeyPatch,
    github_stubs: tuple[type[StubUser], type[StubGitHub]],
) -> None:
    """Existing repositories should be returned for adoption handling."""

    @dataclasses.dataclass
    class _StubGitHub:
        login: str

        def me(self) -> object:
            return github_stubs[0](self.login)

        @staticmethod
        def create_repository(name: str, *, private: bool = False) -> typ.NoReturn:
            raise github3_exceptions.UnprocessableEntity(_StubResponse())

    def _login_handler(token: str) -> _StubGitHub:
        return _StubGitHub("octocat")

    remote = _create_repo_with_login(
        monkeypatch,
        token="example-value",  # ruff:ignore[hardcoded-password-func-arg]  FIXME: test constant, not a real secret
        repo_name="demo-repo",
        login_handler=_login_handler,
    )

    assert remote.owner == "octocat", "remote owner should match the login handler user"
    assert remote.already_exists, (
        "existing repository should be flagged as pre-existing"
    )


def test_confirm_adopt_existing_repository_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing repository adoption should prompt unless --yes was supplied."""
    recorded: dict[str, object] = {}

    def _fake_prompt(question: str, *, default: bool = False) -> bool:
        recorded["question"] = question
        recorded["default"] = default
        return True

    monkeypatch.setattr(fafo.helpers, "_prompt_yes_no", _fake_prompt)

    fafo._confirm_adopt_existing_repository(
        owner="octocat",
        repo_name="demo-repo",
        yes=False,
    )

    assert "octocat/demo-repo" in str(recorded["question"]), (
        "adoption prompt should name the owner/repo being adopted"
    )
    assert recorded["default"] is False, "adoption prompt should default to declining"


def test_confirm_adopt_existing_repository_uses_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The --yes option should skip the adoption prompt."""

    def _fail_prompt(*_: object, **__: object) -> typ.NoReturn:
        msg = "prompt should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr(fafo.helpers, "_prompt_yes_no", _fail_prompt)

    fafo._confirm_adopt_existing_repository(
        owner="octocat",
        repo_name="demo-repo",
        yes=True,
    )
