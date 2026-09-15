"""Which GitHub repository a checkout's remotes name, read from Git's own config.

The rule under test is a refusal to guess. A remote URL is read for the one
``OWNER/REPOSITORY`` it names on GitHub, and every other URL — another host, a
local path, a bundle, a remote carrying no URL at all — is answered with
nothing rather than with a near miss. Both callers depend on that refusal: the
association search asks the repository the child's commits live in, and the
parent's head is fetched from the remote that holds it, so a URL guessed at
would ask a stranger about the child or fetch a parent from another project.

The configuration is read directly rather than through ``git remote get-url
--all``, and both tests that pin a difference between the two are here: an
``insteadOf`` short form is expanded by Git and names nothing to this reader,
and a remote that carries no URL is one Git lists but this reader reports
nothing for. Each test builds the smallest real repository that exhibits the
behaviour, because the question is what Git's configuration holds rather than
what a double believes about it.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_remotes -q
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import wheresat_remotes
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git import Repo

_GITHUB_SPELLINGS: typ.Final = (
    "https://github.com/acme/widget.git",
    "https://github.com/acme/widget",
    "git@github.com:acme/widget.git",
    "ssh://git@github.com/acme/widget",
)

_NOT_GITHUB: typ.Final = (
    "https://gitlab.com/acme/widget.git",
    "/srv/git/widget.git",
    "https://github.com/acme",
    "file:///srv/git/widget.git",
)

_ORPHAN_FETCH: typ.Final = "+refs/heads/*:refs/remotes/orphan/*"
"""Refspec that gives a remote a configuration section and no URL."""

_URL_REWRITE: typ.Final = "url.https://github.com/.insteadOf"
"""Configuration key whose value Git expands a short remote URL with."""


def _repo(tmp_path: Path) -> Repo:
    """Return a seeded repository, which starts with no remotes at all."""
    return git_repo_helpers.seed_repo(tmp_path / "local")


def _configure(repo: Repo, *remotes: tuple[str, str]) -> None:
    """Add one remote per name-and-URL pair, in the order Git is given them."""
    for name, url in remotes:
        repo.create_remote(name, url)


def _names(repo: Repo) -> tuple[wheresat_remotes.RemoteRepository, ...]:
    """Return every remote that names a GitHub repository."""
    return wheresat_remotes.remote_repositories(repo)


@pytest.mark.parametrize("url", _GITHUB_SPELLINGS)
def test_a_github_url_names_the_repository_it_holds(url: str, tmp_path: Path) -> None:
    """Each spelling Git writes is read as the same ``OWNER/REPOSITORY``.

    A clone's spelling is the cloner's choice rather than the tool's, so a run
    that knew only the ``https`` form would fetch a parent's head from nowhere
    in every checkout configured the other two ways.
    """
    repo = _repo(tmp_path)
    _configure(repo, ("origin", url))

    assert _names(repo) == (
        wheresat_remotes.RemoteRepository(remote="origin", repository="acme/widget"),
    ), f"{url!r} should name acme/widget under the remote that carries it"


@pytest.mark.parametrize("url", _NOT_GITHUB)
def test_a_url_that_names_no_repository_names_nothing(url: str, tmp_path: Path) -> None:
    """A URL naming another host or no ``OWNER/REPOSITORY`` is not a near miss."""
    repo = _repo(tmp_path)
    _configure(repo, ("origin", url))

    assert not _names(repo), f"{url!r} should name no repository"
    assert wheresat_remotes.principal_repository(repo) is None, (
        "a remote naming nothing is a run with no question to put"
    )
    assert wheresat_remotes.named_remote(repo, "acme/widget") is None, (
        "no remote holds a repository none of them name"
    )


def test_a_remote_names_the_first_url_that_holds_a_repository(
    tmp_path: Path,
) -> None:
    """A remote's URLs are read in order, and the first repository wins.

    A checkout that has been pushed to through several URLs names its GitHub
    home in one of them; a reader that took the last, or that read a local path
    as the repository itself, would fetch a parent's head from the wrong place.
    """
    repo = _repo(tmp_path)
    _configure(repo, ("origin", "/srv/git/widget.git"))
    repo.git.remote("set-url", "--add", "origin", "https://github.com/acme/widget")
    repo.git.remote("set-url", "--add", "origin", "https://github.com/acme/decoy")

    assert _names(repo) == (
        wheresat_remotes.RemoteRepository(remote="origin", repository="acme/widget"),
    ), "the first URL naming a repository is the one the remote is reported under"


def test_a_remote_with_no_url_names_nothing(tmp_path: Path) -> None:
    """A remote Git lists but configures no URL for carries none.

    GitPython reads the missing URL as a ``KeyError`` rather than as a
    configuration error, so the answer is checked rather than assumed: a
    default handed to the reader comes back as the text of that default, which
    is a URL this module would then parse.
    """
    repo = _repo(tmp_path)
    repo.git.config("remote.orphan.fetch", _ORPHAN_FETCH)

    assert [remote.name for remote in repo.remotes] == ["orphan"], (
        "the remote should be configured, so that naming nothing is the answer"
    )
    assert not _names(repo), "a remote with no URL names no repository"


def test_the_first_remote_naming_a_repository_is_the_principal(
    tmp_path: Path,
) -> None:
    """The checkout's own repository is the first remote that names one.

    The order is Git's own configuration order rather than the alphabetical
    order ``git remote`` lists, which is what makes the principal remote a fact
    about how the checkout was set up rather than about its names.
    """
    repo = _repo(tmp_path)
    _configure(
        repo,
        ("fork", "/srv/git/widget.git"),
        ("origin", "https://github.com/acme/widget"),
        ("upstream", "https://github.com/acme/decoy"),
    )

    assert wheresat_remotes.principal_repository(repo) == "acme/widget", (
        "the principal repository is the one the first remote naming one names"
    )


def test_a_remote_is_found_by_the_repository_it_holds(tmp_path: Path) -> None:
    """A fetch asks the remote holding the parent's repository, not the principal."""
    repo = _repo(tmp_path)
    _configure(
        repo,
        ("origin", "https://github.com/acme/widget"),
        ("upstream", "https://github.com/acme/gadget"),
    )

    assert wheresat_remotes.named_remote(repo, "acme/gadget") == "upstream", (
        "the remote holding the repository is the one named"
    )
    assert wheresat_remotes.named_remote(repo, "acme/absent") is None, (
        "a repository no remote holds is fetched from nowhere"
    )


