"""Unit tests for the creating, refreshing, and entombing half of the store.

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
``test_stack_records.py``, and the interleavings of these operations in
``tests/integration/test_stack_record_lifecycle.py``. The answers a read gives
are in ``test_stack_store_reads.py``, what Git leaves behind when it refuses a
write in ``test_stack_store_refusals.py``, and the sweep and the prune — which
clear what a deleted branch left behind — in ``test_stack_store_clearing.py``.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import stack_records, stack_store, stack_writes
from tests.git_repo_helpers import config_section
from tests.unit.stack_store_helpers import (
    CHILD,
    NEIGHBOUR,
    OID_MATCHER,
    RENAMED,
    advance,
    anchor,
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
    """A stale expectation writes nothing, so a lost race is never silent.

    A refresh is the write a run makes after reading a record, and the read it
    made may already have been overtaken. Git's own compare-and-swap decides,
    so a caller that lost the race finds the record as the winner left it
    rather than overwriting it.
    """
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
