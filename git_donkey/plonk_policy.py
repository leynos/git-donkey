"""Pure completion policy for git-plonk.

This module contains no GitPython, filesystem, or process mutation. It maps
branch names to the completion markers and issue numbers they imply, and scans
commit messages for offline evidence that the work those names describe has
landed on trunk. Infrastructure code uses the evidence to decide which
worktrees are safe to clean.

Three branch grammars are recognized, in this order:

1. issue branches such as ``issue-123-short-title``;
2. the df12-build roadmap grammar, ``roadmap-2-2-1`` and
   ``roadmap-2-2-1-addendum``, whose dotted id is un-namespaced; and
3. the legacy roadmap grammar, such as ``road-1-2-3a-4-short-title``, whose
   mappings are preserved.

An issue branch additionally enables the history signals GitHub writes into
commit messages: closing keywords, the branch named by a classic merge commit,
and the branch name appearing verbatim in a bounded context. A branch that
follows none of the grammars carries no offline matcher at all; the GitHub
merged-pull-request fallback in :mod:`git_donkey.plonk_github` answers for it.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from git_donkey.plonk_records import _CompletionEvidence

_ISSUE_BRANCH_PATTERN = re.compile(r"^issue-(\d+)-")
_DF12_ROADMAP_BRANCH_PATTERN = re.compile(r"^roadmap-(\d+(?:-\d+){1,3})(?:-addendum)?$")
# The historical legacy grammar. The trailing separator is optional so a task
# id may end the name, while the version triple itself stays mandatory.
_LEGACY_ROADMAP_BRANCH_PATTERN = re.compile(
    r"^(?:(\w+)-)?(\d+)-(\d+)-(\d+)(\w+)?(?:-(\d+))?(?:-|$)"
)
_CLOSING_KEYWORD_PATTERN = (
    r"(?<![\w-])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
    r"#{issue_number}(?!\d)"
)
# A branch name is a complete token only when the characters around it cannot
# continue a ref. Git forbids these characters in ref names; whitespace and the
# prose quoting characters below are also accepted as token boundaries.
_FORBIDDEN_REF_CHARS = frozenset("~^:?*[\\")
_PROSE_QUOTES = frozenset("`\"'")


class CompletionCandidate(typ.Protocol):
    """Minimal candidate shape required by completion policy."""

    # Read-only properties state the contract precisely: the policy only ever
    # reads these values, so immutable candidates such as frozen dataclasses
    # satisfy it. Plain attribute declarations would require writable
    # attributes and exclude them.
    @property
    def branch_name(self) -> str:
        """Local branch name the candidate checks out."""

    @property
    def marker(self) -> str | None:
        """Completion marker implied by the branch name, if any."""

    @property
    def issue_number(self) -> int | None:
        """Issue number the branch name names, if any."""


def _marker_body(marker: str) -> str:
    """Return ``marker`` without surrounding parentheses."""
    return marker.removeprefix("(").removesuffix(")")


def _marker_pattern(marker: str) -> re.Pattern[str]:
    """Return the exact-or-dotted history pattern for ``marker``."""
    return re.compile(rf"\({re.escape(_marker_body(marker))}\.?\)")


def _df12_roadmap_marker(branch_name: str) -> str | None:
    """Return the dotted marker for a df12-build roadmap branch, if it matches."""
    match = _DF12_ROADMAP_BRANCH_PATTERN.fullmatch(branch_name)
    if match is None:
        return None
    return f"({match.group(1).replace('-', '.')})"


def _legacy_roadmap_marker(branch_name: str) -> str | None:
    """Return the marker for a legacy roadmap branch, if it matches."""
    match = _LEGACY_ROADMAP_BRANCH_PATTERN.match(branch_name)
    if match is None:
        return None

    namespace, major, minor, patch, suffix, task = match.groups()
    patch_reference = f"{patch}{suffix or ''}"
    parts = [major, minor, patch_reference]
    if namespace:
        parts.insert(0, namespace)
    if task:
        parts.append(task)
    return f"({'.'.join(parts)})"


def issue_number_for_branch(branch_name: str) -> int | None:
    """Return the issue number ``branch_name`` names, if it is an issue branch.

    Parameters
    ----------
    branch_name : str
        Local branch name to classify.

    Returns
    -------
    int | None
        The issue number, or ``None`` when the branch follows another
        convention or none at all. The number enables the issue-only history
        matchers.

    Examples
    --------
    >>> issue_number_for_branch("issue-62-polymer-corrections")
    62
    >>> issue_number_for_branch("roadmap-2-2-1") is None
    True

    """
    match = _ISSUE_BRANCH_PATTERN.match(branch_name)
    if match is None:
        return None
    return int(match.group(1))


def completion_marker_for_branch(branch_name: str) -> str | None:
    """Return the completion marker implied by ``branch_name``, if recognized.

    Issue branches map to a GitHub issue reference. df12-build roadmap branches
    map to their un-namespaced dotted id, with or without an ``-addendum``
    suffix. Legacy roadmap branches map to a dotted reference assembled from the
    optional namespace, the version triple with its optional patch suffix, and
    the optional task number.

    Parameters
    ----------
    branch_name : str
        Local branch name to classify.

    Returns
    -------
    str | None
        The parenthesised completion marker, or ``None`` when ``branch_name``
        follows none of the recognized conventions.

    Examples
    --------
    >>> completion_marker_for_branch("issue-123-fix-bug")
    '(#123)'
    >>> completion_marker_for_branch("roadmap-2-2-1")
    '(2.2.1)'
    >>> completion_marker_for_branch("roadmap-2-2-1-addendum")
    '(2.2.1)'
    >>> completion_marker_for_branch("road-1-2-3a-4-finished-task")
    '(road.1.2.3a.4)'
    >>> completion_marker_for_branch("feature/unstructured") is None
    True
    """
    issue_number = issue_number_for_branch(branch_name)
    if issue_number is not None:
        return f"(#{issue_number})"

    # The df12 grammar must win over the legacy grammar for names such as
    # ``roadmap-2-2-1``, which the legacy namespace rule would otherwise claim
    # as ``(roadmap.2.2.1)``.
    df12_marker = _df12_roadmap_marker(branch_name)
    if df12_marker is not None:
        return df12_marker
    return _legacy_roadmap_marker(branch_name)


@dataclasses.dataclass(frozen=True, slots=True)
class _OfflineMatchers:
    """History matchers one candidate enables."""

    candidate: CompletionCandidate
    marker_pattern: re.Pattern[str] | None
    closing_keyword: re.Pattern[str] | None
    merge_commit: re.Pattern[str] | None

    @property
    def has_matchers(self) -> bool:
        """Whether any history matcher can resolve the candidate."""
        return self.marker_pattern is not None or self.closing_keyword is not None

    def evidence_in(self, message: str) -> _CompletionEvidence | None:
        """Return the strongest evidence ``message`` supplies, if any."""
        if self.marker_pattern is not None and self.marker_pattern.search(message):
            return _CompletionEvidence("marker", self.candidate.marker or "")

        if self.closing_keyword is None:
            return None
        keyword = self.closing_keyword.search(message)
        if keyword is not None:
            return _CompletionEvidence("closing_keyword", keyword.group(0))
        if self.merge_commit is not None:
            merge = self.merge_commit.search(message)
            if merge is not None:
                return _CompletionEvidence(
                    "merge_commit",
                    f"{merge.group('owner')}/{self.candidate.branch_name}",
                )
        if _mentions_branch(message, self.candidate.branch_name):
            return _CompletionEvidence("branch_name", self.candidate.branch_name)
        return None


def _closing_keyword_pattern(issue_number: int) -> re.Pattern[str]:
    """Return the closing-keyword matcher for ``issue_number``."""
    return re.compile(
        _CLOSING_KEYWORD_PATTERN.format(issue_number=issue_number),
        re.IGNORECASE,
    )


def _merge_commit_pattern(branch_name: str) -> re.Pattern[str]:
    """Return the matcher for a merge commit naming ``branch_name`` exactly."""
    return re.compile(
        rf"(?m)^Merge pull request #\d+ from (?P<owner>[^\s/]+)/"
        rf"{re.escape(branch_name)}(?=\s|$)"
    )


def _is_branch_boundary(character: str) -> bool:
    """Return whether ``character`` can end a branch-name token by itself."""
    if not character:
        return True
    return character.isspace() or character in _FORBIDDEN_REF_CHARS


def _is_quoted_pair(before: str, after: str) -> bool:
    """Return whether ``before`` and ``after`` are a matching prose quote pair."""
    return before == after and before in _PROSE_QUOTES


def _mentions_branch(message: str, branch_name: str) -> bool:
    """Return whether ``message`` names ``branch_name`` as a complete token."""
    start = 0
    while (index := message.find(branch_name, start)) != -1:
        before = message[index - 1] if index else ""
        after_index = index + len(branch_name)
        after = message[after_index] if after_index < len(message) else ""
        if _is_branch_boundary(before) and _is_branch_boundary(after):
            return True
        if _is_quoted_pair(before, after):
            quote_width = len(before)
            outer_before = (
                message[index - quote_width - 1] if index > quote_width else ""
            )
            after_quote_index = index + len(branch_name) + quote_width
            outer_after = (
                message[after_quote_index] if after_quote_index < len(message) else ""
            )
            if _is_branch_boundary(outer_before) and _is_branch_boundary(outer_after):
                return True
        start = index + 1
    return False


def _offline_matchers(candidate: CompletionCandidate) -> _OfflineMatchers:
    """Return the history matchers ``candidate`` enables."""
    marker_pattern = (
        _marker_pattern(candidate.marker) if candidate.marker is not None else None
    )
    if candidate.issue_number is None:
        return _OfflineMatchers(
            candidate=candidate,
            marker_pattern=marker_pattern,
            closing_keyword=None,
            merge_commit=None,
        )
    return _OfflineMatchers(
        candidate=candidate,
        marker_pattern=marker_pattern,
        closing_keyword=_closing_keyword_pattern(candidate.issue_number),
        merge_commit=_merge_commit_pattern(candidate.branch_name),
    )


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


def _resolve_messages(
    matcher_list: typ.Sequence[_OfflineMatchers],
    pending: set[int],
    messages: typ.Iterable[str],
) -> dict[int, _CompletionEvidence]:
    """Scan ``messages`` until every pending candidate resolves.

    ``pending`` is consumed in place, so the caller can tell which candidates
    remain unresolved. The scan stops as soon as it is empty.

    Returns
    -------
    dict[int, _CompletionEvidence]
        Evidence keyed by the index of the candidate that produced it.

    """
    found: dict[int, _CompletionEvidence] = {}
    for message in messages:
        for index in tuple(pending):
            evidence = matcher_list[index].evidence_in(message)
            if evidence is not None:
                found[index] = evidence
                pending.remove(index)
        if not pending:
            break
    return found


def completed_candidates[CandidateT: CompletionCandidate](
    candidates: typ.Iterable[CandidateT],
    messages: typ.Iterable[str],
) -> list[tuple[CandidateT, _CompletionEvidence]]:
    r"""Return candidates whose offline evidence appears in commit history.

    Each candidate is checked against the matchers its own name enables:
    its completion marker, and, for issue branches, the closing keywords,
    exact merge-commit branch, and bounded verbatim branch name GitHub writes
    into commit messages. Within one message the marker wins over the weaker
    signals. The scan stops as soon as every candidate with an offline matcher
    has resolved, so a long history is not read in full when the matches appear
    early. Candidates without an offline matcher are left for the GitHub
    fallback and never keep the scan running.

    Parameters
    ----------
    candidates : typ.Iterable[CandidateT]
        Candidates to classify. Each must expose ``branch_name``, ``marker``,
        and ``issue_number``.
    messages : typ.Iterable[str]
        Commit messages to scan, consumed lazily.

    Returns
    -------
    list[tuple[CandidateT, _CompletionEvidence]]
        The completed candidates paired with the evidence that completed them,
        in the order the candidates were supplied. Empty when no candidate
        resolves.

    Examples
    --------
    >>> from pathlib import Path
    >>> from git_donkey.plonk_records import _PlonkCandidate
    >>> candidate = _PlonkCandidate(
    ...     "issue-62-fix", Path("/worktrees/issue-62-fix"), "(#62)", 62
    ... )
    >>> completed = completed_candidates(
    ...     [candidate], ["Fix work (#63)\n\nCloses #62"]
    ... )
    >>> [(one.branch_name, evidence.signal) for one, evidence in completed]
    [('issue-62-fix', 'closing_keyword')]

    """
    candidate_list = list(candidates)
    matcher_list = [_offline_matchers(candidate) for candidate in candidate_list]
    pending = {
        index for index, matchers in enumerate(matcher_list) if matchers.has_matchers
    }
    if not pending:
        return []

    found = _resolve_messages(matcher_list, pending, messages)
    return [(candidate_list[index], found[index]) for index in sorted(found)]
