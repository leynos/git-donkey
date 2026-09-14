"""Render the summary ``git-plonk`` prints when a run finishes.

The summary is the command's user-visible contract, so it is rendered apart
from the workflow that produces the records: the functions here take a finished
:class:`~git_donkey.plonk_records._PlonkResult` and return text, reading no Git
state and touching no filesystem. Planned actions, completed removals, branch
deletions Git refused, and skips each get their own section, and a run with
nothing to report says so rather than printing empty headings.

The record lifecycle gets sections of its own, because its outcomes are not
removals: a tombstone preserves what a deletion would otherwise lose, and a
sweep either rescues an orphan's tip or clears it with nothing to rescue. Those
two sweeps are reported apart — a summary that named them alike would claim a
rescue that did not happen — and the pruned-tombstones heading names the
retention window that was applied, so a window that has been mistyped is
visible rather than silently effective.
"""

from __future__ import annotations

import typing as typ

from git_donkey.plonk_records import _PlonkMode, _PlonkResult


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


def _skipped_entries(result: _PlonkResult) -> typ.Iterator[str]:
    """Yield one report line per worktree git-plonk skipped."""
    for skipped in result.skipped_worktrees:
        yield f"{skipped.worktree_path} ({skipped.reason.value})"


def _failed_branch_entries(result: _PlonkResult) -> typ.Iterator[str]:
    """Yield one report line per branch git-plonk could not delete."""
    for branch_name in result.failed_branch_deletions:
        yield f"{branch_name} (branch deletion failed)"


def _failed_entomb_entries(result: _PlonkResult) -> typ.Iterator[str]:
    """Yield one report line per branch whose tip could not be preserved."""
    for branch_name in result.failed_entombments:
        yield f"{branch_name} (tip not preserved, branch kept)"


def _heading(result: _PlonkResult, planned: str, done: str) -> str:
    """Return ``planned`` for a dry run and ``done`` for a real one."""
    return planned if result.is_dry_run else done


def _append_lifecycle_sections(lines: list[str], result: _PlonkResult) -> None:
    """Append the record-lifecycle sections ``result`` calls for.

    Every section is appended only when it has entries, so a run that touched
    no records says nothing about them. The two sweep sections are appended
    separately rather than merged, because the difference between them is the
    whole point of the split.
    """
    _append_summary_section(
        lines,
        _heading(result, "Planned branch entombments:", "Entombed branches:"),
        result.entombed_branches,
    )
    _append_summary_section(
        lines,
        _heading(
            result,
            "Planned record sweeps (tip preserved):",
            "Swept records (tip preserved):",
        ),
        result.swept_records,
    )
    _append_summary_section(
        lines,
        _heading(
            result,
            "Planned record sweeps (no tip to preserve):",
            "Swept records (no tip to preserve):",
        ),
        result.unrescuable_records,
    )
    if result.tombstone_expire is not None:
        _append_summary_section(
            lines,
            _heading(
                result,
                f"Planned tombstone prunes (older than {result.tombstone_expire}):",
                f"Pruned tombstones (older than {result.tombstone_expire}):",
            ),
            result.pruned_tombstones,
        )


def _render_summary(result: _PlonkResult) -> str:
    """Render a deterministic human-readable summary for ``result``."""
    suffix = " dry-run" if result.is_dry_run else ""
    lines: list[str] = [f"git-plonk: mode={result.mode.value}{suffix}"]
    has_entries = any((
        result.removed_worktrees,
        result.removed_branches,
        result.cleaned_paths,
        result.skipped_worktrees,
        result.failed_branch_deletions,
        result.entombed_branches,
        result.failed_entombments,
        result.swept_records,
        result.unrescuable_records,
        result.pruned_tombstones,
    ))
    if not has_entries:
        lines.append(_empty_summary_message(result))
        return "\n".join(lines)

    if result.is_dry_run:
        _append_summary_section(
            lines, "Planned worktree removals:", result.removed_worktrees
        )
        _append_summary_section(
            lines, "Planned branch deletions:", result.removed_branches
        )
        _append_summary_section(
            lines, "Planned generated path removals:", result.cleaned_paths
        )
    else:
        _append_summary_section(lines, "Removed worktrees:", result.removed_worktrees)
        _append_summary_section(lines, "Removed branches:", result.removed_branches)
        _append_summary_section(lines, "Removed generated paths:", result.cleaned_paths)

    # A branch that outlived its worktree is neither removed nor skipped, so it
    # gets its own section: the run is partial even though the worktree went.
    _append_summary_section(
        lines, "Failed branch deletions:", _failed_branch_entries(result)
    )
    _append_lifecycle_sections(lines, result)
    # A dry run plans no entombment that could fail, so this section is as
    # tense-neutral as the failed deletions above: it only ever has entries
    # from a run that really tried.
    _append_summary_section(
        lines, "Failed entombments:", _failed_entomb_entries(result)
    )
    # Skips are decisions, not plans: dry-run reports the same set, because a
    # preview that hid them would misrepresent the run it previews.
    _append_summary_section(lines, "Skipped worktrees:", _skipped_entries(result))
    return "\n".join(lines)
