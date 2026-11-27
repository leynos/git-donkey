"""Unit tests for ``git-plonk`` summary rendering.

The summary is the command's user-visible contract, so it is pinned exactly:
literal assertions for the shape of a report, and snapshots for the composite
cases. A removal names the completion signal that justified it, and a candidate
no tier resolved is listed as unverified. Snapshots capture absolute worktree
paths, which the path matcher below redacts at record time so no local path is
committed for ``ambrleaks`` to flag. Candidate selection is covered by
``test_plonk_selection.py`` and the cleanup behaviour the summaries describe by
``test_plonk_cleanup.py``.
"""

from __future__ import annotations

import re
import tempfile
import typing as typ
from pathlib import Path

import pytest
from syrupy.matchers import path_type

from git_donkey import plonk, plonk_records, plonk_summary

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


def _evidence(
    signal: plonk_records.CompletionSignal,
    detail: str = "",
) -> plonk_records._CompletionEvidence:
    """Return one completion evidence record."""
    return plonk_records._CompletionEvidence(signal, detail)


def _removal(
    worktree_path: Path,
    signal: plonk_records.CompletionSignal,
    detail: str = "",
) -> plonk_records._RemovalEvidence:
    """Return the removal evidence pairing for ``worktree_path``."""
    return plonk_records._RemovalEvidence(worktree_path, _evidence(signal, detail))


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
    worktree = Path("/repo.worktrees/issue-123-fix")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        is_dry_run=True,
        removed_worktrees=(worktree,),
        removal_evidence=(_removal(worktree, "marker", "(#123)"),),
        removed_branches=("issue-123-fix",),
    )

    assert plonk._render_summary(result) == snapshot(matcher=_WORKTREE_PATH_MATCHER), (
        "expected dry-run summary to report planned actions with their evidence"
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
    marker_worktree = Path("/repo.worktrees/issue-123-fix")
    pull_request_worktree = Path("/repo.worktrees/fix-markdown-gates")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(marker_worktree, pull_request_worktree),
        removal_evidence=(
            _removal(marker_worktree, "marker", "(#123)"),
            _removal(pull_request_worktree, "github_merged_pr", "#61"),
        ),
        removed_branches=("issue-123-fix", "fix-markdown-gates"),
        cleaned_paths=(marker_worktree / "target",),
    )

    assert plonk._render_summary(result) == snapshot(matcher=_WORKTREE_PATH_MATCHER), (
        "expected summary rendering to match snapshot"
    )


