"""The payloads, bodies and pages a case answers the forge's questions with.

Every value the forge double answers is written here rather than in the suite
that supplies it, so a case names the pull request it means and this module
states what GitHub would say about it. The shared record's two lines are the
spelling a person pastes into a body rather than a rendering of the stored one,
which is why they are written out: the parser's own suite pins that spelling,
and the ladder's suite pins that the ladder reads it.

Nothing here opens a repository or a socket.
"""

from __future__ import annotations

import typing as typ

from git_donkey import wheresat_github
from tests.unit.wheresat_helpers import PARENT_HEAD, PR_REPOSITORY, parent_pull_request
from tests.unit.wheresat_parents_corpus import (
    ASSOCIATION_REPOSITORY,
    BOUNDARY,
    CHILD_BRANCH,
    CHILD_IDENTITY,
    PARENT_IDENTITY,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey import stack_records
    from git_donkey.wheresat_records import ParentPullRequest


def child_payload(*, stacked: bool = False) -> ParentPullRequest:
    """Return the child's own pull request, as GitHub would report it.

    Parameters
    ----------
    stacked : bool, optional
        Whether GitHub records the child in a stack, which is what makes the
        ladder ask the forge for the stack at all.

    Returns
    -------
    ParentPullRequest
        The child's payload, headed by :data:`CHILD_BRANCH`.

    """
    return parent_pull_request(
        identity=CHILD_IDENTITY,
        head_ref=CHILD_BRANCH,
        head_repository=PR_REPOSITORY,
        stacked=stacked,
    )


def shared_body(number: int, *, boundary: str = BOUNDARY) -> str:
    """Return a body carrying a shared record, as an author pastes one.

    The two lines are written out here rather than rendered, because what a body
    carries is the spelling a person types; the parser's own suite pins that
    spelling, and this one pins that the ladder reads it.

    Parameters
    ----------
    number : int
        The pull request number the record names as the child's stack parent.
    boundary : str
        The commit the record names as the exclusive replay boundary.

    Returns
    -------
    str
        The two lines the body carries, with no trailing newline.

    """
    return (
        f"Stack parent: {ASSOCIATION_REPOSITORY}#{number}\n"
        f"Replay boundary (exclusive): {boundary}"
    )


def parent_payload() -> ParentPullRequest:
    """Return the parent pull request, as GitHub would report it.

    Returns
    -------
    ParentPullRequest
        The parent's payload, headed by the commit :data:`PARENT_HEAD`.

    """
    return parent_pull_request(identity=PARENT_IDENTITY, head_sha=PARENT_HEAD)


def association_page(
    associations: cabc.Mapping[str, tuple[stack_records.PullRequestIdentity, ...]],
    *,
    truncated: bool = False,
) -> wheresat_github.AssociationPage:
    """Return the association page a search would answer with.

    Parameters
    ----------
    associations : collections.abc.Mapping
        Pull requests per commit, keyed by the commit GitHub associated them
        with and holding a tuple of identities each.
    truncated : bool, optional
        Whether the page reports history it did not examine, which is what
        makes an empty answer ambiguous.

    Returns
    -------
    wheresat_github.AssociationPage
        The page, whose examined count is the number of commits keyed.

    """
    return wheresat_github.AssociationPage(
        associations=associations,
        commits_examined=len(associations),
        truncated=truncated,
    )
