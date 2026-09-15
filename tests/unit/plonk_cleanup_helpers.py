"""Doubles and builders shared by the ``git-plonk`` completed-cleanup suites.

The cleanup workflow is exercised twice over: ``test_plonk_cleanup.py`` pins the
contract with readable one- and two-candidate examples, and
``test_plonk_cleanup_properties.py`` generalises the same rules over generated
batches. Both compose one Git double and one stack record double, so the rules
the doubles enforce — hard mode deletes a branch only once its worktree is gone
and only after its tip has been preserved — hold for every example and every
generated batch alike. A change to how a candidate or a record is described
therefore cannot leave the two suites asserting against differently shaped
adapters.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_donkey import (
    plonk,
    plonk_cleanup,
    plonk_records,
    stack_records,
    stack_store,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git import Repo

    from git_donkey import plonk_worktree_adapter

# A full object ID is all the lifecycle asks of a tip, and the double reports
# the same one for every branch, because no unit test inspects the commit a
# tombstone names.
RECORDED_TIP = "0" * 40

# The record work the read-only double reports, so a dry run's plan is visible
# without either record existing.
PLANNED_ORPHAN = "issue-456-orphaned"
PLANNED_STALE_TOMBSTONE = "issue-789-stale"

# Trunk ref shape a real run resolves: the fetched remote-tracking ref of the
# default branch the principal remote advertises.
TRUNK_REF = "refs/remotes/origin/main"

# The branch and directory ``git donkey`` would have created for the candidate
# worktrees the examples exercise.
WORKTREE_BRANCH = "issue-123-fix"


class FailingGitAdapter:
    """Git adapter double that plans cleanup but refuses to mutate.

    Dry-run cleanup must reuse candidate discovery while skipping destructive
    Git APIs, so ``remove_worktree`` and ``delete_branch`` fail the test if the
    planner ever invokes them. History reports the candidate under test as
    complete, so the planner reaches the mutations it must not perform.
    """

    @staticmethod
    def history_messages(ref: str) -> typ.Iterator[str]:
        """Assert the resolved trunk ref, then yield the completion marker."""
        if ref != TRUNK_REF:
            msg = "expected configured trunk ref"
            raise AssertionError(msg)
        yield marker_for(candidate(WORKTREE_BRANCH, 123))

    @staticmethod
    def skip_reason(worktree_path: Path) -> plonk_records._SkipReason | None:
        """Report every candidate as clean, so planning reaches the mutations."""
        return None

    @staticmethod
    def remove_worktree(worktree_path: Path) -> bool:
        """Fail the test unconditionally — dry runs must not remove worktrees."""
        pytest.fail(f"dry run should not remove worktree {worktree_path}")

    @staticmethod
    def delete_branch(branch_name: str) -> bool:
        """Fail the test unconditionally — dry runs must not delete branches."""
        pytest.fail(f"dry run should not delete branch {branch_name}")


class RecordingGitAdapter:
    """Git adapter double that records removals instead of performing them.

    Trunk history is driven by the ``markers`` the double yields and each
    candidate's state by ``skip_reasons``, so one batch can mix worktrees Git
    would remove with worktrees it would refuse. ``removal_failures`` models a
    worktree that passes the preflight but whose removal still fails, and
    ``deletion_failures`` a branch Git refuses to delete.
    """

    def __init__(
        self,
        markers: typ.Iterable[str],
        *,
        skip_reasons: dict[Path, plonk_records._SkipReason] | None = None,
        removal_failures: typ.Iterable[Path] = (),
        deletion_failures: typ.Iterable[str] = (),
    ) -> None:
        self._markers = tuple(markers)
        self.skip_reasons = dict(skip_reasons or {})
        self.removal_failures = set(removal_failures)
        self.deletion_failures = set(deletion_failures)
        self.removed: list[Path] = []
        self.deleted: list[str] = []

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        """Assert the resolved trunk ref, then yield the configured history."""
        if ref != TRUNK_REF:
            msg = "expected configured trunk ref"
            raise AssertionError(msg)
        yield from self._markers

    def skip_reason(self, worktree_path: Path) -> plonk_records._SkipReason | None:
        """Return the configured reason for ``worktree_path``, if it has one."""
        if worktree_path in self.skip_reasons:
            return self.skip_reasons[worktree_path]
        return None

    def remove_worktree(self, worktree_path: Path) -> bool:
        """Record a removal request, reporting success unless told to fail."""
        if worktree_path in self.removal_failures:
            return False
        self.removed.append(worktree_path)
        return True

    def delete_branch(self, branch_name: str) -> bool:
        """Record a branch deletion, which must follow its worktree's removal."""
        removed_branches = [path.name for path in self.removed]
        if branch_name not in removed_branches:
            msg = "hard mode deletes a branch only once its worktree is gone"
            raise AssertionError(msg)
        if branch_name in self.deletion_failures:
            return False
        self.deleted.append(branch_name)
        return True


