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
    """Return the completion marker implied by ``branch_name``, if recognized.

    Issue branches map to a GitHub issue reference. Roadmap branches map to a
    dotted reference assembled from the optional namespace, the version
    triple with its optional patch suffix, and the optional task number.

    Parameters
    ----------
    branch_name : str
        Local branch name to classify.

    Returns
    -------
    str | None
        The parenthesised completion marker, or ``None`` when ``branch_name``
        follows neither the issue nor the roadmap convention.

    Examples
    --------
    >>> completion_marker_for_branch("issue-123-fix-bug")
    '(#123)'
    >>> completion_marker_for_branch("road-1-2-3a-4-finished-task")
    '(road.1.2.3a.4)'
    >>> completion_marker_for_branch("feature/unstructured") is None
    True
    """
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
    """Return whether any commit message contains ``marker`` or its dotted form.

    Parameters
    ----------
    messages : typ.Iterable[str]
        Commit messages to scan. Consumed lazily and only until a match is
        found.
    marker : str
        Parenthesised completion marker, as returned by
        :func:`completion_marker_for_branch`.

    Returns
    -------
    bool
        ``True`` when a message contains ``marker`` exactly or in its dotted
        form, otherwise ``False``.

    Examples
    --------
    >>> has_completion_marker(["Merge pull request (#123)"], "(#123)")
    True
    >>> has_completion_marker(["Merge pull request (#123.)"], "(#123)")
    True
    >>> has_completion_marker(["Unrelated work"], "(#123)")
    False
    """
    marker_pattern = _marker_pattern(marker)
    return any(marker_pattern.search(message) is not None for message in messages)


def completed_candidates[CandidateT: CompletionCandidate](
    candidates: typ.Iterable[CandidateT],
    messages: typ.Iterable[str],
) -> list[CandidateT]:
    """Return candidates whose completion markers appear in commit history.

    Scanning stops as soon as every candidate marker has been seen, so a long
    history is not read in full when the matches appear early.

    Parameters
    ----------
    candidates : typ.Iterable[CandidateT]
        Candidates to filter. Each must expose a ``marker`` attribute.
    messages : typ.Iterable[str]
        Commit messages to scan, consumed lazily.

    Returns
    -------
    list[CandidateT]
        The candidates whose markers appear in ``messages``, in the order the
        candidates were supplied. Empty when ``candidates`` is empty.
    """
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
