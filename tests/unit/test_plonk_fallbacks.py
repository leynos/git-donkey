"""Unit tests for git-plonk's GitHub and upstream completion tiers.

These tests exercise the fallback tiers that resolve candidates no offline
matcher completes: the read-only GitHub merged-pull-request lookup, the opt-in
gone-upstream check, their ordering, and the unverified reporting they produce.
The core cleanup contract remains in ``test_plonk_cleanup.py``; the adapter's
Git and environment boundaries are covered in
``test_plonk_worktree_adapter.py``.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path

import pytest

from git_donkey import plonk, plonk_github, plonk_records
from tests.unit.plonk_cleanup_helpers import (
    AdapterBehaviour,
    CleanupRunOptions,
    RecordingGitAdapter,
    candidate,
    cleanup_context,
    marker_for,
    run_cleanup,
)

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder


_GITHUB_REMOTE_URL = "git@github.com:leynos/demo.git"
_DESCRIPTIVE_BRANCH = "fix-markdown-gates"
_COMMIT_ID = "1" * 40


def _descriptive_candidate(
    branch_name: str = _DESCRIPTIVE_BRANCH,
    worktree_path: Path | None = None,
    *,
    commit_id: str | None = _COMMIT_ID,
) -> plonk_records._PlonkCandidate:
    """Return a candidate whose branch name enables no offline matcher."""
    return plonk_records._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=worktree_path or Path(f"/repo.worktrees/{branch_name}"),
        commit_id=commit_id,
    )


def _github_context(
    candidates: typ.Iterable[plonk_records._PlonkCandidate],
    *,
    invoking_worktree: Path | None = None,
) -> plonk_records._PlonkContext:
    """Return a context whose principal remote is a GitHub URL."""
    return cleanup_context(
        candidates,
        remote_url=_GITHUB_REMOTE_URL,
        invoking_worktree=invoking_worktree,
    )


class LookupStub:
    """Merged-pull-request lookup double returning one fixed result."""

    def __init__(self, result: plonk_github._MergedPullRequest) -> None:
        """Keep the result to return and start with no recorded queries."""
        self.result = result
        self.queries: list[plonk_github._PullRequestQuery] = []

    def __call__(
        self,
        query: plonk_github._PullRequestQuery,
    ) -> plonk_github._MergedPullRequest:
        """Record ``query`` and return the configured result."""
        self.queries.append(query)
        return self.result


def _run_single_lookup(
    lookup_result: plonk_github._MergedPullRequest,
) -> tuple[plonk_records._PlonkCandidate, plonk._PlonkResult]:
    """Run one descriptive candidate through a fixed GitHub lookup result."""
    worktree = _descriptive_candidate()
    lookup = LookupStub(lookup_result)
    result = run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
        ),
    )
    return worktree, result


class ChangingUpstreamAdapter(RecordingGitAdapter):
    """Adapter double whose upstream verdict changes after the first query."""

    def __init__(self) -> None:
        """Start with no markers and a present-then-gone upstream sequence."""
        super().__init__([])
        self._states: typ.Iterator[plonk_records.UpstreamState] = iter((
            "present",
            "gone",
        ))
        self.calls = 0

    def upstream_state(self, branch_name: str) -> plonk_records.UpstreamState:
        """Return the next configured verdict and count the query."""
        self.calls += 1
        return next(self._states)


def test_upstream_verdict_is_cached_per_branch() -> None:
    """Every worktree on a branch should see the same upstream verdict."""
    first = _descriptive_candidate(worktree_path=Path("/repo.worktrees/first"))
    second = _descriptive_candidate(worktree_path=Path("/repo.worktrees/second"))
    adapter = ChangingUpstreamAdapter()

    result = run_cleanup(
        [first, second],
        adapter,
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=cleanup_context([first, second]),
            upstream_gone=True,
        ),
    )

    assert result.unverified_worktrees == (
        plonk_records._UnverifiedWorktree(
            first.worktree_path,
            plonk_records._UnverifiedReason.UPSTREAM_PRESENT,
        ),
        plonk_records._UnverifiedWorktree(
            second.worktree_path,
            plonk_records._UnverifiedReason.UPSTREAM_PRESENT,
        ),
    ), "expected both worktrees to share the first upstream verdict"
    assert adapter.calls == 1, "expected one upstream query per branch"


def test_github_fallback_removes_a_descriptive_branch() -> None:
    """A merged pull request should complete a branch no marker can describe."""
    worktree = _descriptive_candidate()
    lookup = LookupStub(plonk_github._MergedPullRequest("merged", 61))

    result = run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
        ),
    )

    assert result.removed_worktrees == (worktree.worktree_path,), (
        "expected the merged pull request to remove the worktree"
    )
    assert result.removal_evidence == (
        plonk_records._RemovalEvidence(
            worktree.worktree_path,
            plonk_records._CompletionEvidence("github_merged_pr", "#61"),
        ),
    ), "expected the removal to carry the merged pull-request evidence"
    assert not result.unverified_worktrees, (
        "expected a resolved candidate not to be reported as unverified"
    )
    assert lookup.queries == [
        plonk_github._PullRequestQuery(
            "leynos",
            "demo",
            _DESCRIPTIVE_BRANCH,
            "main",
            _COMMIT_ID,
        )
    ], "expected the lookup to use the branch tip and the advertised base"


def test_github_lookup_runs_once_per_branch() -> None:
    """Repeated branch names should not spend more than one lookup."""
    first = _descriptive_candidate(worktree_path=Path("/repo.worktrees/first"))
    second = _descriptive_candidate(worktree_path=Path("/repo.worktrees/second"))
    lookup = LookupStub(plonk_github._MergedPullRequest("merged", 61))

    result = run_cleanup(
        [first, second],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([first, second]),
            github_lookup=lookup,
        ),
    )

    assert len(lookup.queries) == 1, "expected one lookup for the shared branch"
    assert result.removed_worktrees == (first.worktree_path, second.worktree_path), (
        "expected the resolved branch to make every worktree claiming it removable"
    )


def test_github_lookup_records_a_failure_observation(
    recording_recorder: RecordingRecorder,
) -> None:
    """A failed lookup leaves one bounded failure at the process boundary."""
    _run_single_lookup(plonk_github._MergedPullRequest("unknown"))

    assert recording_recorder.span_operations() == ["github_lookup"], (
        "expected the gh process boundary to be timed"
    )
    assert [
        (observation.operation, observation.outcome)
        for observation in recording_recorder.observations
        if observation.operation == "github_lookup"
    ] == [("github_lookup", "failure")], (
        "expected the failed lookup to record one bounded failure"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )


def test_github_lookup_records_a_success_observation(
    recording_recorder: RecordingRecorder,
) -> None:
    """A successful lookup records one bounded success."""
    _run_single_lookup(plonk_github._MergedPullRequest("merged", 61))

    assert [
        (observation.operation, observation.outcome)
        for observation in recording_recorder.observations
        if observation.operation == "github_lookup"
    ] == [("github_lookup", "success")], (
        "expected the successful lookup to record one bounded success"
    )


def test_github_unknown_leaves_the_candidate_unverified() -> None:
    """A failed lookup should retain the worktree and say why."""
    worktree, result = _run_single_lookup(plonk_github._MergedPullRequest("unknown"))

    assert not result.removed_worktrees, "a failed lookup must never justify a removal"
    assert result.unverified_worktrees == (
        plonk._UnverifiedWorktree(
            worktree.worktree_path,
            plonk_records._UnverifiedReason.GITHUB_UNKNOWN,
        ),
    ), "expected the failed lookup to be reported as unverified"


def test_github_without_a_local_commit_fails_closed() -> None:
    """A candidate with no recorded commit cannot use a name-only merge."""
    worktree = _descriptive_candidate(commit_id=None)
    lookup = LookupStub(plonk_github._MergedPullRequest("merged", 61))

    result = run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
        ),
    )

    assert not result.removed_worktrees, (
        "expected a candidate without a commit to remain in place"
    )
    assert result.unverified_worktrees == (
        plonk_records._UnverifiedWorktree(
            worktree.worktree_path,
            plonk_records._UnverifiedReason.GITHUB_UNKNOWN,
        ),
    ), "expected the unverifiable lookup to be reported as unknown"
    assert not lookup.queries, (
        "expected the sweep not to spend a lookup it cannot verify"
    )


def test_github_not_merged_leaves_the_candidate_unverified() -> None:
    """A definitive absence of a merged pull request is still not completion."""
    worktree, result = _run_single_lookup(plonk_github._MergedPullRequest("not_merged"))

    assert result.unverified_worktrees == (
        plonk._UnverifiedWorktree(
            worktree.worktree_path,
            plonk_records._UnverifiedReason.NO_SIGNAL,
        ),
    ), "expected no merged pull request to leave the candidate unverified"


def test_missing_gh_is_noticed_once_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing tool should be reported once, and resolve nothing."""
    first = _descriptive_candidate("fix-markdown-gates")
    second = _descriptive_candidate("adopt-whitaker")
    monkeypatch.setattr(plonk_github, "gh_available", lambda: False)

    result = run_cleanup(
        [first, second],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(context=_github_context([first, second])),
    )

    stderr = capsys.readouterr().err
    notice = (
        f"{plonk_records.GIT_PLONK_PREFIX}: gh is not installed; merged "
        "pull-request lookups are unavailable"
    )
    assert stderr.count(notice) == 1, "expected one complete notice for the whole run"
    assert {one.worktree_path for one in result.unverified_worktrees} == {
        first.worktree_path,
        second.worktree_path,
    }, "expected every unresolved candidate to be reported as unverified"


