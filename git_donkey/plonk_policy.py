"""Pure completion-marker policy for git-plonk.

This module contains no GitPython, filesystem, or process mutation. It maps
branch names to completion markers and checks commit messages for those markers
so infrastructure code can decide which worktrees are safe to clean.
"""

from __future__ import annotations

import re
import typing as typ

_ISSUE_BRANCH_PATTERN = re.compile(r"^issue-(\d+)-")
_ROADMAP_BRANCH_PATTERN = re.compile(r"^(?:(\w+)-)?(\d+)-(\d+)-(\d+)(\w+)?-(?:(\d+)-)?")


class CompletionCandidate(typ.Protocol):
    """Minimal candidate shape required by completion policy."""

    @property
    def marker(self) -> str:
        """Completion marker naming the work this candidate finishes."""


def _marker_body(marker: str) -> str:
    """Return ``marker`` without surrounding parentheses."""
    return marker.removeprefix("(").removesuffix(")")


def _marker_pattern(marker: str) -> re.Pattern[str]:
    """Return the exact-or-dotted history pattern for ``marker``."""
    return re.compile(rf"\({re.escape(_marker_body(marker))}\.?\)")


def completion_marker_for_branch(branch_name: str) -> str | None:
    """Return the completion marker implied by ``branch_name``, if recognized."""
    issue_match = _ISSUE_BRANCH_PATTERN.match(branch_name)
    if issue_match:
        return f"(#{issue_match.group(1)})"

    roadmap_match = _ROADMAP_BRANCH_PATTERN.match(branch_name)
    if roadmap_match is None:
        return None

    namespace, major, minor, patch, suffix, task = roadmap_match.groups()
    patch_reference = f"{patch}{suffix or ''}"
    parts = [major, minor, patch_reference]
    if namespace:
        parts.insert(0, namespace)
    if task:
        parts.append(task)
    return f"({'.'.join(parts)})"


def has_completion_marker(messages: typ.Iterable[str], marker: str) -> bool:
    """Return whether any commit message contains ``marker`` or its dotted form."""
    marker_pattern = _marker_pattern(marker)
    return any(marker_pattern.search(message) is not None for message in messages)


def completed_candidates[CandidateT: CompletionCandidate](
    candidates: typ.Iterable[CandidateT],
    messages: typ.Iterable[str],
) -> list[CandidateT]:
    """Return candidates whose completion markers appear in commit history."""
    candidate_list = list(candidates)
    candidates_by_marker = {
        _marker_body(candidate.marker): candidate for candidate in candidate_list
    }
    if not candidates_by_marker:
        return []

    marker_alternatives = "|".join(
        re.escape(marker_body) for marker_body in candidates_by_marker
    )
    marker_pattern = re.compile(rf"\(({marker_alternatives})\.?\)")
    matched_markers: set[str] = set()
    for message in messages:
        matched_markers.update(marker_pattern.findall(message))
        if len(matched_markers) == len(candidates_by_marker):
            break

    return [
        candidate
        for candidate in candidate_list
        if _marker_body(candidate.marker) in matched_markers
    ]
