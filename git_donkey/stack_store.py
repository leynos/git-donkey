"""Read the shared stack record through Git.

This is the read half of the record store, and
:mod:`git_donkey.stack_writes` is the write half: between them they are the
only modules in the package that touch a record's two artefacts, so no command
builds a ref path, parses a configuration value, or decides for itself which of
two artefacts wins. The format and its pure decisions live in
:mod:`git_donkey.stack_records`.

Reads and writes are separate protocols. :class:`StackRecordReader` covers the
three reads and is what ``git wheresat`` holds on its default path, where it
must not be able to write;
:class:`~git_donkey.stack_writes.StackRecordWriter` adds the five writes and is
constructed only by a caller that will write.

Two properties are maintained here rather than left to the callers.

- **Orphans are reported before they are swept.** ``orphans`` names every
  record whose branch has gone, which is what lets the sweep clear each one and
  no more: ``refs/stack-bases/<branch>`` and ``refs/heads/<branch>`` are
  created and destroyed together, and the flat layout cannot collide with a
  nested branch name (INV-9).
- **The retention window is validated before it is applied.** ``expiry`` reads
  ``stack.tombstoneExpire`` and refuses one that is empty or that names no past
  instant, because Git's date grammar reads an expression it cannot parse as
  *now* and a window of no length prunes every tombstone in the repository.

Configuration is read through ``git config --local --list -z``, once per
orphan-processing operation rather than once per branch it is asked about, and
the listing is discarded by any write and at the start of the next operation,
so a caller never reads a value a change has already overtaken. Git itself
quotes a hierarchical branch name in the subsection and returns variable names
in the lower case it reads them in (AXIOM-13). A tombstone's age
is read from its own reflog, which is why
:mod:`git_donkey.stack_writes` passes ``--create-reflog``: a ref outside
``refs/heads/`` gets no reflog by default. A tombstone whose age cannot be read
is retained rather than guessed at, so the failure mode is a ref that outlives
its retention window, never a prematurely forgotten parent. Git's own
``gc.reflogExpire`` may eventually expire that entry, and the tombstone is then
kept for good.

See ``docs/stack-records.md`` for the contract these methods implement, and
``docs/adr-004-shared-stack-records.md`` for why the record is shared.

"""

from __future__ import annotations

import contextlib
import dataclasses
import re
import typing as typ

from git import GitCommandError, Repo

