"""Resolve git-plonk completion from the GitHub and upstream tiers.

Offline history is resolved in :mod:`git_donkey.plonk_policy`. This module
handles the candidates that remain: it asks the read-only GitHub
merged-pull-request fallback first, then the opt-in gone-upstream check, and
reports every candidate neither tier resolves as unverified. The tiers run in
that order, a branch is looked up at most once, and the invoking worktree has
already been removed from the candidate set by the caller.

Both tiers fail closed. A GitHub response that cannot be trusted and an
upstream that cannot be queried are unknown, never completion evidence.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import observability, plonk_policy
from git_donkey.plonk_records import (
    MergedPullRequestLookup,
    UpstreamState,
    _CompletionEvidence,
    _GitHubRepository,
    _GitPort,
    _MergedPullRequest,
    _PlonkCandidate,
    _PlonkContext,
    _PlonkMode,
    _PullRequestQuery,
    _UnverifiedReason,
    _UnverifiedWorktree,
)
from git_donkey.plonk_workflow import (
    _CandidateLeftEvent,
    _CleanupOptions,
    _log_candidate_left,
    _record_signal_step,
    _record_unverified,
)


@dataclasses.dataclass(slots=True)
class _SweepState:
    """Mutable state shared by one completed-cleanup sweep.

    Attributes
    ----------
    context : _PlonkContext
        Resolved repository state the sweep runs against.
    adapter : _GitPort
        Git surface used for history, upstream, and mutation.
    mode : _PlonkMode
        Cleanup mode the sweep runs in.
    options : _CleanupOptions
        Completion tiers enabled for this sweep.
    resolutions : dict[str, _CompletionEvidence]
        Evidence found so far, keyed by branch name.
    github_lookups : dict[str, tuple[_CompletionEvidence | None, _UnverifiedReason]]
        Merged-pull-request results already obtained, so each branch is looked
        up at most once per sweep.
    upstream_states : dict[str, UpstreamState]
        Upstream verdicts already obtained, so every worktree on a branch sees
        the same answer.
    github_lookup : MergedPullRequestLookup | None
        Merged-pull-request lookup resolved by the composition layer, or
        ``None`` when the tier is disabled or unavailable.
    handled_branches : set[str]
        Branches this sweep already deleted or planned for deletion, so a
        second worktree on the same branch contributes one summary entry.

    """

    context: _PlonkContext
    adapter: _GitPort
    mode: _PlonkMode
    options: _CleanupOptions
    resolutions: dict[str, _CompletionEvidence] = dataclasses.field(
        default_factory=dict
    )
    github_lookups: dict[str, tuple[_CompletionEvidence | None, _UnverifiedReason]] = (
        dataclasses.field(default_factory=dict)
    )
    upstream_states: dict[str, UpstreamState] = dataclasses.field(default_factory=dict)
    github_lookup: MergedPullRequestLookup | None = None
    handled_branches: set[str] = dataclasses.field(default_factory=set)


def _offline_evidence(
    candidates: typ.Iterable[_PlonkCandidate],
    adapter: _GitPort,
    trunk_ref: str,
) -> dict[str, _CompletionEvidence]:
    """Return the offline evidence trunk history supplies, keyed by branch."""
    return {
        candidate.branch_name: evidence
        for candidate, evidence in plonk_policy.completed_candidates(
            candidates,
            adapter.history_messages(trunk_ref),
        )
    }


def _lookup_pull_request(
    lookup: MergedPullRequestLookup,
    query: _PullRequestQuery,
) -> _MergedPullRequest:
    """Run one lookup inside the GitHub process boundary's observation."""
    with observability.get_recorder().span("github_lookup"):
        result = lookup(query)
    observability.get_recorder().record(
        observability.Observation(
            operation="github_lookup",
            outcome="failure" if result.state == "unknown" else "success",
        )
    )
    return result


def _github_evidence(
    state: _SweepState,
    candidate: _PlonkCandidate,
    repository: _GitHubRepository,
    lookup: MergedPullRequestLookup,
) -> tuple[_CompletionEvidence | None, _UnverifiedReason]:
    """Return the GitHub fallback's verdict for one candidate."""
    if candidate.commit_id is None:
        # Without the local head commit the fallback cannot tell this work
        # apart from an earlier merge of a reused branch name, so it fails
        # closed instead of guessing.
        return None, _UnverifiedReason.GITHUB_UNKNOWN
    result = _lookup_pull_request(
        lookup,
        _PullRequestQuery(
            owner=repository.owner,
            repository=repository.name,
            head_branch=candidate.branch_name,
            base_branch=state.context.branch,
            head_sha=candidate.commit_id,
        ),
    )
    if result.state == "merged":
        if result.number is None:
            return None, _UnverifiedReason.GITHUB_UNKNOWN
        return _CompletionEvidence("github_merged_pr", f"#{result.number}"), (
            _UnverifiedReason.NO_SIGNAL
        )
    if result.state == "unknown":
        return None, _UnverifiedReason.GITHUB_UNKNOWN
    return None, _UnverifiedReason.NO_SIGNAL


