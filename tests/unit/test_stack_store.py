"""Unit tests for the store that reads and writes the shared stack record.

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
``tests/integration/test_stack_record_lifecycle.py``.
"""

from __future__ import annotations

import re
import time
import typing as typ
from pathlib import Path

import pytest
from git import GitCommandError, Repo
from syrupy.matchers import path_type

from git_donkey import stack_records, stack_store
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_TRUNK = "main"
_PARENT = stack_records.StackParent(branch="parent", pull_request=None)
_CHILD = "child"
_NEIGHBOUR = "neighbour"
_RENAMED = "renamed"

# The retention window a caller resolves before it reaches the store. The
# store takes whatever expiry expression it is handed, so the value here is
# the documented default rather than a constant the store owns.
_EXPIRE = "90.days.ago"


def _redact_object_ids(data: str, _: object) -> str:
    """Rewrite full object IDs so a snapshot does not pin a commit hash.

    Parameters
    ----------
    data : str
        The value the snapshot captured.
    _ : object
        The syrupy match, unused by this replacer.

    Returns
    -------
    str
        ``data`` with every 40-character hexadecimal object ID replaced by an
        ``<OID>`` placeholder. The seed commit's hash is derived partly from
        the clock, so a snapshot holding it raw would only ever match the run
        that recorded it.

    """
    return re.sub(r"\b[0-9a-f]{40}\b", "<OID>", data)


# Redact object IDs at record time, whatever path they sit at.
_OID_MATCHER = path_type(types=(str,), replacer=_redact_object_ids)


def _repo(tmp_path: Path) -> Repo:
    """Return a repository holding one seed commit checked out on the trunk."""
    return git_repo_helpers.seed_repo(tmp_path / "repo", branch=_TRUNK)


def _writer(repo: Repo) -> stack_store.GitStackRecordWriter:
    """Return a writer for ``repo``."""
    return stack_store.GitStackRecordWriter(repo)


def _record(
    branch: str, base: str, *, tip: str | None = None
) -> stack_records.StackRecord:
    """Return a record for ``branch`` whose exclusive boundary is ``base``."""
    return stack_records.StackRecord(
        branch=branch,
        parent=_PARENT,
        base=base,
        recorded_from=base if tip is None else tip,
        evidence=stack_records.EVIDENCE_BIRTH,
    )


def _advance(repo: Repo) -> str:
    """Commit an empty change on the trunk and return the new commit's ID."""
    return git_repo_helpers.advance(repo, message="Advance the trunk")


def _delete_branch(repo: Repo, branch: str, *flags: str) -> None:
    """Delete ``branch`` through Git alone, bypassing the store entirely.

    ``git branch -d`` removes the branch's configuration section along with
    the branch, so the record survives only as its anchor.
    """
    repo.git.branch(*(flags or ("-d",)), branch)


def _delete_ref_surgically(repo: Repo, branch: str) -> None:
    """Delete the branch ref alone, leaving its configuration section behind."""
    repo.git.update_ref("-d", f"refs/heads/{branch}")


def _rename_branch(repo: Repo, branch: str, renamed: str) -> None:
    """Rename ``branch``, which carries its section and leaves its anchor."""
    repo.git.branch("-m", branch, renamed)


def _commit_on(repo: Repo, branch: str) -> str:
    """Commit an empty change on ``branch`` and return the new commit's ID."""
    return git_repo_helpers.commit_on(repo, branch)


def _config_section(repo: Repo, branch: str) -> dict[str, str]:
    """Return ``branch``'s configuration section, read from Git directly."""
    prefix = f"branch.{branch}."
    return {
        key[len(prefix) :]: value
        for entry in repo.git.config("--local", "--list", "-z").split("\0")
        if entry
        for key, _, value in (entry.partition("\n"),)
        if key.startswith(prefix)
    }


def _ref_value(repo: Repo, ref: str) -> str | None:
    """Return the commit ``ref`` names, or ``None`` when it does not exist."""
    try:
        return str(repo.git.rev_parse("--verify", "--quiet", ref))
    except GitCommandError:
        return None


def _branch_names_in(repo: Repo, namespace: str) -> set[str]:
    """Return every branch named by a ref under ``namespace``."""
    prefix = f"{namespace}/"
    output = repo.git.for_each_ref("--format=%(refname)", prefix)
    return {
        line[len(prefix) :] for line in output.splitlines() if line.startswith(prefix)
    }


def _anchor(repo: Repo, branch: str) -> str | None:
    """Return the commit the anchor ref names, if the branch has one."""
    return _ref_value(repo, stack_records.base_ref_path(branch))


