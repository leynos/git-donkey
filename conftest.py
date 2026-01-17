"""Shared pytest fixtures for git-donkey tests.

Provides reusable stub classes and fixtures that simplify GitHub API interactions
in the test suite.

Usage
-----
Use the shared stubs fixture in tests::

    def test_example(github_stubs):
        StubUser, StubGitHub = github_stubs
        stub = StubGitHub(login="octo", created={})
        assert stub.me().login == "octo"
"""

from __future__ import annotations

import dataclasses

import pytest


@dataclasses.dataclass
class StubUser:
    """Simple user stub with a login name."""

    login: str


@dataclasses.dataclass
class StubGitHub:
    """Minimal GitHub stub that records repository creation requests."""

    login: str
    created: dict[str, str | bool]

    def me(self) -> StubUser:
        """Return a stub user for the configured login."""
        return StubUser(self.login)

    def create_repository(self, name: str, *, private: bool = False) -> None:
        """Record a repository creation request."""
        self.created["name"] = name
        self.created["private"] = private


@pytest.fixture
def github_stubs() -> tuple[type[StubUser], type[StubGitHub]]:
    """Provide shared GitHub stub classes for tests.

    Returns
    -------
    tuple[type[StubUser], type[StubGitHub]]
        The stub user and GitHub classes for reuse in tests.

    """
    return StubUser, StubGitHub
