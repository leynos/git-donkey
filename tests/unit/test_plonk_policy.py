"""Unit tests for ``git-plonk``'s offline completion recognition.

A branch is only eligible for offline cleanup when its own name implies work
that trunk history can confirm. These tests pin the three recognisers — the
df12-build roadmap grammar, the legacy roadmap grammar, and the issue branch
grammar — and then the history matchers each grammar enables: the completion
marker, the issue-closing keywords GitHub propagates into squash bodies, the
exact branch named by a classic merge commit, and the branch name appearing
verbatim. The scan itself is pinned here too: it returns evidence in candidate
order, prefers marker evidence within a message, and stops as soon as every
candidate with an offline matcher has resolved.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from git_donkey import plonk_policy, plonk_records

_ISSUE_BRANCH = "issue-62-polymer-corrections"
_ISSUE_NUMBER = 62
# History messages consumed when two offline candidates each resolve from one
# message: one per candidate, and no look at the trailing message.
_EXPECTED_CONSUMED_HISTORY_MESSAGES = 2


def _issue_candidate(
    branch_name: str = _ISSUE_BRANCH,
    *,
    issue_number: int = _ISSUE_NUMBER,
) -> plonk_records._PlonkCandidate:
    """Return an issue-branch candidate carrying its marker and issue number."""
    return plonk_records._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=Path(f"/repo.worktrees/{branch_name}"),
        marker=f"(#{issue_number})",
        issue_number=issue_number,
    )


def _branch_candidate(branch_name: str) -> plonk_records._PlonkCandidate:
    """Return a candidate whose name implies no offline matcher."""
    return plonk_records._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=Path(f"/repo.worktrees/{branch_name}"),
    )


@pytest.mark.parametrize(
    ("branch_name", "marker"),
    [
        pytest.param("roadmap-2-2-1", "(2.2.1)", id="three-components"),
        pytest.param("roadmap-6-4-1", "(6.4.1)", id="second-bare-task"),
        pytest.param("roadmap-2-2", "(2.2)", id="two-components"),
        pytest.param("roadmap-1-2-3-4", "(1.2.3.4)", id="four-components"),
        pytest.param("roadmap-2-2-1-addendum", "(2.2.1)", id="addendum"),
        pytest.param("roadmap-1-2-addendum", "(1.2)", id="shallow-addendum"),
    ],
)
def test_df12_roadmap_shapes_map_to_un_namespaced_dotted_markers(
    branch_name: str,
    marker: str,
) -> None:
    """The df12 grammar should complete on its dotted id, suffix or not."""
    assert plonk_policy.completion_marker_for_branch(branch_name) == marker, (
        "expected the df12 roadmap branch to map to its dotted id"
    )


@pytest.mark.parametrize(
    ("branch_name", "marker"),
    [
        pytest.param("roadmap-1", None, id="single-component"),
        pytest.param("roadmap-1-2-3-4-5", None, id="five-components"),
        pytest.param("roadmap-2-2-description", None, id="descriptive-suffix"),
        pytest.param("roadmap-2-2-1-addendum-extra", None, id="extra-addendum"),
        pytest.param("feature/roadmap-2-2-1", None, id="not-at-start"),
    ],
)
def test_df12_grammar_is_fully_anchored(
    branch_name: str,
    marker: str | None,
) -> None:
    """Only complete df12 shapes should be claimed by the df12 recogniser.

    The legacy recogniser may still map a name such as ``roadmap-2-2-1-extra``
    through its historical namespace rule, so the df12 recogniser is asserted
    directly rather than through the combined result.
    """
    assert plonk_policy._df12_roadmap_marker(branch_name) == marker, (
        "expected the df12 recogniser to accept only fully anchored shapes"
    )


@pytest.mark.parametrize(
    ("branch_name", "marker"),
    [
        pytest.param(
            "road-1-2-3a-4-finished-task",
            "(road.1.2.3a.4)",
            id="namespace-suffix-task-and-description",
        ),
        pytest.param(
            "road-1-2-3a-4",
            "(road.1.2.3a.4)",
            id="task-at-end-of-name",
        ),
        pytest.param("road-1-2-3a", "(road.1.2.3a)", id="suffix-at-end"),
        pytest.param("road-1-2-3", "(road.1.2.3)", id="version-at-end"),
        pytest.param(
            "1-20-300-4-implement-plonk",
            "(1.20.300.4)",
            id="no-namespace",
        ),
        pytest.param(
            "1-20-300suffix-4-implement-plonk",
            "(1.20.300suffix.4)",
            id="no-namespace-with-suffix",
        ),
    ],
)
def test_legacy_roadmap_mappings_are_preserved_and_extended(
    branch_name: str,
    marker: str,
) -> None:
    """The legacy grammar should keep its mappings and accept a bare tail."""
    assert plonk_policy.completion_marker_for_branch(branch_name) == marker, (
        "expected the legacy roadmap branch to map to its dotted reference"
    )


@pytest.mark.parametrize(
    ("branch_name", "issue_number"),
    [
        pytest.param(_ISSUE_BRANCH, _ISSUE_NUMBER, id="issue-branch"),
        pytest.param("issue-7-short", 7, id="small-number"),
        pytest.param("roadmap-2-2-1", None, id="df12-roadmap"),
        pytest.param("road-1-2-3a-4-task", None, id="legacy-roadmap"),
        pytest.param("feature/plain", None, id="unrecognised"),
    ],
)
def test_issue_number_is_exposed_only_for_issue_branches(
    branch_name: str,
    issue_number: int | None,
) -> None:
    """Only issue branches should enable the issue-only history matchers."""
    assert plonk_policy.issue_number_for_branch(branch_name) == issue_number, (
        "expected the issue number to be exposed exactly for issue branches"
    )


def test_shared_marker_completes_every_candidate_claiming_it() -> None:
    """One marker should complete every issue branch that shares it."""
    first = _issue_candidate("issue-123-first", issue_number=123)
    second = _issue_candidate("issue-123-second", issue_number=123)

    completed = plonk_policy.completed_candidates(
        [first, second],
        ["Merge pull request (#123)"],
    )

    shared_evidence = plonk_records._CompletionEvidence("marker", "(#123)")
    assert [candidate for candidate, _ in completed] == [first, second], (
        "expected one shared marker to complete both candidates in input order"
    )
    assert [evidence for _, evidence in completed] == [shared_evidence] * 2, (
        "expected both candidates to report the shared marker"
    )


def test_completed_candidates_return_input_order_not_match_order() -> None:
    """Evidence should be reported in candidate order, whatever order it matched."""
    first = _issue_candidate("issue-123-first", issue_number=123)
    second = _issue_candidate("issue-456-second", issue_number=456)

    completed = plonk_policy.completed_candidates(
        [first, second],
        ["Second complete (#456)", "First complete (#123)"],
    )

    assert [candidate for candidate, _ in completed] == [first, second], (
        "expected candidates to stay in the order the sweep selected them"
    )


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("Closes #62", id="closes"),
        pytest.param("Closed #62", id="closed"),
        pytest.param("closes: #62", id="lowercase-with-colon"),
        pytest.param("Fixes #62", id="fixes"),
        pytest.param("Fixed #62", id="fixed"),
        pytest.param("Resolves #62", id="resolves"),
        pytest.param("Resolved: #62", id="resolved-with-colon"),
        pytest.param("Close #62", id="close"),
        pytest.param("Fix #62", id="fix"),
        pytest.param("Resolve #62", id="resolve"),
        pytest.param("Squash merge\n\nFixes #62.", id="body-sentence"),
    ],
)
def test_issue_closing_keywords_complete_an_issue_branch(message: str) -> None:
    """Closing keywords should complete the issue branch they name."""
    candidate = _issue_candidate()

    completed = plonk_policy.completed_candidates([candidate], [message])

    assert len(completed) == 1, "expected the closing keyword to complete the branch"
    _candidate, evidence = completed[0]
    assert evidence.signal == "closing_keyword", (
        "expected the closing keyword to be the reported signal"
    )
    assert "#62" in evidence.detail, (
        "expected the evidence to quote the matched closing keyword"
    )


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("Closes #620", id="longer-number"),
        pytest.param("Closes #6", id="shorter-number"),
        pytest.param("Closes #63", id="different-number"),
        pytest.param("Prefixes #62", id="keyword-inside-a-word"),
        pytest.param("hotfix #62", id="suffix-inside-a-word"),
        pytest.param("Closes leynos/other#62", id="owner-prefixed-reference"),
        pytest.param("Closes issue#62", id="word-before-reference"),
    ],
)
def test_unrelated_references_do_not_complete_an_issue_branch(message: str) -> None:
    """Only a bounded closing keyword should complete the branch."""
    completed = plonk_policy.completed_candidates(
        [_issue_candidate()],
        [message],
    )

    assert not completed, (
        "expected a reference that is not an exact closing keyword to be ignored"
    )


def test_exact_merge_commit_completes_an_issue_branch() -> None:
    """A classic merge commit naming the branch itself should complete it."""
    candidate = _issue_candidate()
    message = f"Merge pull request #63 from leynos/{_ISSUE_BRANCH}"

    completed = plonk_policy.completed_candidates([candidate], [message])

    assert completed == [
        (
            candidate,
            plonk_records._CompletionEvidence(
                "merge_commit",
                f"leynos/{_ISSUE_BRANCH}",
            ),
        )
    ], "expected the merge commit's branch segment to complete the branch"


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(
            f"Merge pull request #63 from leynos/{_ISSUE_BRANCH}-extra",
            id="longer-branch",
        ),
        pytest.param(
            "Merge pull request #63 from leynos/another-branch",
            id="different-branch",
        ),
        pytest.param(
            f"Merge pull request #63 from leynos/nested/{_ISSUE_BRANCH}",
            id="nested-branch",
        ),
    ],
)
def test_merge_commit_requires_the_exact_branch_segment(message: str) -> None:
    """A merge commit should only complete the branch it names exactly."""
    completed = plonk_policy.completed_candidates([_issue_candidate()], [message])

    assert not completed, (
        "expected a differently named merge-commit branch to be ignored"
    )


def test_verbatim_issue_branch_in_a_commit_message_completes_it() -> None:
    """A squash message naming the issue branch verbatim should complete it."""
    candidate = _issue_candidate()

    completed = plonk_policy.completed_candidates(
        [candidate],
        [f"Squash merge of {_ISSUE_BRANCH} into main"],
    )

    assert completed == [
        (
            candidate,
            plonk_records._CompletionEvidence("branch_name", _ISSUE_BRANCH),
        )
    ], "expected the verbatim branch name to complete the issue branch"


@pytest.mark.parametrize("quote", ["`", '"', "'"])
def test_verbatim_branch_in_prose_quotes_completes_it(quote: str) -> None:
    """A matching prose quote pair is a supported exact-name boundary."""
    candidate = _issue_candidate()

    completed = plonk_policy.completed_candidates(
        [candidate],
        [f"Merge {quote}{_ISSUE_BRANCH}{quote} into main"],
    )

    assert completed == [
        (
            candidate,
            plonk_records._CompletionEvidence("branch_name", _ISSUE_BRANCH),
        )
    ], "expected a quoted verbatim branch name to complete the issue branch"


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(f"prefix-{_ISSUE_BRANCH}", id="prefixed"),
        pytest.param(f"{_ISSUE_BRANCH}-extra", id="suffixed"),
        pytest.param(f"prefix-{_ISSUE_BRANCH}-suffix", id="both-sides"),
        pytest.param(f"{_ISSUE_BRANCH}+v2", id="plus-suffix"),
        pytest.param(f"{_ISSUE_BRANCH}@v2", id="at-suffix"),
        pytest.param(f"{_ISSUE_BRANCH}!retry", id="bang-suffix"),
        pytest.param(f"{_ISSUE_BRANCH}.v2", id="dot-suffix"),
        pytest.param(f"{_ISSUE_BRANCH}'follow-up", id="unpaired-quote-suffix"),
        pytest.param(
            f"prefix'{_ISSUE_BRANCH}'suffix",
            id="quoted-substring-in-longer-ref",
        ),
    ],
)
def test_verbatim_branch_matcher_is_bounded(message: str) -> None:
    """A branch name inside a longer token should not complete the branch."""
    completed = plonk_policy.completed_candidates([_issue_candidate()], [message])

    assert not completed, (
        "expected the bounded matcher to reject a branch-name substring"
    )


def test_marker_evidence_wins_within_one_message() -> None:
    """A message matching several matchers should report the strongest signal."""
    candidate = _issue_candidate()

    message = (
        f"Merge pull request #63 from leynos/{_ISSUE_BRANCH}\n\n"
        f"Closes #{_ISSUE_NUMBER}\n\nCompleted (#{_ISSUE_NUMBER})"
    )
    completed = plonk_policy.completed_candidates([candidate], [message])

    assert completed == [
        (candidate, plonk_records._CompletionEvidence("marker", "(#62)"))
    ], "expected the marker to win over the weaker matchers in the same message"


def test_issue_matchers_do_not_apply_to_descriptive_branches() -> None:
    """A descriptive branch has no offline matcher, even for a closing keyword."""
    completed = plonk_policy.completed_candidates(
        [_branch_candidate("fix-markdown-gates")],
        ["Closes #62"],
    )

    assert not completed, (
        "expected descriptive branches to rely on the GitHub fallback instead"
    )


def test_scan_stops_when_all_offline_candidates_resolve() -> None:
    """The scan should not consume history after the last offline match."""
    first = _issue_candidate("issue-123-first", issue_number=123)
    second = _issue_candidate("issue-456-second", issue_number=456)
    consumed_messages = 0

    def messages() -> typ.Iterator[str]:
        """Yield trunk messages, counting how many the consumer pulls."""
        nonlocal consumed_messages
        consumed_messages += 1
        yield "Complete first (#123)"
        consumed_messages += 1
        yield "Complete second (#456)"
        consumed_messages += 1
        yield "Unneeded late history"

    completed = plonk_policy.completed_candidates([first, second], messages())

    assert [candidate for candidate, _ in completed] == [first, second], (
        "expected both offline candidates to resolve"
    )
    assert consumed_messages == _EXPECTED_CONSUMED_HISTORY_MESSAGES, (
        "expected the scan to stop once every offline candidate resolved"
    )


def test_candidates_without_matchers_do_not_keep_the_scan_running() -> None:
    """An unrecognised branch should not prevent early termination."""
    issue = _issue_candidate("issue-123-first", issue_number=123)
    descriptive = _branch_candidate("fix-markdown-gates")
    consumed_messages = 0

    def messages() -> typ.Iterator[str]:
        """Yield one resolving message and one message that must stay unread."""
        nonlocal consumed_messages
        consumed_messages += 1
        yield "Complete first (#123)"
        consumed_messages += 1
        yield "Unneeded late history"

    completed = plonk_policy.completed_candidates([issue, descriptive], messages())

    assert [candidate for candidate, _ in completed] == [issue], (
        "expected only the candidate with an offline matcher to resolve"
    )
    assert consumed_messages == 1, (
        "expected a candidate with no matcher not to keep the scan alive"
    )


def test_completion_marker_has_a_history_matcher_still() -> None:
    """The exact-or-dotted marker matcher should survive the new scan."""
    marker = plonk_policy.completion_marker_for_branch("roadmap-2-2-1")

    assert marker == "(2.2.1)", "expected the df12 marker"
    assert plonk_policy.has_completion_marker(["Done (2.2.1)"], marker), (
        "expected the exact marker to match history"
    )
    assert plonk_policy.has_completion_marker(["Done (2.2.1.)"], marker), (
        "expected the dotted marker form to match history"
    )
