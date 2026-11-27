"""Unit tests for the ``git-plonk`` completed-cleanup workflow.

One sweep walks the completed candidates and turns each into an outcome: a
worktree Git removed, one the preflight or Git itself refused, a branch that
went with its worktree, and a branch Git would not delete. These tests pin that
workflow with readable examples — the skip that keeps one dirty worktree from
abandoning the batch, the hard-mode branch rule, dry runs that plan without
mutating, the failures that are reported rather than fatal, and the bounded
record each step leaves behind. The doubles and builders they share with the
generated batches in ``test_plonk_cleanup_properties.py`` live in
``plonk_cleanup_helpers``; the adapter's own contract against Git is covered in
``test_plonk_worktree_adapter.py``, and the soft pass in
``test_plonk_soft_mode.py``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from git_donkey import plonk, plonk_github, plonk_records
from tests.unit.plonk_cleanup_helpers import (
    WORKTREE_BRANCH,
    AdapterBehaviour,
    CleanupRunOptions,
    FailingGitAdapter,
    RecordingGitAdapter,
    candidate,
    cleanup_context,
    marker_for,
    run_cleanup,
)

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder


def test_dirty_candidate_does_not_abandon_its_clean_siblings() -> None:
    """A skipped worktree should not stop the batch, or the sweep is pointless."""
    dirty = candidate("issue-456-dirty", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(dirty), marker_for(clean)],
        behaviour=AdapterBehaviour(
            skip_reasons={dirty.worktree_path: plonk_records._SkipReason.DIRTY},
        ),
    )

    result = run_cleanup(
        [dirty, clean],
        adapter,
        mode=plonk._PlonkMode.DEFAULT,
    )

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the clean worktree is removed even though its sibling was skipped"
    )
    assert result.skipped_worktrees == (
        plonk_records._SkippedWorktree(
            dirty.worktree_path, plonk_records._SkipReason.DIRTY
        ),
    ), "the dirty worktree is reported with the reason it was left alone"
    assert adapter.removed == [clean.worktree_path], (
        "the dirty worktree is never handed to Git for removal"
    )


def test_hard_mode_keeps_the_branch_of_a_skipped_worktree() -> None:
    """Hard mode deletes completed branches, but only those whose worktree went."""
    clean = candidate("issue-123-clean", 123)
    dirty = candidate("issue-456-dirty", 456)
    adapter = RecordingGitAdapter(
        [marker_for(clean), marker_for(dirty)],
        behaviour=AdapterBehaviour(
            skip_reasons={dirty.worktree_path: plonk_records._SkipReason.DIRTY},
        ),
    )

    result = run_cleanup([clean, dirty], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_branches == (clean.branch_name,), (
        "only the branch whose worktree was actually removed is deleted"
    )
    assert adapter.deleted == [clean.branch_name], (
        "the skipped worktree keeps the branch that holds its uncommitted work"
    )


def test_dry_run_reports_skips_without_mutating() -> None:
    """A preview should name the worktrees a real run would leave alone."""
    clean = candidate("issue-123-clean", 123)
    gone = candidate("issue-456-gone", 456)
    adapter = RecordingGitAdapter(
        [marker_for(clean), marker_for(gone)],
        behaviour=AdapterBehaviour(
            skip_reasons={gone.worktree_path: plonk_records._SkipReason.UNAVAILABLE},
        ),
    )

    result = run_cleanup(
        [clean, gone],
        adapter,
        mode=plonk._PlonkMode.HARD,
        options=CleanupRunOptions(dry_run=True),
    )

    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (clean.worktree_path,), (
        "the clean worktree is planned for removal"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "the clean branch is planned for deletion in hard mode"
    )
    assert result.skipped_worktrees == (
        plonk_records._SkippedWorktree(
            gone.worktree_path, plonk_records._SkipReason.UNAVAILABLE
        ),
    ), "the missing worktree is reported as skipped"
    assert not adapter.removed, "a dry run removes no worktree"
    assert not adapter.deleted, "a dry run deletes no branch"


def test_failed_removal_is_skipped_and_the_batch_continues() -> None:
    """A removal Git refuses should report a skip, not abandon the sweep."""
    stubborn = candidate("issue-456-stubborn", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(stubborn), marker_for(clean)],
        behaviour=AdapterBehaviour(
            removal_failures=frozenset({stubborn.worktree_path})
        ),
    )

    result = run_cleanup([stubborn, clean], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the following clean candidate is still removed"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "a branch is only deleted once its worktree is gone"
    )
    assert result.skipped_worktrees == (
        plonk_records._SkippedWorktree(
            stubborn.worktree_path, plonk_records._SkipReason.REMOVAL_FAILED
        ),
    ), "the failed removal is reported with its own reason"


@pytest.mark.parametrize(
    ("mode", "expected_branches"),
    [
        (plonk._PlonkMode.DEFAULT, ()),
        (plonk._PlonkMode.HARD, ("issue-123-fix",)),
    ],
)
def test_dry_run_completed_mode_reports_plans_without_mutating(
    mode: plonk._PlonkMode,
    expected_branches: tuple[str, ...],
) -> None:
    """Dry-run completed cleanup should plan work without destructive Git APIs."""
    completed = candidate(WORKTREE_BRANCH, 123)

    result = run_cleanup(
        [completed],
        FailingGitAdapter(),
        mode=mode,
        options=CleanupRunOptions(dry_run=True),
    )

    assert result.mode is mode, "expected completed dry-run mode to be preserved"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (completed.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        f"expected planned branch deletion for {mode.value} mode"
    )


def test_failed_branch_deletion_is_reported_and_the_batch_continues() -> None:
    """A branch Git refuses to delete should be reported, not fatal."""
    stubborn = candidate("issue-456-stubborn", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(stubborn), marker_for(clean)],
        behaviour=AdapterBehaviour(deletion_failures=frozenset({stubborn.branch_name})),
    )

    result = run_cleanup([stubborn, clean], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_worktrees == (stubborn.worktree_path, clean.worktree_path), (
        "both worktrees are still removed"
    )
    assert result.failed_branch_deletions == (stubborn.branch_name,), (
        "the surviving branch is reported as a failed deletion"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "a branch is only reported as removed once Git deleted it"
    )
    assert not result.skipped_worktrees, (
        "a failed branch deletion is not a skipped worktree"
    )
    assert adapter.deleted == [clean.branch_name], (
        "the following candidate's branch is still deleted"
    )


def test_skipped_candidate_records_its_bounded_reason(
    recording_recorder: RecordingRecorder,
) -> None:
    """A skipped worktree should record why the preflight refused it."""
    dirty = candidate("issue-456-dirty", 456)
    adapter = RecordingGitAdapter(
        [marker_for(dirty)],
        behaviour=AdapterBehaviour(
            skip_reasons={dirty.worktree_path: plonk_records._SkipReason.DIRTY},
        ),
    )

    run_cleanup([dirty], adapter, mode=plonk._PlonkMode.DEFAULT)

    assert len(recording_recorder.observations) == 1, (
        "a skipped candidate records the preflight and nothing else"
    )
    record = recording_recorder.observations[0]
    assert record.operation == "worktree_preflight", "the preflight records the skip"
    assert record.outcome == "skipped", "the skip is an outcome of its own"
    assert record.skip_reason == "dirty", "the reason is a bounded label"
    assert record.mode == "default", "the record names the mode the sweep ran in"
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )


def test_refused_removal_and_deletion_record_bounded_failures(
    recording_recorder: RecordingRecorder,
) -> None:
    """Each refused Git command should record a failure with its error class."""
    stubborn = candidate("issue-456-stubborn", 456)
    branchless = candidate("issue-789-branchless", 789)
    adapter = RecordingGitAdapter(
        [marker_for(stubborn), marker_for(branchless)],
        behaviour=AdapterBehaviour(
            removal_failures=frozenset({stubborn.worktree_path}),
            deletion_failures=frozenset({branchless.branch_name}),
        ),
    )

    run_cleanup([stubborn, branchless], adapter, mode=plonk._PlonkMode.HARD)

    assert recording_recorder.outcomes("worktree_removal") == ["failure", "success"], (
        "the refused removal is recorded before the removal that succeeded"
    )
    assert recording_recorder.error_kinds("worktree_removal") == [
        "git_command_error"
    ], "the failed removal names the error class"
    assert recording_recorder.outcomes("branch_deletion") == ["failure"], (
        "the refused deletion records a failure"
    )
    assert recording_recorder.error_kinds("branch_deletion") == ["git_command_error"], (
        "the failed deletion names the error class"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )
    assert recording_recorder.leaked_details(("/repo.worktrees/", "issue-")) == set(), (
        "no path or branch name reaches the bounded vocabulary"
    )


@pytest.mark.parametrize(
    ("failed_branch_deletions", "expected_exit_code"),
    [
        pytest.param((), 0, id="complete-sweep"),
        pytest.param(("issue-123-fix",), 1, id="surviving-branch"),
    ],
)
def test_run_git_plonk_reports_partial_sweeps(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_branch_deletions: tuple[str, ...],
    expected_exit_code: int,
) -> None:
    """A branch the sweep could not delete should reach the caller's exit code."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(Path("/repo.worktrees/issue-123-fix"),),
        failed_branch_deletions=failed_branch_deletions,
    )
    monkeypatch.setattr(plonk, "_load_plonk_context", lambda: None)
    monkeypatch.setattr(
        plonk, "_run_completed_cleanup", lambda *_args, **_kwargs: result
    )

    exit_code = plonk.run_git_plonk(hard=True)

    assert exit_code == expected_exit_code, "the sweep reports what it left behind"
    summary = capsys.readouterr().out
    assert ("Failed branch deletions:" in summary) is bool(failed_branch_deletions), (
        "the summary names the branch that survived, and only then"
    )


