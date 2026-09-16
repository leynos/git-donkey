"""Write stack records, anchors, and tombstones through Git.

This is the write half of the record store, and :mod:`git_donkey.stack_store`
is the read half: between them they are the only modules in the package that
touch a record's two artefacts, so no command builds a ref path, parses a
configuration value, or decides for itself which of two artefacts wins. The
format and its pure decisions live in :mod:`git_donkey.stack_records`.

A caller that only reads holds a
:class:`~git_donkey.stack_store.StackRecordReader` and cannot write through it.
The writers here are constructed only by a caller that will write: ``git
donkey`` at branch birth, ``git plonk`` around a deletion, and ``git wheresat``
under ``--record``.

Three properties are maintained here rather than left to the callers.

- **A record is created, never overwritten.** ``create`` refuses unless the
  branch has no record at all, and the anchor write carries an empty
  expected-old value, so a concurrent writer loses rather than silently
  winning. ``refresh`` carries the value the caller expects and lets Git's own
  compare-and-swap decide, so nothing is written when the expectation is stale
  (INV-7).
- **A failed write is undone rather than half made.** The anchor is written
  before the four configuration values, so a create that cannot write them
  removes the anchor it created and a refresh puts back the values it replaced
  and moves the anchor back. A record with an anchor and an incomplete set of
  values is one the reader reports as malformed, which is the state this
  prevents.
- **A tombstone is written before the branch it names is deleted.** ``entomb``
  writes the tombstone first, because the ``git branch -D`` that follows always
  succeeds and there is no refusal to fall back on; the reverse order would
  lose the tip outright. The reverse case is ``create``, which retires any
  tombstone still naming the branch, so no pair of artefacts ever describes one
  branch as both live and deleted (INV-10).

See ``docs/stack-records.md`` for the contract these methods implement, and
``docs/adr-004-shared-stack-records.md`` for why the record is shared.

Notes
-----
Every ref written here is written with ``--create-reflog``, because a ref
outside ``refs/heads/`` gets no reflog by default and a tombstone's age is read
from its own reflog.

"""

from __future__ import annotations

import contextlib
import dataclasses
import typing as typ

from git import GitCommandError

from git_donkey import stack_records, stack_store

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_MISSING_CONFIG_KEY: typ.Final = 5