class FailingStackStore:
    """Stack record double that answers reads but refuses to write.

    A dry run must classify orphans and expired tombstones with reads alone, so
    the reads report work to plan — one orphan whose record still parses, and
    one tombstone past every window — while ``entomb``, ``sweep``, and ``prune``
    fail the test if the planner reaches them. A dry run that reports neither
    the sweep nor the prune has quietly skipped the classification.
    """

    @staticmethod
    def expiry() -> str:
        """Report the documented retention window, applied to the stale tombstone."""
        return stack_records.DEFAULT_TOMBSTONE_EXPIRE

    @staticmethod
    def orphans() -> tuple[str, ...]:
        """Report one orphaned record for the sweep to classify."""
        return (PLANNED_ORPHAN,)

    @staticmethod
    def rescuable(orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Report every orphan as rescuable, since the planner writes nothing."""
        return tuple(orphans)

    @staticmethod
    def branch_tip(branch: str) -> str | None:
        """Report the tip a candidate's branch would have."""
        return RECORDED_TIP

    @staticmethod
    def expired(expire: str) -> tuple[str, ...]:
        """Report one tombstone for the prune to classify."""
        return (PLANNED_STALE_TOMBSTONE,)

    @staticmethod
    def entomb(branch: str, tip: str) -> None:
        """Fail the test unconditionally — a dry run writes no tombstone."""
        pytest.fail(f"dry run should not entomb {branch} at {tip}")

    @staticmethod
    def sweep(orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Fail the test unconditionally — a dry run clears no record."""
        pytest.fail(f"dry run should not sweep orphans {orphans}")

    @staticmethod
    def prune(expire: str) -> tuple[str, ...]:
        """Fail the test unconditionally — a dry run deletes no tombstone."""
        pytest.fail(f"dry run should not prune tombstones older than {expire!r}")


class EntombFirstAdapter(RecordingGitAdapter):
    """Adapter that refuses to delete a branch the store has not entombed.

    The order of the two writes is the whole point of entombing before deleting
    — the tip is only readable while the branch names it — and neither double
    can observe the order alone. This one carries the store, so the rule is
    checked at the moment Git is asked for the deletion.
    """

    def __init__(
        self,
        markers: typ.Iterable[str],
        records: RecordingStackStore,
        *,
        deletion_failures: cabc.Iterable[str] = (),
    ) -> None:
        super().__init__(markers, deletion_failures=deletion_failures)
        self.records = records

    def delete_branch(self, branch_name: str) -> bool:
        """Assert this branch's own tombstone exists, then delete the branch."""
        entombed = [branch for branch, _ in self.records.entombed]
        if branch_name not in entombed:
            msg = "hard mode preserves a branch's tip before deleting it"
            raise AssertionError(msg)
        return super().delete_branch(branch_name)


@dataclasses.dataclass(slots=True)
class RecordingStackStore:
    """Stack record double that records the lifecycle calls it receives.

    ``branch_tips`` decides which branches ``branch_tip`` finds; a branch it
    does not name is reported present at ``RECORDED_TIP``, because a candidate
    that reaches the branch stage of a real run is one whose branch exists.
    ``preservable`` names the orphans whose record still parses, so one sweep
    can mix the orphan it rescues with the one it only clears. ``stale`` names
    the tombstones the retention window reaches, so a test configures the
    answer to that comparison rather than a clock. ``unusable_expiry`` makes
    ``expiry`` refuse the configured window, which is the one read a run cannot
    recover from.

    The fields are named for what the reader reports rather than for the
    methods that report them: ``orphaned`` and ``stale`` cannot both be a field
    and a protocol method, and the method is the half the production store
    declares.
    """

    branch_tips: dict[str, str | None] = dataclasses.field(default_factory=dict)
    orphaned: cabc.Iterable[str] = ()
    preservable: cabc.Iterable[str] = ()
    stale: cabc.Iterable[str] = ()
    expire: str = stack_records.DEFAULT_TOMBSTONE_EXPIRE
    entomb_failures: cabc.Iterable[str] = ()
    unusable_expiry: bool = False
    entombed: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    swept: list[tuple[str, ...]] = dataclasses.field(default_factory=list)
    pruned: list[str] = dataclasses.field(default_factory=list)

    def expiry(self) -> str:
        """Return the configured window, or refuse it as a reader would."""
        if self.unusable_expiry:
            msg = (
                f"the configured {stack_store.TOMBSTONE_EXPIRE_KEY} names no "
                "past instant"
            )
            raise ValueError(msg)
        return self.expire

    def orphans(self) -> tuple[str, ...]:
        """Return the configured orphans as the tuple the protocol answers with."""
        return tuple(self.orphaned)

    def rescuable(self, orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Return the orphans of ``orphans`` whose record still parses."""
        return tuple(branch for branch in orphans if branch in self.preservable)

    def branch_tip(self, branch: str) -> str | None:
        """Return the tip configured for ``branch``, or the default one."""
        return self.branch_tips.get(branch, RECORDED_TIP)

    def expired(self, expire: str) -> tuple[str, ...]:
        """Return the stale tombstones as the tuple the protocol answers with."""
        return tuple(self.stale)

    def entomb(self, branch: str, tip: str) -> None:
        """Record a tombstone, reporting failure for the configured branches."""
        if branch in self.entomb_failures:
            msg = f"cannot write the tombstone for {branch!r}"
            raise stack_store.StackRecordError(msg)
        self.entombed.append((branch, tip))

    def sweep(self, orphans: cabc.Sequence[str]) -> tuple[str, ...]:
        """Record a sweep, preserving the orphans whose record still parses."""
        self.swept.append(tuple(orphans))
        return self.rescuable(orphans)

    def prune(self, expire: str) -> tuple[str, ...]:
        """Record a prune, deleting the tombstones the window reaches."""
        self.pruned.append(expire)
        return tuple(self.stale)


class ForgetfulStackStore(RecordingStackStore):
    """Store double that accepts one branch's tombstone and writes none.

    Hard mode's ordering rule is about one branch: a branch must not be deleted
    while nothing names its tip. A store that honours every write cannot tell
    that rule apart from "something was entombed", so this double drops the
    tombstone of the branch it was told to forget without complaining — the
    silent write failure the adapter's guard exists to catch.
    """

    def __init__(self, forgetful: str) -> None:
        super().__init__()
        self.forgetful = forgetful

    def entomb(self, branch: str, tip: str) -> None:
        """Record every tombstone but the forgotten branch's."""
        if branch != self.forgetful:
            super().entomb(branch, tip)


def candidate(branch_name: str, issue_number: int) -> plonk_records._PlonkCandidate:
    """Return the candidate git donkey creates for ``branch_name``.

    Parameters
    ----------
    branch_name : str
        Branch, and worktree directory name, of the candidate.
    issue_number : int
        Issue number the branch marker refers to.

    Returns
    -------
    plonk_records._PlonkCandidate
        The candidate, marked complete by the matching issue marker.

    """
    return plonk_records._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=Path(f"/repo.worktrees/{branch_name}"),
        marker=f"(#{issue_number})",
    )


def marker_for(candidate: plonk_records._PlonkCandidate) -> str:
    """Return the trunk history line that marks ``candidate`` complete."""
    return f"Merge pull request {candidate.marker}"


def cleanup_context(
    candidates: typ.Iterable[plonk_records._PlonkCandidate],
) -> plonk_records._PlonkContext:
    """Return the repository state a completed cleanup of ``candidates`` sees.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk_records._PlonkCandidate]
        Candidates whose worktree stanzas the context reports.

    Returns
    -------
    plonk_records._PlonkContext
        Context holding one stanza per candidate and no invoking worktree, so
        every candidate is eligible for cleanup.

    """
    return plonk_records._PlonkContext(
        repo_home=typ.cast("Repo", SimpleNamespace()),
        stanzas=[
            {
                "branch": f"refs/heads/{one.branch_name}",
                "worktree": one.worktree_path,
            }
            for one in candidates
        ],
        worktrees_root=Path("/repo.worktrees"),
        trunk_ref=TRUNK_REF,
        invoking_worktree=None,
    )


def cleanup_surfaces(
    adapter: object,
    records: object | None = None,
) -> plonk_cleanup._CleanupSurfaces:
    """Return the Git surfaces a cleanup run over ``adapter`` cleans through.

    Parameters
    ----------
    adapter : object
        Any double exposing the adapter's history, skip, and mutation surface.
    records : object | None, optional
        Any double exposing the stack record surface the lifecycle uses,
        defaulting to one holding no orphans, no tombstones, and a branch for
        every candidate.

    Returns
    -------
    plonk_cleanup._CleanupSurfaces
        The adapter double beside the record double the run reads and writes.

    """
    return plonk_cleanup._CleanupSurfaces(
        adapter=typ.cast("plonk_worktree_adapter._GitWorktreeAdapter", adapter),
        records=typ.cast(
            "stack_store.StackRecordWriter",
            RecordingStackStore() if records is None else records,
        ),
    )


def run_cleanup(
    candidates: typ.Iterable[plonk_records._PlonkCandidate],
    surfaces: plonk_cleanup._CleanupSurfaces,
    mode: plonk._PlonkMode,
    *,
    dry_run: bool = False,
) -> plonk._PlonkResult:
    """Run completed cleanup for ``candidates`` against doubles.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk_records._PlonkCandidate]
        Candidates to clean up.
    surfaces : plonk_cleanup._CleanupSurfaces
        Git and record doubles the run cleans through.
    mode : plonk._PlonkMode
        Cleanup mode to run.
    dry_run : bool, optional
        Whether to plan the work without mutating either double.

    Returns
    -------
    plonk._PlonkResult
        What the run reports it removed and skipped.

    """
    return plonk_cleanup._run_completed_cleanup(
        cleanup_context(candidates),
        mode,
        surfaces,
        dry_run=dry_run,
    )