def test_no_github_disables_the_fallback_without_a_notice(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The opt-out should neither query gh nor warn about it."""
    worktree = _descriptive_candidate()
    monkeypatch.setattr(
        plonk_github,
        "gh_available",
        lambda: pytest.fail("--no-github must not check for gh"),
    )

    result = run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            no_github=True,
        ),
    )

    assert not capsys.readouterr().err, "expected the opt-out to stay quiet"
    assert result.unverified_worktrees == (
        plonk._UnverifiedWorktree(
            worktree.worktree_path,
            plonk_records._UnverifiedReason.NO_SIGNAL,
        ),
    ), "expected the candidate to remain unverified offline"


def test_invoking_worktree_is_excluded_before_the_github_lookup() -> None:
    """The sweep should not spend a lookup on a worktree it must not touch."""
    worktree = _descriptive_candidate()
    lookup = LookupStub(plonk_github._MergedPullRequest("merged", 61))

    result = run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context(
                [worktree],
                invoking_worktree=worktree.worktree_path,
            ),
            github_lookup=lookup,
        ),
    )

    assert not lookup.queries, "expected no lookup for the invoking worktree"
    assert not result.removed_worktrees, "expected the invoking worktree to stay"
    assert not result.unverified_worktrees, (
        "expected the invoking worktree not to be reported as unverified"
    )


def test_history_resolution_precedes_the_github_lookup() -> None:
    """Offline evidence should settle a candidate before any network tier."""
    completed = dataclasses.replace(candidate("issue-123-fix", 123), commit_id="a" * 40)
    lookup = LookupStub(plonk_github._MergedPullRequest("merged", 61))

    result = run_cleanup(
        [completed],
        RecordingGitAdapter([marker_for(completed)]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([completed]),
            github_lookup=lookup,
        ),
    )

    assert not lookup.queries, "expected the offline tier to avoid a lookup"
    assert result.removal_evidence[0].evidence == plonk_records._CompletionEvidence(
        "marker", "(#123)"
    ), "expected the marker evidence to be reported"


@pytest.mark.parametrize(
    ("flag", "expected_reason"),
    [
        pytest.param(
            "absent", plonk_records._UnverifiedReason.NO_SIGNAL, id="flag-absent"
        ),
        pytest.param("present", None, id="flag-present"),
    ],
)
def test_upstream_gone_completes_only_when_enabled(
    flag: str,
    expected_reason: plonk_records._UnverifiedReason | None,
) -> None:
    """A gone upstream is evidence only when the caller opts into it."""
    worktree = _descriptive_candidate()
    adapter = RecordingGitAdapter(
        [],
        behaviour=AdapterBehaviour(
            upstream_states={_DESCRIPTIVE_BRANCH: "gone"},
        ),
    )

    result = run_cleanup(
        [worktree],
        adapter,
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=cleanup_context([worktree]),
            upstream_gone=flag == "present",
        ),
    )

    if expected_reason is None:
        assert result.removed_worktrees == (worktree.worktree_path,), (
            "expected the gone upstream to complete the branch"
        )
        assert result.removal_evidence[0].evidence.signal == "upstream_gone", (
            "expected the removal to name the upstream signal"
        )
        return
    assert result.unverified_worktrees == (
        plonk._UnverifiedWorktree(worktree.worktree_path, expected_reason),
    ), "expected the opt-in tier not to run when it is disabled"


@pytest.mark.parametrize(
    ("upstream", "reason"),
    [
        pytest.param(
            "present", plonk_records._UnverifiedReason.UPSTREAM_PRESENT, id="present"
        ),
        pytest.param(
            "unset", plonk_records._UnverifiedReason.UPSTREAM_UNSET, id="unset"
        ),
        pytest.param(
            "unknown", plonk_records._UnverifiedReason.UPSTREAM_UNKNOWN, id="unknown"
        ),
    ],
)
def test_unresolved_upstream_states_are_reported_distinctly(
    upstream: plonk_records.UpstreamState,
    reason: plonk_records._UnverifiedReason,
) -> None:
    """The summary should preserve why the upstream did not complete a branch."""
    worktree = _descriptive_candidate()
    adapter = RecordingGitAdapter(
        [],
        behaviour=AdapterBehaviour(
            upstream_states={_DESCRIPTIVE_BRANCH: upstream},
        ),
    )

    result = run_cleanup(
        [worktree],
        adapter,
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=cleanup_context([worktree]),
            upstream_gone=True,
        ),
    )

    assert result.unverified_worktrees == (
        plonk._UnverifiedWorktree(worktree.worktree_path, reason),
    ), f"expected the {upstream} upstream to be reported as {reason.value}"


def test_failed_github_lookup_outranks_upstream_presence() -> None:
    """A failed lookup is a stronger warning than a still-present upstream."""
    worktree = _descriptive_candidate()
    adapter = RecordingGitAdapter(
        [],
        behaviour=AdapterBehaviour(
            upstream_states={_DESCRIPTIVE_BRANCH: "present"},
        ),
    )
    lookup = LookupStub(plonk_github._MergedPullRequest("unknown"))

    result = run_cleanup(
        [worktree],
        adapter,
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
            upstream_gone=True,
        ),
    )

    assert result.unverified_worktrees == (
        plonk._UnverifiedWorktree(
            worktree.worktree_path,
            plonk_records._UnverifiedReason.GITHUB_UNKNOWN,
        ),
    ), "expected the failed lookup reason to survive the upstream tier"


def test_upstream_is_skipped_once_github_resolves() -> None:
    """A candidate the GitHub tier completes should never reach the upstream."""
    worktree = _descriptive_candidate()
    lookup = LookupStub(plonk_github._MergedPullRequest("merged", 61))

    result = run_cleanup(
        [worktree],
        RecordingGitAdapter(
            [],
            behaviour=AdapterBehaviour(fail_on_upstream=True),
        ),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
            upstream_gone=True,
        ),
    )

    assert result.removed_worktrees == (worktree.worktree_path,), (
        "expected the GitHub tier to resolve the candidate without the upstream"
    )


def test_github_resolution_records_a_bounded_signal(
    recording_recorder: RecordingRecorder,
) -> None:
    """The fallback should record its signal from the declared vocabulary."""
    _run_single_lookup(plonk_github._MergedPullRequest("merged", 61))

    resolutions = [
        observation
        for observation in recording_recorder.observations
        if observation.operation == "completion_resolution"
    ]
    assert [(one.outcome, one.signal) for one in resolutions] == [
        ("success", "github_merged_pr")
    ], "expected the fallback to record its bounded signal"
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )


def test_unverified_candidate_records_a_bounded_reason(
    recording_recorder: RecordingRecorder,
) -> None:
    """An unresolved candidate should record why, from the declared vocabulary."""
    _run_single_lookup(plonk_github._MergedPullRequest("unknown"))

    resolutions = [
        observation
        for observation in recording_recorder.observations
        if observation.operation == "completion_resolution"
    ]
    assert [(one.outcome, one.unverified_reason) for one in resolutions] == [
        ("unverified", "github_unknown")
    ], "expected the failed lookup to record its bounded reason"
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )
