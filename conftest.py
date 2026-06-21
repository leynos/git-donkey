"""Shared pytest fixtures for git-donkey tests.

The unit and integration suites both need lightweight GitHub API doubles while
keeping the real workflow modules importable. This root ``conftest`` provides
the reusable user and repository stubs; integration-specific Git repository
helpers live in ``tests.integration.conftest``.
"""

from __future__ import annotations

import dataclasses

import pytest


@dataclasses.dataclass
class StubUser:
    """Simple user stub with a login name.

    Attributes
    ----------
    login : str
        The GitHub login name for the stub user.

    """

    login: str


@dataclasses.dataclass
class StubGitHub:
    """Minimal GitHub stub that records repository creation requests.

    Attributes
    ----------
    login : str
        The GitHub login name for the stub user.
    created : dict[str, str | bool]
        Records repository creation parameters for assertions.

    """

    login: str
    created: dict[str, str | bool]

    def me(self) -> StubUser:
        """Return a stub user for the configured login.

        Returns
        -------
        StubUser
            Stub user for the configured login.

        """
        return StubUser(self.login)

    def create_repository(self, name: str, *, private: bool = False) -> None:
        """Record a repository creation request.

        Parameters
        ----------
        name : str
            Repository name.
        private : bool, optional
            Whether the repository should be private.

        """
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
