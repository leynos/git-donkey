"""What the store leaves behind when Git refuses one of its writes.

Git can refuse a write the store has already begun: a lock file left beside a
ref, or beside the configuration, makes an update or an unset fail after an
anchor has been published. Every case here plants the lock rather than
provoking it, because nothing short of another process can put Git in that
state. What each one asserts is the two things a caller depends on afterwards:
the state the refused call left, and which of the two errors it reported.

The writer's ordinary behaviour is in ``test_stack_store.py``, the answers a
read gives in ``test_stack_store_reads.py``, and the sweep and the prune — the
other two calls that clear what a deleted branch left behind — in
``test_stack_store_clearing.py``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from git import Git, GitCommandError

from git_donkey import stack_records, stack_store, stack_writes
from tests.git_repo_helpers import config_section
from tests.unit.stack_store_helpers import (
    CHILD,
    EXPIRE,
    advance,
    anchor,
    backdate_tombstone,
    make_record,
    make_repo,
    make_writer,
)

if typ.TYPE_CHECKING:
    from git import Repo


def _refuse_after_one_value(
    store: stack_writes.GitStackRecordWriter,
    record: stack_records.StackRecord,
    values: dict[stack_records.RecordKey, str],
) -> None:
    """Write the record's first value through Git, then refuse the rest.

    A value write that fails part way through is exactly what the store has to
    undo, and Git cannot be asked to fail on the second of four writes. One
    value is therefore written for real and the refusal raised after it, which
    leaves the state such a failure leaves: the anchor already published and
    the configuration incomplete.

    Parameters
    ----------
    store : stack_writes.GitStackRecordWriter
        The writer whose configuration write this stands in for.
    record : stack_records.StackRecord
        The record the refused write was writing.
    values : dict[stack_records.RecordKey, str]
        The values the refused write was handed, of which the first is written.

    Raises
    ------
    stack_store.StackRecordError
        Always, once the first value is written: this is the refusal the store
        is being asked to survive.

    """
    first = next(iter(values))
    store.repo.git.config(
        "--local", f"branch.{record.branch}.{first.value}", values[first]
    )
    msg = f"the store could not write the rest of {record.branch!r}"
    raise stack_store.StackRecordError(msg)


def _lock_ref(repo: Repo, ref: str) -> Path:
    """Leave the lock file Git takes before it moves ``ref``.

    A lock file that is already there is a writer that is mid-update, which is
    the one way a deletion of an existing ref is refused: Git will not move a
    ref whose lock it cannot take. Nothing short of another process can put Git
    in that state, so the file is planted rather than provoked.

    Parameters
    ----------
    repo : Repo
        Repository the ref belongs to.
    ref : str
        Full ref name whose lock is to be left behind.

    Returns
    -------
    Path
        The lock file that was written, for a test to remove or assert on.

    """
    lock = Path(repo.git_dir) / f"{ref}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")
    return lock


def _lock_configuration(repo: Repo) -> Path:
    """Leave the lock file Git takes before it writes the repository's config.

    Every configuration write and unset needs it, so a value Git is asked to
    clear while it is held is refused with a status of its own rather than with
    the absent-key status the store reads as success.

    Parameters
    ----------
    repo : Repo
        Repository whose configuration is to be left unlocked-to.

    Returns
    -------
    Path
        The lock file that was written, for a test to remove or assert on.

    """
    lock = Path(repo.git_dir) / "config.lock"
    lock.write_text("")
    return lock


def test_a_create_that_cannot_write_a_value_removes_what_it_wrote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create refused part way through leaves no record rather than half of one.

    The anchor is what makes a branch look recorded, and a reader that finds it
    beside an incomplete set of values reports a malformed record. That is
    worse than the state the call found, because the branch then looks stacked
    and is not, so the create takes back everything it wrote.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    monkeypatch.setattr(
        stack_writes.GitStackRecordWriter,
        "_write_configuration",
        _refuse_after_one_value,
    )

    with pytest.raises(stack_store.StackRecordError, match="could not write the rest"):
        make_writer(repo).create(make_record(CHILD, base))

    assert anchor(repo, CHILD) is None, "the anchor this call created is gone"
    assert not config_section(repo, CHILD), "and so is the value it wrote"
    assert isinstance(
        stack_store.GitStackRecordReader(repo).read(CHILD),
        stack_records.RecordAbsent,
    ), "so the branch reads back as having no record at all"


def test_a_repair_that_cannot_clear_a_value_still_reports_only_the_failed_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The undo is best effort, so its own refusal never replaces the write's.

    A lock another writer takes while a create is part-way through refuses the
    repair that follows it, because clearing a value needs the same lock the
    refused write did. The caller is told its write failed, which is the answer
    it can act on: a second error about a record its caller never asked to keep
    would replace that answer with a fact about the repair instead.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    real_config = repo.git.config

    def config_then_locked(_git: Git, *args: object, **kwargs: object) -> str:
        """Answer every Git configuration call, refusing each unset as locked."""
        if "--unset-all" in args:
            msg = "fatal: could not lock config file .git/config: File exists"
            raise GitCommandError(("git", "config"), 128, msg)
        return real_config(*args, **kwargs)

    monkeypatch.setattr(
        stack_writes.GitStackRecordWriter,
        "_write_configuration",
        _refuse_after_one_value,
    )
    monkeypatch.setattr(Git, "config", config_then_locked, raising=False)

    with pytest.raises(stack_store.StackRecordError, match="could not write the rest"):
        store.create(make_record(CHILD, base))

    assert anchor(repo, CHILD) is None, (
        "the anchor is still taken back, so the attempted repair did run"
    )
    assert config_section(repo, CHILD), (
        "and the value the repair could not clear is what it leaves behind: a "
        "record a reader reports as malformed, which is the state the failed "
        "write would have left had the repair not been attempted at all"
    )


def test_a_refresh_that_cannot_write_a_value_restores_the_record_it_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh refused part way through leaves the record it found.

    A refresh replaces the values a live branch's record already holds, so
    undoing one means putting those values back and moving the anchor back to
    the commit the caller read, never deleting the record: the branch keeps the
    boundary it was stacked at.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    before = config_section(repo, CHILD)
    moved = advance(repo)
    monkeypatch.setattr(
        stack_writes.GitStackRecordWriter,
        "_write_configuration",
        _refuse_after_one_value,
    )

    with pytest.raises(stack_store.StackRecordError, match="could not write the rest"):
        store.refresh(make_record(CHILD, moved), expected_old=base)

    assert anchor(repo, CHILD) == base, (
        "the anchor is back at the commit the caller expected, not the one the "
        "refused refresh wrote"
    )
    assert config_section(repo, CHILD) == before, (
        "and the values the refused refresh replaced are back"
    )
    assert store.read(CHILD) == make_record(CHILD, base), (
        "so the record reads back as the one the refresh found"
    )


def test_a_clear_that_cannot_unset_a_value_reports_the_store_error(
    tmp_path: Path,
) -> None:
    """A value Git refuses to unset is a record that could not be cleared.

    The refusal is a configuration lock held by another writer, which is the
    state a concurrent ``git config`` leaves behind. Git answers it with a
    status of its own, so the store must not read it as the absent key it also
    answers with: a caller deciding what to report needs to be told the record
    is still there, which is a fact about the record rather than about the
    command that failed to clear it.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    key = next(iter(stack_records.RecordKey))
    store.preserve_tip(CHILD, base)
    _lock_configuration(repo)

    with pytest.raises(stack_store.StackRecordError) as excinfo:
        store.clear_record(CHILD)

    message = str(excinfo.value)
    assert message.startswith(f"cannot unset 'branch.{CHILD}.{key.value}': "), (
        f"the refusal names the key Git would not clear, got: {message!r}"
    )
    assert "could not lock config file" in message, (
        "and carries Git's own explanation of why it would not"
    )
    assert isinstance(excinfo.value.__cause__, GitCommandError), (
        "the Git error is the cause, so a caller can still reach it"
    )
    assert store.tombstone(CHILD) == base, (
        "the tip was preserved before the values were reached, which is the "
        "order the record's strongest statement is written in"
    )
    assert config_section(repo, CHILD), (
        "and the record the run could not clear is still there"
    )


