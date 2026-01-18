"""Unit tests for github3 and device-flow behavior in git-fafo.

Covers token selection, device-flow success and failure, and repository
creation error handling.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from github3 import exceptions as github3_exceptions

from git_donkey import fafo

if typ.TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass
class _StubAuthInfo:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def _stub_auth_info() -> _StubAuthInfo:
    return _StubAuthInfo(
        device_code="device",
        user_code="USER-CODE",
        verification_uri="https://example.com/device",
        expires_in=900,
        interval=5,
    )


@dataclasses.dataclass
class _StubAuthenticator:
    client_id: str
    auth_url: str
    token_url: str
    scopes: list[str]
    token: str
    created: dict[str, object] | None = None

    def ping(self) -> _StubAuthInfo:
        if self.created is not None:
            self.created["pinged"] = True
        return _stub_auth_info()

    def poll(self) -> str:
        if self.created is not None:
            self.created["polled"] = True
        return self.token


def _fake_authenticator_factory(
    *,
    token: str,
    created: dict[str, object] | None = None,
) -> typ.Callable[..., _StubAuthenticator]:
    def _fake_authenticator(
        *,
        client_id: str,
        auth_url: str,
        token_url: str,
        scopes: list[str],
    ) -> _StubAuthenticator:
        if created is not None:
            created["client_id"] = client_id
            created["auth_url"] = auth_url
            created["token_url"] = token_url
            created["scopes"] = scopes
        return _StubAuthenticator(
            client_id=client_id,
            auth_url=auth_url,
            token_url=token_url,
            scopes=scopes,
            token=token,
            created=created,
        )

    return _fake_authenticator


@pytest.fixture(autouse=True)
def _clear_token_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv(fafo._GIT_DONKEY_CLIENT_ID_ENV, raising=False)
    monkeypatch.setenv(
        "GIT_DONKEY_CREDENTIALS_FILE",
        str(tmp_path / "missing-token"),
    )


def test_github_token_prefers_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefer GITHUB_TOKEN when both are present."""
    monkeypatch.setenv("GITHUB_TOKEN", "primary")
    monkeypatch.setenv("GH_TOKEN", "secondary")

    assert fafo._github_token() == "primary", (
        "GITHUB_TOKEN should take precedence over GH_TOKEN."
    )


def test_github_token_falls_back_to_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback to GH_TOKEN when GITHUB_TOKEN is missing."""
    monkeypatch.setenv("GH_TOKEN", "fallback")

    assert fafo._github_token() == "fallback", "Expected GH_TOKEN fallback value."


def test_github_token_reads_from_credentials_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read token from the configured credentials file when present."""
    token_path = tmp_path / "token.txt"
    token_path.write_text("stored-token\n123\n")
    monkeypatch.setenv("GIT_DONKEY_CREDENTIALS_FILE", str(token_path))

    assert fafo._github_token() == "stored-token", (
        "Expected token from credentials file."
    )


