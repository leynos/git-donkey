"""Unit tests for the reading half of the stack record store.

Every command asks for a ``GitStackRecordReader`` and only writes need the
writer, so these tests pin the answers a read gives: the record a branch holds,
the anchor ref that record is kept reachable by, the absent and orphaned cases
around it, the orphans a sweep would act on, the tombstones a retention window
has reached, and the window itself. The writer sets the repository up rather
than doing the asserting; where a read's contract is stated as agreeing with a
destructive operation, that operation stands beside it as the control. A read
only a write could answer is a read the commands cannot make: ``git wheresat``
reads a record and its anchor back before it refreshes either, so both halves
have to be readable without a writer. Like the writer's suite, these tests run
against real temporary repositories: whether a configuration section survives a
deletion, and how a reflog records time, are facts about Git rather than beliefs
a double could hold.

The writer's effects are covered in ``test_stack_store.py``, which owns the
namespace invariant (INV-9) tying the two halves together, and the pure record
format in ``test_stack_records.py``. The sweep and the prune are in
``test_stack_store_clearing.py``, and what a write Git refuses leaves behind in
``test_stack_store_refusals.py``. The builders and Git-state helpers every
suite shares live in ``stack_store_helpers``.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import stack_records, stack_store
from tests.unit.stack_store_helpers import (
    CHILD,
    EXPIRE,
    NEIGHBOUR,
    RENAMED,
    anchor,
    backdate_tombstone,
    delete_branch,
    delete_ref_surgically,
    make_record,
    make_repo,
    make_writer,
    namespace_violations,
    rename_branch,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_a_created_record_reads_back_as_the_record_that_was_written(
    tmp_path: Path,
) -> None:
    """A write that did not read back would make every later answer a guess."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    record = make_record(CHILD, base)

    make_writer(repo).create(record)

    assert make_writer(repo).read(CHILD) == record, (
        "reading the stored record returns the record that was written"
    )


def test_a_record_whose_branch_is_gone_reads_as_orphaned(tmp_path: Path) -> None:
    """A record outliving its branch is reported, not silently trusted."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    make_writer(repo).create(make_record(CHILD, base))

    delete_branch(repo, CHILD)

    assert isinstance(make_writer(repo).read(CHILD), stack_records.RecordOrphaned), (
        "the branch is gone, so its record cannot be used as evidence"
    )
    assert namespace_violations(repo) == {CHILD}, (
        "a deletion through Git alone leaves the record namespace ahead"
    )


def test_renaming_a_branch_carries_the_record_and_orphans_the_anchor(
    tmp_path: Path,
) -> None:
    """AXIOM-11: ``git branch -m`` carries the section and leaves the anchor."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))

    rename_branch(repo, CHILD, RENAMED)

    carried = store.read(RENAMED)
    assert isinstance(carried, stack_records.StackRecord), (
        "the configuration is authoritative, so the record moved with the branch"
    )
    assert carried.branch == RENAMED, "the record names the branch it was read for"
    assert carried.base == base, "the boundary is unchanged by the rename"
    assert anchor(repo, CHILD) == base, "the anchor of the old name is left behind"
    assert isinstance(store.read(CHILD), stack_records.RecordOrphaned), (
        "the name the anchor still stands for no longer exists"
    )
    assert store.orphans() == (CHILD,), (
        "so the anchor of the old name is the record that outlived its branch"
    )


def test_a_branch_with_no_record_reads_as_absent(tmp_path: Path) -> None:
    """Absence is the ordinary state, so it is a result rather than an error."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(NEIGHBOUR, base)

    assert make_writer(repo).read(NEIGHBOUR) == stack_records.RecordAbsent(), (
        "a branch nobody recorded has no record"
    )


def test_orphans_reports_records_whose_branch_is_gone(tmp_path: Path) -> None:
    """Only the record whose branch has gone is an INV-9 violation."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    repo.git.branch(NEIGHBOUR, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    store.create(make_record(NEIGHBOUR, base))

    delete_branch(repo, CHILD)

    assert store.orphans() == (CHILD,), "the surviving branch is not an orphan"