from git_donkey import stack_records
from git_donkey._constants import REF_NAME_FORMAT

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_ENTRY_SEPARATOR: typ.Final = "\0"
_ABSENT_CONFIG_KEY: typ.Final = 1
_MISSING_REF: typ.Final = 1
_UNKNOWN_REVISION: typ.Final = 128
"""What Git exits with when a revision it was handed does not resolve."""
TOMBSTONE_EXPIRE_KEY: typ.Final = "stack.tombstoneExpire"
"""Repository-local key naming how long a tombstone is kept."""
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

    def anchor(self, branch: str) -> str | None:
        """Return the commit ``branch``'s anchor ref names, if it has one.

        A record's configuration and its anchor can disagree, and the
        reconciliation :meth:`read` performs is deliberately blind to which of
        the two a caller must compare against: a record whose anchor has gone
        still reconciles as a record. A caller about to write one therefore
        reads the ref itself, which is what it passes to the writer as the
        value it expects to find (INV-7).
        """

    def tombstone(self, branch: str) -> str | None:
        """Return the tip preserved when ``branch`` was deleted, if any."""

    def orphans(self) -> tuple[str, ...]:
        """Return branches with a record and no branch (INV-9 violations)."""

    def branch_tip(self, branch: str) -> str | None:
        """Return the commit ``refs/heads/<branch>`` names, if it still exists."""

    def rescuable(self, orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Return the orphans whose recorded tip a sweep would preserve.

        The read half of the sweep in :mod:`git_donkey.stack_writes`, so a
        caller that must not write — a dry run — can still say which orphans
        would be rescued rather than describing every orphan as if its tip
        survived.
        """

    def expired(self, expire: str) -> tuple[str, ...]:
        """Return the tombstones older than ``expire``, without deleting them."""

    def expiry(self) -> str:
        """Return the configured retention window, once it is known usable.

        Raises
        ------
        ValueError
            If the window is empty, names no cutoff, or names an instant that
            is not in the past, which is how Git reads an expression it cannot
            parse and is therefore a typo rather than a window.
        """


@dataclasses.dataclass(frozen=True, slots=True)
class GitStackRecordReader:
    """Git-backed reads of stack records, anchors, and tombstones.

    Parameters
    ----------
    repo : Repo
        Repository holding the configuration and the refs.

    """

    repo: Repo
    _configuration: tuple[tuple[str, str], ...] | None = dataclasses.field(
        default=None, init=False, repr=False, compare=False
    )

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
        with self._one_configuration_listing():
            return stack_records.reconcile(
                branch,
                self._branch_config(branch),
                anchor,
                branch_exists=self._branch_exists(branch),
            )

    def anchor(self, branch: str) -> str | None:
        """Return the commit ``branch``'s anchor ref names, if it has one.

        Parameters
        ----------
        branch : str
            Branch whose anchor is wanted.

        Returns
        -------
        str | None
            The commit at ``refs/stack-bases/<branch>``, or ``None`` when that
            ref does not exist.

        Raises
        ------
        ValueError
            If the branch name would be unsafe in a ref path.

        """
        return self._ref_value(stack_records.base_ref_path(branch))

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
        with self._one_configuration_listing():
            names = set(self._recorded_branches())
            names.update(self._anchored_branches())
            return tuple(
                sorted(name for name in names if not self._branch_exists(name))
            )

    def branch_tip(self, branch: str) -> str | None:
        """Return the commit ``branch`` names now, or ``None`` when it is gone.

        This is the tip a caller preserves before deleting the branch, read
        from the branch itself rather than from any record of it, because the
        record describes a boundary and the tombstone must name a commit.

        The name is validated before the ref is built, because the ref is
        assembled from it: a name Git would read as an option or as an
        expression about some other commit is refused here rather than handed
        to the read, so a caller either gets a commit or a refusal.

        Parameters
        ----------
        branch : str
            Local branch whose tip is wanted.

        Returns
        -------
        str | None
            The commit ``refs/heads/<branch>`` names, or ``None`` when the
            branch does not exist.

        Raises
        ------
        ValueError
            If the branch name would be unsafe in a ref path.

        """
        return self._ref_value(
            f"refs/heads/{stack_records.validate_ref_component(branch)}"
        )

    def rescuable(self, orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Return the orphans whose recorded tip is still readable.

        Parameters
        ----------
        orphans : cabc.Sequence[str]
            Branch names to classify, as reported by ``orphans``. Names
            without a record are ignored, so a caller may pass a stale list.

        Returns
        -------
        tuple[str, ...]
            The orphans a sweep would preserve a tip for, in the order they
            were supplied. The rest are cleared by a sweep all the same: their
            record did not survive the deletion that orphaned them, so there is
            no tip left to preserve and none is invented.

        """
        with self._one_configuration_listing():
            return tuple(
                branch for branch in orphans if self._orphan_tip(branch) is not None
            )

    def expired(self, expire: str) -> tuple[str, ...]:
        """Return the tombstones written before ``expire``, in sorted order.

        A tombstone whose age cannot be read is not among them: an unreadable
        timestamp shortens no parent's life, so it is retained rather than
        guessed at.

        Parameters
        ----------
        expire : str
            A Git date expression, such as ``90.days.ago``.

        Returns
        -------
        tuple[str, ...]
            The branches whose tombstones are older than ``expire``.

        Raises
        ------
        ValueError
            If ``expire`` is empty or Git reports no cutoff for it.

        """
        cutoff = self._expiry_cutoff(expire)
        stale: list[str] = []
        for branch in self._tombstoned_branches():
            timestamp = self._tombstone_timestamp(branch)
            if timestamp is not None and timestamp < cutoff:
                stale.append(branch)
        return tuple(sorted(stale))

    def expiry(self) -> str:
        """Return the configured retention window for tombstones.

        Git reads a date expression it cannot parse as *now*, and a window of
        no length prunes every tombstone in the repository. The window is
        therefore measured before it is used: the reader asks Git for the
        instant *now* names first and for the configured expression second, so
        a window that is not strictly in the past is refused rather than
        applied. A partially parsable expression is read by Git as its
        parsable prefix and cannot be detected here; the caller reports the
        window it used, which is the mitigation for that case.

        Returns
        -------
        str
            The configured window, or ``DEFAULT_TOMBSTONE_EXPIRE`` when the
            key is unset.

        Raises
        ------
        ValueError
            If the configured window is empty or names no cutoff, or if the
            instant it names is now or later.

        """
        expire = self._configured_expire()
        if not expire.strip():
            msg = (
                f"the configured {TOMBSTONE_EXPIRE_KEY} is empty: a retention "
                "window is required, and every window read from an empty value "
                "would prune every tombstone"
            )
            raise ValueError(msg)
        now = self._expiry_cutoff("now")
        cutoff = self._expiry_cutoff(expire)
        if cutoff >= now:
            msg = (
                f"the configured {TOMBSTONE_EXPIRE_KEY} ({expire!r}) names no "
                "past instant: Git reads an expression it cannot parse as now, "
                "which would prune every tombstone in the repository"
            )
            raise ValueError(msg)
        return expire

    @contextlib.contextmanager
    def _one_configuration_listing(self) -> cabc.Iterator[None]:
        """Hold one local-configuration listing for the duration of one read.

        The listing is the repository's whole configuration, so an operation
        that asks about every orphan it holds — a rescuable report, a sweep —
        would otherwise start a Git process for each name it was handed. It is
        taken again for each operation rather than kept beside the repository,
        because this class is not the only writer of the configuration it
        reads: a user's ``git config``, another process, and another instance
        of this class all change it, and the next operation must see that.

        Yields
        ------
        None
            The listing is held, and no value is yielded for it.

        """
        self._forget_config_entries()
        try:
            yield
        finally:
            self._forget_config_entries()

    def _config_entries(self) -> tuple[tuple[str, str], ...]:
        """Return every key and value in the repository's local configuration.

        The listing is read once per operation and reused within it, so a
        caller that asks about every orphan it holds asks Git once rather than
        once per orphan. It is answered from a listing taken since the last
        time one was discarded.

        Returns
        -------
        tuple[tuple[str, str], ...]
            Every configured key with its value, as Git reports them.

        """
        entries = self._configuration
        if entries is None:
            entries = self._read_config_entries()
            object.__setattr__(self, "_configuration", entries)
        return entries

    def _read_config_entries(self) -> tuple[tuple[str, str], ...]:
        """Read the repository's local configuration as Git reports it."""
        output = self.repo.git.config("--local", "--list", "-z")
        entries = []
        for entry in output.split(_ENTRY_SEPARATOR):
            key, separator, value = entry.partition("\n")
            if entry and separator:
                entries.append((key, value))
        return tuple(entries)

    def _forget_config_entries(self) -> None:
        """Discard the held listing, so the next read takes a fresh one.

        A writer calls this before the first value it changes, and an operation
        discards the listing it held on the way out, so no answer is served
        from a listing that a write here, or a change no write here made, has
        already overtaken.
        """
        object.__setattr__(self, "_configuration", None)

    def _branch_config(self, branch: str) -> dict[str, str]:
        """Return ``branch``'s own configuration section, without its prefix."""
        prefix = f"branch.{branch}."
        return {
            key[len(prefix) :]: value
            for key, value in self._config_entries()
            if key.startswith(prefix)
        }

    def _recorded_branches(self) -> cabc.Iterator[str]:
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

    def _anchored_branches(self) -> cabc.Iterator[str]:
        """Yield branches whose anchor ref exists."""
        yield from self._refs_in(stack_records.BASE_NAMESPACE)

    def _tombstoned_branches(self) -> cabc.Iterator[str]:
        """Yield branches whose tombstone ref exists."""
        yield from self._refs_in(stack_records.TOMBSTONE_NAMESPACE)

    def _refs_in(self, namespace: str) -> cabc.Iterator[str]:
        """Yield the branch names recorded under ``namespace``."""
        prefix = f"{namespace}/"
        output = self.repo.git.for_each_ref(REF_NAME_FORMAT, prefix)
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
        return self.branch_tip(branch) is not None

    def _orphan_tip(self, branch: str) -> str | None:
        """Return the tip ``branch``'s orphaned record still carries, if any."""
        config = self._branch_config(branch)
        anchor = self._ref_value(stack_records.base_ref_path(branch))
        if not config and anchor is None:
            return None
        # Reconciliation is asked as though the branch still existed, because
        # the orphan state is the caller's list and what the sweep wants here
        # is the parsed record, not the orphan report.
        result = stack_records.reconcile(branch, config, anchor, branch_exists=True)
        match result:
            case stack_records.StackRecord(recorded_from=tip):
                return tip
        return None

    def _configured_expire(self) -> str:
        """Return the retention window as configured, or the default."""
        try:
            value = self.repo.git.config("--local", "--get", TOMBSTONE_EXPIRE_KEY)
        except GitCommandError as exc:
            if exc.status == _ABSENT_CONFIG_KEY:
                return stack_records.DEFAULT_TOMBSTONE_EXPIRE
            raise
        return str(value).strip()

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

    def _tombstone_timestamp(self, branch: str) -> int | None:
        """Return when ``branch``'s tombstone was written, if it can be read.

        Parameters
        ----------
        branch : str
            Branch whose tombstone's age is read.

        Returns
        -------
        int | None
            When it was written, or ``None`` when its reflog could not be read.

        Raises
        ------
        GitCommandError
            If Git refused the read with a status other than 128, which is what
            it exits with when it cannot resolve the ref it was handed.

        """
        ref = stack_records.tombstone_ref_path(branch)
        try:
            output = self.repo.git.reflog(
                "show", "--date=unix", "--format=%gd", "-1", ref
            )
        except GitCommandError as exc:
            if exc.status == _UNKNOWN_REVISION:
                return None
            raise
        match = _RELOG_ENTRY_TIME.search(output)
        return int(match.group("timestamp")) if match is not None else None
