"""The values the ladder's suite states its world in.

Every identity, boundary and body the parents suite's doubles answer about is
named here once, so the doubles and the cases driving them disagree about
nothing: the repository the association search is put to, the pull requests on
either side of the child, the commits a boundary and an ambiguity are named by,
and the operation the ladder records its questions under.

Nothing here opens a repository or a socket. The doubles that read these values
are in :mod:`tests.unit.wheresat_parents_history`,
:mod:`tests.unit.wheresat_parents_record` and
:mod:`tests.unit.wheresat_parents_forge`, and the reads one walk is handed are
put together in :mod:`tests.unit.wheresat_parents_helpers`.
"""

from __future__ import annotations

import typing as typ

from git_donkey import observability, stack_records

CHILD_BRANCH: typ.Final = "child"
"""The child branch the walk tells apart from the parent's."""

ASSOCIATION_REPOSITORY: typ.Final = "acme/widget"
"""``OWNER/REPOSITORY`` the association search is put to."""

CHILD_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=17
)
"""The child's own pull request, as the association search reports it."""

PARENT_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=16
)
"""The parent pull request the search or a stack may lead to."""

DECOY_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=15
)
"""A pull request on an older commit, which a stronger rung should pre-empt."""

FOREIGN_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository="someone/elsewhere", number=PARENT_IDENTITY.number
)
"""A pull request in another repository, numbering the same as the parent.

It is what tells a forge keyed by the pull request whole from one keyed by its
number: the two answers cannot both be held by the latter.
"""

BOUNDARY: typ.Final = "d" * 40
"""The boundary a body's shared record names."""

OTHER_BOUNDARY: typ.Final = "e" * 40
"""A second boundary, so a body can claim two and a reading name either."""

SILENT_BODY: typ.Final = (
    "This branch was branched off its parent. The commits above the boundary "
    "are its own work."
)
"""A pull request body that claims no shared record at all."""

SECOND_CHILD_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=18
)
"""A second pull request the child branch heads, on an older commit."""

PARENT_IDENTIFICATION: typ.Final[observability.Operation] = "parent_identification"
"""Operation every question about the parent is recorded under."""