def test_orphans_finds_a_record_that_survives_only_in_the_configuration(
    tmp_path: Path,
) -> None:
    """A record whose anchor has gone is still reported once its branch goes."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    repo.git.update_ref("-d", stack_records.base_ref_path(CHILD))
    delete_ref_surgically(repo, CHILD)

    assert store.orphans() == (CHILD,), (
        "the configuration alone is enough to leave a record behind"
    )


def test_a_tombstone_is_not_an_orphan(tmp_path: Path) -> None:
    """A tombstone outlives its branch by design, so it is not a violation."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.preserve_tip(CHILD, base)
    delete_branch(repo, CHILD, "-D")
    store.clear_record(CHILD)

    assert not store.orphans(), "a tombstone is not a record with a missing branch"


def test_expired_reports_a_tombstone_past_the_window_without_deleting_it(
    tmp_path: Path,
) -> None:
    """The report a dry run prints is a read: it must leave the ref alone."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.preserve_tip(CHILD, base)
    store.clear_record(CHILD)
    backdate_tombstone(repo, CHILD, days=100)

    assert store.expired(EXPIRE) == (CHILD,), "the tombstone is past the window"

    assert store.tombstone(CHILD) == base, "reporting it does not delete it"

    assert store.prune(EXPIRE) == (CHILD,), "and pruning still deletes it"


def test_a_tombstone_that_vanished_before_it_was_read_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ref taken between the listing and the read is kept, not raised over.

    The sweep lists the tombstones and then reads each one's age, and another
    process can delete a ref between those two reads. Git then refuses the
    reflog rather than answering with an empty one, and a branch whose age
    cannot be read is one whose window cannot be said to have passed.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    store = make_writer(repo)
    store.preserve_tip(CHILD, base)
    store.clear_record(CHILD)
    backdate_tombstone(repo, CHILD, days=100)
    monkeypatch.setattr(
        stack_store.GitStackRecordReader,
        "_tombstoned_branches",
        lambda _self: iter((CHILD,)),
    )
    repo.git.update_ref("-d", stack_records.tombstone_ref_path(CHILD))

    assert not store.expired(EXPIRE), (
        "a tombstone whose age could not be read is not reported as past it"
    )


def test_expired_agrees_with_prune_on_every_tombstone(tmp_path: Path) -> None:
    """Two ways of asking one question must not answer it differently."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    repo.git.branch(NEIGHBOUR, base)
    store = make_writer(repo)
    store.preserve_tip(CHILD, base)
    store.clear_record(CHILD)
    store.preserve_tip(NEIGHBOUR, base)
    store.clear_record(NEIGHBOUR)
    backdate_tombstone(repo, NEIGHBOUR, days=100)

    reported = store.expired(EXPIRE)

    assert reported == store.prune(EXPIRE), (
        "what the report names is what the prune deletes"
    )
    assert reported == (NEIGHBOUR,), "only the backdated tombstone is past it"


