"""The only writing surface ``git wheresat`` holds, built only when it writes.

A run that neither fetches evidence nor records a result constructs no object
from this module, so the default path holds nothing that could change the
repository (INV-1). A run that does construct one reaches only three ref
namespaces:

- ``refs/wheresat/op/<op-id>/`` is where a transported boundary's evidence
  would be fetched to, one namespace per run.
  :meth:`WheresatRefWriter.release` deletes that namespace and nothing else.
  No console run creates one today: the one fetch this command performs writes
  the durable cache ref below, so the namespace is the surface a run that
  transported a boundary into it would use, and the durability suite is what
  exercises it. The namespace is never swept wholesale: refs live in the common
  ref store, so every worktree of a checkout shares ``refs/wheresat/``, and a
  blanket delete would take a sibling worktree's in-flight evidence with it.
- ``refs/wheresat/parent-head/<owner>/<repository>/<number>`` caches a fetched
  pull request head, so a second run on the same pull request performs no
  fetch at all.
- ``refs/wheresat/boundary/<branch>`` is retained only for a boundary that is
  otherwise reachable from the per-run refs alone, which is what keeps a
  reported boundary alive across ``git gc --prune=now`` (INV-8).

A stack record is the one write here that is not a ref of this module's own:
:meth:`WheresatRefWriter.write_record` delegates to
:mod:`git_donkey.stack_store`, so INV-7's create-only and expected-old
semantics are written once for all three commands rather than re-derived here.

Every value that reaches a ref path is validated first. A branch name and both
components of a repository slug pass through
:func:`git_donkey.stack_records.validate_ref_component`, and an operation id
through :func:`validate_op_id`, which is stricter because an id that nested a
namespace inside another run's would be deleted mid-fetch when the outer run
released its own.

See ``docs/execplans/git-wheresat-sub-command.md`` for the idempotence and
recovery rules these namespaces implement.

"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from git_donkey import stack_records, stack_store
from git_donkey.wheresat_errors import _reported

if typ.TYPE_CHECKING:
    from git import Repo

_ANSWERED_YES: typ.Final = 0
"""Exit status Git reports for a question whose answer is "yes"."""

_REF_NAME_FORMAT: typ.Final = "--format=%(refname)"
"""Format asking ``git for-each-ref`` for a ref's full name and nothing else."""

_OPERATION_NAMESPACE: typ.Final = "refs/wheresat/op"
"""Refs a transported boundary would be fetched into, one namespace per run.

No console run creates one: the fetch this command performs writes the durable
cache ref, so this is the surface a run that transported a boundary would use.
"""

_CACHE_NAMESPACE: typ.Final = "refs/wheresat/parent-head"
"""Durable refs caching a fetched pull request head, one per pull request."""

_BOUNDARY_NAMESPACE: typ.Final = "refs/wheresat/boundary"
"""Durable refs retaining a boundary no other ref would outlive."""

_SLUG_COMPONENTS: typ.Final = 2
"""Number of components an ``owner/name`` repository slug is made of."""

_OP_ID_PATTERN: typ.Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
"""What an operation id's alphabet is, with nothing else permitted.

The pattern is anchored at both ends by construction rather than by the
caller's use of :func:`re.match`, and it settles the character set, the length,
and the first character between them: a nested namespace, a leading hyphen a
command line could read as another option, a colon, and whitespace are all
refused here. It does not settle how the dots may be arranged — ``a..b`` and
``a.`` are named by this alphabet — so :func:`validate_op_id` applies Git's own
ref rules as well.
"""


class WheresatRefError(RuntimeError):
    """A ref Git would not fetch, retain, or release.

    Raised for every way a write of this module can fail, so the run reports
    that it could not complete rather than continuing with evidence it does
    not have (INV-5). Record writes are not wrapped: they raise
    :class:`git_donkey.stack_store.StackRecordError` from the store that owns
    their semantics.

    """


EvidenceRef = typ.NewType("EvidenceRef", str)
"""A ref of the evidence namespaces, built only by the factories below."""