def _upstream_evidence(
    state: _SweepState,
    candidate: _PlonkCandidate,
) -> tuple[_CompletionEvidence | None, _UnverifiedReason]:
    """Return the opt-in upstream check's verdict for one candidate."""
    branch_name = candidate.branch_name
    if branch_name not in state.upstream_states:
        state.upstream_states[branch_name] = state.adapter.upstream_state(branch_name)
    match state.upstream_states[branch_name]:
        case "gone":
            return (
                _CompletionEvidence("upstream_gone", candidate.branch_name),
                _UnverifiedReason.NO_SIGNAL,
            )
        case "unset":
            return None, _UnverifiedReason.UPSTREAM_UNSET
        case "present":
            return None, _UnverifiedReason.UPSTREAM_PRESENT
        case _:
            return None, _UnverifiedReason.UPSTREAM_UNKNOWN


def _fallback_evidence(
    state: _SweepState,
    candidate: _PlonkCandidate,
) -> tuple[_CompletionEvidence | None, _UnverifiedReason]:
    """Return fallback evidence for ``candidate``, or why none was found."""
    reason = _UnverifiedReason.NO_SIGNAL
    repository = state.context.github_repository
    lookup = state.github_lookup
    if repository is not None and lookup is not None:
        if candidate.branch_name not in state.github_lookups:
            state.github_lookups[candidate.branch_name] = _github_evidence(
                state,
                candidate,
                repository,
                lookup,
            )
        evidence, reason = state.github_lookups[candidate.branch_name]
        if evidence is not None:
            return evidence, reason

    if not state.options.upstream_gone:
        return None, reason
    evidence, upstream_reason = _upstream_evidence(state, candidate)
    if evidence is not None:
        return evidence, upstream_reason
    # A GitHub lookup that failed is a stronger warning than an upstream that
    # merely exists or is unset, so it survives the second tier.
    if reason is not _UnverifiedReason.GITHUB_UNKNOWN:
        reason = upstream_reason
    return None, reason


@dataclasses.dataclass(frozen=True, slots=True)
class _FallbackResolution:
    """The fallback tiers' answer for one candidate."""

    candidate: _PlonkCandidate
    evidence: _CompletionEvidence | None = None
    reason: _UnverifiedReason | None = None


def _fetch_fallback_verdicts(
    state: _SweepState,
    candidates: typ.Iterable[_PlonkCandidate],
) -> list[_FallbackResolution]:
    """Fetch and cache the fallback tiers' verdict for each candidate.

    This is the fetch phase: it performs the read-only lookups and caches one
    verdict per branch, but it does not log or record per-candidate outcomes.

    Returns
    -------
    list[_FallbackResolution]
        One answer per unresolved candidate, in sweep order.

    """
    resolutions: list[_FallbackResolution] = []
    for candidate in candidates:
        if candidate.branch_name in state.resolutions:
            continue
        evidence, reason = _fallback_evidence(state, candidate)
        resolutions.append(_FallbackResolution(candidate, evidence, reason))
    return resolutions


def _apply_fallbacks(
    state: _SweepState,
    resolutions: typ.Iterable[_FallbackResolution],
) -> list[_UnverifiedWorktree]:
    """Record fallback outcomes and collect the unverified worktrees."""
    unverified: list[_UnverifiedWorktree] = []
    for resolution in resolutions:
        candidate = resolution.candidate
        if resolution.evidence is not None:
            state.resolutions[candidate.branch_name] = resolution.evidence
            _record_signal_step(
                "completion_resolution",
                "success",
                state.mode,
                resolution.evidence,
            )
            continue
        reason = resolution.reason or _UnverifiedReason.NO_SIGNAL
        unverified.append(_UnverifiedWorktree(candidate.worktree_path, reason))
        _log_candidate_left(
            candidate,
            state.mode,
            _CandidateLeftEvent(
                "Leaving git-plonk candidate unverified",
                "completion_resolution",
                reason.value,
            ),
        )
        _record_unverified(state.mode, reason)
    return unverified


def _resolve_fallbacks(
    state: _SweepState,
    candidates: typ.Iterable[_PlonkCandidate],
) -> list[_UnverifiedWorktree]:
    """Resolve active candidates with the GitHub and upstream tiers.

    Candidates are visited in sweep order. The GitHub lookup runs first and at
    most once per branch; the opt-in upstream check runs only for branches it
    left unresolved. Every unresolved candidate is reported as unverified and
    never removed. Query and reporting are separate steps.

    Returns
    -------
    list[_UnverifiedWorktree]
        The candidates no tier resolved, in sweep order.

    """
    return _apply_fallbacks(state, _fetch_fallback_verdicts(state, candidates))
