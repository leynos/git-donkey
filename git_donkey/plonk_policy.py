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

    marker: str


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
    marker_body = marker.removeprefix("(").removesuffix(")")
    marker_pattern = re.compile(rf"\({re.escape(marker_body)}\.?\)")
    return any(marker_pattern.search(message) is not None for message in messages)


def completed_candidates[CandidateT: CompletionCandidate](
    candidates: typ.Iterable[CandidateT],
    messages: typ.Iterable[str],
) -> list[CandidateT]:
    """Return candidates whose completion markers appear in commit history."""
    history_messages = tuple(messages)
    return [
        candidate
        for candidate in candidates
        if has_completion_marker(history_messages, candidate.marker)
    ]
