"""Read and write the shared stack record through Git.

This is the only module in the package that touches a record's two artefacts:
the four ``branch.<name>.stack*`` configuration keys, and the refs under
``refs/stack-bases/`` and ``refs/stack-tombstones/``. The format and its pure
decisions live in :mod:`git_donkey.stack_records`; what lives here is the Git
access, so no command builds a ref path, parses a configuration value, or
decides for itself which of two artefacts wins.

Reads and writes are separate protocols. :class:`StackRecordReader` covers the
three reads and is what ``git wheresat`` holds on its default path, where it
must not be able to write; :class:`StackRecordWriter` adds the five writes and
is constructed only by a caller that will write.

Three properties are maintained here rather than left to the callers.

- **A record is created, never overwritten.** ``create`` refuses unless the
  branch has no record at all, and the anchor write carries an empty
  expected-old value, so a concurrent writer loses rather than silently
  winning. ``refresh`` carries the value the caller expects and lets Git's own
  compare-and-swap decide, so nothing is written when the expectation is stale
  (INV-7).
- **The record namespace stays a subset of the branch namespace.** ``orphans``
  reports every record whose branch has gone and ``sweep`` clears each one, so
  ``refs/stack-bases/<branch>`` and ``refs/heads/<branch>`` are created and
  destroyed together and the flat layout cannot collide with a nested branch
  name (INV-9).
- **A tombstone is written before the branch it names is deleted.** ``entomb``
  writes the tombstone first, because the ``git branch -D`` that follows always
  succeeds and there is no refusal to fall back on; the reverse order would
  lose the tip outright. The reverse case is ``create``, which retires any
  tombstone still naming the branch, so no pair of artefacts ever describes one
  branch as both live and deleted (INV-10).

Configuration is read once per call through ``git config --local --list -z``
and written through ``git config --local``, so Git itself quotes a hierarchical
branch name in the subsection and returns variable names in the lower case it
reads them in (AXIOM-13).

See ``docs/stack-records.md`` for the contract these methods implement, and
``docs/adr-004-shared-stack-records.md`` for why the record is shared.

Notes
-----
A tombstone's age is read from its own reflog, which is why every write here
passes ``--create-reflog``: a ref outside ``refs/heads/`` gets no reflog by
default. A tombstone whose age cannot be read is retained rather than guessed
at, so the failure mode is a ref that outlives its retention window, never a
prematurely forgotten parent. Git's own ``gc.reflogExpire`` may eventually
expire that entry, and the tombstone is then kept for good.

"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from git import GitCommandError, Repo

from git_donkey import stack_records

_ENTRY_SEPARATOR: typ.Final = "\0"
_MISSING_CONFIG_KEY: typ.Final = 5
_MISSING_REF: typ.Final = 1
_REF_NAME_FORMAT: typ.Final = "--format=%(refname)"
_RECORD_BRANCH_KEY: typ.Final = re.compile(r"^branch\.(?P<branch>.+)\.(?P<key>[^.]+)$")
_RELOG_ENTRY_TIME: typ.Final = re.compile(r"@\{(?P<timestamp>\d+)\}$")
_RECORD_KEY_NAMES: typ.Final = frozenset(key.value for key in stack_records.RecordKey)


class StackRecordError(RuntimeError):
    """A stack record could not be read or written as the caller required."""


class StackRecordConflictError(StackRecordError):
    """A write found a different record than the caller expected.

    Nothing is written when this is raised, so a caller that loses the race
    still knows the record it read is the record that survives.
    """


class StackRecordReader(typ.Protocol):
    """Read-only access to stack records and tombstones."""

    def read(self, branch: str) -> stack_records.RecordResult:
        """Return the reconciled record for one branch."""

    def tombstone(self, branch: str) -> str | None:
        """Return the tip preserved when ``branch`` was deleted, if any."""

    def orphans(self) -> tuple[str, ...]:
        """Return branches with a record and no branch (INV-9 violations)."""


class StackRecordWriter(StackRecordReader, typ.Protocol):
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

    def sweep(self, orphans: typ.Sequence[str]) -> tuple[str, ...]:
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
class GitStackRecordReader:
    """Git-backed reads of stack records, anchors, and tombstones.

    Parameters
    ----------
    repo : Repo
        Repository holding the configuration and the refs.

    """

    repo: Repo

    def read(self, branch: str) -> stack_records.RecordResult:
        """Return the record for ``branch``, or why it cannot be read.

        Parameters
        ----------
        branch : str
            Branch to read. It need not exist: a record whose branch has gone
            is reported as orphaned rather than as an absent record.

        Returns
        -------
        stack_records.RecordResult
            The reconciliation of the branch's configuration with its anchor
            ref.

        Raises
        ------
        ValueError
            If the branch name would be unsafe in a ref path.

        """
        anchor = self._ref_value(stack_records.base_ref_path(branch))
        return stack_records.reconcile(
            branch,
            self._branch_config(branch),
            anchor,
            branch_exists=self._branch_exists(branch),
        )

    def tombstone(self, branch: str) -> str | None:
        """Return the tip preserved for ``branch``, or ``None`` when unrecorded.

        Parameters
        ----------
        branch : str
            Branch whose tombstone is wanted, whether or not it still exists.

        Returns
        -------
        str | None
            The commit the tombstone ref names, or ``None`` when the branch has
            no tombstone.

        Raises
        ------
        ValueError
            If the branch name would be unsafe in a ref path.

        """
        return self._ref_value(stack_records.tombstone_ref_path(branch))

    def orphans(self) -> tuple[str, ...]:
        """Return branches with a record and no branch, in sorted order.

        Every name here violates INV-9: a record outlived the branch it
        describes, so the record namespace is no longer a subset of the branch
        namespace. Both artefacts are considered, because a record can survive
        in either one alone.

        Returns
        -------
        tuple[str, ...]
            Branch names with a record and no ``refs/heads/<branch>``.

        """
        names = set(self._recorded_branches())
        names.update(self._anchored_branches())
        return tuple(sorted(name for name in names if not self._branch_exists(name)))

    def _config_entries(self) -> tuple[tuple[str, str], ...]:
        """Return every key and value in the repository's local configuration."""
        output = self.repo.git.config("--local", "--list", "-z")
        entries = []
        for entry in output.split(_ENTRY_SEPARATOR):
            key, separator, value = entry.partition("\n")
            if entry and separator:
                entries.append((key, value))
        return tuple(entries)

    def _branch_config(self, branch: str) -> dict[str, str]:
        """Return ``branch``'s own configuration section, without its prefix."""
        prefix = f"branch.{branch}."
        return {
            key[len(prefix) :]: value
            for key, value in self._config_entries()
            if key.startswith(prefix)
        }

    def _recorded_branches(self) -> typ.Iterator[str]:
        """Yield branches whose configuration holds a record key.

        A configuration key is a branch name only by convention, so a section
        whose name Git refuses as a ref path component is not a branch and has
        no record to sweep. A name that parses but cannot be a ref path is
        skipped rather than reported: the anchor namespace could never hold
        anything for it.

        Yields
        ------
        str
            Branch name holding at least one record key.

        """
        for key, _ in self._config_entries():
            match = _RECORD_BRANCH_KEY.match(key)
            if match is None or match.group("key") not in _RECORD_KEY_NAMES:
                continue
            branch = match.group("branch")
            try:
                stack_records.validate_ref_component(branch)
            except ValueError:
                continue
            yield branch

    def _anchored_branches(self) -> typ.Iterator[str]:
        """Yield branches whose anchor ref exists."""
        yield from self._refs_in(stack_records.BASE_NAMESPACE)

    def _tombstoned_branches(self) -> typ.Iterator[str]:
        """Yield branches whose tombstone ref exists."""
        yield from self._refs_in(stack_records.TOMBSTONE_NAMESPACE)

    def _refs_in(self, namespace: str) -> typ.Iterator[str]:
        """Yield the branch names recorded under ``namespace``."""
        prefix = f"{namespace}/"
        output = self.repo.git.for_each_ref(_REF_NAME_FORMAT, prefix)
        for line in output.splitlines():
            if line.startswith(prefix):
                yield line[len(prefix) :]

    def _ref_value(self, ref: str) -> str | None:
        """Return the commit ``ref`` names, or ``None`` when it does not exist."""
        try:
            value = self.repo.git.rev_parse("--verify", "--quiet", ref)
        except GitCommandError as exc:
            if exc.status == _MISSING_REF:
                return None
            raise
        return str(value).strip()

    def _branch_exists(self, branch: str) -> bool:
        """Return whether ``refs/heads/<branch>`` exists."""
        return self._ref_value(f"refs/heads/{branch}") is not None

    def _tombstone_timestamp(self, branch: str) -> int | None:
        """Return when ``branch``'s tombstone was written, if it can be read."""
        ref = stack_records.tombstone_ref_path(branch)
        output = self.repo.git.reflog("show", "--date=unix", "--format=%gd", "-1", ref)
        match = _RELOG_ENTRY_TIME.search(output)
        return int(match.group("timestamp")) if match is not None else None


