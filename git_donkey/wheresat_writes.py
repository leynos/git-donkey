"""The writes ``git wheresat`` may make, and nothing else that can write.

A run is a read unless it fetched the parent's head, was asked to record, or
reported a boundary no durable ref reaches. Those three cases are the whole of
the command's writing surface, and they are the methods here rather than
branches of the workflow so that "what can this run change?" has one file for
an answer: the parent head the run brought down into the durable cache ref
(INV-1 permits it because the ref is the command's own, and the cache is what
makes a second run fetch nothing), the ref that keeps a reported boundary from
being collected (INV-8), and the shared stack record ``--record`` refreshes
(INV-7).

All three go through :class:`git_donkey.wheresat_refs.GitWheresatRefWriter`,
which is the command's only object that holds a repository it may write to.
Constructing this module's value object holds a repository and nothing that
writes to it, and the writer is built inside the method that needs it, so a run
that asks for none of the three never has one to reach for — which is what
keeps the read-only promise (INV-1) a property of the code rather than of the
flags a caller happened to pass. The fetch is the one write that happens before
the run has a context to bind, because the head it fetches is part of what the
context is built from, and it is a function here rather than a method for that
reason alone.

A record is written only from a claim. An established boundary an attested
candidate carries is a statement someone made on purpose, and writing it back is
what lets a later run read it as one; a boundary computed from surviving history
is real evidence but nobody's declaration, so it is not promoted into the record
(INV-7). A run that established no boundary at all, or established one only from
derived evidence, writes nothing and returns a warning naming what was left
unwritten, because a reader who passed ``--record`` and reads no warning would
believe the record moved.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import (
    observability,
    stack_records,
    stack_store,
    wheresat_collect,
    wheresat_payload,
    wheresat_records,
    wheresat_refs,
    wheresat_remotes,
)
from git_donkey.wheresat_errors import WheresatGraphError, WheresatUsageError

if typ.TYPE_CHECKING:
    from git import Repo

_RECORD_OPERATION: typ.Final[observability.Operation] = "stack_record_write"
"""Operation a record a run refreshes is recorded under.

