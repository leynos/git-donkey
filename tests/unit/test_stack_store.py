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

import pytest

from git_donkey import stack_records, stack_store
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
    config_section,
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
    from pathlib import Path

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
    assert config_section(repo, CHILD) == {}, "no configuration was written"


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
    assert config_section(repo, CHILD) == {}, "the configuration is removed"
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
    assert config_section(repo, CHILD) == {}, "the live record is cleared"


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
    assert config_section(repo, CHILD) == {}, "and so is the record that did not parse"
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
    store: stack_store.GitStackRecordWriter,
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
    store: stack_store.GitStackRecordWriter,
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