def test_rescuable_reports_the_orphan_whose_record_still_parses(
    tmp_path: Path,
) -> None:
    """A dry run's rescue claim is drawn from the record, never from the anchor."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    repo.git.branch(NEIGHBOUR, base)
    store = make_writer(repo)
    store.create(make_record(CHILD, base))
    store.create(make_record(NEIGHBOUR, base))
    delete_ref_surgically(repo, CHILD)
    delete_branch(repo, NEIGHBOUR)

    orphans = store.orphans()

    assert orphans == (CHILD, NEIGHBOUR), "both records outlived their branch"

    rescued = store.rescuable(orphans)

    assert rescued == (CHILD,), "only the record Git alone spared carries a tip"
    assert store.orphans() == orphans, "classifying an orphan repairs nothing"
    # The difference the summary reports is this pair of answers, so it is the
    # difference the sweep itself must act on.
    assert [branch for branch in orphans if branch not in rescued] == [NEIGHBOUR], (
        "the orphan whose tip could not be preserved is the one left over"
    )
    assert store.sweep(orphans) == rescued, "the sweep rescues exactly those"

    assert store.tombstone(CHILD) == base, "the rescued tip is preserved"
    assert store.tombstone(NEIGHBOUR) is None, (
        "no tip is invented for the record Git destroyed"
    )
    assert namespace_violations(repo) == set(), "INV-9 holds after both"


def test_the_reader_answers_every_read_the_store_declares(tmp_path: Path) -> None:
    """Reads belong to the reader: the writer must not be their only holder.

    Every command asks for a ``StackRecordReader`` and only writes need the
    writer, so a read implemented on the writer alone is a read the commands
    cannot make: a refresh reads the record and its anchor back before it
    replaces either, and a branch with no record has to be readable as having
    none rather than as a failure. The doubles cannot catch this, because a
    double answers whatever it declares.
    """
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(CHILD, base)
    repo.git.branch(NEIGHBOUR, base)
    store = make_writer(repo)
    reader = stack_store.GitStackRecordReader(repo)

    assert reader.anchor(CHILD) is None, (
        "a branch with no record has no anchor ref to name a commit"
    )

    store.create(make_record(CHILD, base))
    store.create(make_record(NEIGHBOUR, base))

    assert reader.anchor(CHILD) == base, (
        "the reader names the commit the record's anchor ref holds"
    )
    delete_ref_surgically(repo, CHILD)
    delete_branch(repo, NEIGHBOUR)

    assert reader.orphans() == (CHILD, NEIGHBOUR), "the reader finds both orphans"
    assert reader.rescuable(reader.orphans()) == (CHILD,), (
        "the reader classifies an orphan by the record it still carries"
    )
    assert reader.branch_tip(CHILD) is None, "a deleted branch has no tip to read"

    store.preserve_tip(CHILD, base)
    store.clear_record(CHILD)
    backdate_tombstone(repo, CHILD, days=100)

    assert reader.tombstone(CHILD) == base, "the reader reads the tombstone"
    assert reader.read(CHILD) == stack_records.RecordAbsent(), (
        "the reader reports no live record once the record is a tombstone"
    )
    assert reader.expiry() == stack_records.DEFAULT_TOMBSTONE_EXPIRE, (
        "the reader resolves the configured window, which is unset here"
    )
    assert reader.expired(reader.expiry()) == (CHILD,), (
        "the reader ages a tombstone against the window it read"
    )


@pytest.mark.parametrize("branch", ["--upload-pack=x", "main~1", "a..b"])
def test_a_branch_tip_read_refuses_a_name_unsafe_in_a_ref_path(
    tmp_path: Path, branch: str
) -> None:
    """A name that would reach Git as an option or a revision is refused first.

    ``branch_tip`` assembles ``refs/heads/<branch>`` from the caller's name, so
    the name is validated before the ref is built: a caller either gets a commit
    or a refusal, and Git is never asked a question the name made up. The three
    names here are the three the validator refuses and ``rev-parse`` would
    otherwise act on — one Git reads as an option, and two it reads as an
    expression about some other commit.
    """
    reader = stack_store.GitStackRecordReader(make_repo(tmp_path))

    with pytest.raises(ValueError, match="invalid ref path component"):
        reader.branch_tip(branch)


def test_expiry_defaults_to_the_documented_window(tmp_path: Path) -> None:
    """An unset key is not an error: it is the ninety day window."""
    repo = make_repo(tmp_path)

    assert make_writer(repo).expiry() == stack_records.DEFAULT_TOMBSTONE_EXPIRE, (
        "an unset key answers with the window the design documents"
    )


def test_expiry_reads_the_configured_window(tmp_path: Path) -> None:
    """A configured window is the one pruned by, spelled as the user spelled it."""
    repo = make_repo(tmp_path)
    repo.git.config("--local", stack_store.TOMBSTONE_EXPIRE_KEY, "30.days.ago")

    assert make_writer(repo).expiry() == "30.days.ago", (
        "the configured window is returned as the user spelled it"
    )


@pytest.mark.parametrize("value", ["", "   ", "0", "banana", "a.b.c", "now"])
def test_expiry_refuses_a_window_that_is_not_in_the_past(
    tmp_path: Path, value: str
) -> None:
    """Git reads what it cannot parse as now, and a window of no length prunes all."""
    repo = make_repo(tmp_path)
    repo.git.config("--local", stack_store.TOMBSTONE_EXPIRE_KEY, value)

    with pytest.raises(ValueError, match=stack_store.TOMBSTONE_EXPIRE_KEY):
        make_writer(repo).expiry()


def test_orphans_finds_a_record_stored_under_a_dotted_branch_name(
    tmp_path: Path,
) -> None:
    """The configuration key's last dot is not where the branch name ends."""
    repo = make_repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch("release-1.2.3", base)
    store = make_writer(repo)
    store.create(make_record("release-1.2.3", base))
    delete_branch(repo, "release-1.2.3")

    assert store.orphans() == ("release-1.2.3",), (
        "a dotted branch name is recognized in the configuration"
    )