The same operation ``git donkey`` records its birth records under, because the
two are one thing to a reader of the trace: the store was written to, by a
command that had a boundary to state.
"""

_NOTHING_TO_RECORD: typ.Final = (
    "nothing was recorded: the run established no boundary to record"
)
"""Warning for a ``--record`` run whose evidence established nothing."""

_UNATTESTED_TO_RECORD: typ.Final = (
    "nothing was recorded: the boundary rests on derived evidence, and only an "
    "attested claim is written back"
)
"""Warning for a ``--record`` run whose boundary no deliberate statement carries."""

_FETCH_OPERATION: typ.Final[observability.Operation] = "evidence_fetch"
"""Operation a run's fetch of the parent's head is recorded under."""

_NO_FETCH: typ.Final = (
    "the parent's head was not fetched: --no-fetch was given and the durable "
    "cache holds no head for it; the cache is filled by a run that may fetch, "
    "so run once without --no-fetch to fill it"
)
"""Why a ``--no-fetch`` run whose cache misses has no head to reason from."""

_NO_REMOTE: typ.Final = (
    "the parent's head was not fetched: no configured remote names "
    "{repository}, the repository the pull request's head lives in, so the "
    "head could not be fetched from its own repository; add that repository as "
    "a remote, or pass --offline to work from local evidence alone"
)
"""Why a head in a repository no remote names was not fetched."""

_NO_HEAD: typ.Final = (
    "the parent's head was not fetched: {identity} could not be fetched from "
    "the remote {remote!r} and the run has no evidence of where the parent's "
    "work begins without it"
)
"""Why the head could not be fetched from the repository that holds it."""

_MOVED_HEAD: typ.Final = (
    "the parent's head was not used: {identity} reports its head as {reported} "
    "but fetching it produced {fetched}, so the head moved while the run was "
    "reading it; run again to read the pull request as it now is"
)
"""Why a fetched head the payload no longer names was refused."""


@dataclasses.dataclass(frozen=True, slots=True)
class ParentHeadFetch:
    """What a run obtained for the parent's head, and what it could not.

    The head is carried as the payload rather than as a commit, because the
    fetch's other result is a field of it: ``head_fetched_from`` names the
    repository the commit came from, which is what gate 1 reads to tell a head
    that is still where the pull requests says it is from one that has been
    rebuilt. The two are set together or not at all, so a caller that has a
    commit has the field that goes with it.

    Parameters
    ----------
    parent : wheresat_records.ParentPullRequest | None
        The payload with ``head_fetched_from`` set, when the head is in hand.
    fault : str | None
        Why the head is not in hand, or ``None`` when it is.
    error_kind : observability.ErrorKind | None
        The bounded class of that failure, for the run's observation.

    """

    parent: wheresat_records.ParentPullRequest | None = None
    fault: str | None = None
    error_kind: observability.ErrorKind | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class WheresatWrites:
    """The writes one run may make, bound to what that run resolved.

    Parameters
    ----------
    repo : git.Repo
        Repository the run read, and the only repository a write may touch.
    context : wheresat_collect.CollectionContext
        What the run resolved, and the readers a write may ask.

    """

    repo: Repo
    context: wheresat_collect.CollectionContext

    def record(
        self,
        assessment: wheresat_records.Assessment,
        *,
        requested: bool,
        expected: str | None,
    ) -> tuple[str, ...]:
        """Refresh the branch's stack record, and say what was left unwritten.

        ``--record`` asks the run to re-state where the branch's own work begins
        in the record ``git donkey`` wrote at its birth, which is the record a
        later run reads as its strongest evidence. A refresh never creates a
        record: a branch that has none is told to be stacked with ``git donkey``
        rather than given a record this run would have to invent a parent for.

        Parameters
        ----------
        assessment : wheresat_records.Assessment
            What the evidence made of the boundary, which decides whether there
            is anything the run may state.
        requested : bool
            Whether the run was asked to record at all. A run that asked for
            nothing writes nothing and warns about nothing.
        expected : str | None
            ``--expected-old`` already resolved to an object ID, or ``None``
            when the run named no expectation.

        Returns
        -------
        tuple[str, ...]
            One warning naming why nothing was recorded, or an empty tuple when
            the run was not asked to record or the record was written.

        Raises
        ------
        WheresatUsageError
            If the record could not be written for a reason the user can act on:
            a branch with no record to refresh, a record that cannot be read, an
            expectation the anchor does not meet, or a store that refused the
            write. Nothing has been written in any of those cases.

        """
        if not requested:
            return ()
        if not isinstance(assessment, wheresat_records.Established):
            _observe("rejected")
            return (_NOTHING_TO_RECORD,)
        if not _attested(assessment.support):
            _observe("rejected")
            return (_UNATTESTED_TO_RECORD,)
        self._refresh(assessment, expected)
        return ()

    def retain(
        self,
        assessment: wheresat_records.Assessment,
    ) -> wheresat_records.Assessment:
        """Return the assessment with its boundary retained, if it needs retaining.

        A boundary that only this run's own refs reach would be collected by the
        next ``git gc --prune=now``, so it is written under a ref of its own
        before it is reported (INV-8). A boundary some other ref already reaches
        is left exactly as it is: the ref count of the repository is part of what
        a run without ``--record`` must not change.

        Parameters
        ----------
        assessment : wheresat_records.Assessment
            What the evidence made of the boundary.

        Returns
        -------
        wheresat_records.Assessment
            The assessment, with the retaining ref named when one was written,
            or an indeterminate result when the boundary could not be kept.

        """
        if not isinstance(assessment, wheresat_records.Established):
            return assessment
        try:
            reaches = self.context.graph.is_reachable_from_durable_ref(
                assessment.old_base
            )
        except WheresatGraphError as exc:
            return _indeterminate(
                assessment, f"cannot tell whether the boundary is retained: {exc}"
            )
        if reaches:
            return assessment
        return self._retain(assessment)

    def _refresh(
        self, assessment: wheresat_records.Established, expected: str | None
    ) -> None:
        """Write the established boundary into the branch's stack record.

        The record is re-read here rather than carried through the assessment,
        because what a refresh preserves is the parent a previous writer stated
        and not anything the assessment needed to decide a boundary: the parent
        a run identifies through the forge is the pull request the change now
        sits beneath, while the record's parent is where the branch was cut
        from, and a refresh restates the second without re-deciding the first.

        Raises
        ------
        WheresatUsageError
            If the record cannot be read or if the anchor is not what the run
            was told to expect. Nothing has been written in either case.

        """
        branch = self.context.request.branch
        existing = self._recorded(branch)
        anchor = self.context.records.anchor(branch)
        expected_old = self._expected_old(anchor, expected)
        record = stack_records.StackRecord(
            branch=branch,
            parent=existing.parent,
            base=assessment.old_base,
            recorded_from=self.context.request.child_tip,
            evidence=stack_records.EVIDENCE_REFRESHED,
        )
        self._write_record(record, expected_old)

    def _recorded(self, branch: str) -> stack_records.StackRecord:
        """Return the record ``branch`` has, which a refresh replaces.

        Returns
        -------
        stack_records.StackRecord
            The record as the store reads it back, with the parent a previous
            writer stated.

        Raises
        ------
        WheresatUsageError
            If the branch has no record to refresh, or its record cannot be
            read. A record is only ever refreshed here: creating one is what
            ``git donkey`` does at branch birth, where the parent is known
            because the user named the base the branch was cut from.

        """
        result = self.context.records.read(branch)
        if isinstance(result, stack_records.StackRecord):
            return result
        _observe("rejected")
        if isinstance(result, stack_records.RecordAbsent):
            msg = (
                f"the branch {branch!r} has no stack record to refresh; a record "
                "is written when a branch is created from another with git donkey"
            )
            raise WheresatUsageError(msg)
        msg = (
            f"the stack record for {branch!r} cannot be read, so it cannot be refreshed"
        )
        raise WheresatUsageError(msg)

    def _expected_old(self, anchor: str | None, expected: str | None) -> str:
        """Return the value the anchor ref must hold for the write to proceed.

        A record whose anchor has gone is replaced by writing the anchor with an
        empty expected value, which is Git's own spelling of "this ref must not
        exist": the create-only half of INV-7. An anchor that is there is
        replaced only when the run was told what it holds, and only when it
        holds that, which is the comparison the user asked for when they passed
        ``--expected-old``. Git's own compare-and-swap still enforces it at the
        write, so an anchor that moves between this read and the write is
        refused rather than overwritten.

        Parameters
        ----------
        anchor : str | None
            Commit the anchor ref names, or ``None`` when it does not exist.
        expected : str | None
            The expectation the run was given, already resolved.

        Returns
        -------
        str
            The commit the anchor must hold, or ``""`` when it must not exist.

        Raises
        ------
        WheresatUsageError
            If the anchor exists and no expectation was given, if an expectation
            was given and no anchor exists, or if the anchor holds a different
            commit than the expectation names.

        """
        branch = self.context.request.branch
        if expected is None:
            if anchor is None:
                return ""
            _observe("rejected", error_kind="stack_record_conflict")
            msg = (
                f"an expected old object ID is required: the branch {branch!r} "
                f"already has a stack record whose anchor ref is {anchor}; pass "
                "--expected-old with that commit to replace it"
            )
            raise WheresatUsageError(msg)
        if anchor is None:
            _observe("rejected", error_kind="stack_record_conflict")
            msg = (
                f"--expected-old was given, but the branch {branch!r} has no "
                "stack-base ref; there is nothing to replace"
            )
            raise WheresatUsageError(msg)
        if anchor != expected:
            _observe("rejected", error_kind="stack_record_conflict")
            msg = (
                f"--expected-old does not match: the branch {branch!r} holds "
                f"{anchor}, not {expected}"
            )
            raise WheresatUsageError(msg)
        return expected

    def _write_record(
        self,
        record: stack_records.StackRecord,
        expected_old: str,
    ) -> None:
        """Write ``record`` through the command's only writing surface.

        Raises
        ------
        WheresatUsageError
            If the store refused the write, including when the anchor moved
            between the run's read and this call. Nothing has been written in
            that case.

        """
        writer = wheresat_refs.GitWheresatRefWriter(self.repo)
        _observe("started")
        with observability.get_recorder().span(_RECORD_OPERATION):
            try:
                writer.write_record(record, expected_old)
            except stack_store.StackRecordConflictError as exc:
                _observe("failure", error_kind="stack_record_conflict")
                msg = f"the record for {record.branch!r} could not be refreshed: {exc}"
                raise WheresatUsageError(msg) from exc
            except (stack_store.StackRecordError, ValueError) as exc:
                _observe("failure")
                msg = f"the record for {record.branch!r} could not be written: {exc}"
                raise WheresatUsageError(msg) from exc
        _observe("success")

    def _retain(
        self, assessment: wheresat_records.Established
    ) -> wheresat_records.Assessment:
        """Return the assessment with a durable ref written for its boundary.

        Returns
        -------
        wheresat_records.Assessment
            The assessment naming the ref that now retains the boundary, or an
            indeterminate result when the ref could not be written: a boundary
            the run cannot keep must not be reported as an answer that outlives
            it.

        """
        branch = self.context.request.branch
        writer = wheresat_refs.GitWheresatRefWriter(self.repo)
        try:
            ref = writer.retain_boundary(branch, assessment.old_base)
        except (wheresat_refs.WheresatRefError, ValueError) as exc:
            return _indeterminate(
                assessment, f"the boundary could not be retained: {exc}"
            )
        return dataclasses.replace(assessment, durable_ref=ref)


def fetch_parent_head(
    repo: Repo,
    parent: wheresat_records.ParentPullRequest,
    *,
    no_fetch: bool,
) -> ParentHeadFetch:
    """Fetch the identified parent's head, or say why the run has none.

    The head is fetched into the durable cache ref and nowhere else. A cache
    that already holds the commit the payload reports is the answer without a
    fetch, and that check comes before ``--no-fetch`` rather than after it: a
    second run on the same pull request performs no transport at all, so a run
    that may not use the network can still reason from the head a previous run
    brought down.

    The fetched commit is held to the payload's ``head_sha`` rather than
    believed, because the boundary this run is about to establish would
    otherwise be a boundary for a commit the pull request no longer names.

    Parameters
    ----------
    repo : git.Repo
        Repository the run read, and the only repository the fetch may write.
    parent : wheresat_records.ParentPullRequest
        The parent pull request the ladder identified, as the forge reported
        it.
    no_fetch : bool
        Whether the run was told not to fetch. Such a run whose cache misses
        has no head, and says so rather than reaching for the network.

    Returns
    -------
    ParentHeadFetch
        The payload with ``head_fetched_from`` set, or the reason the run has
        no head to reason from.

    """
    destination = wheresat_refs.parent_head_ref(parent.identity)
    writer = wheresat_refs.GitWheresatRefWriter(repo)
    if writer.commit_at(destination) == parent.head_sha:
        _observe_fetch("success")
        return _fetched(parent)
    if no_fetch:
        _observe_fetch("not_requested")
        return ParentHeadFetch(fault=_NO_FETCH)
    remote = wheresat_remotes.named_remote(repo, parent.head_repository)
    if remote is None:
        _observe_fetch("unavailable")
        return ParentHeadFetch(
            fault=_NO_REMOTE.format(repository=parent.head_repository)
        )
    commit, failures = _fetched_commit(writer, parent, remote, destination)
    if commit is None:
        return _fetch_failed(parent, remote, failures)
    if commit != parent.head_sha:
        _observe_fetch("failure", error_kind="github_api_error")
        return ParentHeadFetch(
            fault=_MOVED_HEAD.format(
                identity=wheresat_payload.identity_text(parent.identity),
                reported=parent.head_sha,
                fetched=commit,
            ),
            error_kind="github_api_error",
        )
    _observe_fetch("success")
    return _fetched(parent)


def _fetched_commit(
    writer: wheresat_refs.GitWheresatRefWriter,
    parent: wheresat_records.ParentPullRequest,
    remote: str,
    destination: wheresat_refs.EvidenceRef,
) -> tuple[str | None, tuple[str, ...]]:
    """Return the commit the head was fetched to, or every refusal to fetch it.

    The pull request's own ref is asked for first, because that ref is the pull
    request's head rather than a branch that may since have moved or been
    deleted. A remote naming the repository a pull request *came from* — the
    fork the payload names as the head repository — carries no such ref for
    this pull request, so the head branch is asked for second. Both name the
    same commit when both answer, and the payload's ``head_sha`` is what either
    is held to, so the second attempt widens where the head may be found
    without widening what the head may be.

    Parameters
    ----------
    writer : wheresat_refs.GitWheresatRefWriter
        The command's only writing surface.
    parent : wheresat_records.ParentPullRequest
        Pull request whose head is being fetched.
    remote : str
        Remote to fetch from, which names the head's own repository.
    destination : wheresat_refs.EvidenceRef
        Cache ref the head is fetched into.

    Returns
    -------
    tuple[str | None, tuple[str, ...]]
        The fetched commit, or ``None`` with one refusal per ref that was
        tried.

    """
    identity = parent.identity
    source_refs = (
        f"refs/pull/{identity.number}/head",
        f"refs/heads/{parent.head_ref}",
    )
    failures: list[str] = []
    for source_ref in source_refs:
        try:
            commit = writer.fetch_evidence(
                remote, source_ref, destination, expected=parent.head_sha
            )
        except (wheresat_refs.WheresatRefError, ValueError) as exc:
            failures.append(f"{source_ref}: {exc}")
            continue
        return commit, tuple(failures)
    return None, tuple(failures)


def _fetch_failed(
    parent: wheresat_records.ParentPullRequest,
    remote: str,
    failures: tuple[str, ...],
) -> ParentHeadFetch:
    """Return the result of a head no source ref could be fetched from.

    Parameters
    ----------
    parent : wheresat_records.ParentPullRequest
        Pull request whose head could not be fetched.
    remote : str
        Remote every attempt was made against.
    failures : tuple[str, ...]
        What each attempt reported.

    Returns
    -------
    ParentHeadFetch
        The refusal, with each attempt's own words so an operator can see why
        the fetch was refused rather than only that it was.

    """
    _observe_fetch("failure", error_kind="git_command_error")
    reason = _NO_HEAD.format(
        identity=wheresat_payload.identity_text(parent.identity), remote=remote
    )
    reported = "; ".join(failures)
    return ParentHeadFetch(
        fault=f"{reason}: {reported}" if reported else reason,
        error_kind="git_command_error",
    )


def _fetched(parent: wheresat_records.ParentPullRequest) -> ParentHeadFetch:
    """Return the fetch result of a head that is in hand.

    ``head_fetched_from`` is set from the payload's own ``head_repository``,
    which is the repository the fetch was aimed at and the one a cache hit
    cached. Gate 1 asks whether the head is where the pull request says it is,
    and both paths must answer that the same way.

    Parameters
    ----------
    parent : wheresat_records.ParentPullRequest
        The pull request whose head is in hand.

    Returns
    -------
    ParentHeadFetch
        The payload with its ``head_fetched_from`` set.

    """
    return ParentHeadFetch(
        parent=dataclasses.replace(parent, head_fetched_from=parent.head_repository)
    )


def _observe_fetch(
    outcome: observability.Outcome,
    error_kind: observability.ErrorKind | None = None,
) -> None:
    """Record one bounded observation about this run's fetch.

    Parameters
    ----------
    outcome : observability.Outcome
        What became of the fetch.
    error_kind : observability.ErrorKind | None, optional
        Which bounded failure the fetch met, when it met one.

    """
    observability.get_recorder().record(
        observability.Observation(
            operation=_FETCH_OPERATION,
            outcome=outcome,
            error_kind=error_kind,
        )
    )


def _observe(
    outcome: observability.Outcome,
    error_kind: observability.ErrorKind | None = None,
) -> None:
    """Record one bounded observation about this run's record write.

    Parameters
    ----------
    outcome : observability.Outcome
        What became of the write.
    error_kind : observability.ErrorKind | None, optional
        Which bounded failure the write met, when it met one.

    """
    observability.get_recorder().record(
        observability.Observation(
            operation=_RECORD_OPERATION,
            outcome=outcome,
            error_kind=error_kind,
        )
    )


def _attested(support: typ.Sequence[wheresat_records.Establishing]) -> bool:
    """Return whether a deliberate statement carries the boundary.

    A record whose claim gate 8 demoted is carried by its commit as derived
    evidence, and a boundary only the merge base and fork point agree on is
    derived evidence too. Neither may be written back as a record, because
    reading it back would promote a computation into a claim (INV-7).

    Parameters
    ----------
    support : typ.Sequence[wheresat_records.Establishing]
        The candidates the established boundary rests on.

    Returns
    -------
    bool
        ``True`` when at least one supporting candidate is attested.

    """
    return any(
        isinstance(candidate, wheresat_records.AttestedCandidate)
        for candidate in support
    )


def _indeterminate(
    assessment: wheresat_records.Established, reason: str
) -> wheresat_records.Indeterminate:
    """Return the indeterminate result a run that cannot keep its answer reports.

    The boundary's own evidence is reported as the candidates the run collected,
    because that evidence is what the run was about to answer with, and a reader
    of the refusal needs it to see which boundary went unreported.

    Parameters
    ----------
    assessment : wheresat_records.Established
        The boundary the run could not keep.
    reason : str
        Why the run could not keep it.

    Returns
    -------
    wheresat_records.Indeterminate
        The verdict, with the reason it could not be established.

    """
    return wheresat_records.Indeterminate(
        candidates=tuple(assessment.support),
        gates=assessment.gates,
        reasons=(reason,),
    )
