"""Unit tests for ``git-plonk`` summary rendering.

The summary is the command's user-visible contract, so it is pinned exactly:
literal assertions for the shape of a report, and snapshots for the composite
cases. Snapshots capture absolute worktree paths, which the path matcher below
redacts at record time so no local path is committed for ``ambrleaks`` to flag.
Candidate selection is covered by ``test_plonk_selection.py`` and the cleanup
behaviour the summaries describe by ``test_plonk_cleanup.py``.
"""

from __future__ import annotations

import re
import tempfile
import typing as typ
from pathlib import Path

from syrupy.matchers import path_type

from git_donkey import plonk

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _redact_worktree_paths(data: str, _: object) -> str:
    """Rewrite absolute git-donkey worktree mounts to a relative placeholder.

    Parameters
    ----------
    data : str
        The rendered summary text captured by the snapshot.
    _ : object
        The syrupy path match, unused by this replacer.

    Returns
    -------
    str
        ``data`` with each absolute worktree mount, including every ancestor
        segment above ``repo.worktrees``, replaced by a ``<worktrees>``
        placeholder so recorded snapshots contain no absolute POSIX paths for
        ``ambrleaks`` to flag.
    """
    return re.sub(r"(?:/[^/\s]+)*/repo\.worktrees\b", "<worktrees>", data)


# Redact absolute worktree paths at record time so snapshots stay leak-free.
_WORKTREE_PATH_MATCHER = path_type(
    mapping={"": (str,)}, replacer=_redact_worktree_paths
)


def test_redact_worktree_paths_strips_nested_ancestor() -> None:
    """Redaction should drop the whole absolute prefix, not just the mount."""
    # Derive the temporary root rather than hardcoding "/tmp": a literal would
    # trip flake8-bandit's hardcoded-temp-file check for no benefit, since the
    # path is only ever treated as text.
    temp_root = tempfile.gettempdir()
    redacted = _redact_worktree_paths(f"{temp_root}/run/repo.worktrees/branch", None)

    assert redacted.startswith("<worktrees>"), (
        "expected redaction to replace the absolute ancestor prefix"
    )
    assert temp_root not in redacted, (
        "expected no absolute ancestor segment to survive redaction"
    )


def test_redact_worktree_paths_keeps_rooted_mount_stable() -> None:
    """A mount with no ancestor should redact exactly as it did before."""
    redacted = _redact_worktree_paths("- /repo.worktrees/issue-123-fix", None)

    assert redacted == "- <worktrees>/issue-123-fix", (
        "expected rooted worktree mounts to keep their existing redaction"
    )


def test_dry_run_summary_reports_planned_actions(
    snapshot: SnapshotAssertion,
) -> None:
    """Dry-run summaries should name planned work instead of completed removals."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        is_dry_run=True,
        removed_worktrees=(Path("/repo.worktrees/issue-123-fix"),),
        removed_branches=("issue-123-fix",),
    )

    assert plonk._render_summary(result) == snapshot(matcher=_WORKTREE_PATH_MATCHER), (
        "expected dry-run summary to report planned actions"
    )


def test_soft_summary_reports_nothing_to_clean_after_inspection() -> None:
    """Soft mode should distinguish empty worktrees from no matching worktrees."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.SOFT,
        inspected_worktrees=2,
    )

    assert plonk._render_summary(result) == (
        "git-plonk: mode=soft\nNo generated paths to clean in git donkey worktrees."
    ), "expected inspected soft cleanup to report nothing to clean"


def test_soft_summary_reports_no_matching_worktrees_when_none_inspected() -> None:
    """Soft mode should keep the generic no-match message with no worktrees."""
    result = plonk._PlonkResult(mode=plonk._PlonkMode.SOFT)

    assert plonk._render_summary(result) == (
        "git-plonk: mode=soft\nNo matching git donkey worktrees found."
    ), "expected soft cleanup with no worktrees to report no matches"


def test_summary_rendering_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    """Command summaries should stay stable for reviewable CLI output."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(
            Path("/repo.worktrees/issue-123-fix"),
            Path("/repo.worktrees/road-1-2-3a-4-task"),
        ),
        removed_branches=("issue-123-fix", "road-1-2-3a-4-task"),
        cleaned_paths=(Path("/repo.worktrees/issue-123-fix/target"),),
    )

    assert plonk._render_summary(result) == snapshot(matcher=_WORKTREE_PATH_MATCHER), (
        "expected summary rendering to match snapshot"
    )


def test_skipped_only_summary_does_not_claim_nothing_matched() -> None:
    """A sweep that only skipped worktrees is a decision, not an empty match."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.DEFAULT,
        skipped_worktrees=(
            plonk._SkippedWorktree(
                Path("/repo.worktrees/issue-456-dirty"),
                plonk._SkipReason.DIRTY,
            ),
        ),
    )

    summary = plonk._render_summary(result)

    assert "Skipped worktrees:" in summary, "expected a skipped section"
    assert "- /repo.worktrees/issue-456-dirty (uncommitted changes)" in summary, (
        "expected the report to name the worktree and the reason"
    )
    assert "No matching git donkey worktrees found." not in summary, (
        "expected a run that skipped worktrees not to report an empty match"
    )


def test_skipped_summary_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    """Skip reporting should stay stable for reviewable CLI output."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(Path("/repo.worktrees/issue-123-fix"),),
        removed_branches=("issue-123-fix",),
        skipped_worktrees=(
            plonk._SkippedWorktree(
                Path("/repo.worktrees/issue-456-dirty"),
                plonk._SkipReason.DIRTY,
            ),
            plonk._SkippedWorktree(
                Path("/repo.worktrees/issue-789-gone"),
                plonk._SkipReason.UNAVAILABLE,
            ),
        ),
    )

    assert plonk._render_summary(result) == snapshot(matcher=_WORKTREE_PATH_MATCHER), (
        "expected removals and skips to be reported together"
    )
