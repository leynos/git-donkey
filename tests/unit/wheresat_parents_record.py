"""The record double, and the records the cases hand it.

The ladder asks the clone's own record reader one question, so the double
answers with the record a test supplied — absent when it supplied none — and
records the branch each read named. The builders beside it write the three
records a case supplies: one stacked on a pull request, one born on a branch,
and the base record both are shaped from.

Nothing here opens a repository or a socket.
"""

from __future__ import annotations

import dataclasses

from git_donkey import stack_records
from git_donkey.wheresat_records import EvidenceKind
from tests.unit.wheresat_helpers import CHILD_TIP
from tests.unit.wheresat_parents_corpus import BOUNDARY, CHILD_BRANCH


@dataclasses.dataclass(frozen=True, slots=True)
class Records:
    """A record reader that answers with one record, and counts the reads.

    Only ``read`` is implemented, because it is the only record question the
    ladder puts: the double is cast to the port rather than completed, so a
    ladder that reached for another question would fail against it.

    Attributes
    ----------
    record : stack_records.RecordResult
        The record the branch is read as having, absent by default.
    refusal : Exception | None
        Failure the read raises instead of answering.
    reads : list[str]
        The branch of every read, in the order the reads were made.

    """

    record: stack_records.RecordResult = dataclasses.field(
        default_factory=stack_records.RecordAbsent
    )
    refusal: Exception | None = None
    reads: list[str] = dataclasses.field(default_factory=list)

    def read(self, branch: str) -> stack_records.RecordResult:
        """Return the record the test supplied for ``branch``, or refuse it.

        Parameters
        ----------
        branch : str
            Branch the record is asked about, which the double records.

        Returns
        -------
        stack_records.RecordResult
            Whatever the test supplied, which is absent when it supplied none.

        Raises
        ------
        Exception
            The refusal the test built this double with, if it built one. The
            class is not narrowed because what a case refuses with is the
            case's own choice, and the ladder is asserted against it as it is.

        """
        self.reads.append(branch)
        if self.refusal is not None:
            raise self.refusal
        return self.record


def stack_record(parent: stack_records.StackParent) -> stack_records.StackRecord:
    """Return the child's record, stacked as ``parent`` says.

    Parameters
    ----------
    parent : stack_records.StackParent
        Who the branch is stacked on, as the record stores it.

    Returns
    -------
    stack_records.StackRecord
        A record for :data:`CHILD_BRANCH` whose boundary is :data:`BOUNDARY`.

    """
    return stack_records.StackRecord(
        branch=CHILD_BRANCH,
        parent=parent,
        base=BOUNDARY,
        recorded_from=CHILD_TIP,
        evidence=EvidenceKind.MERGE_BASE,
    )


def stacked_on(
    pull_request: stack_records.PullRequestIdentity,
) -> stack_records.StackRecord:
    """Return a record naming a pull request, as a refreshed one does.

    Parameters
    ----------
    pull_request : stack_records.PullRequestIdentity
        Parent pull request the record names.

    Returns
    -------
    stack_records.StackRecord
        The record, holding that pull request and no branch.

    """
    return stack_record(
        stack_records.StackParent(branch=None, pull_request=pull_request)
    )


def born_on(branch: str) -> stack_records.StackRecord:
    """Return a record naming a branch, as one written at birth does.

    Parameters
    ----------
    branch : str
        Parent branch the record names.

    Returns
    -------
    stack_records.StackRecord
        The record, holding that branch and no pull request.

    """
    return stack_record(stack_records.StackParent(branch=branch, pull_request=None))