def _branches(repo: Repo) -> set[str]:
    """Return every local branch name."""
    return {head.name for head in repo.heads}


def _namespace_violations(repo: Repo) -> set[str]:
    """Return the records whose branch does not exist, which is INV-9 broken."""
    return _branch_names_in(repo, stack_records.BASE_NAMESPACE) - _branches(repo)


def _tombstone_log(repo: Repo, branch: str) -> Path:
    """Return the path of ``branch``'s tombstone reflog."""
    return Path(repo.git_dir) / "logs" / "refs" / "stack-tombstones" / branch


def _backdate_tombstone(repo: Repo, branch: str, *, days: int) -> None:
    """Rewrite a tombstone's reflog so the tombstone reads as ``days`` old."""
    tip = str(repo.git.rev_parse(stack_records.tombstone_ref_path(branch)))
    when = int(time.time()) - days * 24 * 60 * 60
    line = f"{tip} {tip} Test User <test@example.com> {when} +0000\tupdate-ref: seed\n"
    _tombstone_log(repo, branch).write_text(line)


def test_create_writes_the_anchor_and_the_four_canonical_keys(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """The stored record is the five artefacts the design document names."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)

    _writer(repo).create(_record(_CHILD, base))

    assert _anchor(repo, _CHILD) == base, "the anchor keeps the boundary reachable"
    assert _config_section(repo, _CHILD) == snapshot(matcher=_OID_MATCHER), (
        "exactly the four canonical lower-case keys are written"
    )
    assert _namespace_violations(repo) == set(), "the branch exists, so INV-9 holds"


def test_a_created_record_reads_back_as_the_record_that_was_written(
    tmp_path: Path,
) -> None:
    """A write that did not read back would make every later answer a guess."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    record = _record(_CHILD, base)

    _writer(repo).create(record)

    assert _writer(repo).read(_CHILD) == record, (
        "reading the stored record returns the record that was written"
    )


def test_a_record_whose_branch_is_gone_reads_as_orphaned(tmp_path: Path) -> None:
    """A record outliving its branch is reported, not silently trusted."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    _writer(repo).create(_record(_CHILD, base))

    _delete_branch(repo, _CHILD)

    assert isinstance(_writer(repo).read(_CHILD), stack_records.RecordOrphaned), (
        "the branch is gone, so its record cannot be used as evidence"
    )
    assert _namespace_violations(repo) == {_CHILD}, (
        "a deletion through Git alone leaves the record namespace ahead"
    )


def test_renaming_a_branch_carries_the_record_and_orphans_the_anchor(
    tmp_path: Path,
) -> None:
    """AXIOM-11: ``git branch -m`` carries the section and leaves the anchor."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))

    _rename_branch(repo, _CHILD, _RENAMED)

    carried = store.read(_RENAMED)
    assert isinstance(carried, stack_records.StackRecord), (
        "the configuration is authoritative, so the record moved with the branch"
    )
    assert carried.branch == _RENAMED, "the record names the branch it was read for"
    assert carried.base == base, "the boundary is unchanged by the rename"
    assert _anchor(repo, _CHILD) == base, "the anchor of the old name is left behind"
    assert isinstance(store.read(_CHILD), stack_records.RecordOrphaned), (
        "the name the anchor still stands for no longer exists"
    )
    assert store.orphans() == (_CHILD,), (
        "so the anchor of the old name is the record that outlived its branch"
    )


def test_a_branch_with_no_record_reads_as_absent(tmp_path: Path) -> None:
    """Absence is the ordinary state, so it is a result rather than an error."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_NEIGHBOUR, base)

    assert _writer(repo).read(_NEIGHBOUR) == stack_records.RecordAbsent(), (
        "a branch nobody recorded has no record"
    )


def test_create_refuses_a_second_record_and_changes_nothing(tmp_path: Path) -> None:
    """Create-only is what stops one writer overwriting another writer's record."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    moved = _advance(repo)

    with pytest.raises(stack_store.StackRecordConflictError):
        store.create(_record(_CHILD, moved))

    assert _anchor(repo, _CHILD) == base, "the first writer's anchor survives"
    assert _config_section(repo, _CHILD)["stackbase"] == base, (
        "the first writer's values survive"
    )


def test_create_refuses_when_only_the_configuration_survives(tmp_path: Path) -> None:
    """A configuration without an anchor is a record, so it is not overwritten."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    repo.git.config("--local", f"branch.{_CHILD}.stackBase", base)

    with pytest.raises(stack_store.StackRecordConflictError):
        _writer(repo).create(_record(_CHILD, base))

    assert _anchor(repo, _CHILD) is None, "the refused create wrote no anchor"


def test_create_writes_nothing_when_the_record_does_not_round_trip(
    tmp_path: Path,
) -> None:
    """A record this package cannot read back is refused before any write."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)

    with pytest.raises(ValueError, match="round trip"):
        _writer(repo).create(_record(_CHILD, "abc1234"))

    assert _anchor(repo, _CHILD) is None, "no anchor was written"
    assert _config_section(repo, _CHILD) == {}, "no configuration was written"


def test_refresh_requires_the_value_the_caller_expected(tmp_path: Path) -> None:
    """A stale expectation writes nothing, so a lost race is never silent."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    moved = _advance(repo)

    with pytest.raises(stack_store.StackRecordConflictError):
        store.refresh(_record(_CHILD, moved), expected_old=moved)

    assert _anchor(repo, _CHILD) == base, "the anchor still names the first boundary"
    assert _config_section(repo, _CHILD)["stackbase"] == base, (
        "the stored values are unchanged"
    )


def test_refresh_with_the_expected_value_updates_both_artefacts(
    tmp_path: Path,
) -> None:
    """The positive control that tells refusing apart from refusing correctly."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    moved = _advance(repo)
    refreshed = _record(_CHILD, moved, tip=base)

    store.refresh(refreshed, expected_old=base)

    assert _anchor(repo, _CHILD) == moved, "the anchor names the refreshed boundary"
    assert _config_section(repo, _CHILD)["stackbase"] == moved, (
        "the configuration names the refreshed boundary"
    )
    assert store.read(_CHILD) == refreshed, "the refreshed record reads back"


def test_refresh_with_an_empty_expectation_refuses_an_existing_anchor(
    tmp_path: Path,
) -> None:
    """An empty expectation means "no anchor", which is Git's own reading."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))

    with pytest.raises(stack_store.StackRecordConflictError):
        store.refresh(_record(_CHILD, base), expected_old="")


def test_entomb_preserves_the_tip_and_clears_the_live_record(tmp_path: Path) -> None:
    """The tombstone survives the branch it describes; the live record does not."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))

    store.entomb(_CHILD, base)
    _delete_branch(repo, _CHILD, "-D")

    assert store.tombstone(_CHILD) == base, "the tip is preserved for the children"
    assert store.read(_CHILD) == stack_records.RecordAbsent(), (
        "the live record went with the branch"
    )
    assert _namespace_violations(repo) == set(), (
        "entomb clears the anchor too, so INV-9 holds after a plonk deletion"
    )


def test_entomb_records_the_tip_of_a_branch_with_no_record(tmp_path: Path) -> None:
    """A parent created from the trunk has no record, yet its tip matters."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)

    _writer(repo).entomb(_CHILD, base)

    assert _writer(repo).tombstone(_CHILD) == base, (
        "the common case is a branch with no record of its own"
    )


def test_creating_a_record_retires_a_tombstone_for_the_same_branch(
    tmp_path: Path,
) -> None:
    """The name is live again, so the old incarnation's tip must not answer."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.entomb(_CHILD, base)

    store.create(_record(_CHILD, base))

    assert store.tombstone(_CHILD) is None, (
        "a live record and a tombstone never describe the same branch"
    )
    assert isinstance(store.read(_CHILD), stack_records.StackRecord), (
        "the branch has a live record again"
    )


def test_orphans_reports_records_whose_branch_is_gone(tmp_path: Path) -> None:
    """Only the record whose branch has gone is an INV-9 violation."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    repo.git.branch(_NEIGHBOUR, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    store.create(_record(_NEIGHBOUR, base))

    _delete_branch(repo, _CHILD)

    assert store.orphans() == (_CHILD,), "the surviving branch is not an orphan"


def test_orphans_finds_a_record_that_survives_only_in_the_configuration(
    tmp_path: Path,
) -> None:
    """A record whose anchor has gone is still reported once its branch goes."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    repo.git.update_ref("-d", stack_records.base_ref_path(_CHILD))
    _delete_ref_surgically(repo, _CHILD)

    assert store.orphans() == (_CHILD,), (
        "the configuration alone is enough to leave a record behind"
    )


def test_a_tombstone_is_not_an_orphan(tmp_path: Path) -> None:
    """A tombstone outlives its branch by design, so it is not a violation."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.entomb(_CHILD, base)
    _delete_branch(repo, _CHILD, "-D")

    assert not store.orphans(), "a tombstone is not a record with a missing branch"


def test_sweep_converts_an_orphan_into_a_tombstone(tmp_path: Path) -> None:
    """The recorded tip is the strongest statement that outlives the branch."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    moved = _advance(repo)
    store.refresh(_record(_CHILD, base, tip=moved), expected_old=base)
    _delete_ref_surgically(repo, _CHILD)

    assert store.sweep(store.orphans()) == (_CHILD,), "the orphan is converted"

    assert store.tombstone(_CHILD) == moved, (
        "the tombstone names the recorded tip, not the boundary or the branch"
    )
    assert _anchor(repo, _CHILD) is None, "the anchor is removed"
    assert _config_section(repo, _CHILD) == {}, "the configuration is removed"
    assert _namespace_violations(repo) == set(), "INV-9 holds again after the sweep"
    assert not store.orphans(), "nothing is left to report"
    assert not store.sweep((_CHILD,)), "sweeping a stale name changes nothing"


def test_sweep_clears_an_anchor_left_by_a_deletion_through_git(
    tmp_path: Path,
) -> None:
    """`git branch -d` takes the section with it, so only the anchor is left."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    _delete_branch(repo, _CHILD)

    assert store.orphans() == (_CHILD,), "the anchor alone is still an orphan"
    assert not store.sweep(store.orphans()), "there is no recorded tip to preserve"

    assert store.tombstone(_CHILD) is None, "no tombstone was invented"
    assert _anchor(repo, _CHILD) is None, "the anchor is removed all the same"
    assert _namespace_violations(repo) == set(), "INV-9 holds again"


def test_sweep_keeps_a_tombstone_that_already_exists(tmp_path: Path) -> None:
    """The crash window between entomb's two writes is repaired, not relabelled."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    tip = _commit_on(repo, _CHILD)
    repo.git.update_ref(
        "--create-reflog", stack_records.tombstone_ref_path(_CHILD), tip
    )
    _delete_ref_surgically(repo, _CHILD)

    assert store.sweep(store.orphans()) == (_CHILD,), "the orphan is cleared"

    assert store.tombstone(_CHILD) == tip, "the tombstone it already had is kept"
    assert store.tombstone(_CHILD) != base, (
        "the recorded boundary does not replace a tip already known"
    )
    assert _config_section(repo, _CHILD) == {}, "the live record is cleared"


def test_sweep_clears_an_unreadable_record_without_inventing_a_tombstone(
    tmp_path: Path,
) -> None:
    """A record that does not parse is cleared, never guessed at."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))
    repo.git.config("--local", f"branch.{_CHILD}.stackBase", "abc1234")
    _delete_ref_surgically(repo, _CHILD)

    assert not store.sweep(store.orphans()), "nothing was converted"

    assert store.tombstone(_CHILD) is None, "no tombstone was invented"
    assert _anchor(repo, _CHILD) is None, "the anchor is removed all the same"
    assert _config_section(repo, _CHILD) == {}, (
        "and so is the record that did not parse"
    )
    assert _namespace_violations(repo) == set(), "INV-9 holds again"


def test_prune_keeps_a_tombstone_inside_the_retention_window(tmp_path: Path) -> None:
    """The default window must not delete a tombstone written seconds ago."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.entomb(_CHILD, base)

    assert not store.prune(_EXPIRE), "a fresh tombstone is inside the ninety day window"
    assert store.tombstone(_CHILD) == base, "the tip is still preserved"


def test_prune_deletes_a_tombstone_older_than_the_window(tmp_path: Path) -> None:
    """The positive control: an old tombstone really is removed."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.entomb(_CHILD, base)
    _backdate_tombstone(repo, _CHILD, days=100)

    assert store.prune(_EXPIRE) == (_CHILD,), "a tombstone past the window is pruned"

    assert store.tombstone(_CHILD) is None, "the ref no longer resolves"
    assert _branch_names_in(repo, stack_records.TOMBSTONE_NAMESPACE) == set(), (
        "Git itself no longer lists the tombstone"
    )


def test_prune_deletes_only_the_tombstones_past_the_cutoff(tmp_path: Path) -> None:
    """Retention is per tombstone, so one old parent does not cost a fresh one."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    repo.git.branch(_NEIGHBOUR, base)
    store = _writer(repo)
    store.entomb(_CHILD, base)
    store.entomb(_NEIGHBOUR, base)
    _backdate_tombstone(repo, _NEIGHBOUR, days=100)

    assert store.prune(_EXPIRE) == (_NEIGHBOUR,), (
        "only the backdated tombstone is past the window"
    )

    assert store.tombstone(_CHILD) == base, "the fresh tombstone survives"
    assert store.tombstone(_NEIGHBOUR) is None, "the old tombstone is gone"


def test_prune_keeps_a_tombstone_whose_age_cannot_be_read(tmp_path: Path) -> None:
    """An unreadable timestamp shortens nothing; the ref is kept instead."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.entomb(_CHILD, base)
    _tombstone_log(repo, _CHILD).unlink()
    future = str(int(time.time()) + 60)

    assert not store.prune(future), "a tombstone with no reflog cannot be aged"

    assert store.tombstone(_CHILD) == base, "so it is retained rather than guessed at"


def test_prune_deletes_a_tombstone_written_before_an_explicit_instant(
    tmp_path: Path,
) -> None:
    """The comparison runs one way only, stated as an instant rather than a phrase."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.entomb(_CHILD, base)
    future = str(int(time.time()) + 60)

    assert store.prune(future) == (_CHILD,), (
        "a tombstone written before the cutoff is pruned"
    )


def test_prune_refuses_an_empty_expiry(tmp_path: Path) -> None:
    """An empty expression is a usage error, not a licence to delete everything."""
    repo = _repo(tmp_path)

    with pytest.raises(ValueError, match="must not be empty"):
        _writer(repo).prune("   ")


@pytest.mark.parametrize("branch", ["feature/child", "Feature/X", "release-1.2.3"])
def test_a_record_round_trips_whatever_shape_the_branch_name_has(
    tmp_path: Path, branch: str
) -> None:
    """A hierarchical or dotted name survives Git's configuration quoting."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(branch, base)
    store = _writer(repo)
    record = _record(branch, base)

    store.create(record)

    assert store.read(branch) == record, "the name round-trips through the store"
    assert not store.orphans(), "the configuration section is matched exactly"
    assert _namespace_violations(repo) == set(), "INV-9 holds for the nested name"
    store.entomb(branch, base)
    assert store.tombstone(branch) == base, "the tombstone mirrors the same layout"


def test_orphans_finds_a_record_stored_under_a_dotted_branch_name(
    tmp_path: Path,
) -> None:
    """The configuration key's last dot is not where the branch name ends."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch("release-1.2.3", base)
    store = _writer(repo)
    store.create(_record("release-1.2.3", base))
    _delete_branch(repo, "release-1.2.3")

    assert store.orphans() == ("release-1.2.3",), (
        "a dotted branch name is recognised in the configuration"
    )


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("create-a-second-record", set()),
        ("refresh", set()),
        ("entomb-without-deleting", set()),
        ("delete-through-plonk", set()),
        ("delete-through-git", {_CHILD}),
        ("delete-by-ref-surgery", {_CHILD}),
        ("rename-through-git", {_CHILD}),
        ("sweep-after-git-delete", set()),
        ("sweep-after-ref-surgery", set()),
    ],
)
def test_the_record_namespace_is_a_subset_of_the_branch_namespace(
    tmp_path: Path, operation: str, expected: set[str]
) -> None:
    """INV-9 after each operation, asserted against Git's own two namespaces."""
    repo = _repo(tmp_path)
    base = repo.head.commit.hexsha
    repo.git.branch(_CHILD, base)
    store = _writer(repo)
    store.create(_record(_CHILD, base))

    _perform(store, repo, base, operation)

    assert _namespace_violations(repo) == expected, (
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
            repo.git.branch(_NEIGHBOUR, base)
            store.create(_record(_NEIGHBOUR, base))
        case "refresh":
            store.refresh(_record(_CHILD, base), expected_old=base)
        case "entomb-without-deleting":
            store.entomb(_CHILD, base)
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
            store.entomb(_CHILD, base)
            _delete_branch(repo, _CHILD, "-D")
        case "delete-through-git":
            _delete_branch(repo, _CHILD)
        case "delete-by-ref-surgery":
            _delete_ref_surgically(repo, _CHILD)
        case "rename-through-git":
            _rename_branch(repo, _CHILD, _RENAMED)
        case "sweep-after-git-delete":
            _delete_branch(repo, _CHILD)
            store.sweep(store.orphans())
        case "sweep-after-ref-surgery":
            _delete_ref_surgically(repo, _CHILD)
            store.sweep(store.orphans())
        case _:  # pragma: no cover - the parameter list is closed
            msg = f"unknown operation {operation!r}"
            raise AssertionError(msg)
