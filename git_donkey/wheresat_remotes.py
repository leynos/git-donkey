"""Which GitHub repository this checkout's remotes name, and which remote does.

Two questions are asked of a remote here, and both are answered by reading the
repository's configuration. The association search is a question about a
repository — what belongs with these commits is a fact about the repository
that holds them — so the checkout has to name one. And the parent's head must
be fetched from the repository it actually lives in, so the fetch has to name
a remote rather than a URL assembled from a guess.

A remote may carry more than one URL, and a URL may name no GitHub repository
at all: another host, a local path, a bundle, an ``insteadOf`` short form. Such
a URL is answered with nothing rather than read as a near miss, because each
caller is looking for the one remote that holds a particular repository, and a
guess would fetch a parent's head from a stranger or ask about the child's
commits in somebody else's repository.

The configuration is read directly rather than through ``git remote get-url
--all``, which reads the same keys but also applies ``url.<base>.insteadOf``
rewrites. A checkout whose remotes are reachable only through a rewrite
therefore names no repository to this reader, and the run reports the head as
unfetchable rather than fetching from a guess: the rewrite is a fact about the
user's configuration, and the command will not turn it into an assumption about
which repository the evidence came from.

"""

from __future__ import annotations

import configparser
import dataclasses
import typing as typ

from git_donkey import stack_records

if typ.TYPE_CHECKING:
    from git import Repo
    from git.config import GitConfigParser

_CONFIG_SECTION: typ.Final = 'remote "{remote}"'
"""Section a remote's own settings are configured under."""


@dataclasses.dataclass(frozen=True, slots=True)
class RemoteRepository:
    """A configured remote and the GitHub repository its URL names.

    Parameters
    ----------
    remote : str
        Name of the remote, as Git is configured with it.
    repository : str
        ``OWNER/REPOSITORY`` the remote's URL names.

    """

    remote: str
    repository: str


def remote_repositories(repo: Repo) -> tuple[RemoteRepository, ...]:
    """Return every remote whose URL names a GitHub repository.

    A remote is reported once, under the first of its URLs that names a
    repository, and remotes are reported in configuration order: a caller that
    wants the principal remote reads the first, and a caller looking for the
    remote that holds one particular repository searches the whole tuple.

    Parameters
    ----------
    repo : git.Repo
        Repository whose remotes are read.

    Returns
    -------
    tuple[RemoteRepository, ...]
        One entry per remote that names a GitHub repository, in the order Git
        is configured with them.

    """
    named: list[RemoteRepository] = []
    with repo.config_reader() as reader:
        for remote in repo.remotes:
            repository = _first_repository(_urls(reader, remote.name))
            if repository is not None:
                named.append(
                    RemoteRepository(remote=remote.name, repository=repository)
                )
    return tuple(named)


def named_remote(repo: Repo, repository: str) -> str | None:
    """Return the name of the remote whose URL names ``repository``, if any.

    Parameters
    ----------
    repo : git.Repo
        Repository whose remotes are read.
    repository : str
        ``OWNER/REPOSITORY`` slug to look for among the remotes' URLs.

    Returns
    -------
    str | None
        The remote's name, or ``None`` when no configured remote names that
        repository. The first remote that names it wins, because two remotes
        naming one repository are the same repository to fetch from.

    """
    for named in remote_repositories(repo):
        if named.repository == repository:
            return named.remote
    return None


def principal_repository(repo: Repo) -> str | None:
    """Return the repository the checkout's own commits are read in, if any.

    The first remote that names a GitHub repository is read as the one the
    child's commits live in, which is the checkout's principal remote in the
    sense this command needs: the repository a question about those commits is
    put to.

    Parameters
    ----------
    repo : git.Repo
        Repository whose remotes are read.

    Returns
    -------
    str | None
        The ``OWNER/REPOSITORY`` slug, or ``None`` when no configured remote
        names one, which is a run with no question to put rather than a
        question that went unanswered.

    """
    named = remote_repositories(repo)
    return named[0].repository if named else None


def _first_repository(urls: tuple[str, ...]) -> str | None:
    """Return the first URL that names a GitHub repository, if any does.

    Parameters
    ----------
    urls : tuple[str, ...]
        Every URL one remote carries, in configuration order.

    Returns
    -------
    str | None
        The ``OWNER/REPOSITORY`` slug, or ``None`` when none of the URLs names
        a GitHub repository.

    """
    for url in urls:
        repository = stack_records.repository_from_remote_url(url)
        if repository is not None:
            return repository
    return None


def _urls(reader: GitConfigParser, remote: str) -> tuple[str, ...]:
    """Return every URL a remote is configured with.

    A remote with no URL is read as carrying none rather than as an error: the
    question is which remote names one repository, and a remote that names
    nothing is not an answer to it.

    Parameters
    ----------
    reader : git.config.GitConfigParser
        Reader over the repository's configuration.
    remote : str
        Name of the remote to read.

    Returns
    -------
    tuple[str, ...]
        The URLs the remote carries, in configuration order.

    """
    try:
        # GitPython reads a remote that carries no URL as a ``KeyError`` rather
        # than as a configuration error, so both are the same answer here. The
        # option is asked for without a default, because a default is returned
        # as ``[default]``: it would make a URL-less remote carry the string of
        # whatever default was passed instead of carrying nothing.
        urls = reader.get_values(_CONFIG_SECTION.format(remote=remote), "url")
    except (KeyError, configparser.Error):
        return ()
    return tuple(str(url) for url in urls)
