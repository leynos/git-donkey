"""Unit tests for the sweep and the prune, and for the listing they share.

Both operations clear what a deleted branch left behind. A sweep resolves
orphans — records whose branch is gone — and converts each one whose recorded
tip is still readable into a tombstone; a prune retires tombstones past the
repository's retention window. Neither invents a fact: an orphan with no
readable tip is cleared without a tombstone, and a tombstone whose age cannot
be read is kept rather than guessed at.

The listing case is here because it is about these operations rather than
about the records they clear. A report over every orphan, and the sweep that
follows it, each ask Git for the repository's configuration once however many
orphans they are handed, which is only observable when an operation is given
more than one.

The writer's ordinary behaviour is in ``test_stack_store.py``, the answers a
read gives in ``test_stack_store_reads.py``, and what each of these operations
leaves behind when Git refuses it in ``test_stack_store_refusals.py``.
"""

from __future__ import annotations

import time
import typing as typ

import pytest
from git import Git

from git_donkey import stack_records
from tests.git_repo_helpers import config_section
from tests.unit.stack_store_helpers import (
    CHILD,
    EXPIRE,
    NEIGHBOUR,
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
    tombstone_log,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


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