def test_a_prune_that_cannot_delete_a_tombstone_reports_the_store_error(
    tmp_path: Path,
) -> None:
    """A tombstone Git refuses to delete is a record that could not be cleared.

    The refusal is a lock file left beside the ref, which is a writer that is
    mid-update. Retention is repository-wide, so the caller stops the run on
    this refusal; the deletion is asked for as part of the record lifecycle and
    its failure has to arrive as that rather than as a bare Git command error.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.preserve_tip(CHILD, base)
    store.clear_record(CHILD)
    backdate_tombstone(repo, CHILD, days=100)
    ref = stack_records.tombstone_ref_path(CHILD)
    _lock_ref(repo, ref)

    with pytest.raises(stack_store.StackRecordError) as excinfo:
        store.prune(EXPIRE)

    message = str(excinfo.value)
    assert message.startswith(f"cannot delete {ref!r}: "), (
        f"the refusal names the ref Git would not delete, got: {message!r}"
    )
    assert "cannot lock ref" in message, (
        "and carries Git's own explanation of why it would not"
    )
    assert isinstance(excinfo.value.__cause__, GitCommandError), (
        "the Git error is the cause, so a caller can still reach it"
    )
    assert store.tombstone(CHILD) == base, (
        "the refused deletion is reported rather than passed over, so the "
        "tombstone it could not remove is still there"
    )
