"""Unit contracts for resolving an explicit ``git donkey`` base.

The workflow keeps every input that already worked — a literal local branch
name, a canonical ``refs/`` value, and a bare branch name on the principal
remote — while also accepting a remote-qualified branch, a tag, or any
commit-ish Git can resolve. These tests pin that precedence and the commit
resolution behind the widened contract, using real repositories so Git itself,
not a double, decides what a base names.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from git_donkey import donkey, helpers
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from git import Repo

_PRINCIPAL_REMOTE = "origin"
_CLONE_URL = "https://example.invalid/repository.git"


def _context(repo: Repo) -> donkey._DonkeyContext:
    """Return a workflow context whose repository is ``repo``."""
    return donkey._DonkeyContext(
        repo_home=repo,
        remote=_PRINCIPAL_REMOTE,
        branch_to_worktree={},
        worktrees_root=Path(repo.working_tree_dir or ".") / "worktrees",
        caller_repo=repo,
    )


def _seed_with_remote_ref(
    tmp_path: Path,
    *,
    remote: str = _PRINCIPAL_REMOTE,
    branch: str = "main",
) -> tuple[Repo, str]:
    """Create a repository with a remote-tracking ref at its seed commit."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    repo.create_remote(remote, _CLONE_URL)
    commit = repo.head.commit.hexsha
    repo.git.update_ref(f"refs/remotes/{remote}/{branch}", commit)
    return repo, commit


def test_principal_remote_qualified_base_becomes_canonical_ref(
    tmp_path: Path,
) -> None:
    """A configured ``<remote>/<branch>`` base names its remote-tracking ref."""
    repo, _commit = _seed_with_remote_ref(tmp_path)
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, "origin/main") == (
        "refs/remotes/origin/main"
    ), "a configured remote prefix is made canonical when its ref exists"


def test_non_principal_remote_qualified_base_becomes_canonical_ref(
    tmp_path: Path,
) -> None:
    """A remote other than the principal one is resolved through its own ref."""
    repo, _commit = _seed_with_remote_ref(tmp_path, remote="upstream")
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, "upstream/main") == (
        "refs/remotes/upstream/main"
    ), "every configured remote can qualify a base, not only the principal one"


def test_nested_remote_name_is_resolved_whole(tmp_path: Path) -> None:
    """A remote whose name contains a slash keeps its whole prefix."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    repo.create_remote("team", _CLONE_URL)
    # `git remote add` rejects a name that extends another, but Git reads such
    # a section from the configuration, which is how nested remotes are used.
    with repo.config_writer() as config:
        config.set_value('remote "team/core"', "url", _CLONE_URL)
    commit = repo.head.commit.hexsha
    repo.git.update_ref("refs/remotes/team/core/main", commit)
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, "team/core/main") == (
        "refs/remotes/team/core/main"
    ), "the longest matching remote name supplies the canonical ref"
    assert donkey._normalize_explicit_base(context, "team/main") == "team/main", (
        "a prefix with no matching ref is left for commit resolution"
    )


def test_literal_local_branch_wins_over_remote_qualified_form(
    tmp_path: Path,
) -> None:
    """A local branch whose name contains a slash keeps its literal meaning."""
    repo, commit = _seed_with_remote_ref(tmp_path)
    repo.create_head("origin/feature", commit)
    repo.git.update_ref("refs/remotes/origin/feature", commit)
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, "origin/feature") == (
        "origin/feature"
    ), "an existing local branch must not be reinterpreted as a remote ref"


@pytest.mark.parametrize(
    "base_branch",
    [
        "refs/remotes/origin/main",
        "refs/heads/main",
        "refs/tags/v1.0",
    ],
)
def test_canonical_refs_are_left_unchanged(
    tmp_path: Path,
    base_branch: str,
) -> None:
    """A fully qualified ref is used exactly as supplied."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, base_branch) == base_branch, (
        "canonical refs are never rewritten by remote-prefix normalization"
    )


def test_unconfigured_remote_prefix_is_left_unchanged(tmp_path: Path) -> None:
    """A prefix that names no configured remote is not normalized."""
    repo, commit = _seed_with_remote_ref(tmp_path)
    repo.git.update_ref("refs/remotes/elsewhere/main", commit)
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, "elsewhere/main") == (
        "elsewhere/main"
    ), "only a configured remote's prefix changes how a base is interpreted"


def test_bare_local_branch_is_left_unchanged(tmp_path: Path) -> None:
    """A bare name that names a local branch keeps that branch's meaning."""
    repo, _commit = _seed_with_remote_ref(tmp_path)
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, "main") == "main", (
        "a local branch name is never rewritten"
    )


def test_missing_remote_qualified_ref_is_left_for_commit_resolution(
    tmp_path: Path,
) -> None:
    """A configured prefix with no matching ref keeps its supplied form."""
    repo, _commit = _seed_with_remote_ref(tmp_path)
    context = _context(repo)

    assert donkey._normalize_explicit_base(context, "origin/absent") == (
        "origin/absent"
    ), "normalization only treats an existing remote-tracking ref as canonical"