def test_skipped_only_summary_does_not_claim_nothing_matched() -> None:
    """A sweep that only skipped worktrees is a decision, not an empty match."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.DEFAULT,
        skipped_worktrees=(
            plonk_records._SkippedWorktree(
                Path("/repo.worktrees/issue-456-dirty"),
                plonk_records._SkipReason.DIRTY,
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
    worktree = Path("/repo.worktrees/issue-123-fix")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(worktree,),
        removal_evidence=(_removal(worktree, "marker", "(#123)"),),
        removed_branches=("issue-123-fix",),
        skipped_worktrees=(
            plonk_records._SkippedWorktree(
                Path("/repo.worktrees/issue-456-dirty"),
                plonk_records._SkipReason.DIRTY,
            ),
            plonk_records._SkippedWorktree(
                Path("/repo.worktrees/issue-789-gone"),
                plonk_records._SkipReason.UNAVAILABLE,
            ),
        ),
    )

    assert plonk._render_summary(result) == snapshot(matcher=_WORKTREE_PATH_MATCHER), (
        "expected removals and skips to be reported together"
    )


def test_dry_run_planned_and_skipped_summary_matches_snapshot(
    snapshot: SnapshotAssertion,
) -> None:
    """A dry run should report its plans and its skips in one reviewable block."""
    worktree = Path("/repo.worktrees/issue-123-fix")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        is_dry_run=True,
        removed_worktrees=(worktree,),
        removal_evidence=(_removal(worktree, "marker", "(#123)"),),
        removed_branches=("issue-123-fix",),
        skipped_worktrees=(
            plonk_records._SkippedWorktree(
                Path("/repo.worktrees/issue-456-dirty"),
                plonk_records._SkipReason.DIRTY,
            ),
        ),
    )

    assert plonk._render_summary(result) == snapshot(matcher=_WORKTREE_PATH_MATCHER), (
        "expected planned removals and skips to be reported together"
    )


def test_failed_branch_deletion_gets_its_own_section() -> None:
    """A branch that outlived its worktree is neither removed nor skipped."""
    worktree = Path("/repo.worktrees/issue-123-fix")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(worktree,),
        removal_evidence=(_removal(worktree, "marker", "(#123)"),),
        failed_branch_deletions=("issue-123-fix",),
    )

    summary = plonk._render_summary(result)

    assert "Failed branch deletions:" in summary, (
        "expected the surviving branch to have a section of its own"
    )
    assert "- issue-123-fix (branch deletion failed)" in summary, (
        "expected the report to name the branch Git kept"
    )
    assert "Removed branches:" not in summary, (
        "a branch Git refused to delete is not reported as removed"
    )
    assert "No matching git donkey worktrees found." not in summary, (
        "expected a failed deletion not to be reported as an empty match"
    )


def test_removal_lines_name_the_completion_signal() -> None:
    """A removal should say which tier justified it."""
    worktree = Path("/repo.worktrees/issue-123-fix")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.DEFAULT,
        removed_worktrees=(worktree,),
        removal_evidence=(_removal(worktree, "marker", "(#123)"),),
    )

    summary = plonk._render_summary(result)

    assert f"- {worktree} (completed by marker (#123))" in summary, (
        "expected the removal line to name the marker evidence"
    )


def test_removal_without_evidence_falls_back_to_plain_path() -> None:
    """A result built without evidence should still render a usable line."""
    worktree = Path("/repo.worktrees/issue-123-fix")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.DEFAULT,
        removed_worktrees=(worktree,),
    )

    assert plonk._render_summary(result).splitlines()[-1] == f"- {worktree}", (
        "expected a result without evidence to render the bare path"
    )


@pytest.mark.parametrize(
    ("signal", "detail", "label"),
    [
        pytest.param("marker", "(#123)", "completed by marker (#123)", id="marker"),
        pytest.param(
            "closing_keyword",
            "Closes #62",
            "completed by closing keyword 'Closes #62'",
            id="closing-keyword",
        ),
        pytest.param(
            "merge_commit",
            "leynos/issue-62-fix",
            "completed by merge commit leynos/issue-62-fix",
            id="merge-commit",
        ),
        pytest.param(
            "branch_name",
            "issue-62-fix",
            "completed by branch name in a commit message",
            id="branch-name",
        ),
        pytest.param(
            "github_merged_pr",
            "#63",
            "completed by merged pull request #63",
            id="github-merged-pr",
        ),
        pytest.param(
            "upstream_gone",
            "issue-62-fix",
            "completed by gone upstream branch",
            id="upstream-gone",
        ),
    ],
)
def test_every_completion_signal_has_a_report_label(
    signal: plonk_records.CompletionSignal,
    detail: str,
    label: str,
) -> None:
    """Every declared signal should render one stable, specific phrase."""
    assert plonk_summary._evidence_label(_evidence(signal, detail)) == label, (
        "expected each signal to render its documented phrase"
    )


def test_unverified_worktrees_get_their_own_section() -> None:
    """A candidate no tier resolved should be named, not silently dropped."""
    worktree = Path("/repo.worktrees/fix-markdown-gates")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.DEFAULT,
        unverified_worktrees=(
            plonk._UnverifiedWorktree(
                worktree,
                plonk_records._UnverifiedReason.GITHUB_UNKNOWN,
            ),
        ),
    )

    summary = plonk._render_summary(result)

    assert "Unverified worktrees:" in summary, (
        "expected the unverified candidate to have a section of its own"
    )
    assert f"- {worktree} (GitHub completion lookup failed)" in summary, (
        "expected the report to name the candidate and the reason"
    )
    assert "No matching git donkey worktrees found." not in summary, (
        "expected an unverified candidate not to be reported as an empty match"
    )


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        pytest.param(
            plonk_records._UnverifiedReason.NO_SIGNAL,
            "no completion signal found",
            id="no-signal",
        ),
        pytest.param(
            plonk_records._UnverifiedReason.GITHUB_UNKNOWN,
            "GitHub completion lookup failed",
            id="github-unknown",
        ),
        pytest.param(
            plonk_records._UnverifiedReason.UPSTREAM_UNSET,
            "no upstream branch configured",
            id="upstream-unset",
        ),
        pytest.param(
            plonk_records._UnverifiedReason.UPSTREAM_PRESENT,
            "upstream branch still exists",
            id="upstream-present",
        ),
        pytest.param(
            plonk_records._UnverifiedReason.UPSTREAM_UNKNOWN,
            "upstream state unknown",
            id="upstream-unknown",
        ),
    ],
)
def test_unverified_reasons_are_rendered_verbatim(
    reason: plonk_records._UnverifiedReason,
    text: str,
) -> None:
    """Each unverified reason should keep its fixed user-facing wording."""
    worktree = Path("/repo.worktrees/fix-markdown-gates")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.DEFAULT,
        unverified_worktrees=(plonk._UnverifiedWorktree(worktree, reason),),
    )

    assert f"- {worktree} ({text})" in plonk._render_summary(result), (
        "expected the unverified reason text to be rendered verbatim"
    )


def test_dry_run_reports_unverified_worktrees() -> None:
    """A preview that hid unverified candidates would misrepresent the run."""
    worktree = Path("/repo.worktrees/fix-markdown-gates")
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.DEFAULT,
        is_dry_run=True,
        unverified_worktrees=(
            plonk._UnverifiedWorktree(
                worktree,
                plonk_records._UnverifiedReason.NO_SIGNAL,
            ),
        ),
    )

    summary = plonk._render_summary(result)

    assert "git-plonk: mode=default dry-run" in summary, (
        "expected the dry-run marker to survive"
    )
    assert f"- {worktree} (no completion signal found)" in summary, (
        "expected the preview to name the unverified candidate"
    )
