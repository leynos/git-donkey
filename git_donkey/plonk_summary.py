"""Render the summary ``git-plonk`` prints when a run finishes.

The summary is the command's user-visible contract, so it is rendered apart
from the workflow that produces the records: the functions here take a finished
:class:`~git_donkey.plonk_records._PlonkResult` and return text, reading no Git
state and touching no filesystem. Planned actions, completed removals, branch
deletions Git refused, skips, and unverified worktrees each get their own
section. A removal names the completion signal that justified it, so the user
can audit the decision rather than trust the batch. A run with nothing to report
says so rather than printing empty headings.
"""

from __future__ import annotations

import typing as typ

from git_donkey.plonk_records import (
    _CompletionEvidence,
    _PlonkMode,
    _PlonkResult,
)


def _empty_summary_message(result: _PlonkResult) -> str:
    """Return the no-op summary line for ``result``."""
    if result.mode is _PlonkMode.SOFT and result.inspected_worktrees > 0:
        return "No generated paths to clean in git donkey worktrees."
    return "No matching git donkey worktrees found."


def _append_summary_section(
    lines: list[str], heading: str, entries: typ.Iterable[object]
) -> None:
    """Append ``heading`` and bullet entries when ``entries`` is populated."""
    section_entries = tuple(entries)
    if not section_entries:
        return

    lines.append(heading)
    lines.extend(f"- {entry}" for entry in section_entries)


def _evidence_label(evidence: _CompletionEvidence) -> str:
    """Return the report phrase naming one completion signal."""
    match evidence.signal:
        case "marker":
            return f"completed by marker {evidence.detail}"
        case "closing_keyword":
            return f"completed by closing keyword '{evidence.detail}'"
        case "merge_commit":
            return f"completed by merge commit {evidence.detail}"
        case "branch_name":
            return "completed by branch name in a commit message"
        case "github_merged_pr":
            return f"completed by merged pull request {evidence.detail}"
        case "upstream_gone":
            return "completed by gone upstream branch"
        case _:
            msg = f"unknown completion signal {evidence.signal!r}"
            raise AssertionError(msg)


def _removal_entries(result: _PlonkResult) -> typ.Iterator[str]:
    """Yield one report line per removed or planned worktree.

    The evidence table is keyed by path, so a result built without evidence
    (as the summary tests do for unrelated sections) still renders the plain
    path rather than inventing a signal.

    Yields
    ------
    str
        The worktree path, annotated with the signal that completed it when
        the result carries that evidence.

    """
    evidence_by_path = {
        record.worktree_path: record.evidence for record in result.removal_evidence
    }
    for worktree_path in result.removed_worktrees:
        evidence = evidence_by_path.get(worktree_path)
        if evidence is None:
            yield str(worktree_path)
            continue
        yield f"{worktree_path} ({_evidence_label(evidence)})"


def _skipped_entries(result: _PlonkResult) -> typ.Iterator[str]:
    """Yield one report line per worktree git-plonk skipped."""
    for skipped in result.skipped_worktrees:
        yield f"{skipped.worktree_path} ({skipped.reason.value})"


def _unverified_entries(result: _PlonkResult) -> typ.Iterator[str]:
    """Yield one report line per worktree no completion tier resolved."""
    for unverified in result.unverified_worktrees:
        yield f"{unverified.worktree_path} ({unverified.reason.value})"


def _failed_branch_entries(result: _PlonkResult) -> typ.Iterator[str]:
    """Yield one report line per branch git-plonk could not delete."""
    for branch_name in result.failed_branch_deletions:
        yield f"{branch_name} (branch deletion failed)"


def _render_summary(result: _PlonkResult) -> str:
    """Render a deterministic human-readable summary for ``result``."""
    suffix = " dry-run" if result.is_dry_run else ""
    lines: list[str] = [f"git-plonk: mode={result.mode.value}{suffix}"]
    has_entries = any((
        result.removed_worktrees,
        result.removed_branches,
        result.cleaned_paths,
        result.skipped_worktrees,
        result.unverified_worktrees,
        result.failed_branch_deletions,
    ))
    if not has_entries:
        lines.append(_empty_summary_message(result))
        return "\n".join(lines)

    if result.is_dry_run:
        _append_summary_section(
            lines, "Planned worktree removals:", _removal_entries(result)
        )
        _append_summary_section(
            lines, "Planned branch deletions:", result.removed_branches
        )
        _append_summary_section(
            lines, "Planned generated path removals:", result.cleaned_paths
        )
    else:
        _append_summary_section(lines, "Removed worktrees:", _removal_entries(result))
        _append_summary_section(lines, "Removed branches:", result.removed_branches)
        _append_summary_section(lines, "Removed generated paths:", result.cleaned_paths)

    # A branch that outlived its worktree is neither removed nor skipped, so it
    # gets its own section: the run is partial even though the worktree went.
    _append_summary_section(
        lines, "Failed branch deletions:", _failed_branch_entries(result)
    )
    # Skips and unverified worktrees are decisions, not plans: dry-run reports
    # the same sets, because a preview that hid them would misrepresent the run
    # it previews.
    _append_summary_section(lines, "Skipped worktrees:", _skipped_entries(result))
    _append_summary_section(
        lines,
        "Unverified worktrees:",
        _unverified_entries(result),
    )
    return "\n".join(lines)
