"""Unit tests for the ``git_donkey.plonk`` cleanup helpers."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from git_donkey import plonk

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


_ROADMAP_WORDS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=1,
    max_size=8,
)


def test_issue_branch_marker_matches_issue_reference() -> None:
    """Issue branches should map to GitHub issue merge markers."""
    assert plonk._completion_marker_for_branch("issue-123-fix-bug") == "(#123)"


@given(namespace=st.none() | _ROADMAP_WORDS, suffix=st.none() | _ROADMAP_WORDS)
def test_roadmap_branch_marker_invariant(
    namespace: str | None,
    suffix: str | None,
) -> None:
    """Roadmap markers should be parenthesized dotted references."""
    prefix = f"{namespace}-" if namespace else ""
    suffix_part = suffix or ""
    branch_name = f"{prefix}1-20-300{suffix_part}-4-implement-plonk"

    marker = plonk._completion_marker_for_branch(branch_name)

    assert marker is not None
    assert marker.startswith("(")
    assert marker.endswith(")")
    assert "-" not in marker
    assert ".." not in marker
    assert plonk._has_completion_marker([f"Merge completed {marker}"], marker)
    dotted_marker = f"{marker.removesuffix(')')}.)"
    assert plonk._has_completion_marker([f"Merge completed {dotted_marker}"], marker)


@given(number=st.integers(min_value=1, max_value=999_999))
def test_issue_branch_marker_invariant(number: int) -> None:
    """Issue markers should match exact issue numbers in commit history."""
    marker = plonk._completion_marker_for_branch(f"issue-{number}-short-title")

    assert marker == f"(#{number})"
    assert marker is not None
    assert plonk._has_completion_marker([f"Squashed work {marker}"], marker)
    assert not plonk._has_completion_marker([f"Squashed work (#{number + 1})"], marker)


def test_roadmap_branch_marker_includes_optional_namespace_suffix_and_task() -> None:
    """Roadmap branches should preserve namespace, suffix, and task number."""
    branch_name = "road-1-2-3a-4-finished-task"

    marker = plonk._completion_marker_for_branch(branch_name)

    assert marker == "(road.1.2.3a.4)"


def test_unrecognized_branch_has_no_completion_marker() -> None:
    """Unrecognized branches should never be selected for default cleanup."""
    assert plonk._completion_marker_for_branch("feature/unstructured") is None


def test_completed_candidates_use_history_markers() -> None:
    """Candidate filtering should keep only branches with matching markers."""
    candidates = [
        plonk._PlonkCandidate(
            branch_name="issue-123-fix",
            worktree_path=Path("/repo.worktrees/issue-123-fix"),
            marker="(#123)",
        ),
        plonk._PlonkCandidate(
            branch_name="road-1-2-3a-4-task",
            worktree_path=Path("/repo.worktrees/road-1-2-3a-4-task"),
            marker="(road.1.2.3a.4)",
        ),
        plonk._PlonkCandidate(
            branch_name="issue-456-open",
            worktree_path=Path("/repo.worktrees/issue-456-open"),
            marker="(#456)",
        ),
    ]

    completed = plonk._completed_candidates(
        candidates,
        ["Merge pull request (#123)", "Roadmap complete (road.1.2.3a.4.)"],
    )

    assert [candidate.branch_name for candidate in completed] == [
        "issue-123-fix",
        "road-1-2-3a-4-task",
    ]


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

    assert plonk._render_summary(result) == snapshot
