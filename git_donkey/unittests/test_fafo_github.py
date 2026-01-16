"""Unit tests for the github3 integration in git-fafo."""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from github3 import exceptions as github3_exceptions

from git_donkey import cli

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_token_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv(cli._GIT_DONKEY_CLIENT_ID_ENV, raising=False)
    monkeypatch.setenv(
        "GIT_DONKEY_CREDENTIALS_FILE",
        str(tmp_path / "missing-token"),
    )


def test_github_token_prefers_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefer GITHUB_TOKEN when both are present."""
    monkeypatch.setenv("GITHUB_TOKEN", "primary")
    monkeypatch.setenv("GH_TOKEN", "secondary")

    assert cli._github_token() == "primary"


def test_github_token_falls_back_to_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback to GH_TOKEN when GITHUB_TOKEN is missing."""
    monkeypatch.setenv("GH_TOKEN", "fallback")

    assert cli._github_token() == "fallback"


def test_github_token_reads_from_credentials_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read token from the configured credentials file when present."""
    token_path = tmp_path / "token.txt"
    token_path.write_text("stored-token\n123\n")
    monkeypatch.setenv("GIT_DONKEY_CREDENTIALS_FILE", str(token_path))

    assert cli._github_token() == "stored-token"


def test_github_token_requires_interactive_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise a SystemExit when prompting is not possible."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)

    with pytest.raises(SystemExit) as excinfo:
        cli._github_token()

    assert excinfo.value.code == 1


def test_github_token_uses_default_client_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the built-in client ID when none is provided."""
    token_path = tmp_path / "token.txt"
    monkeypatch.setenv("GIT_DONKEY_CREDENTIALS_FILE", str(token_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)

    created: dict[str, object] = {}

    @dataclasses.dataclass
    class _StubAuthInfo:
        device_code: str
        user_code: str
        verification_uri: str
        expires_in: int
        interval: int

    @dataclasses.dataclass
    class _StubAuthenticator:
        client_id: str
        auth_url: str
        token_url: str
        scopes: list[str]
        created: dict[str, object]

        def ping(self) -> _StubAuthInfo:
            self.created["pinged"] = True
            return _StubAuthInfo(
                device_code="device",
                user_code="USER-CODE",
                verification_uri="https://example.com/device",
                expires_in=900,
                interval=5,
            )

        def poll(self) -> str:
            self.created["polled"] = True
            return "default-token"

    def _fake_authenticator(
        *,
        client_id: str,
        auth_url: str,
        token_url: str,
        scopes: list[str],
    ) -> _StubAuthenticator:
        created["client_id"] = client_id
        created["auth_url"] = auth_url
        created["token_url"] = token_url
        created["scopes"] = scopes
        return _StubAuthenticator(client_id, auth_url, token_url, scopes, created)

    monkeypatch.setattr(cli.loctocat, "Authenticator", _fake_authenticator)

    token = cli._github_token()

    assert token == "default-token"  # noqa: S105
    assert created["client_id"] == cli._DEFAULT_GITHUB_CLIENT_ID


def test_github_token_authorizes_and_persists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompt, authorize, and persist a new token when none is available."""
    token_path = tmp_path / "token.txt"
    monkeypatch.setenv("GIT_DONKEY_CREDENTIALS_FILE", str(token_path))
    monkeypatch.setenv(cli._GIT_DONKEY_CLIENT_ID_ENV, "client-id")

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)

    created: dict[str, object] = {}

    @dataclasses.dataclass
    class _StubAuthInfo:
        device_code: str
        user_code: str
        verification_uri: str
        expires_in: int
        interval: int

    @dataclasses.dataclass
    class _StubAuthenticator:
        client_id: str
        auth_url: str
        token_url: str
        scopes: list[str]
        created: dict[str, object]

        def ping(self) -> _StubAuthInfo:
            self.created["pinged"] = True
            return _StubAuthInfo(
                device_code="device",
                user_code="USER-CODE",
                verification_uri="https://example.com/device",
                expires_in=900,
                interval=5,
            )

        def poll(self) -> str:
            self.created["polled"] = True
            return "created-token"

    def _fake_authenticator(
        *,
        client_id: str,
        auth_url: str,
        token_url: str,
        scopes: list[str],
    ) -> _StubAuthenticator:
        created["client_id"] = client_id
        created["auth_url"] = auth_url
        created["token_url"] = token_url
        created["scopes"] = scopes
        return _StubAuthenticator(client_id, auth_url, token_url, scopes, created)

    monkeypatch.setattr(cli.loctocat, "Authenticator", _fake_authenticator)

    token = cli._github_token()

    assert token == "created-token"  # noqa: S105
    assert created["scopes"] == ["user", "repo"]
    assert created["client_id"] == "client-id"
    assert created["pinged"] is True
    assert created["polled"] is True
    assert token_path.read_text().splitlines() == ["created-token"]


def test_create_remote_repository_uses_github3_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating the repo should call github3 login and create_repository."""
    created: dict[str, str | bool] = {}

    @dataclasses.dataclass
    class _StubUser:
        login: str

    @dataclasses.dataclass
    class _StubGitHub:
        login: str
        created: dict[str, str | bool]

        def me(self) -> _StubUser:
            return _StubUser(self.login)

        def create_repository(self, name: str, *, private: bool = False) -> None:
            self.created["name"] = name
            self.created["private"] = private

    def _fake_login(*, token: str) -> _StubGitHub:
        created["token"] = token
        return _StubGitHub("octocat", created)

    monkeypatch.setattr(cli.github3, "login", _fake_login)

    auth_value = "token-123"
    owner = cli._create_remote_repository(token=auth_value, repo_name="demo")

    assert owner == "octocat"
    assert created["token"] == auth_value
    assert created["name"] == "demo"
    assert created["private"] is False


def test_create_remote_repository_reports_existing_repo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    class _StubUser:
        login: str

    @dataclasses.dataclass
    class _StubGitHub:
        login: str

        def me(self) -> _StubUser:
            return _StubUser(self.login)

        @staticmethod
        def create_repository(name: str, *, private: bool = False) -> None:
            raise github3_exceptions.UnprocessableEntity(_StubResponse())

    def _fake_login(*, token: str) -> _StubGitHub:
        return _StubGitHub("octocat")

    monkeypatch.setattr(cli.github3, "login", _fake_login)

    auth_value = "example-value"
    with pytest.raises(SystemExit) as excinfo:
        cli._create_remote_repository(token=auth_value, repo_name="demo-repo")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "already exists" in err.lower()