@pytest.fixture
def tagged_repo(tmp_path: Path) -> tuple[Repo, str]:
    """Return a repository with lightweight and annotated tags on its commit."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    repo.create_tag("lightweight")
    repo.create_tag("annotated", message="annotated release")
    return repo, repo.head.commit.hexsha


def test_resolves_lightweight_tag(tagged_repo: tuple[Repo, str]) -> None:
    """A lightweight tag resolves to the commit it names."""
    repo, commit = tagged_repo

    assert helpers._resolve_commit(repo, "lightweight") == commit, (
        "a lightweight tag names its commit"
    )


def test_resolves_annotated_tag(tagged_repo: tuple[Repo, str]) -> None:
    """An annotated tag is peeled to the commit it points at."""
    repo, commit = tagged_repo

    assert helpers._resolve_commit(repo, "annotated") == commit, (
        "an annotated tag is peeled to its commit"
    )


def test_resolves_full_commit_id(tagged_repo: tuple[Repo, str]) -> None:
    """A full object ID resolves to itself."""
    repo, commit = tagged_repo

    assert helpers._resolve_commit(repo, commit) == commit, (
        "a full commit ID names its commit"
    )


def test_resolves_abbreviated_commit_id(tagged_repo: tuple[Repo, str]) -> None:
    """An abbreviated object ID resolves to the full commit ID."""
    repo, commit = tagged_repo

    assert helpers._resolve_commit(repo, commit[:8]) == commit, (
        "an abbreviated commit ID is expanded to the full ID"
    )


def test_resolves_remote_tracking_ref(tmp_path: Path) -> None:
    """A canonical remote-tracking ref resolves without localizing it."""
    repo, commit = _seed_with_remote_ref(tmp_path)

    assert helpers._resolve_commit(repo, "refs/remotes/origin/main") == commit, (
        "a remote-tracking ref resolves to the commit it holds"
    )


def test_rejects_tree_object(tmp_path: Path) -> None:
    """A tree object is not a commit and must not supply a start point."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    tree = repo.git.rev_parse("HEAD^{tree}")

    assert helpers._resolve_commit(repo, tree) is None, (
        "a tree ID cannot name the commit a new branch starts from"
    )


def test_returns_none_for_missing_name(tagged_repo: tuple[Repo, str]) -> None:
    """An unknown name resolves to no commit."""
    repo, _commit = tagged_repo

    assert helpers._resolve_commit(repo, "no-such-revision") is None, (
        "an unknown name resolves to no commit"
    )


@pytest.mark.parametrize("revision", ["", "--all"])
def test_returns_none_for_non_revision_input(
    tagged_repo: tuple[Repo, str],
    revision: str,
) -> None:
    """An empty value and an option-like value resolve to no commit."""
    repo, _commit = tagged_repo

    assert helpers._resolve_commit(repo, revision) is None, (
        "--end-of-options keeps an option-like input from listing every ref"
    )


@pytest.mark.parametrize(
    ("base_branch", "remote_names", "expected"),
    [
        ("main", ["origin"], "main"),
        ("refs/heads/main", ["origin"], "main"),
        ("refs/remotes/origin/main", ["origin"], "main"),
        (
            "refs/remotes/origin/refs/heads/main",
            ["origin"],
            "refs/heads/main",
        ),
        (
            "refs/remotes/upstream/main",
            ["origin", "upstream"],
            None,
        ),
        (
            "refs/remotes/origin/fork/main",
            ["origin", "origin/fork"],
            None,
        ),
        ("refs/tags/v1.0", ["origin"], None),
        ("refs/stash", ["origin"], None),
        ("v1.0", ["origin"], "v1.0"),
    ],
)
def test_local_branch_for_base_removes_one_namespace(
    base_branch: str,
    remote_names: list[str],
    expected: str | None,
) -> None:
    """Only one outer ref namespace is removed when naming a local branch."""
    assert (
        donkey._local_branch_for_base(
            base_branch, remote="origin", remote_names=remote_names
        )
        == expected
    ), "a base owned by another remote or outside refs/heads names no branch"


def test_resolves_message_search_revision(tmp_path: Path) -> None:
    """A message-search revision is resolved before the commit suffix is added."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    release_path = tmp_path / "local" / "release.txt"
    release_path.write_text("release")
    repo.index.add([release_path.as_posix()])
    named = repo.index.commit("release").hexsha

    assert helpers._resolve_commit(repo, ":/^release$") == named, (
        "^{commit} must not become part of the message-search pattern"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("refs/tags/v1.0", False),
        ("refs/remotes/origin/main", False),
        ("refs/remotes/origin/main~1", True),
        ("refs/tags/v1.0^{commit}", True),
        ("HEAD@{1}", True),
    ],
)
def test_is_revision_expression(value: str, *, expected: bool) -> None:
    """Only values with revision syntax are treated as expressions."""
    assert helpers._is_revision_expression(value) is expected, (
        "a literal ref path contains none of the revision syntax characters"
    )