def test_an_instead_of_rewrite_is_not_read_as_a_repository(tmp_path: Path) -> None:
    """A short form is a fact about the user's configuration, not a repository.

    ``git remote get-url`` expands the rewrite, and this reader deliberately
    does not: an expanded URL would have the run fetch a parent's head from a
    host the checkout never named. The two readings are asserted together, so
    the case holds that the rewrite is live rather than that the URL was
    mistyped.
    """
    repo = _repo(tmp_path)
    _configure(repo, ("origin", "gh:acme/widget"))
    repo.git.config(_URL_REWRITE, "gh:")

    assert repo.git.remote("get-url", "origin") == "https://github.com/acme/widget", (
        "the rewrite should be one Git applies to this remote"
    )
    assert not _names(repo), "the short form names no repository to this reader"


def test_a_repository_with_no_remotes_names_nothing(tmp_path: Path) -> None:
    """A checkout with no remote at all is a run with no question to ask.

    It is not an error: the run reports the evidence it has and the gates that
    needed GitHub as unanswered, which is a different result from a run whose
    question was refused.
    """
    repo = _repo(tmp_path)

    assert not repo.remotes, "the seeded repository should have no remotes"
    assert not _names(repo), "no remote names anything to ask about"
    assert wheresat_remotes.principal_repository(repo) is None, (
        "there is no repository for the checkout's own commits"
    )
