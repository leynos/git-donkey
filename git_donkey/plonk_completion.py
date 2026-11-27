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

from git_donkey import helpers, plonk_github, plonk_policy
from git_donkey.plonk_records import (
    GIT_PLONK_PREFIX,
    UpstreamState,
    _CompletionEvidence,
    _PlonkCandidate,
    _PlonkContext,
    _PlonkMode,
    _UnverifiedReason,
    _UnverifiedWorktree,
)
from git_donkey.plonk_workflow import (
    _CleanupOptions,
    _log_unverified_candidate,
    _record_signal_step,
    _record_unverified,
)

if typ.TYPE_CHECKING:
    from git_donkey.plonk_adapters import _GitWorktreeAdapter

_GITHUB_FALLBACK_NOTICE = (
    "gh is not installed; merged pull-request lookups are unavailable"
)


@dataclasses.dataclass(slots=True)
class _SweepState:
    """Mutable state shared by one completed-cleanup sweep.

    Attributes
    ----------
    context : _PlonkContext
        Resolved repository state the sweep runs against.
    adapter : _GitWorktreeAdapter
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

    """

    context: _PlonkContext
    adapter: _GitWorktreeAdapter
    mode: _PlonkMode
    options: _CleanupOptions
    resolutions: dict[str, _CompletionEvidence] = dataclasses.field(
        default_factory=dict
    )
    github_lookups: dict[str, tuple[_CompletionEvidence | None, _UnverifiedReason]] = (
        dataclasses.field(default_factory=dict)
    )
    upstream_states: dict[str, UpstreamState] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class _GitHubTier:
    """The read-only GitHub fallback one sweep may use.

    Attributes
    ----------
    repository : plonk_github._GitHubRepository | None
        Repository parsed from the raw principal-remote URL, or ``None`` when
        the fallback is disabled or the URL is not GitHub.
    lookup : plonk_github.MergedPullRequestLookup | None
        Injected or real lookup, or ``None`` when no lookup is available.

    """

    repository: plonk_github._GitHubRepository | None = None
    lookup: plonk_github.MergedPullRequestLookup | None = None


def _offline_evidence(
    candidates: typ.Iterable[_PlonkCandidate],
    adapter: _GitWorktreeAdapter,
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


def _github_resolution(state: _SweepState) -> _GitHubTier:
    """Return the GitHub tier the sweep may use, if any.

    The fallback is enabled by default only for a GitHub principal remote when
    ``gh`` is installed and the caller has not disabled it. A missing tool is
    reported once per run as a notice; the sweep then fails closed, leaving its
    unresolved candidates unverified.

    Returns
    -------
    _GitHubTier
        The repository to query and the lookup to use. Both are ``None`` when
        the fallback is unavailable, and the lookup alone is ``None`` when the
        repository is GitHub but ``gh`` is missing.

    """
    if state.options.no_github:
        return _GitHubTier()
    repository = plonk_github.parse_github_remote_url(state.context.remote_url)
    if repository is None:
        return _GitHubTier()
    if state.options.github_lookup is not None:
        return _GitHubTier(repository, state.options.github_lookup)
    if not plonk_github.gh_available():
        helpers._eprint(f"{GIT_PLONK_PREFIX}: {_GITHUB_FALLBACK_NOTICE}")
        return _GitHubTier(repository)
    return _GitHubTier(repository, plonk_github.merged_pull_request)


def _github_evidence(
    state: _SweepState,
    candidate: _PlonkCandidate,
    repository: plonk_github._GitHubRepository,
    lookup: plonk_github.MergedPullRequestLookup,
) -> tuple[_CompletionEvidence | None, _UnverifiedReason]:
    """Return the GitHub fallback's verdict for one candidate."""
    result = lookup(
        plonk_github._PullRequestQuery(
            owner=repository.owner,
            repository=repository.name,
            head_branch=candidate.branch_name,
            base_branch=state.context.branch,
        )
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
    github: _GitHubTier,
) -> tuple[_CompletionEvidence | None, _UnverifiedReason]:
    """Return fallback evidence for ``candidate``, or why none was found."""
    reason = _UnverifiedReason.NO_SIGNAL
    if github.repository is not None and github.lookup is not None:
        if candidate.branch_name not in state.github_lookups:
            state.github_lookups[candidate.branch_name] = _github_evidence(
                state,
                candidate,
                github.repository,
                github.lookup,
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


def _resolve_fallbacks(
    state: _SweepState,
    candidates: typ.Iterable[_PlonkCandidate],
) -> list[_UnverifiedWorktree]:
    """Resolve active candidates with the GitHub and upstream tiers.

    Candidates are visited in sweep order. The GitHub lookup runs first and at
    most once per branch; the opt-in upstream check runs only for branches it
    left unresolved. Every unresolved candidate is reported as unverified and
    never removed.

    Returns
    -------
    list[_UnverifiedWorktree]
        The candidates no tier resolved, in sweep order.

    """
    github = _github_resolution(state)
    unverified: list[_UnverifiedWorktree] = []
    for candidate in candidates:
        if candidate.branch_name in state.resolutions:
            continue
        evidence, reason = _fallback_evidence(state, candidate, github)
        if evidence is not None:
            state.resolutions[candidate.branch_name] = evidence
            _record_signal_step(
                "completion_resolution",
                "success",
                state.mode,
                evidence,
            )
            continue
        unverified.append(_UnverifiedWorktree(candidate.worktree_path, reason))
        _log_unverified_candidate(candidate, reason, state.mode)
        _record_unverified(state.mode, reason)
    return unverified
