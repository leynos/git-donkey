"""Doubles and builders shared by the ``git-plonk`` completed-cleanup suites.

The cleanup workflow is exercised twice over: ``test_plonk_cleanup.py`` pins the
contract with readable one- and two-candidate examples, and
``test_plonk_cleanup_properties.py`` generalises the same rules over generated
batches. Both compose one Git double, so the rule the double enforces — hard
mode deletes a branch only once its worktree is gone — holds for every example
and every generated batch alike, and a change to how a candidate is described
cannot leave the two suites asserting against differently shaped adapters.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_donkey import plonk, plonk_github, plonk_records

if typ.TYPE_CHECKING:
    from git import Repo

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
    def upstream_state(branch_name: str) -> plonk_records.UpstreamState:
        """Fail the test unconditionally — dry runs must not query Git remotes."""
        pytest.fail(f"dry run should not query the upstream of {branch_name}")

    @staticmethod
    def remove_worktree(worktree_path: Path) -> bool:
        """Fail the test unconditionally — dry runs must not remove worktrees."""
        pytest.fail(f"dry run should not remove worktree {worktree_path}")

    @staticmethod
    def delete_branch(branch_name: str) -> bool:
        """Fail the test unconditionally — dry runs must not delete branches."""
        pytest.fail(f"dry run should not delete branch {branch_name}")


@dataclasses.dataclass(frozen=True, slots=True)
class AdapterBehaviour:
    """Optional Git behaviour configured on a ``RecordingGitAdapter``.

    Attributes
    ----------
    skip_reasons : dict[Path, plonk_records._SkipReason]
        Preflight refusal each worktree should report.
    upstream_states : dict[str, plonk_records.UpstreamState]
        Upstream state each branch should report; unset branches report
        ``unset``.
    removal_failures : frozenset[Path]
        Worktrees that pass the preflight but whose removal Git refuses.
    deletion_failures : frozenset[str]
        Branches Git refuses to delete.
    fail_on_upstream : bool
        Fail the test if the upstream query is reached, proving a stronger tier
        already resolved the candidate.

    """

    skip_reasons: dict[Path, plonk_records._SkipReason] = dataclasses.field(
        default_factory=dict
    )
    upstream_states: dict[str, plonk_records.UpstreamState] = dataclasses.field(
        default_factory=dict
    )
    removal_failures: frozenset[Path] = frozenset()
    deletion_failures: frozenset[str] = frozenset()
    fail_on_upstream: bool = False


class RecordingGitAdapter:
    """Git adapter double that records removals instead of performing them.

    Trunk history is driven by the ``markers`` the double yields and each
    candidate's state by the configured :class:`AdapterBehaviour`, so one batch
    can mix worktrees Git would remove with worktrees it would refuse.
    """

    def __init__(
        self,
        markers: typ.Iterable[str],
        *,
        behaviour: AdapterBehaviour | None = None,
    ) -> None:
        """Configure the trunk markers and the behaviour scenarios."""
        self._markers = tuple(markers)
        self.behaviour = behaviour or AdapterBehaviour()
        self.skip_reasons = dict(self.behaviour.skip_reasons)
        self.upstream_states = dict(self.behaviour.upstream_states)
        self.removal_failures = set(self.behaviour.removal_failures)
        self.deletion_failures = set(self.behaviour.deletion_failures)
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

    def upstream_state(self, branch_name: str) -> plonk_records.UpstreamState:
        """Return the configured upstream state for ``branch_name``."""
        if self.behaviour.fail_on_upstream:
            pytest.fail(f"upstream of {branch_name} should not be queried")
        return self.upstream_states.get(branch_name, "unset")

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
        issue_number=issue_number,
    )


def marker_for(candidate: plonk_records._PlonkCandidate) -> str:
    """Return the trunk history line that marks ``candidate`` complete."""
    return f"Merge pull request {candidate.marker}"


def _stanza_for(candidate: plonk_records._PlonkCandidate) -> dict[str, object]:
    """Return the parsed worktree stanza ``candidate`` came from."""
    stanza: dict[str, object] = {
        "branch": f"refs/heads/{candidate.branch_name}",
        "worktree": candidate.worktree_path,
    }
    if candidate.commit_id is not None:
        stanza["HEAD"] = candidate.commit_id
    if candidate.is_locked:
        stanza["locked"] = True
    return stanza


def cleanup_context(
    candidates: typ.Iterable[plonk_records._PlonkCandidate],
    *,
    remote_url: str = "",
    invoking_worktree: Path | None = None,
) -> plonk_records._PlonkContext:
    """Return the repository state a completed cleanup of ``candidates`` sees.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk_records._PlonkCandidate]
        Candidates whose worktree stanzas the context reports.
    remote_url : str, optional
        Raw URL configured for the principal remote. Empty means no GitHub
        fallback can be derived from it.
    invoking_worktree : Path | None, optional
        Worktree the sweep was invoked from, which it never removes.

    Returns
    -------
    plonk_records._PlonkContext
        Context holding one stanza per candidate.

    """
    return plonk_records._PlonkContext(
        repo_home=typ.cast("Repo", SimpleNamespace()),
        stanzas=[_stanza_for(one) for one in candidates],
        worktrees_root=Path("/repo.worktrees"),
        anchor_dir=Path("/repo"),
        trunk_ref=TRUNK_REF,
        invoking_worktree=invoking_worktree,
        remote="origin",
        branch="main",
        remote_url=remote_url,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class CleanupRunOptions:
    """Optional inputs to one ``run_cleanup`` invocation.

    Attributes
    ----------
    dry_run : bool
        Whether to plan the work without mutating the adapter.
    no_github : bool
        Whether to disable the GitHub merged-pull-request fallback.
    upstream_gone : bool
        Whether a gone upstream branch counts as completion evidence.
    github_lookup : plonk_github.MergedPullRequestLookup | None
        Injected merged-pull-request lookup, bypassing the real ``gh`` tool.
    context : plonk_records._PlonkContext | None
        Repository state to run against. Defaults to one derived from the
        candidates with no GitHub remote.

    """

    dry_run: bool = False
    no_github: bool = False
    upstream_gone: bool = False
    github_lookup: plonk_github.MergedPullRequestLookup | None = None
    context: plonk_records._PlonkContext | None = None


_DEFAULT_RUN_OPTIONS = CleanupRunOptions()


def run_cleanup(
    candidates: typ.Iterable[plonk_records._PlonkCandidate],
    adapter: object,
    *,
    mode: plonk._PlonkMode,
    options: CleanupRunOptions = _DEFAULT_RUN_OPTIONS,
) -> plonk._PlonkResult:
    """Run completed cleanup for ``candidates`` against an adapter double.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk_records._PlonkCandidate]
        Candidates to clean up.
    adapter : object
        Any double exposing the adapter's history, skip, and mutation surface.
    mode : plonk._PlonkMode
        Cleanup mode to run.
    options : CleanupRunOptions, optional
        Tier and dry-run options for the sweep.

    Returns
    -------
    plonk._PlonkResult
        What the run reports it removed, skipped, and left unverified.

    """
    return plonk._run_completed_cleanup(
        options.context if options.context is not None else cleanup_context(candidates),
        mode,
        typ.cast("plonk._GitWorktreeAdapter", adapter),
        options=plonk._CleanupOptions(
            dry_run=options.dry_run,
            no_github=options.no_github,
            upstream_gone=options.upstream_gone,
            github_lookup=options.github_lookup,
        ),
    )