class StackRecordWriter(stack_store.StackRecordReader, typ.Protocol):
    """Write access. Constructed only by a caller that will write."""

    def create(self, record: stack_records.StackRecord) -> None:
        """Write a record that must not already exist.

        Uses an empty expected-old value on the anchor ref, so a concurrent
        writer loses rather than silently overwrites (AXIOM-4).
        """

    def refresh(self, record: stack_records.StackRecord, expected_old: str) -> None:
        """Update an existing record, requiring the current anchor value."""

    def entomb(self, branch: str, tip: str) -> None:
        """Write the tombstone and remove the live record, in that order.

        A tombstone is written whether or not ``branch`` had a record of its
        own. That is the common case, not an edge case: a parent created from
        the trunk is not itself stacked and so has no record, yet its tip is
        exactly what a surviving child needs for the ``parent-history-intact``
        gate.
        """

    def sweep(self, orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Convert orphaned records into tombstones; return those salvageable."""

    def prune(self, expire: str) -> tuple[str, ...]:
        """Delete tombstones older than ``expire``; return those deleted."""


def _git_failure(exc: GitCommandError) -> str:
    """Return the most specific line Git reported for ``exc``."""
    lines = (exc.stderr or "").strip().splitlines()
    if lines:
        return lines[0]
    return f"git exited with status {exc.status}"


@dataclasses.dataclass(frozen=True, slots=True)
class GitStackRecordWriter(stack_store.GitStackRecordReader):
    """Git-backed writes of stack records, anchors, and tombstones.

    This class is constructed only by a caller that will write: ``git donkey``
    at branch birth, ``git plonk`` around a deletion, and ``git wheresat``
    under ``--record``. A caller that only reads holds a
    :class:`~git_donkey.stack_store.GitStackRecordReader` and cannot write
    through it.

    Parameters
    ----------
    repo : Repo
        Repository holding the configuration and the refs.

    """

    def create(self, record: stack_records.StackRecord) -> None:
        """Write ``record`` for a branch that must not already have one.

        The anchor ref is written with an empty expected-old value, which is
        the create-only gate: a concurrent creator loses and this call raises
        without having changed anything the other writer can see. The four
        configuration values are written only after the anchor exists, so a
        refused create never rewrites the values of the record that won. A
        tombstone for the same branch is retired last, because a name that is
        live again must not still be described by the tip of its previous incarnation.

        Parameters
        ----------
        record : stack_records.StackRecord
            The record to store at branch birth.

        Raises
        ------
        stack_store.StackRecordConflictError
            If the branch already has a record, if the anchor ref appeared
            between the check and the write, or if the anchor cannot be
            created at that path at all, such as when ``<branch>/...`` already
            holds it (AXIOM-12). Nothing has been written in any of those
            cases.
        stack_store.StackRecordError
            If a configuration value cannot be written. The anchor this call
            created and the values it wrote are removed again, so the branch is
            left as it was found rather than half recorded.
        ValueError
            If the record is not one this package's reader reads back, or the
            branch name would be unsafe in a ref path. Nothing has been
            written in those cases either.

        """
        values = stack_records.record_values(record)
        if not isinstance(self.read(record.branch), stack_records.RecordAbsent):
            msg = (
                f"the branch {record.branch!r} already has a stack record; "
                "an existing record is refreshed with its expected old value"
            )
            raise stack_store.StackRecordConflictError(msg)
        self._write_anchor(record, expected_old="")
        try:
            self._write_configuration(record, values)
        except stack_store.StackRecordError:
            # A partial set of keys reads back as a malformed record, and the
            # anchor is what makes the branch look recorded at all, so both go.
            self._undo_write(record, expected_old="", previous={})
            raise
        self._retire_tombstone(record.branch)

    def refresh(self, record: stack_records.StackRecord, expected_old: str) -> None:
        """Update a record, requiring the anchor to still hold ``expected_old``.

        The anchor is written first, with Git's compare-and-swap enforcing the
        expectation, so a stale caller changes nothing at all. An
        ``expected_old`` of ``""`` therefore means "the anchor must not exist",
        which is Git's own reading of an empty expected value.

        Parameters
        ----------
        record : stack_records.StackRecord
            The record to store.
        expected_old : str
            The commit the caller observed in the anchor ref, or ``""`` when it
            observed no anchor at all.

        Raises
        ------
        stack_store.StackRecordConflictError
            If the anchor ref does not hold ``expected_old``, or cannot be
            written at all. Nothing has been written in either case.
        stack_store.StackRecordError
            If a configuration value cannot be written. The values and the
            anchor are put back as this call found them, so a failed refresh
            leaves the record unrefreshed rather than half updated.
        ValueError
            If the record is not one this package's reader reads back, or the
            branch name would be unsafe in a ref path. Nothing has been
            written in those cases either.

        """
        values = stack_records.record_values(record)
        previous = self._branch_config(record.branch)
        self._write_anchor(record, expected_old=expected_old)
        try:
            self._write_configuration(record, values)
        except stack_store.StackRecordError:
            # The record the caller found is put back rather than deleted, so a
            # refresh that fails part way through changes nothing at all.
            self._undo_write(record, expected_old=expected_old, previous=previous)
            raise

    def entomb(self, branch: str, tip: str) -> None:
        """Preserve ``tip`` as ``branch``'s tombstone, then clear its record.

        The tombstone is written first and is written whether or not the branch
        has a record, because the deletion that follows is forced and always
        succeeds. A crash between the two steps leaves a tombstone beside a live
        record, and the branch itself is still there. The sweep does not touch
        that state, because it resolves only records whose branch is gone: a
        live branch keeps the record that attests its own boundary, and a
        tombstone is evidence that a deletion started, not that it finished. The
        reverse order would lose the tip.

        Parameters
        ----------
        branch : str
            Branch about to be deleted.
        tip : str
            Commit the branch names now, to preserve for its surviving
            children.

        Raises
        ------
        stack_store.StackRecordError
            If the tombstone cannot be written. The caller must not delete the
            branch when this is raised.
        ValueError
            If the branch name would be unsafe in a ref path.

        """
        self._write_tombstone(branch, tip)
        self._remove_live_record(branch)

    def sweep(self, orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Clear the records of branches that no longer exist.

        A record that still parses is converted into a tombstone naming the
        child tip it recorded, which is the strongest statement about the
        deleted branch that survives. A record that does not parse is cleared
        without inventing anything, and an orphan whose tombstone already
        exists keeps the tombstone it has, because a tip observed before the
        deletion is never replaced by one recorded earlier.

        Parameters
        ----------
        orphans : cabc.Sequence[str]
            Branch names to clear, as reported by ``orphans``. Names without a
            record are ignored, so a caller may pass a stale list.

        Returns
        -------
        tuple[str, ...]
            The orphans whose recorded tip is now preserved in a tombstone,
            whether this call wrote it or found it, in the order they were
            supplied.

        Raises
        ------
        ValueError
            If a branch name would be unsafe in a ref path.

        """
        return tuple(branch for branch in orphans if self._sweep_one(branch))

    def prune(self, expire: str) -> tuple[str, ...]:
        """Delete tombstones written before ``expire``.

        The age of a tombstone is the time in its own reflog, which is why
        every tombstone write here creates one. A tombstone whose age cannot be
        read is kept, so an unreadable timestamp never shortens a parent's
        life. Git parses ``expire`` with its own date grammar, in which an
        unparsable expression means "now"; callers that accept the value from
        configuration should check it first.

        Parameters
        ----------
        expire : str
            A Git date expression, such as ``90.days.ago``. Tombstones older
            than it are deleted.

        Returns
        -------
        tuple[str, ...]
            The branches whose tombstones were deleted, in sorted order.

        Raises
        ------
        ValueError
            If ``expire`` is empty or Git reports no cutoff for it.

        """
        expired = self.expired(expire)
        for branch in expired:
            self._delete_ref(stack_records.tombstone_ref_path(branch))
        return expired

    def _sweep_one(self, branch: str) -> bool:
        """Clear one orphan's record, reporting whether a tombstone stands."""
        tip = self._orphan_tip(branch)
        if tip is not None and self.tombstone(branch) is None:
            self._write_tombstone(branch, tip)
        self._remove_live_record(branch)
        return tip is not None

    def _write_anchor(
        self, record: stack_records.StackRecord, expected_old: str
    ) -> None:
        """Write the anchor ref, requiring ``expected_old`` to be its value."""
        ref = stack_records.base_ref_path(record.branch)
        try:
            self.repo.git.update_ref("--create-reflog", ref, record.base, expected_old)
        except GitCommandError as exc:
            detail = _git_failure(exc)
            msg = f"cannot write the anchor for {record.branch!r}: {detail}"
            raise stack_store.StackRecordConflictError(msg) from exc

    def _write_configuration(
        self,
        record: stack_records.StackRecord,
        values: cabc.Mapping[stack_records.RecordKey, str],
    ) -> None:
        """Write the record's four configuration values.

        A Git refusal is reported as a failed record write rather than as the
        Git error itself, because both callers have already published an anchor
        by the time this runs and both respond by undoing that anchor.

        Raises
        ------
        stack_store.StackRecordError
            If a value cannot be written, with Git's own explanation.

        """
        for key, value in values.items():
            try:
                self.repo.git.config(
                    "--local", f"branch.{record.branch}.{key.value}", value
                )
            except GitCommandError as exc:
                msg = (
                    f"cannot write {key.value} for {record.branch!r}: "
                    f"{_git_failure(exc)}"
                )
                raise stack_store.StackRecordError(msg) from exc

    def _undo_write(
        self,
        record: stack_records.StackRecord,
        *,
        expected_old: str,
        previous: cabc.Mapping[str, str],
    ) -> None:
        """Put a branch's record back as a write that then failed found it.

        The record keys are cleared and ``previous`` is restored in their
        place: empty for a create, whose branch had no record, and the branch's
        own values for a refresh. The anchor is moved back to ``expected_old``
        under the commit this call wrote, so the repair cannot undo a writer
        that followed it, and an empty ``expected_old`` — a create — asks for
        the anchor this call made to be deleted instead.

        The repair is best effort. A failure here is not reported, because the
        caller is already being told its write failed and a second error would
        replace that one; what it leaves is a record the next read reports as
        malformed, which is the state this call exists to avoid. Every step is
        repaired on its own for the same reason: a step that fails leaves the
        rest repaired rather than abandoning the record part way back, and the
        anchor is moved before the values are restored so that the boundary a
        reader needs is the repair that is attempted first.

        Parameters
        ----------
        record : stack_records.StackRecord
            The record the failed write was writing.
        expected_old : str
            The anchor value the failed write required, which is the value the
            anchor held before it.
        previous : cabc.Mapping[str, str]
            The record's configuration before the failed write, keyed without
            the ``branch.<name>.`` prefix.

        """
        ref = stack_records.base_ref_path(record.branch)
        with contextlib.suppress(GitCommandError):
            if expected_old:
                self.repo.git.update_ref(ref, expected_old, record.base)
            else:
                self.repo.git.update_ref("-d", ref, record.base)
        for key in stack_records.RecordKey:
            key_path = f"branch.{record.branch}.{key.value}"
            with contextlib.suppress(GitCommandError):
                self._unset_configuration(key_path)
            if key.value in previous:
                with contextlib.suppress(GitCommandError):
                    self.repo.git.config("--local", key_path, previous[key.value])

    def _write_tombstone(self, branch: str, tip: str) -> None:
        """Write the tombstone ref for ``branch``, with a reflog to age it by."""
        ref = stack_records.tombstone_ref_path(branch)
        try:
            self.repo.git.update_ref("--create-reflog", ref, tip)
        except GitCommandError as exc:
            detail = _git_failure(exc)
            msg = f"cannot write the tombstone for {branch!r}: {detail}"
            raise stack_store.StackRecordError(msg) from exc

    def _remove_live_record(self, branch: str) -> None:
        """Remove the anchor ref and the configuration values, if present."""
        self._delete_ref(stack_records.base_ref_path(branch))
        for key in stack_records.RecordKey:
            self._unset_configuration(f"branch.{branch}.{key.value}")

    def _retire_tombstone(self, branch: str) -> None:
        """Delete the tombstone of a branch that now has a live record.

        Deleting happens only after the record has been written, so a create
        that fails part way through leaves the tombstone in place rather than
        destroying the only evidence of the previous incarnation.
        """
        if self.tombstone(branch) is not None:
            self._delete_ref(stack_records.tombstone_ref_path(branch))

    def _delete_ref(self, ref: str) -> None:
        """Delete ``ref``, which is not an error when it does not exist."""
        self.repo.git.update_ref("-d", ref)

    def _unset_configuration(self, key: str) -> None:
        """Unset every value of ``key``, which is not an error when it is unset."""
        try:
            self.repo.git.config("--local", "--unset-all", key)
        except GitCommandError as exc:
            if exc.status != _MISSING_CONFIG_KEY:
                raise
