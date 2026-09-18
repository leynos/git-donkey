"""Shared pytest fixtures for git-donkey tests.

The unit and integration suites both need lightweight GitHub API doubles while
keeping the real workflow modules importable. This root ``conftest`` provides
the reusable user and repository stubs, installs the recorder that captures
workflow observability, and declares the record mode the recorded GitHub
cassettes are replayed with; integration-specific Git repository helpers live in
``tests.integration.conftest``.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from git_donkey import observability
from tests.observability_helpers import RecordingRecorder

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def pytest_addoption(parser: pytest.Parser) -> None:
    """Declare how vcrpy treats the recorded GitHub cassettes.

    ``vcrpy`` 7.0.0 ships no pytest plugin, so the record mode is declared here
    rather than through a ``--record-mode`` another package would provide. The
    default is ``none`` on purpose: a suite run replays what was recorded and
    raises inside the code under test for anything the recordings do not hold,
    so an unrecorded request can never reach the network by accident. The other
    modes are for the deliberate recording pass described in
    ``docs/developers-guide.md``, and for nothing else.

    Parameters
    ----------
    parser : pytest.Parser
        Parser the option is added to.

    """
    parser.addoption(
        "--record-mode",
        default="none",
        choices=["none", "once", "new_episodes"],
        help=(
            "how vcrpy treats the recorded GitHub cassettes: 'none' (the "
            "default) replays them and refuses anything unrecorded, 'once' "
            "records a cassette that does not exist yet, and 'new_episodes' "
            "appends interactions a recording does not already hold"
        ),
    )


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


@pytest.fixture
def recording_recorder() -> cabc.Iterator[RecordingRecorder]:
    """Capture the workflow observations a test provokes.

    Yields
    ------
    RecordingRecorder
        The installed recorder. The recorder that was installed beforehand is
        restored when the test finishes.

    """
    recorder = RecordingRecorder()
    previous = observability.set_recorder(recorder)
    yield recorder
    observability.set_recorder(previous)
