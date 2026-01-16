"""Unit tests for the github3 integration in git-fafo."""

from __future__ import annotations

import dataclasses

import pytest

from git_donkey import cli


def test_github_token_prefers_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefer GITHUB_TOKEN when both are present."""
    monkeypatch.setenv("GITHUB_TOKEN", "primary")
    monkeypatch.setenv("GH_TOKEN", "secondary")

    assert cli._github_token() == "primary"


def test_github_token_falls_back_to_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback to GH_TOKEN when GITHUB_TOKEN is missing."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "fallback")

    assert cli._github_token() == "fallback"


def test_github_token_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise a SystemExit when no token is available."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        cli._github_token()

    assert excinfo.value.code == 1


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