def per_run_ref(op_id: str, name: str) -> EvidenceRef:
    """Return ``refs/wheresat/op/<op-id>/<name>``.

    The destination a transported boundary would be fetched into. No console
    run reaches this: the fetch this command performs caches the pull request
    head under :func:`parent_head_ref`, so the per-run namespace stays the
    surface a run that transported a boundary would use, and the durability
    suite is what exercises it.

    Parameters
    ----------
    op_id : str
        Name of the namespace a transported boundary would be fetched into,
        from ``--op-id`` or a generated identifier.
    name : str
        Name of the ref within that namespace, such as ``parent-head``.

    Returns
    -------
    EvidenceRef
        The ref, safe to use as a fetch refspec destination.

    Raises
    ------
    ValueError
        If either component would be unsafe in a ref path, or if ``op_id``
        would nest one run's namespace inside another's.

    """
    return EvidenceRef(
        f"{_per_run_namespace(op_id)}/{stack_records.validate_ref_component(name)}"
    )


def parent_head_ref(identity: stack_records.PullRequestIdentity) -> EvidenceRef:
    """Return the durable cache ref for a pull request head.

    Parameters
    ----------
    identity : stack_records.PullRequestIdentity
        Pull request whose head the ref caches. The number is an ``int`` and
        so cannot carry a refspec separator; only the slug's two components
        need validating.

    Returns
    -------
    EvidenceRef
        ``refs/wheresat/parent-head/<owner>/<repository>/<number>``.

    Raises
    ------
    ValueError
        If the repository is not a two-component ``owner/name`` slug, or
        either of its components would be unsafe in a ref path.

    """
    owner, name = _slug_parts(identity.repository)
    return EvidenceRef(f"{_CACHE_NAMESPACE}/{owner}/{name}/{identity.number}")


def validate_op_id(op_id: str) -> str:
    """Return ``op_id``, refusing one that may not name a run's namespace.

    The operation id is the one command-line value that reaches a ref path, so
    it is checked before anything is built from it: an id that could escape the
    evidence namespace, nest one run inside another, or be read as an option
    would have the run write refs a later release cannot recognize as its own.

    Two rule sets apply, and an id must satisfy both. The narrower is this
    module's: an id is one path component and holds no ``/``, so it cannot
    nest a namespace inside another run's. The wider is Git's own rules for a
    ref path component, which
    :func:`git_donkey.stack_records.ref_component_rejection` states; an id
    that is not a ref path component would reach ``git update-ref`` and be
    refused there, later and less clearly than here.

    Parameters
    ----------
    op_id : str
        Operation id from ``--op-id``, or a generated identifier.

    Returns
    -------
    str
        The id, unchanged.

    Raises
    ------
    ValueError
        If the id is empty, longer than 64 characters, holds a character
        outside letters, digits, dots, hyphens, and underscores, does not
        start with a letter or digit, or is not a name Git accepts as a ref
        path component.

    """
    if not _OP_ID_PATTERN.match(op_id):
        msg = (
            f"invalid operation id {op_id!r}: an op-id must start with a letter "
            "or digit and hold only letters, digits, dots, hyphens, and "
            "underscores, in at most 64 characters"
        )
        raise ValueError(msg)
    reason = stack_records.ref_component_rejection(op_id)
    if reason is not None:
        msg = f"invalid operation id {op_id!r}: {reason}"
        raise ValueError(msg)
    return op_id


def _per_run_namespace(op_id: str) -> str:
    """Return ``refs/wheresat/op/<op-id>``, refusing an op-id that nests.

    Parameters
    ----------
    op_id : str
        Name of the run's namespace.

    Returns
    -------
    str
        The namespace every ref the run creates sits under.

    Raises
    ------
    ValueError
        If ``op_id`` may not name a run's namespace, which is every way one
        could escape the namespace or nest another run inside it.

    """
    return f"{_OPERATION_NAMESPACE}/{validate_op_id(op_id)}"


def _slug_parts(repository: str) -> tuple[str, str]:
    """Return the validated owner and name of an ``owner/name`` slug.

    Parameters
    ----------
    repository : str
        Fully qualified repository slug.

    Returns
    -------
    tuple[str, str]
        The owner and the repository name.

    Raises
    ------
    ValueError
        If the slug is not exactly two non-empty components, or either of
        them would be unsafe in a ref path.

    """
    parts = repository.split("/")
    if len(parts) != _SLUG_COMPONENTS or not all(parts):
        msg = f"invalid repository slug {repository!r}: expected owner/name"
        raise ValueError(msg)
    owner, name = parts
    return (
        stack_records.validate_ref_component(owner),
        stack_records.validate_ref_component(name),
    )