def test_github_token_requires_interactive_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise a SystemExit when prompting is not possible."""
    monkeypatch.setattr(fafo.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(fafo.sys.stdout, "isatty", lambda: False)

    with pytest.raises(SystemExit) as excinfo:
        fafo._github_token()

    assert excinfo.value.code == 1, (
        "Expected SystemExit(1) when prompting is not possible."
    )


def test_github_token_uses_default_client_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the built-in client ID when none is provided."""
    token_path = tmp_path / "token.txt"
    monkeypatch.setenv("GIT_DONKEY_CREDENTIALS_FILE", str(token_path))
    monkeypatch.setattr(fafo.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(fafo.sys.stdout, "isatty", lambda: True)

    created: dict[str, object] = {}

    auth_value = "default-token"
    monkeypatch.setattr(
        fafo.loctocat,
        "Authenticator",
        _fake_authenticator_factory(token=auth_value, created=created),
    )

    token = fafo._github_token()

    assert token == "default-token"  # noqa: S105  FIXME: test constant, not a real secret
    assert created["client_id"] == fafo._DEFAULT_GITHUB_CLIENT_ID, (
        "Expected default client ID for device flow."
    )


def test_github_token_device_flow_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device-flow token acquisition failure (poll returns no token)."""
    token_path = tmp_path / "device-flow-failure-token"
    monkeypatch.setenv("GIT_DONKEY_CREDENTIALS_FILE", str(token_path))
    monkeypatch.setattr(fafo.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(fafo.sys.stdout, "isatty", lambda: True)

    monkeypatch.setattr(
        fafo.loctocat,
        "Authenticator",
        _fake_authenticator_factory(token=""),
    )

    with pytest.raises(SystemExit) as excinfo:
        fafo._github_token()

    assert excinfo.value.code == 1, (
        "Expected SystemExit(1) on device-flow token failure."
    )
    assert not token_path.exists(), (
        "Token file must not be written on device-flow failure."
    )


def test_github_token_authorizes_and_persists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompt, authorize, and persist a new token when none is available."""
    token_path = tmp_path / "token.txt"
    monkeypatch.setenv("GIT_DONKEY_CREDENTIALS_FILE", str(token_path))
    monkeypatch.setenv(fafo._GIT_DONKEY_CLIENT_ID_ENV, "client-id")

    monkeypatch.setattr(fafo.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(fafo.sys.stdout, "isatty", lambda: True)

    created: dict[str, object] = {}

    auth_value = "created-token"
    monkeypatch.setattr(
        fafo.loctocat,
        "Authenticator",
        _fake_authenticator_factory(token=auth_value, created=created),
    )

    token = fafo._github_token()

    assert token == "created-token"  # noqa: S105  FIXME: test constant, not a real secret
    assert created["scopes"] == ["user", "repo"], (
        "Expected repo/user scopes for device flow."
    )
    assert created["client_id"] == "client-id", "Expected override client ID."
    assert created["pinged"] is True, "Expected device-flow ping to run."
    assert created["polled"] is True, "Expected device-flow poll to run."
    assert token_path.read_text().splitlines() == ["created-token"], (
        "Expected token persisted to credentials file."
    )


def test_create_remote_repository_uses_github3_login(
    monkeypatch: pytest.MonkeyPatch,
    github_stubs: tuple[type[typ.Any], type[typ.Any]],
) -> None:
    """Creating the repo should call github3 login and create_repository."""
    created: dict[str, str | bool] = {}

    stub_github = github_stubs[1]

    def _fake_login(*, token: str) -> object:
        created["token"] = token
        return stub_github("octocat", created)

    monkeypatch.setattr(fafo.github3, "login", _fake_login)

    auth_value = "token-123"
    owner = fafo._create_remote_repository(token=auth_value, repo_name="demo")

    assert owner == "octocat", "Expected repository owner to match stub login."
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

    def _fake_login(*, token: str) -> None:
        return None

    monkeypatch.setattr(fafo.github3, "login", _fake_login)

    auth_value = "example-value"
    with pytest.raises(SystemExit) as excinfo:
        fafo._create_remote_repository(token=auth_value, repo_name="demo-repo")

    assert excinfo.value.code == 1, "Expected SystemExit(1) on auth failure."
    err = capsys.readouterr().err
    assert "GitHub authentication failed" in err, "Expected auth failure message."


def test_create_remote_repository_missing_user_login_exits_with_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing user info should exit with a clear error message."""

    class _StubGitHub:
        @staticmethod
        def me() -> None:
            return None

    def _fake_login(*, token: str) -> _StubGitHub:
        return _StubGitHub()

    monkeypatch.setattr(fafo.github3, "login", _fake_login)

    auth_value = "example-value"
    with pytest.raises(SystemExit) as excinfo:
        fafo._create_remote_repository(token=auth_value, repo_name="demo-repo")

    assert excinfo.value.code == 1, (
        "Expected SystemExit(1) when username is unavailable."
    )
    err = capsys.readouterr().err
    assert "could not determine GitHub username" in err, (
        "Expected missing-username error message."
    )


def test_create_remote_repository_reports_existing_repo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    github_stubs: tuple[type[typ.Any], type[typ.Any]],
) -> None:
    """Provide a friendly message when the repository already exists."""

    @dataclasses.dataclass
    class _StubResponse:
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

    @dataclasses.dataclass
    class _StubGitHub:
        login: str

        def me(self) -> object:
            return github_stubs[0](self.login)

        @staticmethod
        def create_repository(name: str, *, private: bool = False) -> None:
            raise github3_exceptions.UnprocessableEntity(_StubResponse())

    def _fake_login(*, token: str) -> _StubGitHub:
        return _StubGitHub("octocat")

    monkeypatch.setattr(fafo.github3, "login", _fake_login)

    auth_value = "example-value"
    with pytest.raises(SystemExit) as excinfo:
        fafo._create_remote_repository(token=auth_value, repo_name="demo-repo")

    assert excinfo.value.code == 1, "Expected SystemExit(1) on API errors."
    err = capsys.readouterr().err
    assert "already exists" in err.lower(), "Expected repository exists message."
