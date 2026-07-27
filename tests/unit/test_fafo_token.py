"""Unit tests for git-fafo token handling.

Covers token selection, device-flow success and failure, and credential
persistence.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_fafo_token -q
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from git_donkey import fafo

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


@dataclasses.dataclass
class _StubAuthInfo:
    """Container for stub device-flow metadata."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def _stub_auth_info() -> _StubAuthInfo:
    """Build stub device-flow metadata."""
    return _StubAuthInfo(
        device_code="device",
        user_code="USER-CODE",
        verification_uri="https://example.com/device",
        expires_in=900,
        interval=5,
    )


@dataclasses.dataclass
class _StubAuthenticator:
    """Stub authenticator that records device-flow calls."""

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
) -> cabc.Callable[..., _StubAuthenticator]:
    """Return a stub authenticator factory with recorded calls."""

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
    """Reset token-related environment variables for each test."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv(fafo._GIT_DONKEY_CLIENT_ID_ENV, raising=False)
    monkeypatch.setenv(
        "GIT_DONKEY_CREDENTIALS_FILE",
        str(tmp_path / "missing-token"),
    )


@pytest.mark.parametrize(
    ("env_vars", "expected", "message"),
    [
        (
            {"GITHUB_TOKEN": "primary", "GH_TOKEN": "secondary"},
            "primary",
            "GITHUB_TOKEN should take precedence over GH_TOKEN.",
        ),
        (
            {"GH_TOKEN": "fallback"},
            "fallback",
            "Expected GH_TOKEN fallback value.",
        ),
    ],
)
def test_github_token_priority(
    monkeypatch: pytest.MonkeyPatch,
    env_vars: dict[str, str],
    expected: str,
    message: str,
) -> None:
    """Test GITHUB_TOKEN takes precedence over GH_TOKEN."""
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    assert fafo._github_token() == expected, message


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
    expected_token = "default-token"  # ruff:ignore[hardcoded-password-string]  FIXME: test constant, not a real secret

    assert token == expected_token, (
        "Expected device-flow token to match the default stub."
    )
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
    expected_token = "created-token"  # ruff:ignore[hardcoded-password-string]  FIXME: test constant, not a real secret

    assert token == expected_token, (
        "Expected device-flow token to match the stubbed value."
    )
    assert created["scopes"] == ["user", "repo"], (
        "Expected repo/user scopes for device flow."
    )
    assert created["client_id"] == "client-id", "Expected override client ID."
    assert created["pinged"] is True, "Expected device-flow ping to run."
    assert created["polled"] is True, "Expected device-flow poll to run."
    assert token_path.read_text().splitlines() == ["created-token"], (
        "Expected token persisted to credentials file."
    )