_GITHUB_REMOTE_URL = "git@github.com:leynos/demo.git"
_DESCRIPTIVE_BRANCH = "fix-markdown-gates"


def _descriptive_candidate(
    branch_name: str = _DESCRIPTIVE_BRANCH,
    worktree_path: Path | None = None,
) -> plonk_records._PlonkCandidate:
    """Return a candidate whose branch name enables no offline matcher."""
    return plonk_records._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=worktree_path or Path(f"/repo.worktrees/{branch_name}"),
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
        )
    ], "expected the lookup to use the raw remote owner and the advertised base"


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


def test_github_unknown_leaves_the_candidate_unverified() -> None:
    """A failed lookup should retain the worktree and say why."""
    worktree = _descriptive_candidate()
    lookup = LookupStub(plonk_github._MergedPullRequest("unknown"))

    result = run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
        ),
    )

    assert not result.removed_worktrees, "a failed lookup must never justify a removal"
    assert result.unverified_worktrees == (
        plonk._UnverifiedWorktree(
            worktree.worktree_path,
            plonk_records._UnverifiedReason.GITHUB_UNKNOWN,
        ),
    ), "expected the failed lookup to be reported as unverified"


def test_github_not_merged_leaves_the_candidate_unverified() -> None:
    """A definitive absence of a merged pull request is still not completion."""
    worktree = _descriptive_candidate()
    lookup = LookupStub(plonk_github._MergedPullRequest("not_merged"))

    result = run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
        ),
    )

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
    assert stderr.count("gh is not installed") == 1, (
        "expected one notice for the whole run"
    )
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
    completed = candidate("issue-123-fix", 123)
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
    worktree = _descriptive_candidate()
    lookup = LookupStub(plonk_github._MergedPullRequest("merged", 61))

    run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
        ),
    )

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
    worktree = _descriptive_candidate()
    lookup = LookupStub(plonk_github._MergedPullRequest("unknown"))

    run_cleanup(
        [worktree],
        RecordingGitAdapter([]),
        mode=plonk._PlonkMode.DEFAULT,
        options=CleanupRunOptions(
            context=_github_context([worktree]),
            github_lookup=lookup,
        ),
    )

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


def test_removal_observation_carries_the_completion_signal(
    recording_recorder: RecordingRecorder,
) -> None:
    """A real removal should record the signal that justified it."""
    clean = candidate("issue-123-clean", 123)

    run_cleanup(
        [clean],
        RecordingGitAdapter([marker_for(clean)]),
        mode=plonk._PlonkMode.DEFAULT,
    )

    signals = [
        observation.signal
        for observation in recording_recorder.observations
        if observation.operation == "worktree_removal"
        and observation.outcome == "success"
    ]
    assert signals == ["marker"], (
        "expected the removal observation to carry the marker signal"
    )
