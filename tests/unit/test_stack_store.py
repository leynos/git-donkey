"""Unit tests for the creating, sweeping, and pruning half of the store.

The store's contract is mostly Git's own behaviour, so every test here runs
against a real temporary repository rather than a double for ``Repo``. Whether
a configuration subsection keeps the case and the punctuation of a branch name,
whether a ref update can be made create-only, and whether deleting a branch
destroys its configuration section are all facts about Git; a Python double
would only assert the test author's belief about them. The namespace invariant
(INV-9) is checked the same way, by reading ``refs/stack-bases/`` and
``refs/heads/`` through Git and comparing the two sets, never by asking the
store under test.

The pure format and its reconciliation rules are covered in
``test_stack_records.py``; the interleavings of these operations are covered in
``tests/integration/test_stack_record_lifecycle.py``, and the answers the
reader gives, which these tests assert with, in ``test_stack_store_reads.py``.
"""

from __future__ import annotations

import time
import typing as typ
from pathlib import Path

import pytest
from git import Git, GitCommandError

from git_donkey import stack_records, stack_store, stack_writes
from tests.git_repo_helpers import config_section
from tests.unit.stack_store_helpers import (
    CHILD,
    EXPIRE,
    NEIGHBOUR,
    OID_MATCHER,
    RENAMED,
    advance,
    anchor,
    backdate_tombstone,
    branch_names_in,
    commit_on,
    delete_branch,
    delete_ref_surgically,
    make_record,
    make_repo,
    make_writer,
    namespace_violations,
    rename_branch,
    tombstone_log,
)

if typ.TYPE_CHECKING:
    from git import Repo
    from syrupy.assertion import SnapshotAssertion


def test_create_writes_the_anchor_and_the_four_canonical_keys(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """The stored record is the five artefacts the design document names."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)

    make_writer(repo).create(make_record(CHILD, base))

    assert anchor(repo, CHILD) == base, "the anchor keeps the boundary reachable"
    assert config_section(repo, CHILD) == snapshot(matcher=OID_MATCHER), (
        "exactly the four canonical lower-case keys are written"
    )
    assert namespace_violations(repo) == set(), "the branch exists, so INV-9 holds"


def test_create_refuses_a_second_record_and_changes_nothing(tmp_path: Path) -> None:
    """Create-only is what stops one writer overwriting another writer's record."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    moved = advance(repo)

    with pytest.raises(stack_store.StackRecordConflictError):
        store.create(make_record(CHILD, moved))

    assert anchor(repo, CHILD) == base, "the first writer's anchor survives"
    assert config_section(repo, CHILD)["stackbase"] == base, (
        "the first writer's values survive"
    )


def test_create_refuses_when_only_the_configuration_survives(tmp_path: Path) -> None:
    """A configuration without an anchor is a record, so it is not overwritten."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    repo.git.config("--local", f"branch.{CHILD}.stackBase", base)

    with pytest.raises(stack_store.StackRecordConflictError):
        make_writer(repo).create(make_record(CHILD, base))

    assert anchor(repo, CHILD) is None, "the refused create wrote no anchor"


def test_create_writes_nothing_when_the_record_does_not_round_trip(
    tmp_path: Path,
) -> None:
    """A record this package cannot read back is refused before any write."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)

    with pytest.raises(ValueError, match="round trip"):
        make_writer(repo).create(make_record(CHILD, "abc1234"))

    assert anchor(repo, CHILD) is None, "no anchor was written"
    assert not config_section(repo, CHILD), "no configuration was written"


def test_refresh_requires_the_value_the_caller_expected(tmp_path: Path) -> None:
    """A stale expectation writes nothing, so a lost race is never silent."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    moved = advance(repo)

    with pytest.raises(stack_store.StackRecordConflictError):
        store.refresh(make_record(CHILD, moved), expected_old=moved)

    assert anchor(repo, CHILD) == base, "the anchor still names the first boundary"
    assert config_section(repo, CHILD)["stackbase"] == base, (
        "the stored values are unchanged"
    )


def test_refresh_with_the_expected_value_updates_both_artefacts(
    tmp_path: Path,
) -> None:
    """The positive control that tells refusing apart from refusing correctly."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    moved = advance(repo)
    refreshed = make_record(CHILD, moved, tip=base)

    store.refresh(refreshed, expected_old=base)

    assert anchor(repo, CHILD) == moved, "the anchor names the refreshed boundary"
    assert config_section(repo, CHILD)["stackbase"] == moved, (
        "the configuration names the refreshed boundary"
    )
    assert store.read(CHILD) == refreshed, "the refreshed record reads back"


def test_refresh_with_an_empty_expectation_refuses_an_existing_anchor(
    tmp_path: Path,
) -> None:
    """An empty expectation means "no anchor", which is Git's own reading."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))

    with pytest.raises(stack_store.StackRecordConflictError):
        store.refresh(make_record(CHILD, base), expected_old="")


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


def test_entomb_preserves_the_tip_and_clears_the_live_record(tmp_path: Path) -> None:
    """The tombstone survives the branch it describes; the live record does not."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))

    store.entomb(CHILD, base)
    delete_branch(repo, CHILD, "-D")

    assert store.tombstone(CHILD) == base, "the tip is preserved for the children"
    assert store.read(CHILD) == stack_records.RecordAbsent(), (
        "the live record went with the branch"
    )
    assert namespace_violations(repo) == set(), (
        "entomb clears the anchor too, so INV-9 holds after a plonk deletion"
    )


def test_an_entomb_that_cannot_clear_a_value_reports_the_store_error(
    tmp_path: Path,
) -> None:
    """A value Git refuses to unset is a record that could not be cleared.

    The refusal is a configuration lock held by another writer, which is the
    state a concurrent ``git config`` leaves behind. Git answers it with a
    status of its own, so the store must not read it as the absent key it also
    answers with: a caller deciding whether to stop a run needs to be told the
    record is still there, which is a fact about the record rather than about
    the command that failed to clear it.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    key = next(iter(stack_records.RecordKey))
    _lock_configuration(repo)

    with pytest.raises(stack_store.StackRecordError) as excinfo:
        store.entomb(CHILD, base)

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


def test_entomb_records_the_tip_of_a_branch_with_no_record(tmp_path: Path) -> None:
    """A parent created from the trunk has no record, yet its tip matters."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)

    make_writer(repo).entomb(CHILD, base)

    assert make_writer(repo).tombstone(CHILD) == base, (
        "the common case is a branch with no record of its own"
    )


def test_creating_a_record_retires_a_tombstone_for_the_same_branch(
    tmp_path: Path,
) -> None:
    """The name is live again, so the old incarnation's tip must not answer."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.entomb(CHILD, base)

    store.create(make_record(CHILD, base))

    assert store.tombstone(CHILD) is None, (
        "a live record and a tombstone never describe the same branch"
    )
    assert isinstance(store.read(CHILD), stack_records.StackRecord), (
        "the branch has a live record again"
    )


def test_sweep_converts_an_orphan_into_a_tombstone(tmp_path: Path) -> None:
    """The recorded tip is the strongest statement that outlives the branch."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    moved = advance(repo)
    store.refresh(make_record(CHILD, base, tip=moved), expected_old=base)
    delete_ref_surgically(repo, CHILD)

    assert store.sweep(store.orphans()) == (CHILD,), "the orphan is converted"

    assert store.tombstone(CHILD) == moved, (
        "the tombstone names the recorded tip, not the boundary or the branch"
    )
    assert anchor(repo, CHILD) is None, "the anchor is removed"
    assert not config_section(repo, CHILD), "the configuration is removed"
    assert namespace_violations(repo) == set(), "INV-9 holds again after the sweep"
    assert not store.orphans(), "nothing is left to report"
    assert not store.sweep((CHILD,)), "sweeping a stale name changes nothing"


def test_sweep_clears_an_anchor_left_by_a_deletion_through_git(
    tmp_path: Path,
) -> None:
    """`git branch -d` takes the section with it, so only the anchor is left."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    delete_branch(repo, CHILD)

    assert store.orphans() == (CHILD,), "the anchor alone is still an orphan"
    assert not store.sweep(store.orphans()), "there is no recorded tip to preserve"

    assert store.tombstone(CHILD) is None, "no tombstone was invented"
    assert anchor(repo, CHILD) is None, "the anchor is removed all the same"
    assert namespace_violations(repo) == set(), "INV-9 holds again"


def test_sweep_keeps_a_tombstone_that_already_exists(tmp_path: Path) -> None:
    """The crash window between entomb's two writes is repaired, not relabelled."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    tip = commit_on(repo, CHILD)
    repo.git.update_ref("--create-reflog", stack_records.tombstone_ref_path(CHILD), tip)
    delete_ref_surgically(repo, CHILD)

    assert store.sweep(store.orphans()) == (CHILD,), "the orphan is cleared"

    assert store.tombstone(CHILD) == tip, "the tombstone it already had is kept"
    assert store.tombstone(CHILD) != base, (
        "the recorded boundary does not replace a tip already known"
    )
    assert not config_section(repo, CHILD), "the live record is cleared"


def test_a_tombstone_beside_a_live_branch_survives_a_sweep(tmp_path: Path) -> None:
    """The crash window inside ``entomb`` is left alone rather than tidied up.

    ``entomb`` writes the tombstone first and clears the record second, so an
    interruption leaves both, with the branch itself still there. Clearing that
    in the sweep would look symmetrical and would be wrong: the sweep resolves
    only records whose branch is gone, because a live branch's record is the
    only attestation of that branch's boundary, and clearing it would destroy
    the attestation while leaving the branch in place. The state is benign, and
    resolves itself when the branch is next selected for deletion, so a run
    reports it as neither an orphan nor anything to sweep.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    tip = commit_on(repo, CHILD)
    repo.git.update_ref("--create-reflog", stack_records.tombstone_ref_path(CHILD), tip)

    assert not store.orphans(), (
        "a branch that still exists is not an orphan, whatever else is written"
    )
    assert not store.sweep(store.orphans()), "so the sweep has nothing to convert"

    assert store.tombstone(CHILD) == tip, "the tombstone the crash left is kept"
    assert isinstance(store.read(CHILD), stack_records.StackRecord), (
        "the live record still attests the branch's boundary"
    )
    assert anchor(repo, CHILD) == base, "and its anchor still reaches that boundary"
    assert namespace_violations(repo) == set(), (
        "and the branch it belongs to is still there, so INV-9 never broke"
    )
    assert config_section(repo, CHILD), "nor was its record cleared"


def test_sweep_clears_an_unreadable_record_without_inventing_a_tombstone(
    tmp_path: Path,
) -> None:
    """A record that does not parse is cleared, never guessed at."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    repo.git.config("--local", f"branch.{CHILD}.stackBase", "abc1234")
    delete_ref_surgically(repo, CHILD)

    assert not store.sweep(store.orphans()), "nothing was converted"

    assert store.tombstone(CHILD) is None, "no tombstone was invented"
    assert anchor(repo, CHILD) is None, "the anchor is removed all the same"
    assert not config_section(repo, CHILD), "and so is the record that did not parse"
    assert namespace_violations(repo) == set(), "INV-9 holds again"


def test_prune_keeps_a_tombstone_inside_the_retention_window(tmp_path: Path) -> None:
    """The default window must not delete a tombstone written seconds ago."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.entomb(CHILD, base)

    assert not store.prune(EXPIRE), "a fresh tombstone is inside the ninety day window"
    assert store.tombstone(CHILD) == base, "the tip is still preserved"


def test_prune_deletes_a_tombstone_older_than_the_window(tmp_path: Path) -> None:
    """The positive control: an old tombstone really is removed."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.entomb(CHILD, base)
    backdate_tombstone(repo, CHILD, days=100)

    assert store.prune(EXPIRE) == (CHILD,), "a tombstone past the window is pruned"

    assert store.tombstone(CHILD) is None, "the ref no longer resolves"
    assert branch_names_in(repo, stack_records.TOMBSTONE_NAMESPACE) == set(), (
        "Git itself no longer lists the tombstone"
    )


def test_prune_deletes_only_the_tombstones_past_the_cutoff(tmp_path: Path) -> None:
    """Retention is per tombstone, so one old parent does not cost a fresh one."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    repo.git.branch(NEIGHBOUR, base)
    store = make_writer(repo)
    store.entomb(CHILD, base)
    store.entomb(NEIGHBOUR, base)
    backdate_tombstone(repo, NEIGHBOUR, days=100)

    assert store.prune(EXPIRE) == (NEIGHBOUR,), (
        "only the backdated tombstone is past the window"
    )

    assert store.tombstone(CHILD) == base, "the fresh tombstone survives"
    assert store.tombstone(NEIGHBOUR) is None, "the old tombstone is gone"


def test_prune_keeps_a_tombstone_whose_age_cannot_be_read(tmp_path: Path) -> None:
    """An unreadable timestamp shortens nothing; the ref is kept instead."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.entomb(CHILD, base)
    tombstone_log(repo, CHILD).unlink()
    future = str(int(time.time()) + 60)

    assert not store.prune(future), "a tombstone with no reflog cannot be aged"

    assert store.tombstone(CHILD) == base, "so it is retained rather than guessed at"


def test_prune_deletes_a_tombstone_written_before_an_explicit_instant(
    tmp_path: Path,
) -> None:
    """The comparison runs one way only, stated as an instant rather than a phrase."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.entomb(CHILD, base)
    future = str(int(time.time()) + 60)

    assert store.prune(future) == (CHILD,), (
        "a tombstone written before the cutoff is pruned"
    )


def test_prune_refuses_an_empty_expiry(tmp_path: Path) -> None:
    """An empty expression is a usage error, not a licence to delete everything."""
    repo = make_repo(tmp_path)

    with pytest.raises(ValueError, match="must not be empty"):
        make_writer(repo).prune("   ")


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
    store.entomb(CHILD, base)
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


def test_one_listing_answers_every_orphan_an_operation_is_asked_about(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rescuable report and a sweep each ask for the configuration once.

    The listing is the repository's whole configuration, so reading it per
    orphan costs a Git process for every record the repository holds, and a
    sweep pays it twice over: once for the report of what it would preserve and
    once for the write. Each operation lists it once, however many orphans it
    is handed. The counts below are of the calls Git was actually asked to
    make.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    store = make_writer(repo)
    for branch in (CHILD, NEIGHBOUR):
        repo.git.branch(branch, base)
        store.create(make_record(branch, base))
        delete_ref_surgically(repo, branch)
    orphans = store.orphans()
    assert orphans == (CHILD, NEIGHBOUR), "both records are orphans"
    listings: list[tuple[object, ...]] = []
    real_config = repo.git.config

    def counting_config(_git: Git, *args: object, **kwargs: object) -> str:
        """Answer every configuration call, counting the full listings."""
        if "--list" in args:
            listings.append(args)
        return real_config(*args, **kwargs)

    monkeypatch.setattr(Git, "config", counting_config, raising=False)

    assert store.rescuable(orphans) == (CHILD, NEIGHBOUR), "both tips survive"

    assert len(listings) == 1, (
        "the report lists the configuration once for both orphans rather than "
        "once for each of them"
    )
    listings.clear()

    assert store.sweep(orphans) == (CHILD, NEIGHBOUR), "both tips are preserved"

    assert len(listings) == 1, (
        "the sweep lists it once too, because every orphan is read before the "
        "first record is cleared"
    )


def test_a_read_after_a_write_is_not_answered_from_before_it(tmp_path: Path) -> None:
    """A write discards the listing, so the record it wrote is what reads back.

    The store reads before it writes — a create lists the configuration to
    refuse a record that is already there — so a listing that outlived the
    write would answer the read that follows with the state the create found
    rather than the one it left.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    record = make_record(CHILD, base)
    assert isinstance(store.read(CHILD), stack_records.RecordAbsent), (
        "the branch has no record, and reading it lists the configuration"
    )

    store.create(record)

    assert store.read(CHILD) == record, (
        "the record read back is the one the write made rather than the "
        "configuration as it stood before it"
    )


@pytest.mark.parametrize("branch", ["feature/child", "Feature/X", "release-1.2.3"])
def test_a_record_round_trips_whatever_shape_the_branch_name_has(
    tmp_path: Path, branch: str
) -> None:
    """A hierarchical or dotted name survives Git's configuration quoting."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(branch, base)
    store = make_writer(repo)
    record = make_record(branch, base)

    store.create(record)

    assert store.read(branch) == record, "the name round-trips through the store"
    assert not store.orphans(), "the configuration section is matched exactly"
    assert namespace_violations(repo) == set(), "INV-9 holds for the nested name"
    store.entomb(branch, base)
    assert store.tombstone(branch) == base, "the tombstone mirrors the same layout"


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("create-a-second-record", set()),
        ("refresh", set()),
        ("entomb-without-deleting", set()),
        ("delete-through-plonk", set()),
        ("delete-through-git", {CHILD}),
        ("delete-by-ref-surgery", {CHILD}),
        ("rename-through-git", {CHILD}),
        ("sweep-after-git-delete", set()),
        ("sweep-after-ref-surgery", set()),
    ],
)
def test_the_record_namespace_is_a_subset_of_the_branch_namespace(
    tmp_path: Path, operation: str, expected: set[str]
) -> None:
    """INV-9 after each operation, asserted against Git's own two namespaces."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))

    _perform(store, repo, base, operation)

    assert namespace_violations(repo) == expected, (
        "the record namespace is a subset of the branch namespace afterwards"
    )


def _perform(
    store: stack_writes.GitStackRecordWriter,
    repo: Repo,
    base: str,
    operation: str,
) -> None:
    """Apply one named lifecycle operation to the repository."""
    match operation:
        case "create-a-second-record":
            repo.git.branch(NEIGHBOUR, base)
            store.create(make_record(NEIGHBOUR, base))
        case "refresh":
            store.refresh(make_record(CHILD, base), expected_old=base)
        case "entomb-without-deleting":
            store.entomb(CHILD, base)
        case _:
            _perform_destructive(store, repo, base, operation)


def _perform_destructive(
    store: stack_writes.GitStackRecordWriter,
    repo: Repo,
    base: str,
    operation: str,
) -> None:
    """Apply one named operation that removes the child's branch or its ref."""
    match operation:
        case "delete-through-plonk":
            store.entomb(CHILD, base)
            delete_branch(repo, CHILD, "-D")
        case "delete-through-git":
            delete_branch(repo, CHILD)
        case "delete-by-ref-surgery":
            delete_ref_surgically(repo, CHILD)
        case "rename-through-git":
            rename_branch(repo, CHILD, RENAMED)
        case "sweep-after-git-delete":
            delete_branch(repo, CHILD)
            store.sweep(store.orphans())
        case "sweep-after-ref-surgery":
            delete_ref_surgically(repo, CHILD)
            store.sweep(store.orphans())
        case _:  # pragma: no cover - the parameter list is closed
            msg = f"unknown operation {operation!r}"
            raise AssertionError(msg)
