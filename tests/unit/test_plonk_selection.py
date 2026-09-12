"""Unit tests for ``git-plonk``'s completed-candidate selection.

A worktree is only eligible for cleanup when its branch encodes a completion
marker that trunk history confirms. These tests pin the marker derivations, the
streaming history scan that filters candidates, and the canonical trunk ref the
scan reads: the default branch the principal remote advertises, fetched
explicitly, never a stale ``refs/remotes/<remote>/HEAD`` alias and never a local
``main``. Reading the advertisement is a query and acquiring the ref is a
command, so the two are tested apart.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from git import Repo
from hypothesis import given
from hypothesis import strategies as st

from git_donkey import plonk, plonk_policy, plonk_records
from tests import git_repo_helpers

# History messages consumed before the streaming scan stops: one per candidate
# marker, so the trailing "Unneeded late history" message is never pulled.
_EXPECTED_CONSUMED_HISTORY_MESSAGES = 2

# Exit status ``helpers._die`` uses when the command cannot run at all, which a
# repository without a remote is: Cyclopts is not involved, the command rejects
# its own precondition.
_USAGE_ERROR_EXIT_CODE = 2

# Exit status for a remote that cannot be queried or cannot supply a branch.
_DISCOVERY_FAILURE_EXIT_CODE = 1

_ROADMAP_WORDS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=1,
    max_size=8,
)


def test_issue_branch_marker_matches_issue_reference() -> None:
    """Issue branches should map to GitHub issue merge markers."""
    assert plonk_policy.completion_marker_for_branch("issue-123-fix-bug") == "(#123)", (
        "expected issue branch to map to GitHub issue marker"
    )


@given(namespace=st.none() | _ROADMAP_WORDS, suffix=st.none() | _ROADMAP_WORDS)
def test_roadmap_branch_marker_invariant(
    namespace: str | None,
    suffix: str | None,
) -> None:
    """Roadmap markers should be parenthesized dotted references."""
    prefix = f"{namespace}-" if namespace else ""
    suffix_part = suffix or ""
    branch_name = f"{prefix}1-20-300{suffix_part}-4-implement-plonk"

    marker = plonk_policy.completion_marker_for_branch(branch_name)

    assert marker is not None, "expected roadmap branch to produce a marker"
    assert marker.startswith("("), "expected roadmap marker to start with parenthesis"
    assert marker.endswith(")"), "expected roadmap marker to end with parenthesis"
    assert "-" not in marker, "expected roadmap marker to use dotted separators"
    assert ".." not in marker, "expected roadmap marker to avoid empty components"
    assert plonk_policy.has_completion_marker([f"Merge completed {marker}"], marker), (
        "expected exact roadmap marker to match history"
    )
    dotted_marker = f"{marker.removesuffix(')')}.)"
    assert plonk_policy.has_completion_marker(
        [f"Merge completed {dotted_marker}"], marker
    ), "expected dotted roadmap marker variant to match history"


@given(number=st.integers(min_value=1, max_value=999_999))
def test_issue_branch_marker_invariant(number: int) -> None:
    """Issue markers should match exact issue numbers in commit history."""
    marker = plonk_policy.completion_marker_for_branch(f"issue-{number}-short-title")

    assert marker == f"(#{number})", "expected issue marker to include issue number"
    assert marker is not None, "expected issue branch to produce a marker"
    assert plonk_policy.has_completion_marker([f"Squashed work {marker}"], marker), (
        "expected exact issue marker to match history"
    )
    assert not plonk_policy.has_completion_marker(
        [f"Squashed work (#{number + 1})"], marker
    ), "expected different issue marker not to match history"


def test_roadmap_branch_marker_includes_optional_namespace_suffix_and_task() -> None:
    """Roadmap branches should preserve namespace, suffix, and task number."""
    branch_name = "road-1-2-3a-4-finished-task"

    marker = plonk_policy.completion_marker_for_branch(branch_name)

    assert marker == "(road.1.2.3a.4)", (
        "expected roadmap marker to include namespace, suffix, and task"
    )


def test_unrecognized_branch_has_no_completion_marker() -> None:
    """Unrecognized branches should never be selected for default cleanup."""
    assert plonk_policy.completion_marker_for_branch("feature/unstructured") is None, (
        "expected unrecognized branch to have no completion marker"
    )


def test_completed_candidates_use_history_markers() -> None:
    """Candidate filtering should keep only branches with matching markers."""
    candidates = [
        plonk_records._PlonkCandidate(
            branch_name="issue-123-fix",
            worktree_path=Path("/repo.worktrees/issue-123-fix"),
            marker="(#123)",
        ),
        plonk_records._PlonkCandidate(
            branch_name="road-1-2-3a-4-task",
            worktree_path=Path("/repo.worktrees/road-1-2-3a-4-task"),
            marker="(road.1.2.3a.4)",
        ),
        plonk_records._PlonkCandidate(
            branch_name="issue-456-open",
            worktree_path=Path("/repo.worktrees/issue-456-open"),
            marker="(#456)",
        ),
    ]

    completed = plonk_policy.completed_candidates(
        candidates,
        ["Merge pull request (#123)", "Roadmap complete (road.1.2.3a.4.)"],
    )

    assert [candidate.branch_name for candidate in completed] == [
        "issue-123-fix",
        "road-1-2-3a-4-task",
    ], "expected only candidates with matching history markers"


def test_completed_candidates_streams_history_until_markers_match() -> None:
    """Candidate filtering should not consume history after all markers match."""
    candidates = [
        plonk_records._PlonkCandidate(
            branch_name="issue-123-fix",
            worktree_path=Path("/repo.worktrees/issue-123-fix"),
            marker="(#123)",
        ),
        plonk_records._PlonkCandidate(
            branch_name="issue-456-fix",
            worktree_path=Path("/repo.worktrees/issue-456-fix"),
            marker="(#456)",
        ),
    ]

    consumed_messages = 0

    def messages() -> typ.Iterator[str]:
        nonlocal consumed_messages
        consumed_messages += 1
        yield "Merge pull request (#123)"
        consumed_messages += 1
        yield "Merge pull request (#456)"
        consumed_messages += 1
        yield "Unneeded late history"

    completed = plonk_policy.completed_candidates(candidates, messages())

    assert [candidate.branch_name for candidate in completed] == [
        "issue-123-fix",
        "issue-456-fix",
    ], "expected streaming scan to stop after all markers match"
    assert consumed_messages == _EXPECTED_CONSUMED_HISTORY_MESSAGES, (
        "expected history scan to stop after all markers match"
    )


def test_fetched_trunk_ref_ignores_a_stale_remote_head_alias(
    tmp_path: Path,
) -> None:
    """Completion history should follow the advertised default, not a stale alias."""
    repo, _remote_repo = git_repo_helpers.repo_with_remote_default(
        tmp_path / "local", tmp_path / "remote.git"
    )
    repo.git.push("origin", "main:refs/heads/legacy")
    repo.remote("origin").fetch()
    repo.git.symbolic_ref("refs/remotes/origin/HEAD", "refs/remotes/origin/legacy")

    trunk_ref = plonk._fetch_canonical_trunk_ref(repo)

    assert (
        repo.git.symbolic_ref("refs/remotes/origin/HEAD")
        == "refs/remotes/origin/legacy"
    ), "the fixture leaves a local alias naming a branch the remote does not default to"
    assert trunk_ref == "refs/remotes/origin/main", (
        "the advertised default wins over the stale local alias"
    )


def test_advertised_trunk_reads_the_advertisement_without_fetching(
    tmp_path: Path,
) -> None:
    """Querying the advertised trunk should not acquire the ref it names."""
    repo, _remote_repo = git_repo_helpers.repo_with_remote_default(
        tmp_path / "local", tmp_path / "remote.git", default_branch="trunk"
    )
    repo.git.update_ref("-d", "refs/remotes/origin/trunk")

    remote, branch = plonk._advertised_trunk(repo)

    assert (remote, branch) == ("origin", "trunk"), (
        "the query reports the remote and the branch it advertises"
    )
    remote_refs = repo.git.for_each_ref("--format=%(refname)", "refs/remotes")
    assert not remote_refs, "reading the advertisement creates no remote-tracking ref"


def test_fetched_trunk_ref_follows_the_advertised_default_name(
    tmp_path: Path,
) -> None:
    """A repository whose trunk is not ``main`` should not be judged against main."""
    repo, _remote_repo = git_repo_helpers.repo_with_remote_default(
        tmp_path / "local", tmp_path / "remote.git", default_branch="trunk"
    )

    trunk_ref = plonk._fetch_canonical_trunk_ref(repo)

    assert repo.active_branch.name == "main", (
        "the fixture keeps the local branch discovery must ignore"
    )
    assert trunk_ref == "refs/remotes/origin/trunk", (
        "the advertised default is fetched into its remote-tracking ref"
    )
    assert repo.commit(trunk_ref).hexsha == repo.commit("main").hexsha, (
        "the fetched trunk holds the remote's history"
    )


def test_advertised_trunk_requires_a_remote(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repository without a remote cannot resolve a trunk, and must not guess."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")

    with pytest.raises(SystemExit) as exc_info:
        plonk._advertised_trunk(repo)

    assert exc_info.value.code == _USAGE_ERROR_EXIT_CODE, (
        "a repository with no remote cannot run the command at all"
    )
    assert "git-plonk: no remotes configured" in capsys.readouterr().err, (
        "the failure names the command and the missing remote"
    )


def test_advertised_trunk_fails_without_an_advertised_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A remote that advertises no default should fail rather than fall back."""
    remote_path = tmp_path / "remote.git"
    Repo.init(remote_path, bare=True)
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    repo.create_remote("origin", remote_path.as_posix())

    with pytest.raises(SystemExit) as exc_info:
        plonk._advertised_trunk(repo)

    assert exc_info.value.code == _DISCOVERY_FAILURE_EXIT_CODE, (
        "an unadvertised default is an error, not a licence to guess"
    )
    assert "does not advertise a default branch" in capsys.readouterr().err, (
        "the failure explains that the remote named no default"
    )
