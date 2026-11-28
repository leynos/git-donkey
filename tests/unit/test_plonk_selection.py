"""Unit tests for ``git-plonk``'s completed-candidate selection.

A worktree is only eligible for cleanup when its branch encodes a completion
marker that trunk history confirms. These tests pin the marker derivations, the
stanza filtering that turns ``git worktree list --porcelain`` output into
candidates, the streaming history scan that narrows them to completed work, and
the canonical trunk ref the scan reads: the default branch the principal remote
advertises, fetched explicitly, never a stale ``refs/remotes/<remote>/HEAD``
alias and never a local ``main``. Reading the advertisement is a query and
acquiring the ref is a command, so the two are tested apart.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from git import Repo
from hypothesis import given
from hypothesis import strategies as st

from git_donkey import (
    helpers,
    layout,
    plonk,
    plonk_policy,
    plonk_records,
    plonk_selection,
)
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

# Directory ``git donkey`` groups its linked worktrees under, beside the clone.
_WORKTREES_ROOT_NAME = "repo.worktrees"


def _stanza(worktree_path: Path, branch: str) -> dict[str, object]:
    """Build the parsed stanza ``git worktree list --porcelain`` yields.

    Parameters
    ----------
    worktree_path : Path
        Linked worktree path, as Git would print it.
    branch : str
        Local branch name, without the ``refs/heads/`` prefix Git prints.

    Returns
    -------
    dict[str, object]
        One stanza as :func:`git_donkey.helpers._parse_worktree_porcelain`
        produces it.

    """
    return {"worktree": worktree_path.as_posix(), "branch": f"refs/heads/{branch}"}


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


def test_detached_head_stanza_yields_no_branch_or_candidate(tmp_path: Path) -> None:
    """A detached worktree should carry no branch, so it is never a candidate."""
    worktrees_root = tmp_path / _WORKTREES_ROOT_NAME
    anchor = _stanza(tmp_path, "main")
    stanza: dict[str, object] = {
        "worktree": (worktrees_root / "issue-123-fix").as_posix(),
        "detached": True,
        "HEAD": "0" * 40,
    }

    assert plonk_selection._branch_name_from_stanza(stanza) is None, (
        "a detached worktree names no local branch"
    )
    assert not plonk_selection._donkey_worktree_candidates(
        [anchor, stanza], worktrees_root, tmp_path
    ), "a worktree with no branch cannot be judged complete"


def test_stanza_without_a_worktree_field_is_ignored(tmp_path: Path) -> None:
    """A stanza naming no path should not become a candidate or a path."""
    worktrees_root = tmp_path / _WORKTREES_ROOT_NAME
    anchor = _stanza(tmp_path, "main")
    stanza: dict[str, object] = {"branch": "refs/heads/issue-123-fix"}

    assert plonk_selection._worktree_path_from_stanza(stanza) is None, (
        "a stanza with no worktree field yields no path"
    )
    assert not plonk_selection._donkey_worktree_paths(
        [anchor, stanza], worktrees_root, tmp_path
    ), "the path filter skips the stanza rather than inventing a location"
    assert not plonk_selection._donkey_worktree_candidates(
        [anchor, stanza], worktrees_root, tmp_path
    ), "a pathless stanza cannot be cleaned up"


def test_worktree_path_is_expanded_and_resolved() -> None:
    """A stanza path should be made absolute, with ``~`` expanded."""
    worktree_path = plonk_selection._worktree_path_from_stanza({
        "worktree": f"~/{_WORKTREES_ROOT_NAME}/issue-123-fix"
    })

    assert worktree_path is not None, "a named worktree yields a path"
    assert worktree_path.is_absolute(), "Git's output is made absolute before use"
    assert (
        worktree_path
        == (Path.home() / _WORKTREES_ROOT_NAME / "issue-123-fix").resolve()
    ), "the tilde is expanded against the user's home directory"


def test_worktree_root_and_outside_paths_are_not_donkey_worktrees(
    tmp_path: Path,
) -> None:
    """Only paths strictly below the worktrees root belong to git-donkey."""
    worktrees_root = tmp_path / _WORKTREES_ROOT_NAME

    assert not plonk_selection._is_git_donkey_worktree(
        worktrees_root, worktrees_root
    ), "the worktrees root is not itself a donkey worktree"
    assert not plonk_selection._is_git_donkey_worktree(tmp_path, worktrees_root), (
        "the main checkout is not a donkey worktree"
    )
    assert not plonk_selection._is_git_donkey_worktree(
        tmp_path / f"{_WORKTREES_ROOT_NAME}-other" / "issue-123-fix", worktrees_root
    ), "a sibling sharing the root's name as a string prefix is still outside it"


def test_donkey_worktree_paths_keep_only_worktrees_under_the_root(
    tmp_path: Path,
) -> None:
    """Path selection should exclude the main checkout and outside worktrees."""
    worktrees_root = tmp_path / _WORKTREES_ROOT_NAME
    inside = worktrees_root / "issue-123-fix"
    outside = tmp_path / "elsewhere" / "issue-123-fix"
    stanzas: list[dict[str, object]] = [
        {
            "worktree": tmp_path.as_posix(),
            "branch": "refs/heads/main",
        },
        _stanza(inside, "issue-123-fix"),
        _stanza(outside, "issue-124-fix"),
    ]

    assert plonk_selection._donkey_worktree_paths(
        stanzas, worktrees_root, tmp_path
    ) == [inside.resolve()], (
        "only the linked worktree under the donkey root is a cleanup target"
    )


def test_donkey_worktree_candidates_retain_every_branched_worktree(
    tmp_path: Path,
) -> None:
    """Candidate selection should keep every in-root branch, recognized or not."""
    worktrees_root = tmp_path / _WORKTREES_ROOT_NAME
    stanzas = [
        _stanza(tmp_path, "main"),
        _stanza(worktrees_root / "feature-work", "feature/unstructured"),
        _stanza(worktrees_root / "road-1-2-3a-4-task", "road-1-2-3a-4-task"),
        _stanza(tmp_path / "elsewhere" / "issue-124-fix", "issue-124-fix"),
        _stanza(worktrees_root / "issue-123-fix", "issue-123-fix"),
    ]

    candidates = plonk_selection._donkey_worktree_candidates(
        stanzas, worktrees_root, tmp_path
    )

    assert [candidate.branch_name for candidate in candidates] == [
        "feature/unstructured",
        "road-1-2-3a-4-task",
        "issue-123-fix",
    ], "worktrees outside the root are excluded; unrecognized branches are kept"
    assert [candidate.marker for candidate in candidates] == [
        None,
        "(road.1.2.3a.4)",
        "(#123)",
    ], "each candidate carries the marker its branch encodes, if any"
    assert [candidate.issue_number for candidate in candidates] == [
        None,
        None,
        123,
    ], "only an issue branch exposes an issue number for the history matchers"


def test_candidates_carry_the_checked_out_commit_and_lock_state(
    tmp_path: Path,
) -> None:
    """Selection should preserve the details the later tiers rely on."""
    worktrees_root = tmp_path / _WORKTREES_ROOT_NAME
    commit_id = "a" * 40
    stanzas: list[dict[str, object]] = [
        {
            "worktree": (worktrees_root / "issue-123-fix").as_posix(),
            "HEAD": commit_id,
            "branch": "refs/heads/issue-123-fix",
            "locked": True,
        },
        _stanza(worktrees_root / "issue-456-fix", "issue-456-fix"),
    ]

    candidates = plonk_selection._donkey_worktree_candidates(
        stanzas, worktrees_root, tmp_path
    )

    assert [candidate.commit_id for candidate in candidates] == [commit_id, None], (
        "each candidate carries the checked-out commit, when Git reported one"
    )
    assert [candidate.is_locked for candidate in candidates] == [True, False], (
        "each candidate carries the registration's lock state"
    )


def test_anchor_stanza_is_never_a_cleanup_target(tmp_path: Path) -> None:
    """The main checkout stays out of scope even when a root contains it."""
    worktrees_root = tmp_path / "root"
    anchor = worktrees_root / "main-checkout"
    linked = worktrees_root / "issue-123-fix"
    stanzas = [
        _stanza(anchor, "road-1-2-3a-4-task"),
        _stanza(linked, "issue-123-fix"),
    ]

    assert plonk_selection._donkey_worktree_paths(stanzas, worktrees_root, anchor) == [
        linked.resolve()
    ], "the anchor must not be selected for soft cleanup"
    assert [
        candidate.branch_name
        for candidate in plonk_selection._donkey_worktree_candidates(
            stanzas, worktrees_root, anchor
        )
    ] == ["issue-123-fix"], (
        "the anchor must not become a completed-cleanup candidate, even when its "
        "branch carries a marker"
    )


def test_stanzas_without_the_anchor_remain_cleanup_targets(tmp_path: Path) -> None:
    """Every stanza stays eligible when the listing omits the anchor."""
    worktrees_root = tmp_path / _WORKTREES_ROOT_NAME
    stanzas = [_stanza(worktrees_root / "issue-123-fix", "issue-123-fix")]

    assert plonk_selection._donkey_worktree_paths(
        stanzas, worktrees_root, tmp_path / "repo"
    ) == [(worktrees_root / "issue-123-fix").resolve()], (
        "exclusion is by identity, so omitting the anchor does not drop a candidate"
    )


def test_symlinked_configured_root_scopes_cleanup_to_the_real_directory(
    tmp_path: Path,
) -> None:
    """Cleanup follows the resolved root, not its lexical collapse."""
    repo, _remote = git_repo_helpers.repo_with_remote_default(
        tmp_path / "repo", tmp_path / "remote.git"
    )
    shared_subdir = tmp_path / "shared" / "subdir"
    shared_subdir.mkdir(parents=True)
    intended_root = tmp_path / "shared" / "trees"
    intended_root.mkdir()
    link = tmp_path / "repo" / "link"
    link.symlink_to(shared_subdir, target_is_directory=True)
    repo.git.config(layout.WORKTREES_ROOT_CONFIG_KEY, "link/../trees")

    resolved = layout.resolve_layout(repo, helpers._parse_worktree_porcelain(repo))
    stanzas: list[dict[str, object]] = [
        _stanza(tmp_path / "repo", "main"),
        _stanza(intended_root / "issue-123-fix", "issue-123-fix"),
        _stanza(tmp_path / "repo" / "trees" / "issue-456-fix", "issue-456-fix"),
    ]

    paths = plonk_selection._donkey_worktree_paths(
        stanzas, resolved.worktrees_root, resolved.anchor_dir
    )

    assert paths == [(intended_root / "issue-123-fix").resolve()], (
        "the resolved root must scope cleanup; a worktree under the lexical "
        "collapse of the same configuration is out of scope"
    )


def test_completed_candidates_use_history_markers() -> None:
    """Candidate filtering should keep only branches with matching markers."""
    candidates = [
        plonk_records._PlonkCandidate(
            branch_name="issue-123-fix",
            worktree_path=Path("/repo.worktrees/issue-123-fix"),
            marker="(#123)",
            issue_number=123,
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
            issue_number=456,
        ),
    ]

    completed = plonk_policy.completed_candidates(
        candidates,
        ["Merge pull request (#123)", "Roadmap complete (road.1.2.3a.4.)"],
    )

    assert [candidate.branch_name for candidate, _ in completed] == [
        "issue-123-fix",
        "road-1-2-3a-4-task",
    ], "expected only candidates with matching history markers"
    assert [evidence.signal for _, evidence in completed] == [
        "marker",
        "marker",
    ], "expected the marker tier to supply the evidence"


def test_completed_candidates_streams_history_until_markers_match() -> None:
    """Candidate filtering should not consume history after all markers match."""
    candidates = [
        plonk_records._PlonkCandidate(
            branch_name="issue-123-fix",
            worktree_path=Path("/repo.worktrees/issue-123-fix"),
            marker="(#123)",
            issue_number=123,
        ),
        plonk_records._PlonkCandidate(
            branch_name="issue-456-fix",
            worktree_path=Path("/repo.worktrees/issue-456-fix"),
            marker="(#456)",
            issue_number=456,
        ),
    ]

    consumed_messages = 0

    def messages() -> typ.Iterator[str]:
        """Yield trunk messages, counting how many the consumer pulls."""
        nonlocal consumed_messages
        consumed_messages += 1
        yield "Merge pull request (#123)"
        consumed_messages += 1
        yield "Merge pull request (#456)"
        consumed_messages += 1
        yield "Unneeded late history"

    completed = plonk_policy.completed_candidates(candidates, messages())

    assert [candidate.branch_name for candidate, _ in completed] == [
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

    trunk_ref = plonk._resolve_trunk(repo)[2]

    assert (
        repo.git.symbolic_ref("refs/remotes/origin/HEAD")
        == "refs/remotes/origin/legacy"
    ), "the fixture leaves a local alias naming a branch the remote does not default to"
    assert trunk_ref == "refs/remotes/origin/main", (
        "the advertised default wins over the stale local alias"
    )


def test_configured_remote_url_uses_the_first_fetch_url(tmp_path: Path) -> None:
    """Git fetches from the first configured URL, so the fallback parses it."""
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    repo.create_remote("origin", "git@github.com:leynos/first.git")
    repo.git.config(
        "--add",
        "remote.origin.url",
        "git@github.com:leynos/second.git",
    )

    assert plonk._configured_remote_url(repo, "origin") == (
        "git@github.com:leynos/first.git"
    ), "expected the first raw URL, matching Git's fetch behaviour"


def test_configured_remote_url_is_empty_when_unset(tmp_path: Path) -> None:
    """A repository without the remote URL should not guess a fallback."""
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")

    assert not plonk._configured_remote_url(repo, "origin"), (
        "expected an unset remote URL to disable the GitHub fallback"
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

    trunk_ref = plonk._resolve_trunk(repo)[2]

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