class WheresatRefWriter(typ.Protocol):
    """The only Git surface in this command that mutates anything."""

    def fetch_evidence(
        self,
        remote: str,
        source_ref: str,
        destination: EvidenceRef,
        *,
        expected: str | None = None,
    ) -> str:
        """Fetch one ref into the evidence namespace and nowhere else."""

    def commit_at(self, ref: str) -> str | None:
        """Return the commit ``ref`` names, or ``None`` when it names none."""

    def retain_boundary(self, branch: str, commit: str) -> str:
        """Keep a durable ref for an otherwise unreachable boundary (INV-8)."""

    def release(self, op_id: str) -> None:
        """Delete a run's per-run namespace, and only that namespace."""

    def write_record(
        self, record: stack_records.StackRecord, expected_old: str | None
    ) -> None:
        """Write a stack record through ``stack_store``, honouring INV-7."""


@dataclasses.dataclass(frozen=True, slots=True)
class GitWheresatRefWriter:
    """Git-backed evidence fetches, boundary retainers, and record writes.

    Parameters
    ----------
    repo : Repo
        Repository to write to. This is the one object in the command that
        may change a repository, and a run builds it only when it has
        something to fetch or to record.

    """

    repo: Repo

    def fetch_evidence(
        self,
        remote: str,
        source_ref: str,
        destination: EvidenceRef,
        *,
        expected: str | None = None,
    ) -> str:
        """Fetch one ref into the evidence namespace, and say what it holds.

        A destination that already holds ``expected`` is left alone, which is
        what makes a second run on the same pull request perform no fetch at
        all. A destination holding any other commit is deleted first, because a
        cache that has gone stale must be replaced rather than reported as the
        answer it is no longer.

        Parameters
        ----------
        remote : str
            Remote to fetch from, by name.
        source_ref : str
            Ref at the remote holding the evidence, such as
            ``refs/pull/123/head``.
        destination : EvidenceRef
            Ref of this run's evidence namespace to fetch it into.
        expected : str | None, optional
            Commit the caller believes the destination already holds. When it
            holds that commit, no fetch is performed and it is returned.

        Returns
        -------
        str
            The commit the destination holds once the fetch has succeeded.

        Raises
        ------
        WheresatRefError
            If Git refuses the fetch or the deletion, or if the fetch leaves
            the destination without a commit. The run has no evidence to
            reason from in any of those cases, and must not read that absence
            as an answer.

        """
        held = self.commit_at(destination)
        if expected is not None and held == expected:
            return expected
        if held is not None:
            self._delete_ref(destination)
        self._fetch_into_evidence_ref(remote, source_ref, destination)
        commit = self.commit_at(destination)
        if commit is None:
            msg = (
                f"the fetch of {source_ref} from {remote!r} left no commit at "
                f"{destination}"
            )
            raise WheresatRefError(msg)
        return commit

    def retain_boundary(self, branch: str, commit: str) -> str:
        """Keep a durable ref for an otherwise unreachable boundary (INV-8).

        An existing ref for the branch is rewritten rather than left alone:
        the boundary a branch was cut at is that branch's answer, and a later
        run may establish a different one.

        Parameters
        ----------
        branch : str
            Branch whose boundary is being retained.
        commit : str
            Commit established as the boundary, already resolved through the
            graph, so it is written by object ID and cannot be misread.

        Returns
        -------
        str
            The ref that now retains ``commit``, for the report to name.

        Raises
        ------
        WheresatRefError
            If Git refuses to write the ref.
        ValueError
            If the branch name would be unsafe in a ref path.

        """
        ref = f"{_BOUNDARY_NAMESPACE}/{stack_records.validate_ref_component(branch)}"
        status, _, stderr = self.repo.git.update_ref(
            "--create-reflog",
            ref,
            commit,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != _ANSWERED_YES:
            reported = _reported(stderr, status)
            msg = f"cannot retain the boundary for {branch!r}: {reported}"
            raise WheresatRefError(msg)
        return ref

    def release(self, op_id: str) -> None:
        """Delete a run's per-run namespace, and only that namespace.

        A console run never holds one to release — the fetch it performs writes
        the durable cache ref — so this is the cleanup a run that transported a
        boundary into its own namespace performs, and the durability suite is
        where it is exercised.

        The refs are enumerated and deleted one at a time rather than by
        prefix, because Git refuses to delete a name that only has refs
        beneath it, and because enumerating leaves the deletion set exactly
        the refs this run created. Git matches the namespace at a slash
        boundary, so a sibling run whose op-id merely starts with this one's
        is not touched.

        Parameters
        ----------
        op_id : str
            Name of the namespace, the one its refs were created under.

        Raises
        ------
        WheresatRefError
            If Git refuses to delete one of the refs.
        ValueError
            If ``op_id`` is not one a per-run namespace can be named by.

        """
        for ref in self._refs_under(_per_run_namespace(op_id)):
            self._delete_ref(ref)

    def write_record(
        self, record: stack_records.StackRecord, expected_old: str | None
    ) -> None:
        """Write a stack record through ``stack_store``, honouring INV-7.

        An expected value of ``None`` means the caller saw no record at all,
        and only a create can follow: ``stack_store`` refuses to create over a
        record that exists, so a record written between the run's read and
        this call is not silently replaced. A supplied value is the one the
        caller read from the anchor ref, and Git's compare-and-swap refuses
        the write when the anchor has moved since.

        Parameters
        ----------
        record : stack_records.StackRecord
            The record to store.
        expected_old : str | None
            Commit the caller observed in the anchor ref, or ``None`` when it
            observed no record at all.

        Raises
        ------
        stack_store.StackRecordError
            If the record cannot be written, including the conflict raised
            when a create finds a record already there, or a refresh finds a
            different anchor value. Nothing has been written in that case.
        ValueError
            If the record is not one the reader reads back, or the branch
            name would be unsafe in a ref path.

        """
        writer = stack_store.GitStackRecordWriter(self.repo)
        if expected_old is None:
            writer.create(record)
            return
        writer.refresh(record, expected_old)

    def commit_at(self, ref: str) -> str | None:
        """Return the commit ``ref`` names, or ``None`` when it names none.

        The question is asked of the object store rather than of the ref, so a
        ref that exists but names something other than a commit answers the
        same way as a ref that is not there: neither is evidence a run may
        reason from, and the two are the same absence to a caller.

        Parameters
        ----------
        ref : str
            Ref, or any revision, to resolve.

        Returns
        -------
        str | None
            The full object ID of the commit, or ``None`` when the revision
            does not name one.

        """
        status, output, _ = self.repo.git.rev_parse(
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != _ANSWERED_YES:
            return None
        return str(output).strip()

    def _delete_ref(self, ref: str) -> None:
        """Delete one ref of this module's own namespaces.

        Parameters
        ----------
        ref : str
            Ref to delete.

        Raises
        ------
        WheresatRefError
            If Git refuses the deletion.

        """
        status, _, stderr = self.repo.git.update_ref(
            "-d",
            ref,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != _ANSWERED_YES:
            reported = _reported(stderr, status)
            msg = f"cannot delete the evidence ref {ref}: {reported}"
            raise WheresatRefError(msg)

    def _fetch_into_evidence_ref(
        self, remote: str, source_ref: str, destination: EvidenceRef
    ) -> None:
        """Fetch ``source_ref`` from ``remote`` straight into ``destination``.

        The refspec is the whole of what is asked of the remote: nothing else
        is pruned, no tag is brought down, no ``FETCH_HEAD`` is written, and
        submodule recursion is refused, so a populated submodule's repository
        is not written to either. The refspec is not forced, so a destination
        that appeared before the fetch is reported rather than silently
        replaced.

        Parameters
        ----------
        remote : str
            Remote to fetch from, by name.
        source_ref : str
            Ref at the remote holding the evidence, such as
            ``refs/pull/123/head``.
        destination : EvidenceRef
            Ref of the run's evidence namespace to fetch it into.

        Raises
        ------
        WheresatRefError
            If Git refuses the fetch.

        """
        status, _, stderr = self.repo.git.fetch(
            "--no-prune",
            "--no-tags",
            "--no-write-fetch-head",
            "--no-recurse-submodules",
            "--end-of-options",
            remote,
            f"{source_ref}:{destination}",
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != _ANSWERED_YES:
            reported = _reported(stderr, status)
            msg = f"cannot fetch {source_ref} from {remote!r}: {reported}"
            raise WheresatRefError(msg)

    def _refs_under(self, namespace: str) -> tuple[str, ...]:
        """Return every ref at or below ``namespace``, in Git's own order."""
        status, output, stderr = self.repo.git.for_each_ref(
            _REF_NAME_FORMAT,
            namespace,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != _ANSWERED_YES:
            reported = _reported(stderr, status)
            msg = f"cannot list the refs under {namespace}: {reported}"
            raise WheresatRefError(msg)
        return tuple(line for line in str(output).splitlines() if line)