@dataclasses.dataclass(frozen=True, slots=True)
class GitStackRecordWriter(GitStackRecordReader):
    """Git-backed writes of stack records, anchors, and tombstones.

    This class is constructed only by a caller that will write: ``git donkey``
    at branch birth, ``git plonk`` around a deletion, and ``git wheresat``
    under ``--record``. A caller that only reads holds a
    :class:`GitStackRecordReader` and cannot write through it.

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
        live again must not still be described by the tip of its previous
        incarnation.

        Parameters
        ----------
        record : stack_records.StackRecord
            The record to store at branch birth.

        Raises
        ------
        StackRecordConflictError
            If the branch already has a record, if the anchor ref appeared
            between the check and the write, or if the anchor cannot be
            created at that path at all, such as when ``<branch>/...`` already
            holds it (AXIOM-12). Nothing has been written in any of those
            cases.
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
            raise StackRecordConflictError(msg)
        self._write_anchor(record, expected_old="")
        self._write_configuration(record, values)
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
        StackRecordConflictError
            If the anchor ref does not hold ``expected_old``, or cannot be
            written at all. Nothing has been written in either case.
        ValueError
            If the record is not one this package's reader reads back, or the
            branch name would be unsafe in a ref path. Nothing has been
            written in those cases either.

        """
        values = stack_records.record_values(record)
        self._write_anchor(record, expected_old=expected_old)
        self._write_configuration(record, values)

    def entomb(self, branch: str, tip: str) -> None:
        """Preserve ``tip`` as ``branch``'s tombstone, then clear its record.

        The tombstone is written first and is written whether or not the branch
        has a record, because the deletion that follows is forced and always
        succeeds. A crash between the two steps leaves a tombstone and a live
        record, which the sweep clears; the reverse order would lose the tip.

        Parameters
        ----------
        branch : str
            Branch about to be deleted.
        tip : str
            Commit the branch names now, to preserve for its surviving
            children.

        Raises
        ------
        StackRecordError
            If the tombstone cannot be written. The caller must not delete the
            branch when this is raised.
        ValueError
            If the branch name would be unsafe in a ref path.

        """
        self._write_tombstone(branch, tip)
        self._remove_live_record(branch)

    def sweep(self, orphans: typ.Sequence[str]) -> tuple[str, ...]:
        """Clear the records of branches that no longer exist.

        A record that still parses is converted into a tombstone naming the
        child tip it recorded, which is the strongest statement about the
        deleted branch that survives. A record that does not parse is cleared
        without inventing anything, and an orphan whose tombstone already
        exists keeps the tombstone it has, because a tip observed before the
        deletion is never replaced by one recorded earlier.

        Parameters
        ----------
        orphans : typ.Sequence[str]
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
        cutoff = self._expiry_cutoff(expire)
        pruned = []
        for branch in self._tombstoned_branches():
            timestamp = self._tombstone_timestamp(branch)
            if timestamp is not None and timestamp < cutoff:
                self._delete_ref(stack_records.tombstone_ref_path(branch))
                pruned.append(branch)
        return tuple(sorted(pruned))

    def _sweep_one(self, branch: str) -> bool:
        """Clear one orphan's record, reporting whether a tombstone stands."""
        config = self._branch_config(branch)
        anchor = self._ref_value(stack_records.base_ref_path(branch))
        if not config and anchor is None:
            return False
        # Reconciliation is asked as though the branch still existed, because
        # the orphan state is the caller's list and what the sweep wants here
        # is the parsed record, not the orphan report.
        result = stack_records.reconcile(branch, config, anchor, branch_exists=True)
        if isinstance(result, stack_records.RecordAbsent):
            return False
        tip = (
            result.recorded_from
            if isinstance(result, stack_records.StackRecord)
            else None
        )
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
            raise StackRecordConflictError(msg) from exc

    def _write_configuration(
        self,
        record: stack_records.StackRecord,
        values: typ.Mapping[stack_records.RecordKey, str],
    ) -> None:
        """Write the record's four configuration values."""
        for key, value in values.items():
            self.repo.git.config(
                "--local", f"branch.{record.branch}.{key.value}", value
            )

    def _write_tombstone(self, branch: str, tip: str) -> None:
        """Write the tombstone ref for ``branch``, with a reflog to age it by."""
        ref = stack_records.tombstone_ref_path(branch)
        try:
            self.repo.git.update_ref("--create-reflog", ref, tip)
        except GitCommandError as exc:
            detail = _git_failure(exc)
            msg = f"cannot write the tombstone for {branch!r}: {detail}"
            raise StackRecordError(msg) from exc

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

    def _expiry_cutoff(self, expire: str) -> int:
        """Return the instant ``expire`` names, in seconds since the epoch."""
        if not expire.strip():
            msg = "a tombstone expiry must not be empty"
            raise ValueError(msg)
        output = self.repo.git.rev_parse(f"--since={expire}")
        _, separator, value = str(output).partition("=")
        if not separator or not value.isdigit():
            msg = f"git reported no expiry cutoff for {expire!r}"
            raise ValueError(msg)
        return int(value)
